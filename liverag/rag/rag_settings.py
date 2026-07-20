"""LightRAG Core Service需要的核心配置类
负责：全局模型配置 + 当前 KB 的 storage 路径 → 单个 RagEngine 配置
包括环境变量、运行时配置、脱敏、Provider 校验"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

#优先读取.env.local
load_dotenv(".env.local",override=True) #可以覆盖当前进程中已经存在的同名环境变量
load_dotenv()

#用户数据根目录
USER_DATA_DIR = Path(os.getenv("LIVERAG_USER_DATA_DIR", "~/.LiveRAG")).expanduser()
#默认LiveRAG存储目录
DEFAULT_RAG_STORAGE_DIR = USER_DATA_DIR / "rag" / "storage"
#默认原文件目录
DEFAULT_UPLOAD_DIR = USER_DATA_DIR / "rag" / "sources"
#默认日志目录
DEFAULT_RAG_LOG_DIR = USER_DATA_DIR / "rag" / "logs"
#默认知识库根目录
DEFAULT_KNOWLEDGE_BASES_DIR = USER_DATA_DIR / "rag" / "knowledge_bases"

# 源码仓库根目录：rag_settings.py -> rag/ -> liverag/ -> 项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _str_env(name:str,default:str="") -> str:
    """读取字符串环境变量"""

    return os.getenv(name, default).strip()

def _int_env(name:str,default:int)->int:
    """读取整数环境变量"""

    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)

def _float_env(
    name: str,
    default: float,
) -> float:
    """读取浮点数环境变量"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)

def _bool_env(name:str,default:bool)->bool:
    """读取布尔值环境变量"""

    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

