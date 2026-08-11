"""测试./config/settings.py配置
·所有必填模型变量存在时，能正确读取。
·缺少 LLM/EMBEDDING配置 时，抛出 ValidationError。
·rag_port 非法时，指出端口范围不合法
·frozen冻结配置，尝试修改属性时，抛出 ValidationError。"""

import pytest
from pydantic import ValidationError

from liverag.config.settings import (
    Settings,
    VoiceSettings,
    load_context_model_settings,
    load_rag_client_settings,
    load_voice_settings,
    merge_runtime_rag_config,
    public_context_model_config,
    public_model_options,
    public_rag_client_config,
    read_runtime_context_model_config,
    write_runtime_context_model_config,
)


def test_public_tts_options_are_limited_to_cherry_and_ethan():
    provider = public_model_options()["tts"]["providers"][0]

    assert [item["id"] for item in provider["voices"]] == ["Cherry", "Ethan"]
    assert provider["default_voice"] == "Cherry"

MODEL_ENV = {
    "LIVERAG_RAG_LLM_MODEL": "qwen-plus",
    "LIVERAG_RAG_LLM_API_KEY": "llm-secret",
    "LIVERAG_RAG_LLM_BASE_URL": "https://llm.example/v1",
    "LIVERAG_RAG_EMBEDDING_MODEL": "text-embedding-v4",
    "LIVERAG_RAG_EMBEDDING_BASE_URL": "https://embedding.example/v1",
    "LIVERAG_RAG_EMBEDDING_API_KEY": "embedding-secret",
}


def set_model_env(monkeypatch):
    for name, value in MODEL_ENV.items():
        monkeypatch.setenv(name, value)


def test_settings_reads_complete_model_config(monkeypatch, tmp_path):
    """测试 Settings类在所有必填模型变量存在时，能正确读取配置。"""
    set_model_env(monkeypatch)
    user_data_dir = tmp_path / "user-data"
    monkeypatch.setenv("LIVERAG_USER_DATA_DIR", str(user_data_dir))
    monkeypatch.setenv("LIVERAG_RAG_PORT", "19819")

    settings = Settings(_env_file=None)

    assert settings.user_data_dir == user_data_dir
    assert settings.rag_port == 19819
    assert settings.rag_llm_model == "qwen-plus"
    assert settings.rag_llm_api_key == "llm-secret"
    assert settings.rag_llm_base_url == "https://llm.example/v1"
    assert settings.rag_embedding_model == "text-embedding-v4"
    assert settings.rag_embedding_base_url == "https://embedding.example/v1"
    assert settings.rag_embedding_api_key == "embedding-secret"


def test_settings_rejects_missing_llm_model(monkeypatch):
    """测试 Settings类在缺少 LIVERAG_RAG_LLM_MODEL 环境变量时，是否抛出 ValidationError。"""
    set_model_env(monkeypatch)
    monkeypatch.delenv("LIVERAG_RAG_LLM_MODEL")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "rag_llm_model" in str(exc_info.value)
    assert "Field required" in str(exc_info.value)


@pytest.mark.parametrize(
    ("env_name", "field_name"),
    [
        ("LIVERAG_RAG_EMBEDDING_MODEL", "rag_embedding_model"),
        ("LIVERAG_RAG_EMBEDDING_BASE_URL", "rag_embedding_base_url"),
        ("LIVERAG_RAG_EMBEDDING_API_KEY", "rag_embedding_api_key"),
    ],
)
def test_settings_rejects_missing_embedding_config(monkeypatch, env_name, field_name):
    """测试 Settings类在缺少 Embedding 配置时，是否抛出 ValidationError，并明确指出缺失字段。"""
    set_model_env(monkeypatch)
    monkeypatch.delenv(env_name)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert field_name in str(exc_info.value)
    assert "Field required" in str(exc_info.value)


@pytest.mark.parametrize("rag_port", [0, 49823])
def test_settings_rejects_invalid_rag_port(monkeypatch, rag_port):
    """测试 Settings类在 rag_port 非法时，是否抛出 ValidationError，并指出端口范围不合法。"""
    set_model_env(monkeypatch)
    monkeypatch.setenv("LIVERAG_RAG_PORT", str(rag_port))

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "rag_port" in str(exc_info.value)


def test_settings_is_frozen(monkeypatch):
    """测试 Settings类是否为 frozen，尝试修改属性时，是否抛出 ValidationError。"""
    set_model_env(monkeypatch)
    settings = Settings(_env_file=None)

    with pytest.raises(ValidationError, match="frozen"):
        settings.rag_port = 1234


