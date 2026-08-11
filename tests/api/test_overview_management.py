"""M3-C 管理 API Overview 协调逻辑测试。"""

from __future__ import annotations

import logging
from types import ModuleType
from typing import Any

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient

from liverag.api.rag_gateway import GatewayResponse
from liverag.context.defaults import DEFAULT_KNOWLEDGE_OVERVIEW_FALLBACK


def _response(
    *,
    status: str,
    data: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    status_code: int = 200,
) -> GatewayResponse:
    return GatewayResponse(
        status_code=status_code,
        body={
            "request_id": "req-overview",
            "status": status,
            "data": data,
            "metrics": {},
            "error": error,
        },
    )


@pytest.mark.asyncio
async def test_raw_knowledge_overview_returns_structured_data(
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"summary": {"total_documents": 3}}

    async def fake_get(path: str, *, params: dict[str, Any]) -> GatewayResponse:
        assert path == "/v1/knowledge-bases/kb-one/overview"
        assert params == {
            "entity_limit": 20,
            "relation_limit": 12,
            "document_limit": 20,
            "topic_limit": 12,
        }
        return _response(status="ok", data=expected)

    monkeypatch.setattr(api_server.rag_gateway, "get", fake_get)

    assert await api_server._raw_knowledge_overview("kb-one") == expected


