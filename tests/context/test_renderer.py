"""M2-B 固定 SessionSystemPrompt 测试。"""

import json
from pathlib import Path
from typing import cast

import pytest

from liverag.config.settings import RagToolMode
from liverag.context.defaults import (
    DEFAULT_KNOWLEDGE_OVERVIEW_FALLBACK,
    DEFAULT_RAG_TOOL_DESCRIPTION,
    RAG_DISABLED_DESCRIPTION,
)
from liverag.context.renderer import SessionPromptRenderer
from liverag.context.store import ContextStore
from liverag.runtime.paths import build_runtime_paths


@pytest.fixture
def store(tmp_path: Path) -> ContextStore:
    """创建与真实用户数据完全隔离的 ContextStore。"""

    value = ContextStore(build_runtime_paths(tmp_path / "user-data"))
    value.initialize()
    return value


def write_history(
    store: ContextStore,
    kb_id: str,
    records: list[dict[str, object]],
) -> None:
    """为指定知识库写入测试用长期 history。"""

    directory = store.paths.history_dir / kb_id
    directory.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    )
    (directory / "history.jsonl").write_text(content, encoding="utf-8")


def test_read_recent_history_returns_latest_records_for_selected_kb(
    store: ContextStore,
) -> None:
    write_history(
        store,
        "kb-one",
        [
            {"cursor": 1, "content": "第一条"},
            {"cursor": 2, "content": "第二条"},
            {"cursor": 3, "content": "第三条"},
        ],
    )
    write_history(
        store,
        "kb-two",
        [{"cursor": 1, "content": "其他知识库的记录"}],
    )

    records = store.read_recent_history("kb-one", limit=2)

    assert [record["content"] for record in records] == ["第二条", "第三条"]
    assert all(record["content"] != "其他知识库的记录" for record in records)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "2"])
def test_read_recent_history_rejects_invalid_limit(
    store: ContextStore,
    limit: object,
) -> None:
    with pytest.raises(ValueError, match="limit"):
        store.read_recent_history("kb-one", limit=cast(int, limit))


def test_read_knowledge_overview_creates_stable_fallback(
    store: ContextStore,
) -> None:
    overview = store.read_knowledge_overview("kb-one")
    overview_file = (
        store.paths.context_dir / "kb-one" / "knowledge_overview.md"
    )
    meta_file = (
        store.paths.context_dir / "kb-one" / "knowledge_overview_meta.json"
    )

    assert overview == DEFAULT_KNOWLEDGE_OVERVIEW_FALLBACK.rstrip() + "\n"
    assert overview_file.read_text(encoding="utf-8") == overview
    metadata = json.loads(meta_file.read_text(encoding="utf-8"))
    assert metadata["kb_id"] == "kb-one"
    assert metadata["stale"] is True
    assert metadata["reason"] == "default_created"
    assert metadata["source"] == "default"


def test_lock_session_system_prompt_writes_once(store: ContextStore) -> None:
    store.start_session("session-1", "kb-one")

    first = store.lock_session_system_prompt("session-1", "第一次渲染")
    second = store.lock_session_system_prompt("session-1", "第二次渲染")

    assert first == "第一次渲染\n"
    assert second == first
    assert store.read_session_system_prompt("session-1") == first


def test_lock_session_system_prompt_rejects_empty_content(
    store: ContextStore,
) -> None:
    store.start_session("session-1", "kb-one")

    with pytest.raises(ValueError, match="prompt"):
        store.lock_session_system_prompt("session-1", " \n ")


def test_lock_session_system_prompt_requires_initialized_session(
    store: ContextStore,
) -> None:
    with pytest.raises(ValueError, match="valid kb_id"):
        store.lock_session_system_prompt("missing-session", "提示词")


