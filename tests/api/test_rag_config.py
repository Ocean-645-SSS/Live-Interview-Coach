"""M3-B 语音链路 RAG 配置管理接口测试。"""

from fastapi.testclient import TestClient


def test_rag_config_partial_update_and_masked_key_round_trip(
    api_client: TestClient,
) -> None:
    created = api_client.put(
        "/rag/config",
        json={
            "enabled": True,
            "base_url": "http://127.0.0.1:9721",
            "api_key": "rag-real-secret",
            "top_k": 8,
            "rag_tool_mode": "auto",
        },
    )

    assert created.status_code == 200
    first = created.json()["data"]["config"]
    assert first["api_key"] != "rag-real-secret"
    assert first["api_key_set"] is True

    updated = api_client.put(
        "/rag/config",
        json={
            "api_key": first["api_key"],
            "top_k": 3,
            "rag_tool_mode": "never",
        },
    )

    assert updated.status_code == 200
    config = updated.json()["data"]["config"]
    assert config["api_key"] == first["api_key"]
    assert config["top_k"] == 3
    assert config["rag_tool_mode"] == "never"
    assert config["base_url"] == "http://127.0.0.1:9721"


def test_rag_config_rejects_unknown_tool_mode(api_client: TestClient) -> None:
    response = api_client.put(
        "/rag/config",
        json={"rag_tool_mode": "always"},
    )

    assert response.status_code == 422
