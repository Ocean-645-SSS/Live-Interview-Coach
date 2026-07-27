"""M2-C ContextManager 会话级 RAG 调用协调测试。"""

from types import SimpleNamespace
from typing import Any

import pytest

from liverag.agent.tool.rag_client import RagQueryError, RagQueryResult
from liverag.context.manager import ContextManager


class FakeContextStore:
    """提供 ContextManager 所需的最小 runtime state 接口。"""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "session_id": "session-1",
            "kb_id": "kb-alpha",
            "state": "active",
        }
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.messages: list[dict[str, Any]] = []

    def read_runtime_state(self, session_id: str) -> dict[str, Any]:
        assert session_id == "session-1"
        return dict(self.state)

    def write_runtime_state(
        self,
        session_id: str,
        state: dict[str, Any],
    ) -> None:
        assert session_id == "session-1"
        self.state = dict(state)
        self.writes.append((session_id, dict(state)))

    def append_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        turn_index: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        assert session_id == "session-1"
        self.messages.append(
            {
                "role": role,
                "content": content,
                "turn_index": turn_index,
                "metadata": metadata or {},
            }
        )


class FakeRagClient:
    """记录 ContextManager 传入的完整查询参数。"""

    def __init__(
        self,
        *,
        result: RagQueryResult,
        rag_tool_mode: str = "auto",
    ) -> None:
        self.result = result
        self.settings = SimpleNamespace(rag_tool_mode=rag_tool_mode)
        self.store = FakeContextStore()
        self.calls: list[dict[str, Any]] = []

    async def query_context(self, **kwargs: Any) -> RagQueryResult:
        self.calls.append(kwargs)
        return self.result


def hit_result() -> RagQueryResult:
    return RagQueryResult(
        request_id="request-1",
        kb_id="kb-alpha",
        query="M2-C 是什么？",
        effective_query="M2-C 是什么？",
        hit=True,
        has_context=True,
        context="M2-C 负责连接 Agent 与 RAG Core。",
        metrics={"latency_ms": 12.5},
    )


def miss_result() -> RagQueryResult:
    return RagQueryResult(
        request_id="request-2",
        kb_id="kb-alpha",
        query="未知问题",
        effective_query="未知问题",
        hit=False,
        has_context=False,
        context="",
        metrics={"latency_ms": 8.0},
    )


@pytest.mark.parametrize("session_id", ["", "   "])
def test_init_rejects_blank_session_id(session_id: str) -> None:
    client = FakeRagClient(result=hit_result())

    with pytest.raises(ValueError, match="session_id"):
        ContextManager(
            rag_client=client,  # type: ignore[arg-type]
            session_id=session_id,
            rag_tool_mode="auto",
        )


def test_init_rejects_unknown_rag_tool_mode() -> None:
    client = FakeRagClient(result=hit_result())

    with pytest.raises(ValueError, match="rag_tool_mode"):
        ContextManager(
            rag_client=client,  # type: ignore[arg-type]
            session_id="session-1",
            rag_tool_mode="always",  # type: ignore[arg-type]
        )


async def test_query_knowledge_base_passes_session_turn_and_tool_metadata() -> None:
    client = FakeRagClient(result=hit_result())
    manager = ContextManager(
        rag_client=client,  # type: ignore[arg-type]
        session_id="session-1",
        rag_tool_mode="auto",
    )

    result = await manager.query_knowledge_base(
        query="M2-C 是什么？",
        turn_index=3,
    )

    assert result is client.result
    assert client.calls == [
        {
            "query": "M2-C 是什么？",
            "last_query": None,
            "session_id": "session-1",
            "source": "agent_tool",
            "tool_name": "search_knowledge_base",
            "turn_index": 3,
        }
    ]


