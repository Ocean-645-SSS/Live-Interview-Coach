"""M2-C RagClient 单知识库查询与审计测试。"""

import asyncio
import json
from pathlib import Path
from typing import Any

import aiohttp
import pytest

import liverag.agent.tool.rag_client as rag_client_module
from liverag.agent.tool.rag_client import RagClient, RagQueryResult
from liverag.config.settings import RagClientSettings
from liverag.context.store import ContextStore
from liverag.runtime.paths import build_runtime_paths


class FakeResponse:
    """提供 RagClient 所需的最小 aiohttp 响应协议。"""

    def __init__(
        self,
        *,
        status: int = 200,
        payload: Any = None,
        body: str = "",
        json_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.body = body
        self.json_error = json_error

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self) -> Any:
        if self.json_error is not None:
            raise self.json_error
        return self.payload

    async def text(self) -> str:
        return self.body


class FakeClientSession:
    """记录请求参数，并返回预设响应或异常。"""

    def __init__(
        self,
        *,
        response: FakeResponse | None = None,
        post_error: Exception | None = None,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> None:
        self.response = response
        self.post_error = post_error
        self.timeout = timeout
        self.requests: list[dict[str, Any]] = []

    async def __aenter__(self) -> "FakeClientSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> FakeResponse:
        self.requests.append({"url": url, "json": json, "headers": headers})
        if self.post_error is not None:
            raise self.post_error
        assert self.response is not None
        return self.response


@pytest.fixture
def store(tmp_path: Path) -> ContextStore:
    """创建完全隔离的 session 存储。"""

    value = ContextStore(build_runtime_paths(tmp_path / "user-data"))
    value.initialize()
    value.start_session("session-1", "kb-alpha")
    return value


@pytest.fixture
def settings() -> RagClientSettings:
    """使用确定值构造客户端配置。"""

    return RagClientSettings(
        base_url="http://rag-core:9819",
        api_key="secret-key",
        query_mode="naive",
        timeout_ms=750,
        top_k=4,
        chunk_top_k=3,
        context_max_chars=20,
        enable_rerank=False,
        cache_ttl_s=0,
        rag_tool_mode="auto",
    )


@pytest.fixture
def client(settings: RagClientSettings, store: ContextStore) -> RagClient:
    return RagClient(
        settings,
        store,
        kb_id="kb-alpha",
        kb_name="Alpha",
    )


def install_fake_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: FakeResponse | None = None,
    post_error: Exception | None = None,
) -> list[FakeClientSession]:
    """替换 ClientSession，并返回创建出的 fake session。"""

    sessions: list[FakeClientSession] = []

    def factory(*, timeout: aiohttp.ClientTimeout) -> FakeClientSession:
        session = FakeClientSession(
            response=response,
            post_error=post_error,
            timeout=timeout,
        )
        sessions.append(session)
        return session

    monkeypatch.setattr(rag_client_module.aiohttp, "ClientSession", factory)
    return sessions


def success_envelope(
    *,
    hit: bool = True,
    has_context: bool = True,
    context: str = "M2-C 负责连接 Agent 与 RAG Core。",
) -> dict[str, Any]:
    """构造符合 M1 契约的查询响应。"""

    return {
        "request_id": "request-1",
        "status": "ok",
        "data": {
            "kb_id": "kb-alpha",
            "query": "M2-C 是什么？",
            "effective_query": "LiveRAG M2-C 是什么？",
            "rewritten": True,
            "hit": hit,
            "has_context": has_context,
            "context": context,
            "context_truncated": False,
            "answer": None,
            "references": [
                {
                    "document_id": "doc-1",
                    "file_path": "plan.md",
                }
            ],
            "chunks": [
                {
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "content": context,
                    "score": 0.95,
                }
            ],
            "duration": 0.01,
        },
        "metrics": {"mode": "naive"},
        "error": None,
    }


async def run_query(client: RagClient) -> RagQueryResult:
    return await client.query_context(
        query="  M2-C 是什么？  ",
        last_query="M2-B 是什么？",
        session_id="session-1",
        source="agent_tool",
        tool_name="search_knowledge_base",
        turn_index=2,
    )


