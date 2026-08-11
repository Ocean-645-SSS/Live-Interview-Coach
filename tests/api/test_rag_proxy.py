"""Interview Coach knowledge-base gateway tests."""

from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from liverag.api.rag_gateway import GatewayFileResponse, GatewayResponse, RagGateway


def _envelope(
    *,
    status: str,
    data: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "request_id": "req-test",
        "status": status,
        "data": data,
        "metrics": {},
        "error": error,
    }


def test_rag_ready_preserves_unavailable_status(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(path: str, **_: Any) -> GatewayResponse:
        assert path == "/v1/readyz"
        return GatewayResponse(
            status_code=503,
            body=_envelope(
                status="error",
                error={"type": "RagUnavailable", "message": "RAG unavailable"},
            ),
        )

    monkeypatch.setattr(api_server.rag_gateway, "get", fake_get)
    response = api_client.get("/rag/ready")
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "RagUnavailable"


def test_knowledge_base_proxy_preserves_success_envelope(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _envelope(
        status="ok",
        data={"knowledge_bases": [{"kb_id": "default", "name": "个人简历"}]},
    )

    async def fake_get(path: str, **_: Any) -> GatewayResponse:
        assert path == "/v1/knowledge-bases"
        return GatewayResponse(status_code=200, body=payload)

    monkeypatch.setattr(api_server.rag_gateway, "get", fake_get)
    response = api_client.get("/rag/knowledge-bases")
    assert response.status_code == 200
    assert response.json() == payload


def test_create_job_knowledge_base_builds_name_from_company_and_role(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_post_json(path: str, *, payload: dict[str, Any]) -> GatewayResponse:
        captured.update(path=path, payload=payload)
        return GatewayResponse(
            status_code=200,
            body=_envelope(status="ok", data={"kb_id": "kb-job"}),
        )

    monkeypatch.setattr(api_server.rag_gateway, "post_json", fake_post_json)
    response = api_client.post(
        "/rag/knowledge-bases",
        json={"company": "示例公司", "role": "后端工程师", "description": "岗位资料"},
    )
    assert response.status_code == 200
    assert captured == {
        "path": "/v1/knowledge-bases",
        "payload": {"name": "示例公司 · 后端工程师", "description": "岗位资料"},
    }


def test_create_job_knowledge_base_requires_company_and_role(api_client: TestClient) -> None:
    response = api_client.post("/rag/knowledge-bases", json={"company": "示例公司"})
    assert response.status_code == 422


def test_default_resume_metadata_cannot_be_edited(api_client: TestClient) -> None:
    response = api_client.patch("/rag/knowledge-bases/default", json={"name": "其他名称"})
    assert response.status_code == 409


def test_query_context_forwards_path_and_payload(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_post_json(path: str, *, payload: dict[str, Any]) -> GatewayResponse:
        captured.update(path=path, payload=payload)
        return GatewayResponse(
            status_code=200,
            body=_envelope(status="ok", data={"context": "命中的资料"}),
        )

    monkeypatch.setattr(api_server.rag_gateway, "post_json", fake_post_json)
    response = api_client.post(
        "/rag/knowledge-bases/kb-1/query/context",
        json={"query": "测试问题"},
    )
    assert response.status_code == 200
    assert captured["path"] == "/v1/knowledge-bases/kb-1/query/context"
    assert captured["payload"]["query"] == "测试问题"


def test_text_document_forwards_path_and_payload(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_post_json(path: str, *, payload: dict[str, Any]) -> GatewayResponse:
        captured.update(path=path, payload=payload)
        return GatewayResponse(
            status_code=200,
            body=_envelope(status="ok", data={"track_id": "insert-1"}),
        )

    monkeypatch.setattr(api_server.rag_gateway, "post_json", fake_post_json)
    response = api_client.post(
        "/rag/knowledge-bases/kb-1/documents/text",
        json={"text": "待索引文本", "file_source": "notes.txt", "document_id": "doc-1"},
    )
    assert response.status_code == 200
    assert captured["path"] == "/v1/knowledge-bases/kb-1/documents/text"
    assert captured["payload"]["text"] == "待索引文本"


def test_file_upload_forwards_files_and_pdf_password(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_post_files(
        path: str,
        *,
        files: list[Any],
        pdf_password: str | None = None,
    ) -> GatewayResponse:
        captured.update(
            path=path,
            filenames=[item.filename for item in files],
            pdf_password=pdf_password,
        )
        return GatewayResponse(
            status_code=200,
            body=_envelope(status="ok", data={"parsed_count": 1}),
        )

    monkeypatch.setattr(api_server.rag_gateway, "post_files", fake_post_files)
    response = api_client.post(
        "/rag/knowledge-bases/kb-1/documents/files",
        files={"files": ("resume.pdf", b"pdf bytes", "application/pdf")},
        data={"pdf_password": "secret"},
    )
    assert response.status_code == 200
    assert captured["filenames"] == ["resume.pdf"]
    assert captured["pdf_password"] == "secret"


def test_document_source_forwards_file_response(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_file(
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> GatewayFileResponse:
        assert path == "/v1/knowledge-bases/kb-1/documents/doc-1/source"
        assert params == {"disposition": "attachment"}
        return GatewayFileResponse(
            status_code=200,
            body=b"source text",
            headers={"content-type": "text/plain; charset=utf-8"},
        )

    monkeypatch.setattr(api_server.rag_gateway, "get_file", fake_get_file)
    response = api_client.get(
        "/rag/knowledge-bases/kb-1/documents/doc-1/source",
        params={"disposition": "attachment"},
    )
    assert response.status_code == 200
    assert response.content == b"source text"


def test_delete_document_forwards_query_parameter(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_delete(
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> GatewayResponse:
        captured.update(path=path, params=params)
        return GatewayResponse(
            status_code=200,
            body=_envelope(status="ok", data={"deleted": True}),
        )

    monkeypatch.setattr(api_server.rag_gateway, "delete", fake_delete)
    response = api_client.delete(
        "/rag/knowledge-bases/kb-1/documents/doc-1",
        params={"delete_llm_cache": "true"},
    )
    assert response.status_code == 200
    assert captured["params"] == {"delete_llm_cache": True}


def test_document_summary_prefers_original_filename_and_hides_internal_path() -> None:
    payload = RagGateway._normalize_document_summary(
        {
            "document_id": "doc-1",
            "original_filename": "resume.docx",
            "file_path": "documents/doc-1/source/uuid.docx",
            "source_file_path": "C:/private/documents/doc-1/source/uuid.docx",
        }
    )
    assert payload["original_filename"] == "resume.docx"
    assert payload["file_path"] == "resume.docx"
    assert "source_file_path" not in payload
