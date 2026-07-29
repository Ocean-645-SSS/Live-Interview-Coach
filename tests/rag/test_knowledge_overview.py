"""M3-C RagEngine 结构化知识库概览测试。"""

from __future__ import annotations

from typing import Any

import pytest

from liverag.rag.engine import RagEngine
from liverag.rag.rag_settings import RAGSettings


class FakeDocumentStatusStore:
    def __init__(
        self,
        *,
        counts: dict[str, int],
        documents: list[tuple[str, dict[str, Any]]],
    ) -> None:
        self.counts = counts
        self.documents = documents

    async def get_all_status_counts(self) -> dict[str, int]:
        return self.counts

    async def get_docs_paginated(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[str, dict[str, Any]]], int]:
        assert page == 1
        assert page_size >= 1
        return self.documents[:page_size], len(self.documents)


class FakeBatchStore:
    def __init__(self, payloads: dict[Any, dict[str, Any]]) -> None:
        self.payloads = payloads

    async def get_by_ids(self, keys: list[Any]) -> list[dict[str, Any]]:
        return [self.payloads.get(key, {}) for key in keys]


class FakeGraph:
    async def node_degree(self, name: str) -> int:
        return {"LiveRAG": 4, "FastAPI": 2, "LiveKit": 1}.get(name, 0)


class FakeOverviewRag:
    def __init__(
        self,
        *,
        counts: dict[str, int],
        documents: list[tuple[str, dict[str, Any]]],
        entities: dict[str, dict[str, Any]] | None = None,
        relations: dict[str, dict[str, Any]] | None = None,
        entity_chunks: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.doc_status = FakeDocumentStatusStore(
            counts=counts,
            documents=documents,
        )
        self.full_entities = FakeBatchStore(entities or {})
        self.full_relations = FakeBatchStore(relations or {})
        self.entity_chunks = FakeBatchStore(entity_chunks or {})
        self.chunk_entity_relation_graph = FakeGraph()


def _engine_with_rag(rag: FakeOverviewRag) -> RagEngine:
    engine = RagEngine(RAGSettings(kb_id="kb-one", kb_name="产品知识库"))
    engine.rag = rag  # type: ignore[assignment]
    return engine


@pytest.mark.asyncio
async def test_knowledge_overview_returns_stable_empty_shape_without_processed_documents() -> None:
    engine = _engine_with_rag(
        FakeOverviewRag(
            counts={"all": 2, "processed": 0, "failed": 1},
            documents=[
                (
                    "doc-pending",
                    {
                        "status": "pending",
                        "file_path": "pending.md",
                        "chunks_count": 0,
                    },
                ),
                (
                    "doc-failed",
                    {
                        "status": "failed",
                        "file_path": "failed.md",
                        "chunks_count": 0,
                    },
                ),
            ],
        )
    )

    result = await engine.knowledge_overview()

    assert result["summary"] == {
        "total_documents": 2,
        "processed_documents": 0,
        "failed_documents": 1,
        "pending_documents": 1,
        "total_chunks": 0,
        "total_entities": 0,
        "total_relationships": 0,
    }
    assert result["topics"] == []
    assert result["top_entities"] == []
    assert result["top_relationships"] == []
    assert [item["document_id"] for item in result["documents"]] == [
        "doc-pending",
        "doc-failed",
    ]


@pytest.mark.asyncio
async def test_knowledge_overview_aggregates_entities_relations_topics_and_documents() -> None:
    engine = _engine_with_rag(
        FakeOverviewRag(
            counts={"all": 3, "processed": 2, "failed": 1},
            documents=[
                (
                    "doc-one",
                    {
                        "status": "processed",
                        "file_path": "one.md",
                        "chunks_count": 3,
                        "updated_at": "2026-07-29T10:00:00Z",
                    },
                ),
                (
                    "doc-two",
                    {
                        "status": "processed",
                        "file_path": "two.md",
                        "chunks_count": 2,
                        "updated_at": "2026-07-29T11:00:00Z",
                    },
                ),
                (
                    "doc-failed",
                    {
                        "status": "failed",
                        "file_path": "failed.md",
                        "chunks_count": 0,
                    },
                ),
            ],
            entities={
                "doc-one": {"entity_names": ["LiveRAG", "FastAPI", "LiveRAG"]},
                "doc-two": {"entity_names": ["LiveRAG", "LiveKit"]},
            },
            relations={
                "doc-one": {
                    "relation_pairs": [
                        ["LiveRAG", "FastAPI"],
                        ["LiveRAG", "FastAPI"],
                    ]
                },
                "doc-two": {"relation_pairs": [["LiveRAG", "LiveKit"]]},
            },
            entity_chunks={
                "LiveRAG": {"count": 5},
                "FastAPI": {"count": 3},
                "LiveKit": {"count": 2},
            },
        )
    )

    result = await engine.knowledge_overview(
        entity_limit=3,
        relation_limit=2,
        document_limit=3,
        topic_limit=3,
    )

    assert result["summary"] == {
        "total_documents": 3,
        "processed_documents": 2,
        "failed_documents": 1,
        "pending_documents": 0,
        "total_chunks": 5,
        "total_entities": 3,
        "total_relationships": 2,
    }
    assert result["top_entities"][0] == {
        "name": "LiveRAG",
        "mention_count": 3,
        "document_count": 2,
        "chunk_count": 5,
        "degree": 4,
        "is_topic_like": True,
    }
    assert [item["name"] for item in result["topics"]] == [
        "LiveRAG",
        "FastAPI",
        "LiveKit",
    ]
    assert result["top_relationships"][0] == {
        "source": "LiveRAG",
        "target": "FastAPI",
        "document_count": 1,
        "mention_count": 2,
    }
    assert result["documents"][0]["topic_preview"] == "LiveRAG / FastAPI"
    assert result["documents"][1]["top_entities"] == ["LiveRAG", "LiveKit"]
    assert result["documents"][2]["top_entities"] == []
