"""LiveKit Agent对话行为适配层:VoiceAssistant 决定每轮对话怎样进入 LLM、RAG 工具是否可用，以及user消息、RAG Evidence、assistant消息怎样用同一个 turn_index 对齐
所在的流程：
LiveKit 房间
  ↓
providers.py 创建 AgentSession：负责使用哪些模型、怎样创建语音流水线
  ├── STT
  ├── LLM
  ├── TTS
  └── VAD
  ↓
assistant.py：VoiceAssistant：负责这条流水线里的助手应该怎样行动
  ├── 接收用户最终转写
  ├── 管理 turn_index
  ├── 保存 user/assistant 消息
  ├── 向 LLM 暴露 RAG 工具
  └── 控制 auto/never 模式
  ↓
ContextManager
  ├── 消息落盘
  ├── 短追问改写
  └── RAG 调用协调
  ↓
RagClient
  ↓
RAG Core

VoiceAssistant 继承 LiveKit Agent，并重写 llm_node 将实时对话事件接入 LiveRAG 业务;
每发现一条新增用户消息就递增 turn_index 并持久化,本轮所有 RAG 工具调用和最终助手回答都复用这个编号，从而实现 Evidence 对齐;
auto 模式保留知识库 Function Tool，由模型自主调用；never 模式在交给 LLM 前直接过滤该工具;
LLM 输出仍按 Chunk 实时交给 TTS，同时在本地累积完整文本，生成结束后写入 Session 存档。
"""

import time
from asyncio.log import logger
from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Any

from livekit.agents import Agent, ToolError, llm

from liverag.config.settings import RagToolMode
from liverag.context.manager import ContextManager
from liverag.logging.events import EventLogger


# TODO
@dataclass
class _TurnLatencyTrace:
    """保存单轮对话里模型与工具链路的关键时间点。"""

    turn_index: int
    inference_started_at: float  # LLM开始推理时间
    llm_cycle_index: int = 0
    llm_cycle_started_at: float | None = None
    tool_decision_at: float | None = None  # LLM决定调用RAG
    rag_completed_at: float | None = None  # RAG查询完成
    tool_returned_at: float | None = None  # 结果返回给LLM
    output_started_at: float | None = None
    used_tool: bool = False


