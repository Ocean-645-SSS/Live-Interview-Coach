"""管理 API 健康检查测试。"""

from fastapi.testclient import TestClient


def test_api_exposes_only_interview_product_routes(api_client: TestClient) -> None:
    paths = {route.path for route in api_client.app.routes}

    assert "/health" in paths
    assert "/api/interviews" in paths
    assert "/rag/knowledge-bases" in paths
    assert "/rag/ready" in paths
    assert "/prompt/soul" not in paths
    assert "/sessions" not in paths
    assert "/model/config" not in paths
    assert "/rag/session-query/context" not in paths


def test_health_returns_ok(api_client: TestClient) -> None:
    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
