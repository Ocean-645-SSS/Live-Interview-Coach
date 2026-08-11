"""Interview Coach and RAG Core settings tests."""

import pytest
from pydantic import ValidationError

from liverag.config.settings import (
    Settings,
    VoiceSettings,
    load_voice_settings,
    public_model_options,
)

MODEL_ENV = {
    "LIVERAG_RAG_LLM_MODEL": "qwen-plus",
    "LIVERAG_RAG_LLM_API_KEY": "llm-secret",
    "LIVERAG_RAG_LLM_BASE_URL": "https://llm.example/v1",
    "LIVERAG_RAG_EMBEDDING_MODEL": "text-embedding-v4",
    "LIVERAG_RAG_EMBEDDING_BASE_URL": "https://embedding.example/v1",
    "LIVERAG_RAG_EMBEDDING_API_KEY": "embedding-secret",
}


def test_public_tts_options_are_limited_to_cherry_and_ethan() -> None:
    provider = public_model_options()["tts"]["providers"][0]
    assert [item["id"] for item in provider["voices"]] == ["Cherry", "Ethan"]
    assert provider["default_voice"] == "Cherry"


def test_settings_reads_complete_rag_model_config(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MODEL_ENV.items():
        monkeypatch.setenv(key, value)

    settings = Settings()
    assert settings.rag_llm_model == "qwen-plus"
    assert settings.rag_embedding_model == "text-embedding-v4"


def test_settings_rejects_missing_rag_llm_model(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MODEL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("LIVERAG_RAG_LLM_MODEL")

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("rag_port", [0, 49823])
def test_settings_rejects_invalid_rag_port(
    monkeypatch: pytest.MonkeyPatch,
    rag_port: int,
) -> None:
    for key, value in MODEL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LIVERAG_RAG_PORT", str(rag_port))

    with pytest.raises(ValidationError):
        Settings()


def test_voice_settings_use_interview_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("VOICE_TTS_MODEL", raising=False)
    monkeypatch.delenv("VOICE_TTS_VOICE", raising=False)
    monkeypatch.delenv("VOICE_TTS_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    voice = load_voice_settings(tmp_path)
    assert voice.stt_provider == "volcengine_bigmodel"
    assert voice.tts_provider == "dashscope_realtime"
    assert voice.tts_model == "qwen3-tts-flash-realtime"
    assert voice.tts_voice == "Cherry"


def test_voice_settings_read_interview_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("VOLCENGINE_STT_APP_ID", "stt-app")
    monkeypatch.setenv("VOLCENGINE_STT_ACCESS_TOKEN", "stt-token")
    monkeypatch.setenv("VOICE_LLM_MODEL", "qwen-plus")
    monkeypatch.setenv("VOICE_LLM_API_KEY", "llm-key")
    monkeypatch.setenv("VOICE_TTS_VOICE", "Ethan")
    monkeypatch.setenv("VOICE_TTS_API_KEY", "tts-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "tts-key")

    voice = load_voice_settings(tmp_path)
    assert voice.stt_app_id == "stt-app"
    assert voice.stt_access_token == "stt-token"
    assert voice.llm_model == "qwen-plus"
    assert voice.llm_api_key == "llm-key"
    assert voice.tts_voice == "Ethan"
    assert voice.tts_api_key == "tts-key"


def test_voice_settings_is_frozen() -> None:
    settings = VoiceSettings()
    with pytest.raises((AttributeError, ValidationError)):
        settings.tts_voice = "Ethan"
