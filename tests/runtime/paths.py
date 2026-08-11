import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    user_data_dir: Path
    db_file: Path
    rag_knowledge_bases_dir: Path
    logs_dir: Path


def build_runtime_paths(
    user_data_dir: Path | None = None,
) -> RuntimePaths:
    """构建并初始化 LiveRAG 的基础运行时目录。"""
    root = user_data_dir

    if root is None:
        configured_root = os.getenv("LIVERAG_USER_DATA_DIR")
        root = Path(configured_root) if configured_root else Path.home() / ".LiveRAG"

    root = root.expanduser()

    paths = RuntimePaths(
        user_data_dir=root,
        db_file=root / "liverag.db",
        rag_knowledge_bases_dir=root / "rag" / "knowledge_bases",
        logs_dir=root / "logs",
    )

    paths.user_data_dir.mkdir(parents=True, exist_ok=True)
    paths.rag_knowledge_bases_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)

    return paths