def test_voice_settings_use_dashscope_defaults(monkeypatch, tmp_path):
    """未配置 TTS 字段时固定使用 DashScope 实时 TTS 默认值。"""

    for name in (
        "VOICE_TTS_PROVIDER",
        "VOICE_TTS_MODEL",
        "VOICE_TTS_VOICE",
        "VOICE_TTS_API_KEY",
        "VOICE_TTS_BASE_URL",
        "DASHSCOPE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_voice_settings(tmp_path)

    assert settings.tts_provider == "dashscope_realtime"
    assert settings.tts_model == "qwen3-tts-flash-realtime"
    assert settings.tts_voice == "Cherry"
    assert settings.tts_api_key == ""
    assert settings.tts_base_url == "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


def test_voice_settings_read_dashscope_environment(monkeypatch, tmp_path):
    """DashScope 模型、音色、密钥和地址可由环境变量覆盖。"""

    monkeypatch.setenv("VOICE_TTS_MODEL", "custom-tts-model")
    monkeypatch.setenv("VOICE_TTS_VOICE", "Serena")
    monkeypatch.setenv("VOICE_TTS_API_KEY", "tts-secret")
    monkeypatch.setenv("VOICE_TTS_BASE_URL", "wss://tts.example/ws/")

    settings = load_voice_settings(tmp_path)

    assert settings.tts_provider == "dashscope_realtime"
    assert settings.tts_model == "custom-tts-model"
    assert settings.tts_voice == "Serena"
    assert settings.tts_api_key == "tts-secret"
    assert settings.tts_base_url == "wss://tts.example/ws"


def test_voice_settings_fall_back_to_dashscope_api_key(monkeypatch, tmp_path):
    """未提供专用 TTS 密钥时复用 DASHSCOPE_API_KEY。"""

    monkeypatch.delenv("VOICE_TTS_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "shared-secret")

    settings = load_voice_settings(tmp_path)

    assert settings.tts_api_key == "shared-secret"


def test_voice_settings_reject_runtime_minimax_provider(tmp_path):
    """运行时配置不能把仅支持 DashScope 的链路切换到 MiniMax。"""

    config_file = tmp_path / "model" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        '{"voice":{"tts":{"provider":"minimax"}}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="只支持 DashScope TTS"):
        load_voice_settings(tmp_path)


def test_voice_settings_is_frozen():
    """单次会话的语音配置创建后不可热修改。"""

    settings = VoiceSettings()

    with pytest.raises((AttributeError, ValidationError)):
        settings.tts_voice = "Serena"


def test_context_model_runtime_config_overrides_environment_and_masks_secret(
    monkeypatch,
    tmp_path,
):
    """Context Model JSON 覆盖环境默认值，对外响应不泄露密钥。"""

    monkeypatch.setenv("CONTEXT_MODEL_MODEL", "env-model")
    monkeypatch.setenv("CONTEXT_MODEL_API_KEY", "env-secret")
    write_runtime_context_model_config(
        {
            "model": "runtime-model",
            "api_key": "runtime-secret",
            "temperature": 0.25,
            "max_tokens": 1234,
        },
        tmp_path,
    )

    loaded = load_context_model_settings(tmp_path)
    public = public_context_model_config(loaded)

    assert loaded.model == "runtime-model"
    assert loaded.api_key == "runtime-secret"
    assert loaded.temperature == 0.25
    assert loaded.max_tokens == 1234
    assert public["api_key"] != "runtime-secret"
    assert public["api_key_set"] is True


def test_context_model_writer_filters_unknown_fields(tmp_path):
    """Context Model JSON 只保存后端支持的配置字段。"""

    write_runtime_context_model_config(
        {
            "model": "qwen-max",
            "unknown": "discard-me",
        },
        tmp_path,
    )

    assert read_runtime_context_model_config(tmp_path) == {
        "model": "qwen-max"
    }


def test_rag_runtime_merge_preserves_masked_api_key(monkeypatch, tmp_path):
    """RAG 局部更新时，前端掩码不会覆盖已有真实密钥。"""

    monkeypatch.setenv("LIGHTRAG_API_KEY", "")
    merge_runtime_rag_config(
        {
            "api_key": "rag-real-secret",
            "top_k": 8,
            "rag_tool_mode": "auto",
        },
        tmp_path,
    )
    first = public_rag_client_config(
        load_rag_client_settings(tmp_path)
    )

    merge_runtime_rag_config(
        {
            "api_key": first["api_key"],
            "top_k": 2,
            "rag_tool_mode": "never",
        },
        tmp_path,
    )
    loaded = load_rag_client_settings(tmp_path)

    assert loaded.api_key == "rag-real-secret"
    assert loaded.top_k == 2
    assert loaded.rag_tool_mode == "never"


def test_rag_runtime_rejects_unknown_tool_mode(tmp_path):
    """运行时 RAG 工具模式只允许 auto 或 never。"""

    with pytest.raises(
        ValueError,
        match="rag_tool_mode must be one of",
    ):
        merge_runtime_rag_config(
            {"rag_tool_mode": "always"},
            tmp_path,
        )


def test_rag_client_timeout_cannot_expire_before_rag_core(tmp_path):
    """A stale short runtime override must not abort a healthy core query."""

    merge_runtime_rag_config({"timeout_ms": 5000}, tmp_path)

    loaded = load_rag_client_settings(tmp_path)

    assert loaded.timeout_ms == 15000
