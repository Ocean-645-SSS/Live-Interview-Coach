"""测试 M1 RagEngine 的 LightRAG 边界和安全查询契约。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import liverag.rag.engine as engine_module
from liverag.rag.engine import (
    NO_EVIDENCE_ANSWER,
    RagEngine,
    RagEngineError,
    RagQueryTimeoutError,
)
from liverag.rag.rag_settings import RAGSettings
from liverag.rag.schemas import ConversationOptions, QueryOptions


class FakeLightRAG:
    """记录 RagEngine 与 LightRAG 之间的异步交互。"""

    def __init__(self, **kwargs: Any) -> None:
        self.constructor_kwargs = kwargs
        self.initialize_calls = 0
        self.finalize_calls = 0
        self.enqueue_calls: list[dict[str, Any]] = []
        self.pipeline_process_calls = 0
        self.query_calls: list[tuple[str, Any]] = []
        self.query_results: list[dict[str, Any] | BaseException] = []

    async def initialize_storages(self) -> None:
        self.initialize_calls += 1

    async def finalize_storages(self) -> None:
        self.finalize_calls += 1

    async def apipeline_enqueue_documents(
        self,
        texts: list[str],
        *,
        ids: list[str],
        file_paths: list[str],
        track_id: str,
    ) -> None:
        self.enqueue_calls.append(
            {
                "texts": texts,
                "ids": ids,
                "file_paths": file_paths,
                "track_id": track_id,
            }
        )

    async def apipeline_process_enqueue_documents(self) -> None:
        self.pipeline_process_calls += 1

    async def aquery_llm(self, query: str, *, param: Any) -> dict[str, Any]:
        self.query_calls.append((query, param))
        result = self.query_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def engine_settings(tmp_path: Path) -> RAGSettings:
    """使用完整配置形状，并把所有运行目录放在临时目录中。"""

    kb_root = tmp_path / "knowledge_bases" / "kb_alpha"
    return RAGSettings(
        user_data_dir=str(tmp_path),
        knowledge_bases_dir=str(tmp_path / "knowledge_bases"),
        working_dir=str(kb_root / "storage"),
        upload_dir=str(kb_root / "sources"),
        rag_log_dir=str(kb_root / "logs"),
        workspace="kb_alpha",
        kb_id="kb_alpha",
        kb_name="Alpha",
        llm_model="fake-llm",
        llm_api_key="fake-key",
        llm_base_url="https://llm.invalid/v1",
        embedding_model="fake-embedding",
        embedding_api_key="fake-key",
        embedding_base_url="https://embedding.invalid/v1",
        embedding_dim=1024,
        query_timeout_ms=10,
    )


@pytest.fixture
def fake_lightrag_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[FakeLightRAG], Callable[..., FakeLightRAG]]:
    instances: list[FakeLightRAG] = []

    def factory(**kwargs: Any) -> FakeLightRAG:
        instance = FakeLightRAG(**kwargs)
        instances.append(instance)
        return instance

    monkeypatch.setattr(engine_module, "LightRAG", factory)
    return instances, factory


async def initialized_engine(
    engine_settings: RAGSettings,
    fake_lightrag_factory: tuple[list[FakeLightRAG], Callable[..., FakeLightRAG]],
) -> tuple[RagEngine, FakeLightRAG]:
    engine = RagEngine(engine_settings)
    await engine.initialize()
    return engine, fake_lightrag_factory[0][0]


@pytest.mark.asyncio
async def test_initialize_uses_selected_working_dir_and_initializes_storage_once(
    engine_settings: RAGSettings,
    fake_lightrag_factory: tuple[list[FakeLightRAG], Callable[..., FakeLightRAG]],
) -> None:
    engine = RagEngine(engine_settings)

    await engine.initialize()
    await engine.initialize()

    instances = fake_lightrag_factory[0]
    assert len(instances) == 1
    assert instances[0].constructor_kwargs["working_dir"] == engine_settings.absolute_working_dir
    assert instances[0].constructor_kwargs["workspace"] == "kb_alpha"
    assert instances[0].initialize_calls == 1


@pytest.mark.asyncio
async def test_enqueue_maps_documents_to_lightrag(
    engine_settings: RAGSettings,
    fake_lightrag_factory: tuple[list[FakeLightRAG], Callable[..., FakeLightRAG]],
) -> None:
    engine, fake = await initialized_engine(engine_settings, fake_lightrag_factory)

    result = await engine.enqueue_documents(
        texts=["Alpha 文档正文"],
        document_ids=["doc_alpha"],
        file_sources=["sources/doc_alpha/alpha.md"],
        track_id="job_alpha",
    )
    await asyncio.sleep(0)

    assert fake.enqueue_calls == [
        {
            "texts": ["Alpha 文档正文"],
            "ids": ["doc_alpha"],
            "file_paths": ["sources/doc_alpha/alpha.md"],
            "track_id": "job_alpha",
        }
    ]
    assert result["kb_id"] == "kb_alpha"
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_query_context_maps_options_and_returns_structured_evidence(
    engine_settings: RAGSettings,
    fake_lightrag_factory: tuple[list[FakeLightRAG], Callable[..., FakeLightRAG]],
) -> None:
    engine, fake = await initialized_engine(engine_settings, fake_lightrag_factory)
    fake.query_results.append(
        {
            "status": "success",
            "llm_response": {"content": "Alpha 是项目的检索模块。"},
            "data": {
                "references": [{"document_id": "doc_alpha", "file_path": "alpha.md"}],
                "chunks": [
                    {
                        "chunk_id": "chunk_alpha_1",
                        "document_id": "doc_alpha",
                        "content": "Alpha 是项目的检索模块。",
                        "score": 0.92,
                    }
                ],
            },
        }
    )

    result, metrics = await engine.query_context(
        "Alpha 是什么？",
        "default",
        QueryOptions(
            mode="hybrid",
            top_k=8,
            chunk_top_k=3,
            include_references=True,
            include_chunk_content=True,
        ),
        ConversationOptions(),
    )

    _, query_param = fake.query_calls[0]
    assert query_param.mode == "hybrid"
    assert query_param.top_k == 8
    assert query_param.chunk_top_k == 3
    assert query_param.only_need_context is True
    assert result.kb_id == "kb_alpha"
    assert result.hit is True
    assert result.has_context is True
    assert result.references[0].document_id == "doc_alpha"
    assert result.chunks[0].chunk_id == "chunk_alpha_1"
    assert result.chunks[0].content == "Alpha 是项目的检索模块。"
    assert result.duration >= 0
    assert metrics["chunks_count"] == 1


@pytest.mark.asyncio
async def test_query_context_without_evidence_returns_safe_empty_result(
    engine_settings: RAGSettings,
    fake_lightrag_factory: tuple[list[FakeLightRAG], Callable[..., FakeLightRAG]],
) -> None:
    engine, fake = await initialized_engine(engine_settings, fake_lightrag_factory)
    fake.query_results.append(
        {
            "status": "success",
            "llm_response": {"content": "[no-context]"},
            "data": {"references": [], "chunks": []},
        }
    )

    result, _ = await engine.query_context(
        "不存在的事实",
        "default",
        QueryOptions(),
        ConversationOptions(),
    )

    assert result.kb_id == "kb_alpha"
    assert result.hit is False
    assert result.has_context is False
    assert result.context == ""
    assert result.references == []
    assert result.chunks == []


@pytest.mark.asyncio
async def test_query_answer_without_evidence_returns_fixed_refusal(
    engine_settings: RAGSettings,
    fake_lightrag_factory: tuple[list[FakeLightRAG], Callable[..., FakeLightRAG]],
) -> None:
    engine, fake = await initialized_engine(engine_settings, fake_lightrag_factory)
    fake.query_results.append(
        {
            "status": "success",
            "llm_response": {"content": "模型猜测的答案"},
            "data": {"references": [], "chunks": []},
        }
    )

    result, _ = await engine.query_answer(
        "请猜一个答案",
        "default",
        QueryOptions(),
        ConversationOptions(),
    )

    assert len(fake.query_calls) == 1
    assert result.hit is False
    assert result.has_context is False
    assert result.answer == NO_EVIDENCE_ANSWER
    assert result.references == []
    assert result.chunks == []


@pytest.mark.asyncio
async def test_query_timeout_is_mapped_to_domain_timeout(
    engine_settings: RAGSettings,
    fake_lightrag_factory: tuple[list[FakeLightRAG], Callable[..., FakeLightRAG]],
) -> None:
    engine, fake = await initialized_engine(engine_settings, fake_lightrag_factory)
    fake.query_results.append(asyncio.TimeoutError())

    with pytest.raises(RagQueryTimeoutError) as caught:
        await engine.query_context(
            "会超时的问题",
            "default",
            QueryOptions(),
            ConversationOptions(),
        )

    assert isinstance(caught.value.__cause__, asyncio.TimeoutError)


@pytest.mark.asyncio
async def test_lightrag_failure_is_mapped_to_domain_error(
    engine_settings: RAGSettings,
    fake_lightrag_factory: tuple[list[FakeLightRAG], Callable[..., FakeLightRAG]],
) -> None:
    engine, fake = await initialized_engine(engine_settings, fake_lightrag_factory)
    fake.query_results.append(RuntimeError("provider unavailable"))

    with pytest.raises(RagEngineError) as caught:
        await engine.query_context(
            "触发异常的问题",
            "default",
            QueryOptions(),
            ConversationOptions(),
        )

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert str(caught.value.__cause__) == "provider unavailable"
