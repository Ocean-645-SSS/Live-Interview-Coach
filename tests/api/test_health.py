"""管理 API 健康检查测试。"""

from fastapi.testclient import TestClient


def test_health_returns_ok(api_client: TestClient) -> None:
    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
