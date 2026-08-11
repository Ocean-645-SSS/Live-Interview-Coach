"""M3-C Knowledge Overview 生成器测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import liverag.context.overview as overview_module
from liverag.config.settings import ContextModelSettings, RagClientSettings
from liverag.context.overview import KnowledgeOverviewGenerator


class FakeOverviewStore:
    """记录生成器对 Overview 存储的读写。"""

    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    def read_knowledge_overview_prompt(self) -> str:
        return "生成知识库概览。"

    def write_knowledge_overview(self, kb_id: str, content: str, **meta: Any) -> None:
        self.writes.append({"kb_id": kb_id, "content": content, **meta})

    def read_knowledge_overview_meta(self, kb_id: str) -> dict[str, Any]:
        return {"kb_id": kb_id, "stale": False}


@pytest.mark.asyncio
async def test_generate_accepts_rag_settings_keyword_and_writes_missing_key_fallback() -> None:
    store = FakeOverviewStore()
    generator = KnowledgeOverviewGenerator(
        store=store,  # type: ignore[arg-type]
        settings=ContextModelSettings(api_key=""),
    )

    result = await generator.generate(
        kb_id="kb-one",
        kb_name="产品知识库",
        raw_overview={"summary": {"total_documents": 2}},
        rag_settings=RagClientSettings(),
        source_job_id="job-one",
    )

    assert result["generated"] is False
    assert result["fallback"] is True
    assert "名称：产品知识库" in result["content"]
    assert store.writes == [
        {
            "kb_id": "kb-one",
            "content": result["content"],
            "reason": "missing_context_model_api_key",
            "stale": True,
            "source": "fallback",
            "source_job_id": "job-one",
            "raw_overview": {"summary": {"total_documents": 2}},
        }
    ]


@pytest.mark.asyncio
async def test_generate_cleans_model_fence_and_marks_overview_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeOverviewStore()
    captured: dict[str, Any] = {}

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="```markdown\n# 概览\n正文\n```")
                    )
                ]
            )

    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(overview_module, "AsyncClient", FakeAsyncClient)
    generator = KnowledgeOverviewGenerator(
        store=store,  # type: ignore[arg-type]
        settings=ContextModelSettings(api_key="secret"),
    )

    result = await generator.generate(
        kb_id="kb-one",
        kb_name="产品知识库",
        raw_overview={"topics": [{"name": "LiveRAG"}]},
        rag_settings=RagClientSettings(top_k=6),
        source_job_id="job-one",
    )

    assert result["generated"] is True
    assert result["content"] == "# 概览\n正文"
    assert captured["model"] == "qwen-max"
    assert '"top_k": 6' in captured["messages"][1]["content"]
    assert store.writes[0]["stale"] is False
    assert store.writes[0]["source"] == "context_model"