class VoiceAssistant(Agent):
    """继承LiveKit的Agent，把LiveKit事件和LiveRAG业务连接起来"""

    def __init__(
        self,
        *,
        context_manager: ContextManager,
        session_system_prompt: str,
        rag_tool_mode: RagToolMode,
        event_logger: EventLogger | None = None,
    ) -> None:
        super().__init__(instructions=session_system_prompt)    #固定好的本轮session的session_system_prompt
        self.context_manager = context_manager
        self.rag_tool_mode = rag_tool_mode
        self.event_logger = event_logger
        self.turn_index = 0
        # self._turn_traces=dict[int, _TurnLatencyTrace] = {}
        self._last_recorded_user_count = (
            0  # 当前已从 LiveKit ChatContext 保存的用户消息数，用于防止重复落盘。
        )
        # 成功注册 AgentSession committed 事件后，消息落盘以事件为准；
        # 不支持事件 API 的兼容环境仍使用 llm_node() 兜底。
        self._session_context_hooks_registered = False

    def record_committed_user_transcript(self, content: str) -> int:
        """保存 LiveKit 已确认的最终用户转写，并分配 turn_index。"""

        clean_text = content.strip()
        if not clean_text:
            return self.turn_index

        self.turn_index += 1
        self.context_manager.record_user_message(
            content=clean_text,
            turn_index=self.turn_index,
        )
        self._last_recorded_user_count += 1
        self._log(
            "user.message.committed",
            {
                "turn_index": self.turn_index,
                "text_len": len(clean_text),
                "text_preview": clean_text[:80],
            },
        )
        return self.turn_index

    def record_committed_assistant_message(self, content: str) -> None:
        """保存 LiveKit 已提交到对话历史的助手消息。"""

        clean_text = content.strip()
        if not clean_text:
            return

        self.context_manager.record_assistant_message(
            content=clean_text,
            turn_index=self.turn_index,
        )
        self._log(
            "assistant.message.committed",
            {
                "turn_index": self.turn_index,
                "text_len": len(clean_text),
            },
        )

    @llm.function_tool( #装饰器作用：把该函数方法描述成 LLM 可调用的 Function Tool
        name="search_knowledge_base",
        description=(
            "查询当前通话锁定的个人知识库。"
            "当问题需要依据知识库、文档、资料、项目内容或长期记忆回答时调用。"
            "必须优先依据工具返回结果作为依据。"
            "如果未命中或者查询失败，必须如实说明知识库依据不足，不准编造。"
            "闲聊、问候、简单确认、普通解释不需要调用。"
        ),
    )
    async def search_knowledge_base(self, query: str) -> str:
        """负责向LLM注册工具：查询个人知识库并返回可用于回答的精简上下文。

        主要流程：
        LLM决定调用search_knowledge_base
        → VoiceAssistant.search_knowledge_base()
        → _query_knowledge_base_tool_text()
        → ContextManager.query_knowledge_base()
        → RagClient.query()
        → M1 RAG Core"""

        return await self._query_knowledge_base_tool_text(query=query, source="tool")

    async def _query_knowledge_base_tool_text(self, *, query: str, source: str) -> str:
        """负责把一次RAG工具调用完整包装起来，真正执行查询、记录日志、转换结果"""

        # 获得干净的query
        clean_query = query.strip()
        if not clean_query:
            raise ToolError("知识库查询内容不得为空!")

        try:
            # 调用RAG Core检索
            result = await self.context_manager.query_knowledge_base(
                query=clean_query,
                source=source,
                turn_index=self.turn_index,
                tool_name="search_knowledge_base",
            )
        except Exception as exc:
            raise ToolError("知识库查询失败，请说明暂时无法查询知识库") from exc

        if result.error is not None:
            raise ToolError("知识库查询失败，请说明暂时无法查询知识库。")

        if not result.has_context or not result.context:
            return "知识库检索结果：未找到足够依据。请明确告诉用户知识库依据不足，不要编造。"

        return result.context

    def llm_node(
        self, chat_context: llm.ChatContext, tools: list[llm.Tool], model_settings: Any
    ) -> AsyncIterable[llm.ChatChunk | str]:  # 返回异步流
        """VoiceAssistant对LiveKit默认LLM调用流程的包装，加入user消息去重保存、RAG工具控制和assistant回答保存

        从ChatContext提取用户消息
        → 找到尚未存档的新消息
        → 分配turn_index并落盘
        → 根据auto/never过滤RAG工具
        → 调用LiveKit默认llm_node()
        → 流式返回LLM输出
        → 收集完整助手文本
        → 助手回答落盘"""

        # 找出用户消息
        user_message = self._user_texts(chat_context)
        # 保存新增用户消息，并且确定当前轮次
        turn_index = self._ensure_user_turns_recorded(user_message)

        # 根据RAG模式选择工具：auto->tools    never:除了search_knowledge_base的tools
        active_tools = (
            tools if self.rag_tool_mode == "auto" else self._without_knowledge_tool(tools)
        )

        # 真正执行异步LLM流程：
        # VoiceAssistant.llm_node()→ Agent.default.llm_node()→ AgentSession里的openai.LLM→ 配置的LLM Provider→ 流式返回ChatChunk
        async def _stream() -> AsyncIterable[llm.ChatChunk | str]:
            # 收集回答片段
            assistant_parts: list[str] = []

            # 调用LiveKit默认LLM流程
            async for chunk in Agent.default.llm_node(
                self,
                chat_ctx=chat_context,  # 上下文
                tools=active_tools,  # 可用工具
                model_settings=model_settings,  # 模型设置
            ):
                # 从chunk中提取增量文字
                text = self._chunk_text(chunk)
                if text:
                    # 留一份用于最终存档
                    assistant_parts.append(text)
                # 把chunk原样交给LiveKit，继续TTS+播放
                yield chunk
            # 合并完整答案
            assistant_text = "".join(assistant_parts).strip()

            if assistant_text and not self._session_context_hooks_registered:
                # 本地存档：记录助手回答到messages.jsonl
                self.context_manager.record_assistant_message(
                    content=assistant_text,
                    turn_index=turn_index,
                )

        return _stream()

    @staticmethod
    def _user_texts(chat_ctx: llm.ChatContext) -> list[str]:
        """从 ChatContext 里提取所有用户文本。"""

        msgs = getattr(chat_ctx, "messages", [])  # 获取messages
        if callable(msgs):  # 兼容接口
            msgs = msgs()

        texts: list[str] = []
        # 从最新消息到最早消息遍历
        for msg in reversed(list(msgs or [])):
            # 筛选用户消息
            if getattr(msg, "role", None) == "user":
                # 提取用户文字
                text = (msg.text_content or "").strip()
                if text:
                    texts.append(text)
        # 恢复正常时间顺序
        return list(reversed(texts))

    def _ensure_user_turns_recorded(self, user_texts: list[str]) -> int:
        """找出尚未保存的最新用户消息，分配turn_index，
        确保 ChatContext 中新增的用户输入都被记录，交给messages.jsonl
        
        VoiceAssistant
        → ContextManager.record_user_message()
        → ContextStore.append_message()
        → sessions/{session_id}/messages.jsonl"""

        # 判断是否存在新消息，没有直接返回，防止重复保存
        if len(user_texts) <= self._last_recorded_user_count:
            return self.turn_index

        # 截取还没有保存的内容
        for text in user_texts[self._last_recorded_user_count :]:
            clean_text = text.strip()
            if not clean_text:
                continue

            # 增加对话轮次
            self.turn_index += 1
            # 保存用户消息
            self.context_manager.record_user_message(content=clean_text, turn_index=self.turn_index)

            # 记录日志
            self._log(
                "user.message.recorded",
                {
                    "turn_index": self.turn_index,
                    "text_len": len(clean_text),
                    "text_preview": clean_text[:80],
                },
            )
        # 更新已处理消息数
        self._last_recorded_user_count = len(user_texts)

        # 返回当前轮次
        return self.turn_index

    def _log(self, event_name: str, payload: dict[str, Any]) -> None:
        """记录运行事件。"""

        logger.info(event_name, extra=payload)
        if self.event_logger is not None:
            self.event_logger.append(event_name, payload)

    @staticmethod
    def _without_knowledge_tool(tools: list[llm.Tool]) -> list[llm.Tool]:
        """在never模式，从工具列表中移除知识库RAG工具。"""

        return [tool for tool in tools if not VoiceAssistant._is_knowledge_tool(tool)]

    @staticmethod
    def _is_knowledge_tool(tool: llm.Tool) -> bool:
        """判断 LiveKit 工具对象是否是知识库工具。"""

        target_name = "search_knowledge_base"

        # 提取工具name+id
        tool_id = str(getattr(tool, "id", "") or "")
        tool_name = str(getattr(tool, "name", "") or "")
        # 提取工具详情function
        function_info = getattr(
            tool,
            "function_info",
            None,
        )

        function_name = ""
        if function_info is not None:
            function_name = str(
                getattr(
                    function_info,
                    "name",
                    "",
                )
                or ""
            )

        return target_name in {tool_id, tool_name, function_name}

    @staticmethod
    def _chunk_text(chunk: llm.ChatChunk | str) -> str:
        """从 LiveKit ChatChunk 中提取增量文本。"""

        if isinstance(chunk, str):
            return chunk
        # 增量文本
        delta = getattr(chunk, "delta", None)
        content = getattr(delta, "content", None) if delta is not None else None
        return content or ""

    @staticmethod
    def _elapsed_ms(start: float) -> float:
        """返回从 start 到当前的毫秒耗时。"""

        return round((time.perf_counter() - start) * 1000.0, 1)

    @staticmethod
    def _elapsed_ms_between(start: float, end: float) -> float:
        """返回两个时间点之间的毫秒差。"""

        return round((end - start) * 1000.0, 1)

    @staticmethod
    def _elapsed_ms_since(start: float | None, end: float) -> float | None:
        """返回从可选起点到指定终点的毫秒差。"""

        if start is None:
            return None
        return round((end - start) * 1000.0, 1)


