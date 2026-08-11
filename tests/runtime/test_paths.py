"""测试 runtime/paths.py 路径
·build_runtime_paths 函数能正确派生所有路径。"""

from pathlib import Path

from liverag.runtime.paths import (
    RuntimePaths,
    build_runtime_paths,
    ensure_runtime_dirs,
)


def test_build_runtime_paths_uses_given_root(tmp_path: Path):
    """测试 build_runtime_paths 函数是否正确使用给定的根目录。"""
    root = tmp_path / "live-rag"

    paths = build_runtime_paths(root)

    assert isinstance(paths, RuntimePaths)
    assert paths.user_data_dir == root
    assert paths.db_file == root / "liverag.db"
    assert paths.rag_knowledge_bases_dir == root / "rag" / "knowledge_bases"
    assert paths.logs_dir == root / "logs"


def test_build_runtime_paths_uses_environment_variable(tmp_path, monkeypatch):
    """测试 build_runtime_paths 函数是否正确使用环境变量 LIVERAG_USER_DATA_DIR。"""
    root = tmp_path / "from-env"
    monkeypatch.setenv("LIVERAG_USER_DATA_DIR", str(root))

    paths = build_runtime_paths()

    assert paths.user_data_dir == root
    assert paths.db_file == root / "liverag.db"


def test_build_runtime_paths_does_not_create_directories(tmp_path: Path):
    """测试 build_runtime_paths 函数是否不会创建目录。"""
    root = tmp_path / "not-created"

    build_runtime_paths(root)

    assert not root.exists()


def test_ensure_runtime_dirs_creates_directories(tmp_path: Path):
    """测试 ensure_runtime_dirs 函数是否创建所需的目录。"""
    paths = build_runtime_paths(tmp_path / "live-rag")

    ensure_runtime_dirs(paths)

    assert paths.prompts_dir.is_dir()
    assert paths.history_dir.is_dir()
    assert paths.context_dir.is_dir()
    assert paths.sessions_dir.is_dir()
    assert paths.model_dir.is_dir()
    assert paths.rag_knowledge_bases_dir.is_dir()
    assert paths.logs_dir.is_dir()