async def test_query_context_returns_hit_and_records_request(
    client: RagClient,
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功响应被转换成 evidence，并与 session/turn 对齐。"""

    sessions = install_fake_session(
        monkeypatch,
        response=FakeResponse(payload=success_envelope()),
    )

    result = await run_query(client)

    assert result.request_id == "request-1"
    assert result.kb_id == "kb-alpha"
    assert result.query == "M2-C 是什么？"
    assert result.effective_query == "LiveRAG M2-C 是什么？"
    assert result.rewritten is True
    assert result.hit is True
    assert result.has_context is True
    assert result.context == "M2-C 负责连接 Agent 与 RA"
    assert result.evidence_documents[0].document_id == "doc-1"
    assert result.evidence_chunks[0].content.startswith("M2-C")
    assert result.to_tool_payload()["status"] == "hit"

    request = sessions[0].requests[0]
    assert request["url"] == ("http://rag-core:9819/v1/knowledge-bases/kb-alpha/query/context")
    assert request["headers"]["X-API-Key"] == "secret-key"
    assert request["json"] == {
        "query": "M2-C 是什么？",
        "profile": "voice",
        "options": {
            "mode": "naive",
            "top_k": 4,
            "chunk_top_k": 3,
            "enable_rerank": False,
            "include_references": True,
            "include_chunk_content": True,
            "context_max_chars": 20,
        },
        "conversation": {
            "last_query": "M2-B 是什么？",
            "rewrite_followup": False,
        },
    }
    assert sessions[0].timeout is not None
    assert sessions[0].timeout.total == 0.75

    records = store.read_rag_context("session-1")
    assert len(records) == 1
    assert records[0]["session_id"] == "session-1"
    assert records[0]["kb_id"] == "kb-alpha"
    assert records[0]["turn_index"] == 2
    assert records[0]["request_id"] == "request-1"
    assert records[0]["hit"] is True
    assert records[0]["has_context"] is True
    assert records[0]["evidence_count"] == 1
    assert records[0]["evidence_documents"][0]["document_id"] == "doc-1"
    assert records[0]["duration"] >= 0


async def test_query_context_returns_stable_miss(
    client: RagClient,
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_session(
        monkeypatch,
        response=FakeResponse(
            payload=success_envelope(
                hit=False,
                has_context=False,
                context="",
            )
        ),
    )

    result = await run_query(client)

    assert result.error is None
    assert result.hit is False
    assert result.has_context is False
    assert result.context == ""
    assert result.to_tool_payload()["status"] == "miss"
    assert store.read_rag_context("session-1")[0]["hit"] is False


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (404, "http_4xx"),
        (422, "http_4xx"),
        (500, "http_5xx"),
        (502, "http_5xx"),
        (504, "timeout"),
    ],
)
async def test_query_context_maps_http_errors(
    client: RagClient,
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    error_type: str,
) -> None:
    install_fake_session(
        monkeypatch,
        response=FakeResponse(
            status=status_code,
            body="知识库查询失败",
        ),
    )

    result = await run_query(client)

    assert result.error is not None
    assert result.error.type == error_type
    assert result.error.status_code == status_code
    assert result.to_tool_payload()["status"] == "failed"
    assert store.read_rag_context("session-1")[0]["error"]["type"] == error_type


@pytest.mark.parametrize(
    ("exception", "error_type"),
    [
        (asyncio.TimeoutError(), "timeout"),
        (aiohttp.ClientConnectionError("connection refused"), "transport"),
    ],
)
async def test_query_context_maps_network_errors(
    client: RagClient,
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
    exception: Exception,
    error_type: str,
) -> None:
    install_fake_session(monkeypatch, post_error=exception)

    result = await run_query(client)

    assert result.error is not None
    assert result.error.type == error_type
    assert result.hit is False
    assert result.has_context is False
    assert store.read_rag_context("session-1")[0]["error"]["type"] == error_type


async def test_query_context_maps_invalid_json_to_protocol_error(
    client: RagClient,
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_session(
        monkeypatch,
        response=FakeResponse(
            json_error=json.JSONDecodeError("invalid", "not-json", 0),
        ),
    )

    result = await run_query(client)

    assert result.error is not None
    assert result.error.type == "protocol"
    assert "JSON" in result.error.message
    assert store.read_rag_context("session-1")[0]["error"]["type"] == "protocol"


def protocol_cases() -> list[tuple[Any, str]]:
    """返回关键的 HTTP 200 协议错误样本。"""

    base = success_envelope()
    return [
        ("not-an-object", "不是 JSON 对象"),
        ({"status": "ok"}, "缺少 status 或 data"),
        ({**base, "request_id": ""}, "request_id"),
        ({**base, "metrics": []}, "metrics"),
        (
            {
                **base,
                "status": "error",
                "data": None,
                "error": {"message": "业务失败"},
            },
            "业务失败",
        ),
        ({**base, "data": []}, "data 必须是对象"),
        (
            {
                **base,
                "data": {**base["data"], "kb_id": "kb-beta"},
            },
            "kb_id 不一致",
        ),
        (
            {
                **base,
                "data": {**base["data"], "hit": "yes"},
            },
            "hit 必须是布尔值",
        ),
        (
            {
                **base,
                "data": {
                    **base["data"],
                    "hit": False,
                    "has_context": True,
                },
            },
            "相互矛盾",
        ),
    ]


@pytest.mark.parametrize(("payload", "message_fragment"), protocol_cases())
async def test_query_context_rejects_protocol_errors(
    client: RagClient,
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
    payload: Any,
    message_fragment: str,
) -> None:
    install_fake_session(
        monkeypatch,
        response=FakeResponse(payload=payload),
    )

    result = await run_query(client)

    assert result.error is not None
    assert result.error.type == "protocol"
    assert message_fragment in result.error.message
    assert result.hit is False
    assert result.has_context is False
    assert result.evidence_documents == []
    assert result.evidence_chunks == []
    assert store.read_rag_context("session-1")[0]["error"]["type"] == "protocol"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"query": "   "}, "query"),
        ({"session_id": "   "}, "session_id"),
        ({"turn_index": None}, "turn_index"),
        ({"turn_index": -1}, "turn_index"),
        ({"turn_index": True}, "turn_index"),
    ],
)
async def test_query_context_rejects_invalid_input_before_network(
    client: RagClient,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    message: str,
) -> None:
    def fail_factory(*args: object, **kwargs: object) -> None:
        raise AssertionError("参数非法时不应创建 HTTP session")

    monkeypatch.setattr(rag_client_module.aiohttp, "ClientSession", fail_factory)
    arguments: dict[str, Any] = {
        "query": "问题",
        "last_query": None,
        "session_id": "session-1",
        "turn_index": 1,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        await client.query_context(**arguments)


async def test_query_context_rejects_missing_session_before_network(
    client: RagClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_factory(*args: object, **kwargs: object) -> None:
        raise AssertionError("session 不存在时不应创建 HTTP session")

    monkeypatch.setattr(rag_client_module.aiohttp, "ClientSession", fail_factory)

    with pytest.raises(ValueError, match="session 不存在"):
        await client.query_context(
            query="问题",
            last_query=None,
            session_id="missing-session",
            turn_index=1,
        )


async def test_query_context_rejects_session_kb_mismatch_before_network(
    settings: RagClientSettings,
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_factory(*args: object, **kwargs: object) -> None:
        raise AssertionError("KB 不一致时不应创建 HTTP session")

    monkeypatch.setattr(rag_client_module.aiohttp, "ClientSession", fail_factory)
    client = RagClient(settings, store, kb_id="kb-beta")

    with pytest.raises(ValueError, match="知识库不一致"):
        await client.query_context(
            query="问题",
            last_query=None,
            session_id="session-1",
            turn_index=1,
        )


def test_result_tool_payload_has_stable_hit_miss_failed_states() -> None:
    """工具输出只暴露 Agent 回答所需的稳定状态。"""

    miss = RagQueryResult(
        kb_id="kb-alpha",
        query="问题",
        effective_query="问题",
        hit=False,
        has_context=False,
        context="",
        metrics={},
    )
    failed = RagQueryResult.failed(
        kb_id="kb-alpha",
        query="问题",
        duration=0.1,
        error_type="timeout",
        message="超时",
    )

    assert miss.to_tool_payload()["status"] == "miss"
    assert failed.to_tool_payload()["status"] == "failed"
