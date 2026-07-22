"""M2-A 不可变原始 Session 存档测试。"""

import json
from pathlib import Path
from typing import Any

import pytest

from liverag.context.store import ContextStore
from liverag.runtime.paths import build_runtime_paths


@pytest.fixture
def store(tmp_path: Path) -> ContextStore:
    """创建完全隔离于真实用户数据目录的 ContextStore。"""

    value = ContextStore(build_runtime_paths(tmp_path / "user-data"))
    value.initialize()
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取测试生成的 JSONL，并让格式错误直接导致测试失败。"""

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_start_session_creates_independent_archives_and_retention_metadata(
    store: ContextStore,
) -> None:
    store.start_session("session-one", "kb-one")
    store.start_session("session-two", "kb-two")

    first = store.paths.sessions_dir / "session-one"
    second = store.paths.sessions_dir / "session-two"

    for directory in (first, second):
        assert directory.is_dir()
        assert (directory / "messages.jsonl").is_file()
        assert (directory / "rag_context.jsonl").is_file()
        assert (directory / "runtime.json").is_file()
        assert (directory / "session_system_prompt.md").is_file()

    assert first != second
    assert store.read_runtime_state("session-one")["kb_id"] == "kb-one"
    assert store.read_runtime_state("session-two")["kb_id"] == "kb-two"
    assert store.read_runtime_state("session-one")["retention"] == {
        "cleanup_enabled": False
    }


def test_messages_are_appended_with_required_audit_fields(store: ContextStore) -> None:
    store.start_session("session-1", "kb-1")

    store.append_message(
        session_id="session-1",
        role="user",
        content="第一个问题",
        turn_index=1,
        duration=1.25,
    )
    store.append_message(
        session_id="session-1",
        role="assistant",
        content="第一个回答",
        turn_index=1,
        duration=0.75,
        metadata={"source": "agent"},
    )

    records = store.read_message(session_id="session-1")

    assert [record["content"] for record in records] == ["第一个问题", "第一个回答"]
    assert all(record["session_id"] == "session-1" for record in records)
    assert all(record["kb_id"] == "kb-1" for record in records)
    assert all(record["turn_index"] == 1 for record in records)
    assert all(isinstance(record["timestamp"], str) and record["timestamp"] for record in records)
    assert [record["duration"] for record in records] == [1.25, 0.75]
    assert records[1]["metadata"] == {"source": "agent"}


def test_rag_contexts_are_appended_with_required_audit_fields(store: ContextStore) -> None:
    store.start_session("session-1", "kb-1")

    store.append_rag_context(
        "session-1",
        {
            "turn_index": 1,
            "duration": 0.12,
            "query": "第一个问题",
            "hit": True,
            "evidence_count": 1,
        },
    )
    store.append_rag_context(
        "session-1",
        {
            "turn_index": 2,
            "duration": 0.08,
            "query": "第二个问题",
            "hit": False,
            "evidence_count": 0,
        },
    )

    records = store.read_rag_context("session-1")

    assert [record["query"] for record in records] == ["第一个问题", "第二个问题"]
    assert all(record["session_id"] == "session-1" for record in records)
    assert all(record["kb_id"] == "kb-1" for record in records)
    assert [record["turn_index"] for record in records] == [1, 2]
    assert [record["duration"] for record in records] == [0.12, 0.08]
    assert all(isinstance(record["timestamp"], str) and record["timestamp"] for record in records)


def test_end_session_preserves_raw_records_and_updates_runtime(store: ContextStore) -> None:
    store.start_session("session-1", "kb-1")
    store.append_message(
        session_id="session-1",
        role="user",
        content="需要永久保留的问题",
        turn_index=1,
        duration=0.5,
    )
    store.append_rag_context(
        "session-1",
        {"turn_index": 1, "duration": 0.1, "query": "问题", "hit": False},
    )

    session_dir = store.paths.sessions_dir / "session-1"
    messages_file = session_dir / "messages.jsonl"
    rag_file = session_dir / "rag_context.jsonl"
    messages_before = messages_file.read_bytes()
    rag_before = rag_file.read_bytes()

    store.end_session("session-1")

    assert messages_file.read_bytes() == messages_before
    assert rag_file.read_bytes() == rag_before
    runtime = store.read_runtime_state("session-1")
    assert runtime["state"] == "ended"
    assert isinstance(runtime["ended_at"], str) and runtime["ended_at"]
    assert isinstance(runtime["duration"], float)
    assert runtime["duration"] >= 0
    assert runtime["retention"]["cleanup_enabled"] is False


@pytest.mark.parametrize("filename,reader", [
    ("messages.jsonl", lambda value: value.read_message(session_id="session-1")),
    ("rag_context.jsonl", lambda value: value.read_rag_context("session-1")),
])
def test_corrupt_jsonl_line_is_skipped(
    store: ContextStore,
    filename: str,
    reader: Any,
) -> None:
    store.start_session("session-1", "kb-1")
    path = store.paths.sessions_dir / "session-1" / filename
    path.write_text(
        '{"sequence": 1}\n{this is not json}\n{"sequence": 2}\n',
        encoding="utf-8",
    )

    assert reader(store) == [{"sequence": 1}, {"sequence": 2}]


def test_invalid_duration_is_rejected(store: ContextStore) -> None:
    store.start_session("session-1", "kb-1")

    with pytest.raises(ValueError, match="duration"):
        store.append_message(
            session_id="session-1",
            role="user",
            content="问题",
            turn_index=1,
            duration=-0.1,
        )

    with pytest.raises(ValueError, match="duration"):
        store.append_rag_context(
            "session-1",
            {"turn_index": 1, "duration": -0.1},
        )


def test_audit_records_require_an_initialized_session(store: ContextStore) -> None:
    with pytest.raises(ValueError, match="valid kb_id"):
        store.append_message(
            session_id="missing-session",
            role="user",
            content="问题",
            turn_index=1,
            duration=0.1,
        )

    with pytest.raises(ValueError, match="valid kb_id"):
        store.append_rag_context(
            "missing-session",
            {"turn_index": 1, "duration": 0.1},
        )

