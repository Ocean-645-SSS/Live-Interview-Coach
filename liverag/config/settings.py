"""统一读取 LiveRAG Agent 配置文件。"""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Literal, cast

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RagToolMode = Literal["auto", "never"]

def _str_env(name: str, default: str = "") -> str:
    """读取字符串环境变量。"""

    return os.getenv(name, default).strip()


def _int_env(name: str, default: int) -> int:
    """读取整数环境变量。"""

    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _float_env(name: str, default: float) -> float:
    """读取浮点数环境变量。"""

    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _bool_env(name: str, default: bool) -> bool:
    """读取布尔环境变量。"""

    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _rag_tool_mode_env() -> RagToolMode:
    """读取 RAG 工具调用模式。"""

    value = _str_env("LIGHTRAG_TOOL_MODE", "auto")
    return cast(RagToolMode, value if value in {"auto", "never"} else "auto")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        frozen=True,
        extra="ignore",
        env_prefix="LIVERAG_",
    )

    user_data_dir: Path = Path("~/.LiveRAG")
    rag_port: int = Field(default=9819, ge=1, le=49822)

    rag_llm_model: str = Field(min_length=1, description="RAG LLM模型名称")
    rag_llm_api_key: str = Field(min_length=1, description="RAG LLM模型API Key")
    rag_llm_base_url: str = Field(min_length=1, description="RAG LLM模型基础URL")

    rag_embedding_model: str = Field(min_length=1)
    rag_embedding_base_url: str = Field(min_length=1)
    rag_embedding_api_key: str = Field(min_length=1)


@dataclass(frozen=True)
class RagClientSettings:
    """语音链路访问 RAG 服务的配置。"""

    enabled: bool = _bool_env("LIGHTRAG_ENABLED", True)
    base_url: str = _str_env("LIGHTRAG_BASE_URL", "http://127.0.0.1:9721").rstrip("/")
    api_key: str = _str_env("LIGHTRAG_API_KEY", _str_env("KB_SERVICE_API_KEY", ""))
    query_mode: str = _str_env("LIGHTRAG_QUERY_MODE", _str_env("LIGHTRAG_VOICE_MODE", "naive"))
    timeout_ms: int = _int_env("LIGHTRAG_TIMEOUT_MS", 900)
    top_k: int = _int_env("LIGHTRAG_TOP_K", _int_env("LIGHTRAG_VOICE_TOP_K", 4))
    chunk_top_k: int = _int_env("LIGHTRAG_CHUNK_TOP_K", _int_env("LIGHTRAG_VOICE_CHUNK_TOP_K", 4))
    context_max_chars: int = _int_env(
        "LIGHTRAG_CONTEXT_MAX_CHARS",
        _int_env("LIGHTRAG_VOICE_CONTEXT_MAX_CHARS", 1800),
    )
    cache_ttl_s: float = _float_env("LIGHTRAG_CACHE_TTL_S", 45.0)
    enable_rerank: bool = _bool_env("LIGHTRAG_VOICE_ENABLE_RERANK", False)
    rag_tool_mode: RagToolMode = field(default_factory=_rag_tool_mode_env)

    def __post_init__(self) -> None:
        """校验 RAG 工具调用模式。"""

        if self.rag_tool_mode not in {"auto", "never"}:
            raise ValueError("rag_tool_mode must be one of: auto, never")

