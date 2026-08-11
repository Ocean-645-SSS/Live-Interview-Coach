"""M3-B Context Model 配置管理接口测试。"""

from fastapi.testclient import TestClient


def test_context_model_config_updates_numeric_fields_and_masks_key(
    api_client: TestClient,
) -> None:
    response = api_client.put(
        "/model/context-config",
        json={
            "model": "qwen-max",
            "base_url": "https://context.example/v1",
            "api_key": "context-real-secret",
            "temperature": 0.5,
            "max_tokens": 1234,
            "max_session_chars": 9000,
            "history_reference_limit": 6,
            "timeout_ms": 4321,
        },
    )

    assert response.status_code == 200
    config = response.json()["data"]["context_model"]
    assert config["temperature"] == 0.5
    assert config["max_tokens"] == 1234
    assert config["timeout_ms"] == 4321
    assert config["api_key"] != "context-real-secret"
    assert config["api_key_set"] is True


def test_context_model_masked_key_round_trip_preserves_secret(
    api_client: TestClient,
) -> None:
    created = api_client.put(
        "/model/context-config",
        json={"api_key": "context-real-secret"},
    )
    masked = created.json()["data"]["context_model"]["api_key"]

    updated = api_client.put(
        "/model/context-config",
        json={"api_key": masked, "max_tokens": 2500},
    )

    assert updated.status_code == 200
    config = updated.json()["data"]["context_model"]
    assert config["api_key"] == masked
    assert config["max_tokens"] == 2500


def test_context_model_rejects_invalid_url(api_client: TestClient) -> None:
    response = api_client.put(
        "/model/context-config",
        json={"base_url": "context.example"},
    )

    assert response.status_code == 422
    assert "http(s) URL" in response.json()["detail"]


def test_context_model_rejects_non_positive_timeout(
    api_client: TestClient,
) -> None:
    response = api_client.put(
        "/model/context-config",
        json={"timeout_ms": 0},
    )

    assert response.status_code == 422
