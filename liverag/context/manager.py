"""当前会话的RAG工具调用协调器
作用：
保存用户和助手原始消息，维护最近用户文本和最近 RAG 查询主题，并用规则补全“继续说”这类短追问；
实际查询时，它把固定 session_id、当前 turn_index 和工具元数据交给 RagClient；
回答结束后，再读取同一轮 Evidence，给助手消息标记是否查询、命中或失败；
原始记录使用追加式 JSONL，Runtime 只维护最近状态快照

检索知识库调用链：
ContextManager.search_knowledge_base()
→ query_knowledge_base()
→ RagClient.query_context()
→ RAG Core
→ 写入rag_context.jsonl
→ 返回payload：to_tool_payload()

与RAG Client之间的联系
VoiceAssistant
        ↓
ContextManager
├── 记录user/assistant消息(ContextStore)
├── 维护last_query
├── 管理session_id和turn_index
└── 调用RagClient
        ↓
    RAG Core HTTP
"""

from dataclasses import asdict
from typing import Any, ClassVar

from liverag.agent.tool.rag_client import RagClient, RagQueryResult
from liverag.config.settings import RagToolMode


class ContextManager:
    """通话中只负责最近消息落盘、追问改写和 RAG 工具调用"""

    _FOLLOWUP_PHRASES: ClassVar[set[str]] = {
            "接着说",
            "继续",
            "继续说",
            "详细说",
            "详细说说",
            "展开说说",
            "展开讲讲",
            "然后呢",
            "还有呢",
            "再说说",
            "再讲讲",
            "具体点",
            "讲详细点",
            "说详细点",
        }


    def __init__(
        self,
        *,
        rag_client:RagClient, #RAG检索业务层
        session_id:str,
        rag_tool_mode:RagToolMode
    ):
        """绑定上下文依赖(ContextStore+RagClient)"""

        if not session_id.strip():
            raise ValueError("session_id 不得为空！")
        if rag_tool_mode not in {"auto","never"}:
            raise ValueError("rag_tool_mode 必须是auto/never！")

        self.rag_client = rag_client
        self.session_id = session_id
        self.rag_tool_mode = rag_tool_mode
        self._last_user_text=""  #用户最近说的一句话
        self._previous_user_text = ""  #用户在这之前说的一句话
        self._last_rag_query = ""  #最近一次真正提交给RAG的查询

        # 当前进程内按轮次汇总 RAG 使用情况，避免保存助手消息时重复全量扫描 rag_context.jsonl。
        # rag_context.jsonl 仍然是完整、持久、可审计的事实来源。
        self._rag_state_by_turn: dict[int, dict[str, bool]] = {}
        self._rag_result_by_turn: dict[tuple[int, str], RagQueryResult] = {}


    def record_user_message(self,*,content:str,turn_index:int)->None:
        """当LiveKit确认用户这一轮的最终语音转写后，保存原始用户消息、维护追问上下文，并更新当前session的运行状态

        调用链：
        VoiceAssistant.llm_node()
        → _ensure_user_turns_recorded()
        → ContextManager.record_user_message()
        → ContextStore.append_message()
        → ContextStore.read/write_runtime_state()"""

        query=content.strip()
        if not query:
            return

        # 如果新消息与上一条不同，就把旧的 _last_user_text 推到 _previous_user_text
        if query!=self._last_user_text:
            self._previous_user_text=self._last_user_text
        # 更新用户最近说的一句话
        self._last_user_text=query

        #保存原始用户消息
        self.rag_client.store.append_message(
            session_id=self.session_id,
            role="user",
            content=query,
            turn_index=turn_index
        )

        #写入当前session运行状态
        self._write_runtime_state(turn_index=turn_index,rag_result=None)


    async def query_knowledge_base(
        self,
        *,
        query:str,
        source:str="agent_tool",
        tool_name:str="search_knowledge_base",
        turn_index:int,
    )->RagQueryResult:
        """通过当前锁定的KB 查询RAG

        调用链：
        VoiceAssistant工具
        → ContextManager.query_knowledge_base()
        → 补全短追问
        → RagClient.query_context()
        → M1 /query/context
        → 更新当前轮次汇总
        → 运行状态写rag_context.jsonl
        → 返回RagQueryResult"""

        if self.rag_tool_mode == "never":
            raise RuntimeError("本次会话已禁用知识库检索")

        #补充完善短追问，如果不是追问直接返回原问题
        last_query=self._last_rag_query or None
        rag_query=self._build_rag_query(query)

        cache_key = (turn_index, " ".join(rag_query.casefold().split()))
        cached = self._rag_result_by_turn.get(cache_key)
        if cached is not None:
            result = cached.model_copy(deep=True)
            result.metrics["cache_hit"] = True
            self._update_turn_rag_state(turn_index=turn_index, result=result)
            self._write_runtime_state(turn_index=turn_index, rag_result=result)
            return result

        #发送HTTP请求，调用RAG Core
        result=await self.rag_client.query_context(
            query=rag_query,
            last_query=last_query,
            session_id=self.session_id,
            source=source,
            tool_name=tool_name,
            turn_index=turn_index
            )

        if result.error is None:
            self._rag_result_by_turn[cache_key] = result.model_copy(deep=True)

        # RagClient 已将完整查询结果追加到 rag_context.jsonl；
        # 这里额外维护轻量的轮次摘要，供助手消息元数据 O(1) 读取。
        self._update_turn_rag_state(turn_index=turn_index, result=result)

        #写入运行状态，方便前端与排查
        self._write_runtime_state(turn_index=turn_index,rag_result=result)

        return result


    async def search_knowledge_base(
        self,
        *,
        query: str,
        turn_index: int,
    ) -> dict[str, Any]:
        """执行知识库工具查询，并返回 Agent 可使用的结果。"""

        result = await self.query_knowledge_base(
            query=query,
            turn_index=turn_index,
        )

        #转换为Agent可用的工具响应
        return result.to_tool_payload()


    def record_assistant_message(self,*,content:str,turn_index:int):
        """在助手的一轮回答生成之后，将回答原文写入messages.jsonl，
        同时记录回答长度和该轮是否查询过RAG，并更新runtime.json中的最近回答记录"""

        #计算回答字符数
        char_count=len(content.strip())

        # O(1) 读取当前轮 RAG 汇总，不再重复全量扫描 rag_context.jsonl。
        turn_rag_state = self._rag_state_by_turn.get(turn_index, {})
        rag_queried = bool(turn_rag_state.get("queried", False))
        rag_hit = bool(turn_rag_state.get("hit", False))
        rag_failed = bool(turn_rag_state.get("failed", False))

        #判断回答是否太长了
        too_long=char_count>180

        #生成metadata
        metadata={
            "char_count":char_count,
            "tts_text_chars":char_count,  #预计传给TTS的长度
            "tts_text_chars_source":"assistant_text",
            "too_long":too_long,
            "rag_queried":rag_queried,
            "rag_hit":rag_hit,
            "rag_failed":rag_failed,
            "rag_tool_mode":self.rag_client.settings.rag_tool_mode
        }

        #保存原始assistant消息
        self.rag_client.store.append_message(
            session_id=self.session_id,
            role="assistant",
            content=content,
            turn_index=turn_index,
            metadata=metadata,
        )

        #更新运行状态
        state=self.rag_client.store.read_runtime_state(self.session_id)
        state.update(
            {
            "last_assistant_chars": char_count,
            "last_tts_text_chars": char_count,
            "last_tts_text_chars_source": "assistant_text",
            "last_answer_too_long": too_long,
            "last_answer_rag_queried": rag_queried,
            "last_answer_rag_hit":rag_hit,
            "last_answer_rag_failed":rag_failed,
            "rag_tool_mode": self.rag_client.settings.rag_tool_mode,
            }
        )
        self.rag_client.store.write_runtime_state(session_id=self.session_id,state=state)

    def _update_turn_rag_state(self, *, turn_index: int, result: RagQueryResult) -> None:
        """汇总当前轮一次或多次 RAG 调用的命中与失败状态。"""

        turn_state = self._rag_state_by_turn.setdefault(
            turn_index,
            {
                "queried": False,
                "hit": False,
                "failed": False,
            },
        )
        turn_state["queried"] = True
        turn_state["hit"] = turn_state["hit"] or (
            result.hit and result.has_context
        )
        turn_state["failed"] = turn_state["failed"] or result.error is not None


    def _build_rag_query(self,user_text:str):
        """为短追问补充上一轮内容"""

        query=user_text.strip()

        #不是追问
        if not self._is_followup_query(user_text):
            self._last_rag_query=query
            return query

        #是追问
        anchor = self._last_rag_query or self._previous_user_text
        if not anchor:
            return query
        return f"上一轮问题：{anchor}\n当前追问：{query}\n请围绕上一轮主题继续补充。"


    @classmethod
    def _is_followup_query(cls, user_text: str) -> bool:
        """判断用户输入是否是短追问：不为空 + 长度小于12个字 + 包含设置好的短追问固定短语"""

        text = user_text.strip()
        return bool(text and len(text) <= 12 and any(phrase in text for phrase in cls._FOLLOWUP_PHRASES))


    def _write_runtime_state(self,*,turn_index:int,rag_result:RagQueryResult|None) -> None:
        """封装user/RAG字段，写入运行状态，方便前端排查错误"""

        #读取ContextState
        state=self.rag_client.store.read_runtime_state(self.session_id)

        #更新state状态
        state.update(
            {
                "turn_index": turn_index,
                "last_user_text": self._last_user_text,
                "previous_user_text": self._previous_user_text,
                "last_rag_query": self._last_rag_query,
                "rag_tool_mode": self.rag_client.settings.rag_tool_mode,
            }
        )

        if rag_result is not None:
            state["last_rag"] = {
                "hit": rag_result.hit,
                "has_context": bool(rag_result.has_context),
                "request_id": rag_result.request_id,
                "metrics": rag_result.metrics,
                "error": (asdict(rag_result.error) if rag_result.error is not None else None),
            }

        #写入运行状态
        self.rag_client.store.write_runtime_state(self.session_id,state)

