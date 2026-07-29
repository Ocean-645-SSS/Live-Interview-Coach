"""M3-B 语音模型配置管理接口测试。"""

from types import ModuleType

from fastapi.testclient import TestClient


def test_model_config_partial_update_preserves_and_masks_secrets(
    api_client: TestClient,
) -> None:
    first = api_client.put(
        "/model/config",
        json={
            "voice": {
                "stt": {
                    "provider": "volcengine_bigmodel",
                    "model": "bigmodel",
                    "app_id": "app-real-secret",
                    "access_token": "token-real-secret",
                },
                "llm": {
                    "model": "qwen-flash",
                    "base_url": "https://llm.example/v1",
                    "api_key": "llm-real-secret",
                },
                "tts": {
                    "provider": "dashscope_realtime",
                    "model": "qwen3-tts-flash-realtime",
                    "voice": "Cherry",
                    "api_key": "tts-real-secret",
                },
            }
        },
    )

    assert first.status_code == 200
    first_voice = first.json()["data"]["voice"]
    assert first_voice["llm"]["api_key"] != "llm-real-secret"
    assert first_voice["tts"]["api_key"] != "tts-real-secret"
    assert first_voice["stt"]["access_token"] != "token-real-secret"

    second = api_client.put(
        "/model/config",
        json={
            "voice": {
                "tts": {
                    "voice": "Serena",
                    "api_key": first_voice["tts"]["api_key"],
                }
            }
        },
    )

    assert second.status_code == 200
    voice = second.json()["data"]["voice"]
    assert voice["tts"]["voice"] == "Serena"
    assert voice["tts"]["api_key"] == first_voice["tts"]["api_key"]
    assert voice["llm"]["model"] == "qwen-flash"


def test_model_config_rejects_unsupported_tts_provider(
    api_client: TestClient,
) -> None:
    response = api_client.put(
        "/model/config",
        json={"voice": {"tts": {"provider": "minimax"}}},
    )

    assert response.status_code == 422
    assert "dashscope_realtime" in response.json()["detail"]


def test_model_config_rejects_invalid_llm_url(api_client: TestClient) -> None:
    response = api_client.put(
        "/model/config",
        json={"voice": {"llm": {"base_url": "not-a-url"}}},
    )

    assert response.status_code == 422
    assert "http(s) URL" in response.json()["detail"]


def test_model_effective_state_reports_pending_reconnect(
    api_client: TestClient,
    api_server: ModuleType,
) -> None:
    active_voice = api_client.get("/model/config").json()["data"]["voice"]
    api_server.store.start_session("session-model-state", "default")
    api_server.store.write_runtime_state(
        "session-model-state",
        {
            "state": "active",
            "active_session": {"voice": active_voice},
        },
    )

    changed = api_client.put(
        "/model/config",
        json={"voice": {"tts": {"voice": "Serena"}}},
    )
    assert changed.status_code == 200

    response = api_client.get(
        "/model/effective-state/session-model-state"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["active_session"]["voice"]["tts"]["voice"] == "Cherry"
    assert data["configured"]["voice"]["tts"]["voice"] == "Serena"
    assert data["pending_reconnect"] is True
