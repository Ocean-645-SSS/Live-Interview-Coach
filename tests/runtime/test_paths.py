from pathlib import Path

from liverag.runtime.paths import build_runtime_paths, ensure_runtime_dirs


def test_build_runtime_paths_uses_given_root(tmp_path: Path) -> None:
    root = tmp_path / "interview-coach"
    paths = build_runtime_paths(root)
    assert paths.user_data_dir == root
    assert paths.db_file == root / "liverag.db"
    assert paths.rag_knowledge_bases_dir == root / "rag" / "knowledge_bases"
    assert paths.logs_dir == root / "logs"
    assert not root.exists()


def test_ensure_runtime_dirs_creates_required_directories(tmp_path: Path) -> None:
    paths = build_runtime_paths(tmp_path / "interview-coach")
    ensure_runtime_dirs(paths)
    assert paths.user_data_dir.is_dir()
    assert paths.rag_knowledge_bases_dir.is_dir()
    assert paths.logs_dir.is_dir()
