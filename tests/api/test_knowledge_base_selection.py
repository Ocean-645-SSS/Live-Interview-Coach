"""新 Session 默认知识库选择测试。"""

from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient


def test_selecting_knowledge_base_while_idle_configures_next_session(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_knowledge_base_detail(kb_id: str) -> dict[str, Any]:
        assert kb_id == "kb-b"
        return {"kb_id": "kb-b", "name": "知识库 B"}

    async def fake_ready(_path: str):
        return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(api_server, "_knowledge_base_detail", fake_knowledge_base_detail)
    monkeypatch.setattr(api_server.rag_gateway, "get", fake_ready)

    response = api_client.put(
        "/session/knowledge-base",
        json={"kb_id": "kb-b"},
    )

    assert response.status_code == 200
    assert response.json()["configured"] == {"kb_id": "kb-b", "name": "知识库 B"}
    assert api_server.metadata_store.get_session_config("knowledge_base") == {
        "kb_id": "kb-b",
        "name": "知识库 B",
    }


def test_stale_active_session_does_not_block_next_session_configuration(
    api_client: TestClient,
    api_server: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_knowledge_base_detail(kb_id: str) -> dict[str, Any]:
        return {"kb_id": kb_id, "name": "知识库 B"}

    async def fake_ready(_path: str):
        return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(api_server, "_knowledge_base_detail", fake_knowledge_base_detail)
    monkeypatch.setattr(api_server.rag_gateway, "get", fake_ready)
    api_server.store.start_session(session_id="stale-session", kb_id="kb-a")

    response = api_client.put("/session/knowledge-base", json={"kb_id": "kb-b"})

    assert response.status_code == 200
    assert response.json()["configured"]["kb_id"] == "kb-b"
    assert api_server.store.read_runtime_state("stale-session")["kb_id"] == "kb-a"
