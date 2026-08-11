"""Paths shared by Interview Coach persistence and local services."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    user_data_dir: Path
    db_file: Path
    rag_knowledge_bases_dir: Path
    logs_dir: Path


def get_user_data_dir() -> Path:
    return Path(os.getenv("LIVERAG_USER_DATA_DIR", "~/.LiveRAG")).expanduser()


def build_runtime_paths(user_data_dir: Path | None = None) -> RuntimePaths:
    root = (user_data_dir or get_user_data_dir()).expanduser()
    return RuntimePaths(
        user_data_dir=root,
        db_file=root / "liverag.db",
        rag_knowledge_bases_dir=root / "rag" / "knowledge_bases",
        logs_dir=root / "logs",
    )


def ensure_runtime_dirs(paths: RuntimePaths) -> None:
    for directory in (paths.user_data_dir, paths.rag_knowledge_bases_dir, paths.logs_dir):
        directory.mkdir(parents=True, exist_ok=True)