def register_session_context_hooks(session: Any, assistant: VoiceAssistant) -> bool:
    """监听 LiveKit committed 事件，并转发给 VoiceAssistant 做业务存档。"""

    on = getattr(session, "on", None)
    if not callable(on):
        return False

    seen_assistant_items: set[str] = set()

    @session.on("user_input_transcribed")
    def _on_user_input_transcribed(ev: Any) -> None:
        if not bool(getattr(ev, "is_final", False)):
            return
        transcript = str(getattr(ev, "transcript", "") or "").strip()
        if transcript:
            assistant.record_committed_user_transcript(transcript)

    @session.on("conversation_item_added")
    def _on_conversation_item_added(ev: Any) -> None:
        item = getattr(ev, "item", None)
        if item is None or getattr(item, "type", None) != "message":
            return
        if getattr(item, "role", None) != "assistant":
            return

        text = str(getattr(item, "text_content", "") or "").strip()
        if not text:
            return

        item_id = str(getattr(item, "id", "") or "")
        dedupe_key = item_id or f"{getattr(item, 'created_at', '')}:{text}"
        if dedupe_key in seen_assistant_items:
            return
        seen_assistant_items.add(dedupe_key)
        assistant.record_committed_assistant_message(text)

    assistant._session_context_hooks_registered = True
    return True