async def test_search_knowledge_base_returns_agent_tool_payload() -> None:
    client = FakeRagClient(result=miss_result())
    manager = ContextManager(
        rag_client=client,  # type: ignore[arg-type]
        session_id="session-1",
        rag_tool_mode="auto",
    )

    payload = await manager.search_knowledge_base(
        query="未知问题",
        turn_index=1,
    )

    assert payload["status"] == "miss"
    assert payload["context"] == ""
    assert "不得编造" in payload["instruction"]


async def test_never_mode_does_not_call_rag_or_write_runtime_state() -> None:
    client = FakeRagClient(result=hit_result(), rag_tool_mode="never")
    manager = ContextManager(
        rag_client=client,  # type: ignore[arg-type]
        session_id="session-1",
        rag_tool_mode="never",
    )

    with pytest.raises(RuntimeError, match="禁用"):
        await manager.search_knowledge_base(
            query="M2-C 是什么？",
            turn_index=1,
        )

    assert client.calls == []
    assert client.store.writes == []


async def test_followup_query_uses_previous_rag_query_as_anchor() -> None:
    client = FakeRagClient(result=hit_result())
    manager = ContextManager(
        rag_client=client,  # type: ignore[arg-type]
        session_id="session-1",
        rag_tool_mode="auto",
    )

    await manager.query_knowledge_base(
        query="M2-C 是什么？",
        turn_index=1,
    )
    await manager.query_knowledge_base(
        query="继续说",
        turn_index=2,
    )

    assert client.calls[0]["last_query"] is None
    assert client.calls[1]["last_query"] == "M2-C 是什么？"
    assert client.calls[1]["query"] == (
        "上一轮问题：M2-C 是什么？\n"
        "当前追问：继续说\n"
        "请围绕上一轮主题继续补充。"
    )


async def test_runtime_state_records_last_rag_result() -> None:
    client = FakeRagClient(result=hit_result())
    manager = ContextManager(
        rag_client=client,  # type: ignore[arg-type]
        session_id="session-1",
        rag_tool_mode="auto",
    )

    await manager.query_knowledge_base(
        query="M2-C 是什么？",
        turn_index=4,
    )

    state = client.store.state
    assert state["session_id"] == "session-1"
    assert state["kb_id"] == "kb-alpha"
    assert state["turn_index"] == 4
    assert state["last_rag_query"] == "M2-C 是什么？"
    assert state["rag_tool_mode"] == "auto"
    assert state["last_rag"] == {
        "hit": True,
        "has_context": True,
        "request_id": "request-1",
        "metrics": {"latency_ms": 12.5},
        "error": None,
    }


async def test_runtime_state_serializes_structured_rag_error() -> None:
    failed = RagQueryResult(
        request_id="request-3",
        kb_id="kb-alpha",
        query="问题",
        effective_query="问题",
        hit=False,
        has_context=False,
        context="",
        metrics={"latency_ms": 750.0},
        error=RagQueryError(
            type="timeout",
            message="RAG Core 查询超时",
        ),
    )
    client = FakeRagClient(result=failed)
    manager = ContextManager(
        rag_client=client,  # type: ignore[arg-type]
        session_id="session-1",
        rag_tool_mode="auto",
    )

    payload = await manager.search_knowledge_base(
        query="问题",
        turn_index=5,
    )

    assert payload["status"] == "failed"
    assert client.store.state["last_rag"]["error"] == {
        "type": "timeout",
        "message": "RAG Core 查询超时",
        "status_code": None,
    }


async def test_assistant_message_uses_in_memory_turn_rag_summary() -> None:
    client = FakeRagClient(result=hit_result())
    manager = ContextManager(
        rag_client=client,  # type: ignore[arg-type]
        session_id="session-1",
        rag_tool_mode="auto",
    )

    await manager.query_knowledge_base(
        query="M2-C 是什么？",
        turn_index=6,
    )
    manager.record_assistant_message(
        content="M2-C 负责连接 Agent 与 RAG Core。",
        turn_index=6,
    )

    metadata = client.store.messages[-1]["metadata"]
    assert metadata["rag_queried"] is True
    assert metadata["rag_hit"] is True
    assert metadata["rag_failed"] is False
