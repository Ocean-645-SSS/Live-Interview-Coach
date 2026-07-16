"""统一管理LiveRAG用户数据目录的路径。"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)  # 不可修改的数据对象
class RunTimePaths:
    """集中描述运行时需要读写的所有用户文件路径。"""

    user_data_dir: Path
    db_file: Path
    logs_dir: Path
    prompts_dir: Path
    system_prompt_template_file: Path
    soul_file: Path
    history_compress_prompt_file: Path
    knowledge_overview_prompt_file: Path
    history_dir: Path
    context_dir: Path
    session_dir: Path
    session_system_prompt_file: Path
    messages_file: Path
    rag_context_file: Path
    runtime_state_file: Path
    model_dir: Path
    model_config_file: Path
    context_model_config_file: Path
    rag_dir: Path
    rag_knowledge_bases_dir: Path


def get_user_data_dir() -> Path:
    """返回用户数据根目录，默认是 ~/.LiveRAG。"""
    # .expanduser() 方法将路径中的 ~ 符号展开为当前用户的主目录路径。
    return Path(os.getenv("LIVERAG_USER_DATA_DIR", "~/.LiveRAG")).expanduser()


def build_runtime_paths(user_data_dir: Path | None = None) -> "RunTimePaths":
    """根据用户数据根目录派生所有运行文件路径。"""
    root = (user_data_dir or get_user_data_dir()).expanduser()
    prompts_dir = root / "prompts"
    history_dir = root / "history"
    context_dir = root / "context"
    session_dir = root / "session"
    model_dir = root / "model"
    rag_dir = root / "rag"
    rag_knowledge_bases_dir = rag_dir / "knowledge_bases"
    logs_dir = root / "logs"

    return RunTimePaths(
        user_data_dir=root,
        db_file=root / "liverag.db",
        logs_dir=logs_dir,
        prompts_dir=prompts_dir,
        system_prompt_template_file=prompts_dir / "system_prompt_template.md",
        soul_file=prompts_dir / "SOUL.md",
        history_compress_prompt_file=prompts_dir / "history_compress_prompt.md",
        knowledge_overview_prompt_file=prompts_dir / "knowledge_overview_prompt.md",
        history_dir=history_dir,
        context_dir=context_dir,
        session_dir=session_dir,
        session_system_prompt_file=session_dir / "session_system_prompt.md",
        messages_file=session_dir / "messages.json",
        rag_context_file=session_dir / "rag_context.json",
        runtime_state_file=session_dir / "runtime_state.json",
        model_dir=model_dir,
        model_config_file=model_dir / "model_config.json",
        context_model_config_file=model_dir / "context_model_config.json",
        rag_knowledge_bases_dir=rag_knowledge_bases_dir,
        rag_dir=rag_dir,
    )


def ensure_runtime_paths_exist(paths: "RunTimePaths") -> None:
    """单独负责创建所需要的目录。"""

    for directory in (
        paths.prompts_dir,
        paths.history_dir,
        paths.context_dir,
        paths.session_dir,
        paths.model_dir,
        paths.rag_knowledge_bases_dir,
        paths.logs_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
