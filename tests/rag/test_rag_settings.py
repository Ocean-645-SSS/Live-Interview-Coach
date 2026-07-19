"""测试 RAG 路径配置的严格源码边界。"""

from pathlib import Path

from liverag.rag.rag_settings import _path_env


def test_path_env_uses_resolved_default_when_variable_is_missing(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.delenv("TEST_RAG_PATH", raising=False)
    default = tmp_path / "default-storage"

    result = _path_env("TEST_RAG_PATH", default)

    assert Path(result) == default.resolve()


def test_path_env_rejects_path_inside_project_root(monkeypatch, tmp_path: Path):
    project_root = tmp_path / "source-repository"
    candidate = project_root / "runtime" / "storage"
    default = tmp_path / "safe-data" / "storage"
    monkeypatch.setenv("TEST_RAG_PATH", str(candidate))

    result = _path_env(
        "TEST_RAG_PATH",
        default,
        project_root=project_root,
    )

    assert Path(result) == default.resolve()


def test_path_env_rejects_relative_path_that_resolves_inside_project(
    monkeypatch,
    tmp_path: Path,
):
    project_root = tmp_path / "source-repository"
    project_root.mkdir()
    default = tmp_path / "safe-data" / "storage"
    monkeypatch.chdir(project_root)
    monkeypatch.setenv("TEST_RAG_PATH", "runtime/storage")

    result = _path_env(
        "TEST_RAG_PATH",
        default,
        project_root=project_root,
    )

    assert Path(result) == default.resolve()


def test_path_env_accepts_external_absolute_path_even_if_named_liverag(
    monkeypatch,
    tmp_path: Path,
):
    project_root = tmp_path / "source-repository"
    candidate = tmp_path / "external" / "LiveRAG" / "storage"
    default = tmp_path / "safe-data" / "storage"
    monkeypatch.setenv("TEST_RAG_PATH", str(candidate))

    result = _path_env(
        "TEST_RAG_PATH",
        default,
        project_root=project_root,
    )

    assert Path(result) == candidate.resolve()