def _path_env(
    name: str,
    default: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> str:
    """读取路径环境变量，拒绝指向源码仓库内部的路径。"""

    raw = _str_env(name, "")
    if not raw:
        return str(default.expanduser().resolve())

    candidate = Path(raw).expanduser().resolve()
    source_root = project_root.expanduser().resolve()
    #判断候选路径是否位于原码仓库内
    if candidate.is_relative_to(source_root):
        return str(default.expanduser().resolve())

    return str(candidate)


@dataclass(frozen=True)
class RAGSettings:
    """RAGEngine配置类"""

    #===============RAG Core服务配置====================
    host: str = _str_env("KB_SERVICE_HOST", "127.0.0.1")
    port: int = _int_env("KB_SERVICE_PORT", 9721)
    api_key: str = _str_env("KB_SERVICE_API_KEY", _str_env("LIGHTRAG_API_KEY", ""))

    #===============用户数据和知识库路径=================
    user_data_dir: str = _str_env("LIVERAG_USER_DATA_DIR", str(USER_DATA_DIR))
    knowledge_bases_dir: str = _path_env("LIGHTRAG_KNOWLEDGE_BASES_DIR", DEFAULT_KNOWLEDGE_BASES_DIR)
    working_dir: str = _path_env("LIGHTRAG_WORKING_DIR", DEFAULT_RAG_STORAGE_DIR)
    upload_dir: str = _path_env("LIGHTRAG_UPLOAD_DIR", DEFAULT_UPLOAD_DIR)
    rag_log_dir: str = _path_env("LIGHTRAG_LOG_DIR", DEFAULT_RAG_LOG_DIR)
    workspace: str = _str_env("LIGHTRAG_WORKSPACE", _str_env("WORKSPACE", ""))
    kb_id: str = _str_env("LIGHTRAG_KB_ID", "default")
    kb_name: str = _str_env("LIGHTRAG_KB_NAME", "默认知识库")

    #=============LigthRAG存储后端配置===================
    kv_storage: str = _str_env("LIGHTRAG_KV_STORAGE", "JsonKVStorage")
    vector_storage: str = _str_env("LIGHTRAG_VECTOR_STORAGE", "NanoVectorDBStorage")
    graph_storage: str = _str_env("LIGHTRAG_GRAPH_STORAGE", "NetworkXStorage")
    doc_status_storage: str = _str_env("LIGHTRAG_DOC_STATUS_STORAGE", "JsonDocStatusStorage")

    #=====================LLM配置=======================
    llm_model: str = _str_env("LIGHTRAG_LLM_MODEL", _str_env("LLM_MODEL", "qwen-plus"))
    llm_base_url: str = _str_env(
        "LIGHTRAG_LLM_BASE_URL",
        _str_env("LLM_BINDING_HOST", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    )
    llm_api_key: str = _str_env(
        "LIGHTRAG_LLM_API_KEY",
        _str_env("LLM_BINDING_API_KEY", _str_env("DASHSCOPE_API_KEY", _str_env("OPENAI_API_KEY", ""))),
    )

    #====================Embedding模型配置======================
    embedding_model: str = _str_env("LIGHTRAG_EMBEDDING_MODEL", _str_env("EMBEDDING_MODEL", "text-embedding-v4"))
    embedding_base_url: str = _str_env(
        "LIGHTRAG_EMBEDDING_BASE_URL",
        _str_env("EMBEDDING_BINDING_HOST", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    )
    embedding_api_key: str = _str_env(
        "LIGHTRAG_EMBEDDING_API_KEY",
        _str_env("EMBEDDING_BINDING_API_KEY", _str_env("DASHSCOPE_API_KEY", _str_env("OPENAI_API_KEY", ""))),
    )
    embedding_dim: int = _int_env("LIGHTRAG_EMBEDDING_DIM", _int_env("EMBEDDING_DIM", 1024))
    max_embed_tokens: int = _int_env("LIGHTRAG_MAX_EMBED_TOKENS", _int_env("MAX_EMBED_TOKENS", 8192))
    #查询超时限制（毫秒）
    query_timeout_ms: int = _int_env("LIGHTRAG_TIMEOUT_MS",10000)

    
    #====================文档切块配置==============================
    chunk_token_size: int = _int_env("LIGHTRAG_CHUNK_SIZE", _int_env("CHUNK_SIZE", 1200))
    chunk_overlap_token_size: int = _int_env(
        "LIGHTRAG_CHUNK_OVERLAP_SIZE",
        _int_env("CHUNK_OVERLAP_SIZE", 100),
    )

    #====================并发与批处理配置========================
    embedding_batch_num: int = _int_env(
        "LIGHTRAG_EMBEDDING_BATCH_NUM",
        _int_env("EMBEDDING_BATCH_NUM", 10),
    ) #一批提交多少embedding
    embedding_func_max_async: int = _int_env(
        "LIGHTRAG_EMBEDDING_FUNC_MAX_ASYNC",
        _int_env("EMBEDDING_FUNC_MAX_ASYNC", 8),
    ) #最多同时执行多少embedding请求
    llm_model_max_async: int = _int_env("LIGHTRAG_MAX_ASYNC", _int_env("MAX_ASYNC", 4)) #最多同时执行多少LLM请求
    max_parallel_insert: int = _int_env(
        "LIGHTRAG_MAX_PARALLEL_INSERT",
        _int_env("MAX_PARALLEL_INSERT", 2),
    ) #最多并发插入几条
    entity_extract_max_gleaning: int = _int_env(
        "LIGHTRAG_ENTITY_EXTRACT_MAX_GLEANING",
        _int_env("ENTITY_EXTRACT_MAX_GLEANING", 1),
    ) #对同一个文本块进行实体与关系抽取后，最多再追加几轮补充抽取
    enable_llm_cache: bool = _bool_env(
        "LIGHTRAG_ENABLE_LLM_CACHE",
        _bool_env("ENABLE_LLM_CACHE", True),
    ) #是否启用 LightRAG 的通用 LLM 响应缓存：开启后，下次再有相同请求，直接读取缓存结果
    enable_llm_cache_for_entity_extract: bool = _bool_env(
        "LIGHTRAG_ENABLE_LLM_CACHE_FOR_EXTRACT",
        _bool_env("ENABLE_LLM_CACHE_FOR_EXTRACT", True),
    ) #是否专门缓存实体和关系抽取阶段的 LLM 结果

    #=====================普通查询配置=============================
    default_mode: str = _str_env("LIGHTRAG_DEFAULT_MODE", "mix")
    top_k: int = _int_env("LIGHTRAG_TOP_K", _int_env("TOP_K", 60))
    chunk_top_k: int = _int_env("LIGHTRAG_CHUNK_TOP_K", _int_env("CHUNK_TOP_K", 20))
    enable_rerank: bool = _bool_env("LIGHTRAG_ENABLE_RERANK", True)

    #====================Voice配置==============================
    voice_mode: str = _str_env("LIGHTRAG_VOICE_MODE", "naive")
    voice_top_k: int = _int_env("LIGHTRAG_VOICE_TOP_K", 4)
    voice_chunk_top_k: int = _int_env("LIGHTRAG_VOICE_CHUNK_TOP_K", 4)
    voice_context_max_chars: int = _int_env("LIGHTRAG_VOICE_CONTEXT_MAX_CHARS", 1800)
    voice_enable_rerank: bool = _bool_env("LIGHTRAG_VOICE_ENABLE_RERANK", False)


    """文件之间的层级关系
    user_data_dir（整个应用共享）
        └── rag/
            └── knowledge_bases/       ← knowledge_bases_dir：所有知识库共享的物理目录的父目录
                └── {kb_id}/           ← 单个知识库根目录
                    ├── sources/       ← upload_dir：存储原始上传文件
                    ├── storage/       ← working_dir：存储LightRAG索引、向量和图谱数据
                    └── logs/
    """
    @property
    def absolute_working_dir(self) -> str:
        """返回绝对 RAG 存储目录。"""

        return str(Path(self.working_dir).expanduser().resolve())

    @property
    def absolute_user_data_dir(self) -> str:
        """返回绝对用户数据目录。
        存储LiveRAG全部运行数据的根目录"""

        return str(Path(self.user_data_dir).expanduser().resolve())

    @property
    def absolute_upload_dir(self) -> str:
        """返回绝对上传目录。"""

        return str(Path(self.upload_dir).expanduser().resolve())

    @property
    def absolute_knowledge_bases_dir(self) -> str:
        """返回知识库根目录。"""

        return str(Path(self.knowledge_bases_dir).expanduser().resolve())

    def provider_ready(self) -> bool:
        """判断 LLM 和 Embedding provider 是否具备api_key。"""

        return bool(self.llm_api_key and self.embedding_api_key)

    @property
    def query_timeout_seconds(self) -> float:
        """查询超时限制（秒）"""
        return self.query_timeout_ms / 1000