def test_render_auto_includes_all_context_and_tool_rules(
    store: ContextStore,
) -> None:
    store.start_session("session-1", "kb-one")
    store.write_soul("保持温和、简短。")
    write_history(
        store,
        "kb-one",
        [
            {
                "cursor": 7,
                "timestamp": "2026-07-23T10:00:00+00:00",
                "content": "用户正在开发 LiveRAG。",
            }
        ],
    )
    store.write_knowledge_overview(
        "kb-one",
        "# 知识库概览\n\n包含 LiveRAG 架构资料。",
        stale=False,
        source="context_model",
    )

    result = SessionPromptRenderer(store=store, history_limit=20).render(
        session_id="session-1",
        kb_id="kb-one",
        kb_name="项目知识库",
        rag_tool_mode="auto",
    )

    assert "保持温和、简短。" in result.prompt
    assert "用户正在开发 LiveRAG。" in result.prompt
    assert "包含 LiveRAG 架构资料。" in result.prompt
    assert DEFAULT_RAG_TOOL_DESCRIPTION.strip() in result.prompt
    assert RAG_DISABLED_DESCRIPTION.strip() not in result.prompt
    assert result.history_count == 1
    assert result.prompt_chars == len(result.prompt)
    assert result.kb_id == "kb-one"
    assert result.kb_name == "项目知识库"
    assert store.read_session_system_prompt("session-1") == result.prompt


def test_render_never_uses_disabled_rules_without_auto_tool_description(
    store: ContextStore,
) -> None:
    store.start_session("session-1", "kb-one")

    result = SessionPromptRenderer(store=store, history_limit=20).render(
        session_id="session-1",
        kb_id="kb-one",
        kb_name="项目知识库",
        rag_tool_mode="never",
    )

    assert RAG_DISABLED_DESCRIPTION.strip() in result.prompt
    assert DEFAULT_RAG_TOOL_DESCRIPTION.strip() not in result.prompt


def test_render_keeps_first_prompt_after_context_and_mode_change(
    store: ContextStore,
) -> None:
    store.start_session("session-1", "kb-one")
    store.write_soul("首次 SOUL")
    write_history(
        store,
        "kb-one",
        [{"cursor": 1, "content": "首次 history"}],
    )
    renderer = SessionPromptRenderer(store=store, history_limit=20)

    first = renderer.render(
        session_id="session-1",
        kb_id="kb-one",
        kb_name="项目知识库",
        rag_tool_mode="auto",
    )

    store.write_soul("修改后的 SOUL")
    write_history(
        store,
        "kb-one",
        [{"cursor": 2, "content": "修改后的 history"}],
    )
    second = renderer.render(
        session_id="session-1",
        kb_id="kb-one",
        kb_name="项目知识库",
        rag_tool_mode="never",
    )

    assert second.prompt == first.prompt
    assert second.prompt_chars == first.prompt_chars
    assert "首次 SOUL" in second.prompt
    assert "首次 history" in second.prompt
    assert "修改后的 SOUL" not in second.prompt
    assert "修改后的 history" not in second.prompt
    assert DEFAULT_RAG_TOOL_DESCRIPTION.strip() in second.prompt
    assert RAG_DISABLED_DESCRIPTION.strip() not in second.prompt


def test_render_rejects_session_knowledge_base_mismatch(
    store: ContextStore,
) -> None:
    store.start_session("session-1", "kb-one")

    with pytest.raises(ValueError, match="kb_id"):
        SessionPromptRenderer(store=store, history_limit=20).render(
            session_id="session-1",
            kb_id="kb-two",
            kb_name="其他知识库",
            rag_tool_mode="auto",
        )


def test_render_rejects_unknown_rag_tool_mode(store: ContextStore) -> None:
    store.start_session("session-1", "kb-one")

    with pytest.raises(ValueError, match="rag_tool_mode"):
        SessionPromptRenderer(store=store, history_limit=20).render(
            session_id="session-1",
            kb_id="kb-one",
            kb_name="项目知识库",
            rag_tool_mode=cast(RagToolMode, "sometimes"),
        )


@pytest.mark.parametrize("history_limit", [0, -1, True, 1.5])
def test_renderer_rejects_invalid_history_limit(
    store: ContextStore,
    history_limit: object,
) -> None:
    with pytest.raises(ValueError, match="history_limit"):
        SessionPromptRenderer(
            store=store,
            history_limit=cast(int, history_limit),
        )
