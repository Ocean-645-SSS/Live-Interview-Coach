"""M2-E 通话结束后的长期 history 压缩测试。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import liverag.context.history as history_module
from liverag.config.settings import ContextModelSettings
from liverag.context.history import HistoryCompactor
from liverag.context.store import ContextStore
from liverag.runtime.paths import build_runtime_paths


@pytest.fixture
def store(tmp_path: Path) -> ContextStore:
    value = ContextStore(build_runtime_paths(tmp_path / "user-data"))
    value.initialize()
    return value


def settings(**overrides: Any) -> ContextModelSettings:
    values: dict[str, Any] = {
        "model": "test-context-model",
        "base_url": "https://context-model.invalid/v1",
        "api_key": "test-key",
        "max_tokens": 256,
        "max_session_chars": 16_000,
        "history_reference_limit": 2,
        "timeout_ms": 2_000,
        "temperature": 0.0,
    }
    values.update(overrides)
    return ContextModelSettings(**values)


class FakeCompletions:
    def __init__(self, *, text: str = "", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.text))]
        )


class FakeOpenAI:
    def __init__(self, *, text: str = "", error: Exception | None = None) -> None:
        self.completions = FakeCompletions(text=text, error=error)
        self.chat = SimpleNamespace(completions=self.completions)
        self.init_kwargs: dict[str, Any] | None = None

    def constructor(self, **kwargs: Any) -> "FakeOpenAI":
        self.init_kwargs = kwargs
        return self


def install_model(
    monkeypatch: pytest.MonkeyPatch,
    *,
    text: str = "",
    error: Exception | None = None,
) -> FakeOpenAI:
    client = FakeOpenAI(text=text, error=error)
    monkeypatch.setattr(history_module, "AsyncOpenAI", client.constructor)
    return client


def add_session_messages(store: ContextStore, session_id: str, kb_id: str) -> Path:
    store.start_session(session_id, kb_id)
    store.append_message(
        session_id=session_id,
        role="user",
        content="请记住我正在测试 M2-E。",
        turn_index=1,
    )
    store.append_message(
        session_id=session_id,
        role="assistant",
        content="好的，我会把有长期价值的信息写入 history。",
        turn_index=1,
    )
    return store.paths.sessions_dir / session_id / "messages.jsonl"


@pytest.mark.asyncio
async def test_compact_appends_traceable_history_and_preserves_raw_messages(
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages_file = add_session_messages(store, "session-1", "kb-one")
    messages_before = messages_file.read_bytes()
    store.write_soul("回答应当简洁。")
    store.write_knowledge_overview(
        "kb-one",
        "这是 LiveRAG 项目知识库。",
        stale=False,
        source="test",
    )
    store.append_history("kb-one", "较早记录", "session-old-1")
    store.append_history("kb-one", "最近记录", "session-old-2")
    client = install_model(
        monkeypatch,
        text="```text\n用户正在测试 M2-E 的长期历史压缩。\n```",
    )

    result = await HistoryCompactor(
        store=store,
        settings=settings(),
    ).compact_after_call(
        session_id="session-1",
        kb_id="kb-one",
        kb_name="项目知识库",
    )

    assert result["updated"] is True
    assert result["reason"] == "appended"
    assert result["message_count"] == 2
    assert result["session_truncated"] is False
    assert result["record"]["source_session_id"] == "session-1"
    assert result["record"]["content"] == "用户正在测试 M2-E 的长期历史压缩。"
    assert store.read_recent_history("kb-one", limit=1) == [result["record"]]
    assert messages_file.read_bytes() == messages_before
    assert client.init_kwargs == {
        "api_key": "test-key",
        "base_url": "https://context-model.invalid/v1",
        "timeout": 2.0,
    }

    call = client.completions.calls[0]
    assert call["model"] == "test-context-model"
    assert call["temperature"] == 0.0
    assert call["max_tokens"] == 256
    assert call["messages"][0]["content"] == store.read_history_compress_prompt()
    prompt = call["messages"][1]["content"]
    assert "kb_id: kb-one" in prompt
    assert "kb_name: 项目知识库" in prompt
    assert "回答应当简洁。" in prompt
    assert "这是 LiveRAG 项目知识库。" in prompt
    assert "较早记录" in prompt
    assert "最近记录" in prompt
    assert "user: 请记住我正在测试 M2-E。" in prompt
    assert "assistant: 好的，我会把有长期价值的信息写入 history。" in prompt


@pytest.mark.asyncio
async def test_compact_only_references_recent_history_from_selected_kb(
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_session_messages(store, "session-1", "kb-one")
    store.append_history("kb-one", "超出引用窗口", "old-1")
    store.append_history("kb-one", "窗口内记录一", "old-2")
    store.append_history("kb-one", "窗口内记录二", "old-3")
    store.append_history("kb-two", "其他知识库私有记录", "other-session")
    client = install_model(monkeypatch, text="新的长期记录")

    await HistoryCompactor(
        store=store,
        settings=settings(history_reference_limit=2),
    ).compact_after_call(
        session_id="session-1",
        kb_id="kb-one",
        kb_name="知识库一",
    )

    prompt = client.completions.calls[0]["messages"][1]["content"]
    assert "窗口内记录一" in prompt
    assert "窗口内记录二" in prompt
    assert "超出引用窗口" not in prompt
    assert "其他知识库私有记录" not in prompt
    assert store.read_recent_history("kb-two", limit=10)[0]["content"] == "其他知识库私有记录"


@pytest.mark.asyncio
@pytest.mark.parametrize("model_text", ["", "   ", "NO_HISTORY", "no_history"])
async def test_compact_does_not_append_when_model_returns_no_history(
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
    model_text: str,
) -> None:
    messages_file = add_session_messages(store, "session-1", "kb-one")
    messages_before = messages_file.read_bytes()
    install_model(monkeypatch, text=model_text)

    result = await HistoryCompactor(
        store=store,
        settings=settings(),
    ).compact_after_call(
        session_id="session-1",
        kb_id="kb-one",
        kb_name="知识库一",
    )

    assert result == {
        "updated": False,
        "reason": "no_history_value",
        "message_count": 2,
        "session_truncated": False,
    }
    assert store.read_recent_history("kb-one", limit=10) == []
    assert messages_file.read_bytes() == messages_before


@pytest.mark.asyncio
async def test_compact_empty_session_skips_model(
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store.start_session("session-empty", "kb-one")

    def fail_if_constructed(**_kwargs: Any) -> None:
        pytest.fail("empty session must not construct an OpenAI client")

    monkeypatch.setattr(history_module, "AsyncOpenAI", fail_if_constructed)
    result = await HistoryCompactor(
        store=store,
        settings=settings(),
    ).compact_after_call(
        session_id="session-empty",
        kb_id="kb-one",
        kb_name="知识库一",
    )

    assert result == {"updated": False, "reason": "empty_session"}
    assert store.read_recent_history("kb-one", limit=10) == []


@pytest.mark.asyncio
async def test_compact_missing_api_key_skips_model(
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages_file = add_session_messages(store, "session-1", "kb-one")
    messages_before = messages_file.read_bytes()

    def fail_if_constructed(**_kwargs: Any) -> None:
        pytest.fail("missing API key must not construct an OpenAI client")

    monkeypatch.setattr(history_module, "AsyncOpenAI", fail_if_constructed)
    result = await HistoryCompactor(
        store=store,
        settings=settings(api_key=""),
    ).compact_after_call(
        session_id="session-1",
        kb_id="kb-one",
        kb_name="知识库一",
    )

    assert result == {
        "updated": False,
        "reason": "missing_context_model_api_key",
    }
    assert store.read_recent_history("kb-one", limit=10) == []
    assert messages_file.read_bytes() == messages_before


@pytest.mark.asyncio
async def test_compact_model_failure_is_non_destructive(
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages_file = add_session_messages(store, "session-1", "kb-one")
    messages_before = messages_file.read_bytes()
    install_model(monkeypatch, error=RuntimeError("provider unavailable"))

    result = await HistoryCompactor(
        store=store,
        settings=settings(),
    ).compact_after_call(
        session_id="session-1",
        kb_id="kb-one",
        kb_name="知识库一",
    )

    assert result["updated"] is False
    assert result["reason"] == "context_model_failed"
    assert result["error"] == "RuntimeError: provider unavailable"
    assert result["message_count"] == 2
    assert result["session_truncated"] is False
    assert store.read_recent_history("kb-one", limit=10) == []
    assert messages_file.read_bytes() == messages_before


@pytest.mark.asyncio
async def test_compact_truncates_long_session_from_the_front(
    store: ContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store.start_session("session-1", "kb-one")
    store.append_message(
        session_id="session-1",
        role="user",
        content="旧内容-" + ("A" * 80),
        turn_index=1,
    )
    store.append_message(
        session_id="session-1",
        role="assistant",
        content="最新内容-" + ("Z" * 40),
        turn_index=1,
    )
    client = install_model(monkeypatch, text="压缩结果")

    result = await HistoryCompactor(
        store=store,
        settings=settings(max_session_chars=70),
    ).compact_after_call(
        session_id="session-1",
        kb_id="kb-one",
        kb_name="知识库一",
    )

    assert result["updated"] is True
    assert result["session_truncated"] is True
    prompt = client.completions.calls[0]["messages"][1]["content"]
    session_section = prompt.split("# 本次通话消息\n", maxsplit=1)[1]
    assert "Z" * 40 in session_section
    assert "A" not in session_section


def test_clean_model_text_only_removes_outer_code_fence() -> None:
    assert HistoryCompactor._clean_model_text("```json\n摘要内容\n```") == "摘要内容"
    assert HistoryCompactor._clean_model_text("普通摘要") == "普通摘要"
