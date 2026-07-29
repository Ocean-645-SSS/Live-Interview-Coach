"""M3-C RAG Core Overview HTTP 接口测试。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import liverag.rag.server as server


class FakeOverviewEngine:
    def __init__(self) -> None:
        self.calls: list[dict[str, int]] = []

    async def knowledge_overview(self, **limits: int) -> dict[str, Any]:
        self.calls.append(limits)
        return {"summary": {"total_documents": 0}}


class FakeOverviewManager:
    def __init__(self, engine: FakeOverviewEngine) -> None:
        self.engine = engine

    async def get_engine(self, kb_id: str) -> FakeOverviewEngine:
        assert kb_id == "kb-one"
        return self.engine


@pytest.fixture
def overview_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, FakeOverviewEngine]:
    engine = FakeOverviewEngine()
    monkeypatch.setattr(server, "manager", FakeOverviewManager(engine))
    return TestClient(server.app, raise_server_exceptions=False), engine


def _auth_headers() -> dict[str, str]:
    if not server.settings.api_key:
        return {}
    return {"X-API-Key": server.settings.api_key}


def test_overview_endpoint_uses_defaults(
    overview_client: tuple[TestClient, FakeOverviewEngine],
) -> None:
    client, engine = overview_client

    response = client.get(
        "/v1/knowledge-bases/kb-one/overview",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert engine.calls == [
        {
            "entity_limit": 20,
            "relation_limit": 12,
            "document_limit": 10,
            "topic_limit": 8,
        }
    ]


def test_overview_endpoint_forwards_valid_overrides(
    overview_client: tuple[TestClient, FakeOverviewEngine],
) -> None:
    client, engine = overview_client

    response = client.get(
        "/v1/knowledge-bases/kb-one/overview",
        params={
            "entity_limit": 5,
            "relation_limit": 4,
            "document_limit": 3,
            "topic_limit": 2,
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert engine.calls[0]["entity_limit"] == 5
    assert engine.calls[0]["document_limit"] == 3


@pytest.mark.parametrize(
    "params",
    [
        {"entity_limit": -1},
        {"entity_limit": 101},
        {"document_limit": 101},
        {"topic_limit": 101},
    ],
)
def test_overview_endpoint_rejects_out_of_range_limits(
    overview_client: tuple[TestClient, FakeOverviewEngine],
    params: dict[str, int],
) -> None:
    client, engine = overview_client

    response = client.get(
        "/v1/knowledge-bases/kb-one/overview",
        params=params,
        headers=_auth_headers(),
    )

    assert response.status_code == 422
    assert engine.calls == []
