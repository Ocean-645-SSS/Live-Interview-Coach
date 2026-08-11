"""管理 API 的 RAG 代理测试。"""

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
    assert response.json()["status"] == "error"
    assert response.json()["error"]["type"] == "RagUnavailable"


def test_knowledge_base_proxy_preserves_success_envelope(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _envelope(
        status="ok",
        data={"knowledge_bases": [{"kb_id": "default", "name": "默认知识库"}]},
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

    async def fake_post_json(
        path: str,
        *,
        payload: dict[str, Any],
    ) -> GatewayResponse:
        captured["path"] = path
        captured["payload"] = payload
        return GatewayResponse(
            status_code=200,
            body=_envelope(
                status="ok",
                data={"kb_id": "kb_job", "name": payload["name"]},
            ),
        )

    monkeypatch.setattr(api_server.rag_gateway, "post_json", fake_post_json)

    response = api_client.post(
        "/rag/knowledge-bases",
        json={
            "company": "字节跳动",
            "role": "后端开发工程师",
            "description": "商业化技术",
        },
    )

    assert response.status_code == 200
    assert captured == {
        "path": "/v1/knowledge-bases",
        "payload": {
            "name": "字节跳动 · 后端开发工程师",
            "description": "商业化技术",
        },
    }


def test_create_job_knowledge_base_requires_company_and_role(
    api_client: TestClient,
) -> None:
    response = api_client.post(
        "/rag/knowledge-bases",
        json={"company": "字节跳动"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "创建岗位资料库必须填写公司名称和岗位名称"


def test_default_resume_metadata_cannot_be_edited(api_client: TestClient) -> None:
    response = api_client.patch(
        "/rag/knowledge-bases/default",
        json={"name": "其他名称"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "个人简历资料库的名称和用途不可修改"


def test_query_context_forwards_path_and_payload(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_post_json(
        path: str,
        *,
        payload: dict[str, Any],
    ) -> GatewayResponse:
        captured["path"] = path
        captured["payload"] = payload
        return GatewayResponse(
            status_code=200,
            body=_envelope(status="ok", data={"context": "命中的上下文"}),
        )

    monkeypatch.setattr(api_server.rag_gateway, "post_json", fake_post_json)

    response = api_client.post(
        "/rag/knowledge-bases/kb-1/query/context",
        json={"query": "测试问题"},
    )

    assert response.status_code == 200
    assert captured["path"] == "/v1/knowledge-bases/kb-1/query/context"
    assert captured["payload"]["query"] == "测试问题"
    assert response.json()["data"]["context"] == "命中的上下文"


def test_query_answer_forwards_path_and_complete_payload(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_post_json(
        path: str,
        *,
        payload: dict[str, Any],
    ) -> GatewayResponse:
        captured["path"] = path
        captured["payload"] = payload
        return GatewayResponse(
            status_code=200,
            body=_envelope(
                status="ok",
                data={
                    "kb_id": "kb-1",
                    "answer": "这是知识库生成的答案。",
                    "hit": True,
                },
            ),
        )

    monkeypatch.setattr(api_server.rag_gateway, "post_json", fake_post_json)

    request_payload = {
        "query": " 测试问题 ",
        "profile": "voice",
        "options": {
            "mode": "hybrid",
            "top_k": 8,
            "chunk_top_k": 3,
            "include_references": True,
            "include_chunk_content": True,
            "context_max_chars": 2400,
        },
        "conversation": {
            "last_query": "上一轮问题",
            "rewrite_followup": True,
        },
    }
    response = api_client.post(
        "/rag/knowledge-bases/kb-1/query/answer",
        json=request_payload,
    )

    assert response.status_code == 200
    assert captured["path"] == "/v1/knowledge-bases/kb-1/query/answer"
    assert captured["payload"] == {
        "query": "测试问题",
        "profile": "voice",
        "options": {
            **request_payload["options"],
            "hl_keywords": [],
            "ll_keywords": [],
        },
        "conversation": request_payload["conversation"],
    }
    assert response.json()["data"]["answer"] == "这是知识库生成的答案。"


def test_query_answer_preserves_upstream_error_envelope(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream_body = _envelope(
        status="error",
        error={"type": "RagQueryTimeoutError", "message": "RAG 查询超时"},
    )

    async def fake_post_json(
        path: str,
        *,
        payload: dict[str, Any],
    ) -> GatewayResponse:
        assert path == "/v1/knowledge-bases/kb-1/query/answer"
        assert payload["query"] == "测试问题"
        return GatewayResponse(status_code=504, body=upstream_body)

    monkeypatch.setattr(api_server.rag_gateway, "post_json", fake_post_json)

    response = api_client.post(
        "/rag/knowledge-bases/kb-1/query/answer",
        json={"query": "测试问题"},
    )

    assert response.status_code == 504
    assert response.json() == upstream_body


def test_text_document_forwards_path_and_payload(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_post_json(
        path: str,
        *,
        payload: dict[str, Any],
    ) -> GatewayResponse:
        captured["path"] = path
        captured["payload"] = payload
        return GatewayResponse(
            status_code=200,
            body=_envelope(
                status="ok",
                data={"track_id": "insert-1"},
            ),
        )

    monkeypatch.setattr(api_server.rag_gateway, "post_json", fake_post_json)
    monkeypatch.setattr(
        api_server,
        "_mark_overview_stale_if_ok",
        lambda *_args, **_kwargs: None,
    )

    response = api_client.post(
        "/rag/knowledge-bases/kb-1/documents/text",
        json={
            "text": "待索引文本",
            "file_source": "notes.txt",
            "document_id": "doc-notes",
        },
    )

    assert response.status_code == 200
    assert captured["path"] == "/v1/knowledge-bases/kb-1/documents/text"
    assert captured["payload"] == {
        "text": "待索引文本",
        "file_source": "notes.txt",
        "document_id": "doc-notes",
    }


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
        captured["path"] = path
        captured["filenames"] = [item.filename for item in files]
        captured["pdf_password"] = pdf_password
        return GatewayResponse(
            status_code=200,
            body=_envelope(status="ok", data={"parsed_count": 1}),
        )

    monkeypatch.setattr(api_server.rag_gateway, "post_files", fake_post_files)
    monkeypatch.setattr(
        api_server,
        "_mark_overview_stale_if_ok",
        lambda *_args, **_kwargs: None,
    )

    response = api_client.post(
        "/rag/knowledge-bases/kb-1/documents/files",
        files={"files": ("secret.pdf", b"pdf bytes", "application/pdf")},
        data={"pdf_password": "correct-password"},
    )

    assert response.status_code == 200
    assert captured["path"] == "/v1/knowledge-bases/kb-1/documents/files"
    assert captured["filenames"] == ["secret.pdf"]
    assert captured["pdf_password"] == "correct-password"


def test_document_source_forwards_file_response(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_get_file(
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> GatewayFileResponse:
        captured["path"] = path
        captured["params"] = params
        return GatewayFileResponse(
            status_code=200,
            body=b"source text",
            headers={
                "content-type": "text/plain; charset=utf-8",
                "content-disposition": 'attachment; filename="notes.txt"',
            },
        )

    monkeypatch.setattr(api_server.rag_gateway, "get_file", fake_get_file)

    response = api_client.get(
        "/rag/knowledge-bases/kb-1/documents/doc-1/source",
        params={"disposition": "attachment"},
    )

    assert response.status_code == 200
    assert response.content == b"source text"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert captured["path"] == "/v1/knowledge-bases/kb-1/documents/doc-1/source"
    assert captured["params"] == {"disposition": "attachment"}


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
        captured["path"] = path
        captured["params"] = params
        return GatewayResponse(
            status_code=200,
            body=_envelope(status="ok", data={"deleted": True}),
        )

    monkeypatch.setattr(api_server.rag_gateway, "delete", fake_delete)
    monkeypatch.setattr(
        api_server,
        "_mark_overview_stale_if_ok",
        lambda *_args, **_kwargs: None,
    )

    response = api_client.delete(
        "/rag/knowledge-bases/kb-1/documents/doc-1",
        params={"delete_llm_cache": "true"},
    )

    assert response.status_code == 200
    assert captured["path"] == "/v1/knowledge-bases/kb-1/documents/doc-1"
    assert captured["params"] == {"delete_llm_cache": True}


def test_document_summary_prefers_original_filename_and_hides_internal_path() -> None:
    payload = RagGateway._normalize_document_summary(
        {
            "document_id": "doc-1",
            "original_filename": "Fino-Net 项目说明 v1.docx",
            "file_path": "documents/doc-1/source/uuid.docx",
            "source_file_path": "C:/private/documents/doc-1/source/uuid.docx",
        }
    )

    assert payload["original_filename"] == "Fino-Net 项目说明 v1.docx"
    assert payload["file_path"] == "Fino-Net 项目说明 v1.docx"
    assert "source_file_path" not in payload


@pytest.mark.parametrize(
    ("route_suffix", "upstream_suffix"),
    [
        ("context", "context"),
        ("data", "data"),
    ],
)
def test_session_query_uses_locked_knowledge_base(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    route_suffix: str,
    upstream_suffix: str,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_effective_knowledge_base(session_id: str) -> dict[str, Any]:
        captured["session_id"] = session_id
        return {"kb_id": "kb-locked", "name": "Locked"}

    async def fake_post_json(
        path: str,
        *,
        payload: dict[str, Any],
    ) -> GatewayResponse:
        captured["path"] = path
        captured["payload"] = payload
        return GatewayResponse(
            status_code=200,
            body=_envelope(status="ok", data={"result": "ok"}),
        )

    monkeypatch.setattr(
        api_server,
        "_effective_session_knowledge_base",
        fake_effective_knowledge_base,
    )
    monkeypatch.setattr(api_server.rag_gateway, "post_json", fake_post_json)

    response = api_client.post(
        f"/rag/session-query/{route_suffix}",
        params={"session_id": "session-1"},
        json={"query": "会话问题"},
    )

    assert response.status_code == 200
    assert captured["session_id"] == "session-1"
    assert captured["path"] == f"/v1/knowledge-bases/kb-locked/query/{upstream_suffix}"
    assert captured["payload"]["query"] == "会话问题"
