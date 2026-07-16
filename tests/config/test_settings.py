"""测试./config/settings.py配置
·所有必填模型变量存在时，能正确读取。
·缺少 LLM/EMBEDDING配置 时，抛出 ValidationError。
·rag_port 非法时，指出端口范围不合法
·frozen冻结配置，尝试修改属性时，抛出 ValidationError。"""

import pytest
from pydantic import ValidationError

from liverag.config.settings import Settings

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
