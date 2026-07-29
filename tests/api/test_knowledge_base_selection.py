"""新 Session 默认知识库选择测试。"""

from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from liverag.api.rag_gateway import GatewayResponse


def test_selecting_next_knowledge_base_does_not_change_active_session(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_server.store.start_session(session_id="session-locked", kb_id="kb-a")

    async def fake_knowledge_base_detail(kb_id: str) -> dict[str, Any]:
        assert kb_id == "kb-b"
        return {"kb_id": "kb-b", "name": "知识库 B"}

    async def fake_get(path: str, **_: Any) -> GatewayResponse:
        assert path == "/v1/knowledge-bases/kb-b/ready"
        return GatewayResponse(
            status_code=200,
            body={
                "request_id": "req-ready",
                "status": "ok",
                "data": {"ready": True},
                "metrics": {},
                "error": None,
            },
        )

    monkeypatch.setattr(api_server, "_knowledge_base_detail", fake_knowledge_base_detail)
    monkeypatch.setattr(api_server.rag_gateway, "get", fake_get)

    response = api_client.put(
        "/session/knowledge-base",
        json={"kb_id": "kb-b"},
    )

    assert response.status_code == 200
    assert response.json()["configured"]["kb_id"] == "kb-b"
    configured = api_server.metadata_store.get_session_config("knowledge_base")
    assert configured["kb_id"] == "kb-b"
    active = api_server.store.read_runtime_state("session-locked")
    assert active["kb_id"] == "kb-a"
