"""当前会话的RAG工具调用协调器
作用：
固定并且校验session_id+rag_tool_mode，维护上一轮检索问题，
将Agent提交的query+turn_index补充为完整参数交给RagClient查询该会话唯一锁定的知识库，
把完整的RagQueryResult转换为Agent安全使用的hit/miss/failed工具结果

调用链：
ContextManager.search_knowledge_base()
→ query_knowledge_base()
→ RagClient.query_context()
→ RAG Core
→ rag_context.jsonl
→ to_tool_payload()
"""

from dataclasses import asdict
from typing import Any, ClassVar

from liverag.agent.tool.rag_client import RagClient, RagQueryResult
from liverag.config.settings import RagToolMode


class ContextManager:
    """通话中只负责消息落盘、追问改写和 RAG 工具调用"""

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
        """绑定上下文依赖"""

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


    async def query_knowledge_base(
        self,
        *,
        query:str,
        source:str="agent_tool",
        tool_name:str="search_knowledge_base",
        turn_index:int,
    )->RagQueryResult:
        """通过当前锁定的KB 查询RAG"""

        if self.rag_tool_mode == "never":
            raise RuntimeError("本次会话已禁用知识库检索")
        
        #补充完善短追问，不过不是追问直接返回原问题
        last_query=self._last_rag_query or None
        rag_query=self._build_rag_query(query)

        #发送HTTP请求，调用RAG Core
        result=await self.rag_client.query_context(
            query=rag_query,
            last_query=last_query,
            session_id=self.session_id,
            source=source,
            tool_name=tool_name,
            turn_index=turn_index
            )

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

        #转换为Agent可用的工具相应
        return result.to_tool_payload()

    
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
        """判断用户输入是否是短追问。"""

        text = user_text.strip()
        return bool(text and len(text) <= 12 and any(phrase in text for phrase in cls._FOLLOWUP_PHRASES))


    def _write_runtime_state(self,*,turn_index:int,rag_result:RagQueryResult|None) -> None:
        """写入运行状态，方便前端排查错误"""

        #获取ContextState
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

