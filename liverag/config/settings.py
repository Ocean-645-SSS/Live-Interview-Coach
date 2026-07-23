"""统一读取 LiveRAG Agent 配置文件。"""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RagToolMode = Literal["auto", "never"]

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