@pytest.mark.asyncio
async def test_raw_knowledge_overview_raises_for_upstream_error(
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(path: str, *, params: dict[str, Any]) -> GatewayResponse:
        del path, params
        return _response(
            status="error",
            status_code=503,
            error={"type": "RagUnavailable", "message": "RAG Core unavailable"},
        )

    monkeypatch.setattr(api_server.rag_gateway, "get", fake_get)

    with pytest.raises(RuntimeError, match="RAG Core unavailable"):
        await api_server._raw_knowledge_overview("kb-one")


@pytest.mark.asyncio
async def test_raw_knowledge_overview_rejects_malformed_success(
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(path: str, *, params: dict[str, Any]) -> GatewayResponse:
        del path, params
        return _response(status="ok", data=None)

    monkeypatch.setattr(api_server.rag_gateway, "get", fake_get)

    with pytest.raises(RuntimeError, match="invalid overview data"):
        await api_server._raw_knowledge_overview("kb-one")


@pytest.mark.asyncio
async def test_background_generation_logs_failure_context(
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail_detail(kb_id: str) -> dict[str, Any]:
        raise RuntimeError(f"cannot load {kb_id}")

    monkeypatch.setattr(api_server, "_knowledge_base_detail", fail_detail)

    with caplog.at_level(logging.ERROR, logger="liverag.api.server"):
        await api_server._generate_overview_for_completed_job("kb-one", "job-one")

    record = next(
        item for item in caplog.records if item.message == "knowledge_overview.background_generation_failed"
    )
    assert record.kb_id == "kb-one"
    assert record.job_id == "job-one"
    assert record.error == "cannot load kb-one"


def test_get_context_overview_returns_default_content_and_meta(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_detail(kb_id: str) -> dict[str, Any]:
        return {"kb_id": kb_id, "name": "产品知识库"}

    monkeypatch.setattr(api_server, "_knowledge_base_detail", fake_detail)

    response = api_client.get("/rag/knowledge-bases/kb-one/context/overview")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["kb_id"] == "kb-one"
    assert data["content"] == DEFAULT_KNOWLEDGE_OVERVIEW_FALLBACK.rstrip() + "\n"
    assert data["meta"]["stale"] is True
    assert data["meta"]["reason"] == "default_created"
    assert data["meta"]["source"] == "default"


def test_put_context_overview_persists_trimmed_manual_content(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_detail(kb_id: str) -> dict[str, Any]:
        return {"kb_id": kb_id, "name": "产品知识库"}

    monkeypatch.setattr(api_server, "_knowledge_base_detail", fake_detail)

    response = api_client.put(
        "/rag/knowledge-bases/kb-one/context/overview",
        json={"content": "  # 手动概览\n\n正文  "},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["kb_id"] == "kb-one"
    assert data["kb_name"] == "产品知识库"
    assert data["content"] == "# 手动概览\n\n正文\n"
    assert data["meta"]["stale"] is False
    assert data["meta"]["reason"] == "manual_update"
    assert data["meta"]["source"] == "manual"
    assert data["meta"]["source_job_id"] is None


def test_put_context_overview_rejects_blank_without_mutating_existing_content(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_detail(kb_id: str) -> dict[str, Any]:
        return {"kb_id": kb_id, "name": "产品知识库"}

    monkeypatch.setattr(api_server, "_knowledge_base_detail", fake_detail)
    api_server.store.write_knowledge_overview(
        "kb-one",
        "原有概览",
        stale=False,
        reason="manual_update",
        source="manual",
    )

    response = api_client.put(
        "/rag/knowledge-bases/kb-one/context/overview",
        json={"content": "   \n  "},
    )

    assert response.status_code == 422
    assert api_server.store.read_knowledge_overview("kb-one") == "原有概览\n"
    assert api_server.store.read_knowledge_overview_meta("kb-one")["stale"] is False


@pytest.mark.parametrize("method", ["get", "put"])
def test_context_overview_rejects_missing_knowledge_base_without_creating_files(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    async def missing_detail(kb_id: str) -> dict[str, Any]:
        raise HTTPException(status_code=404, detail=f"knowledge base not found: {kb_id}")

    monkeypatch.setattr(api_server, "_knowledge_base_detail", missing_detail)
    path = "/rag/knowledge-bases/missing/context/overview"

    response = (
        api_client.get(path)
        if method == "get"
        else api_client.put(path, json={"content": "不能写入"})
    )

    assert response.status_code == 404
    assert not (api_server.store.paths.context_dir / "missing").exists()


def test_mark_overview_stale_only_for_successful_document_change(
    api_server: ModuleType,
) -> None:
    api_server.store.write_knowledge_overview(
        "kb-one",
        "当前概览",
        stale=False,
        reason="manual_update",
        source="manual",
    )

    api_server._mark_overview_stale_if_ok(
        _response(status="error", status_code=500),
        "kb-one",
        reason="failed_change",
    )
    assert api_server.store.read_knowledge_overview_meta("kb-one")["stale"] is False

    api_server._mark_overview_stale_if_ok(
        _response(status="ok"),
        "kb-one",
        reason="document_deleted",
    )
    meta = api_server.store.read_knowledge_overview_meta("kb-one")
    assert meta["stale"] is True
    assert meta["reason"] == "document_deleted"
    assert meta["source"] == "stale_marker"


@pytest.mark.parametrize(
    ("job_status", "document_status", "expected"),
    [
        ("processing", "processed", False),
        ("failed", "failed", False),
        ("processed", "processed", True),
        ("partial_failed", "processed", True),
        ("partial_failed", "failed", False),
    ],
)
def test_schedule_overview_requires_completed_job_with_processed_document(
    api_server: ModuleType,
    job_status: str,
    document_status: str,
    expected: bool,
) -> None:
    background_tasks = BackgroundTasks()
    response = _response(
        status="ok",
        data={
            "job_id": "job-one",
            "status": job_status,
            "documents": [{"index_status": document_status}],
        },
    )

    api_server._schedule_overview_generation_after_completed_job(
        response,
        kb_id="kb-one",
        job_id="job-one",
        background_tasks=background_tasks,
    )

    assert bool(background_tasks.tasks) is expected
    data = response.body["data"]
    assert isinstance(data, dict)
    assert ("overview_generation" in data) is expected
    if expected:
        assert data["overview_generation"] == {
            "scheduled": True,
            "trigger": "index_completed",
            "job_id": "job-one",
        }


def test_schedule_overview_suppresses_fresh_result_from_same_job(
    api_server: ModuleType,
) -> None:
    api_server.store.write_knowledge_overview(
        "kb-one",
        "已经生成",
        stale=False,
        reason="index_completed",
        source="context_model",
        source_job_id="job-one",
    )
    background_tasks = BackgroundTasks()
    response = _response(
        status="ok",
        data={
            "job_id": "job-one",
            "status": "processed",
            "documents": [{"job_document_status": "processed"}],
        },
    )

    api_server._schedule_overview_generation_after_completed_job(
        response,
        kb_id="kb-one",
        job_id="job-one",
        background_tasks=background_tasks,
    )

    assert background_tasks.tasks == []
    data = response.body["data"]
    assert isinstance(data, dict)
    assert "overview_generation" not in data
