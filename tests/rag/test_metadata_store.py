"""测试/rag/metadata_store.py模块的功能。"""
import sqlite3
from pathlib import Path
import pytest
from liverag.rag.metadata_store import MetadataStore


EXPECTED_TABLES = {
    "knowledge_bases",
    "documents",
    "ingest_jobs",
    "ingest_job_documents",
}


def read_user_tables(db_path: Path) -> set[str]:
    """读取 SQLite 数据库中用户创建的表名，排除系统表。"""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()

    return {row[0] for row in rows}


def test_initialize_creates_metadata_tables(tmp_path: Path):
    """测试 MetadataStore.initialize() 方法是否正确创建了所有预期的元数据表。"""
    db_path = tmp_path / "data" / "liverag.db"
    knowledge_bases_dir = tmp_path / "rag" / "knowledge_bases"
    store = MetadataStore(db_path, knowledge_bases_dir)

    store.initialize()

    assert db_path.is_file()
    assert knowledge_bases_dir.is_dir()
    assert read_user_tables(db_path) == EXPECTED_TABLES


def test_initialize_is_idempotent(tmp_path: Path):
    """测试 MetadataStore.initialize() 方法是否具有幂等性，即多次调用不会产生副作用。"""
    store = MetadataStore(
        tmp_path / "liverag.db",
        tmp_path / "knowledge_bases",
    )

    store.initialize()
    store.initialize()

    assert read_user_tables(store.db_path) == EXPECTED_TABLES


def test_ingest_job_documents_has_composite_primary_key(tmp_path: Path):
    """测试 ingest_job_documents 表是否具有复合主键 (job_id, document_id)。"""
    db_path = tmp_path / "liverag.db"
    store = MetadataStore(db_path, tmp_path / "knowledge_bases")
    store.initialize()

    with sqlite3.connect(db_path) as conn:
        columns = conn.execute(
            "PRAGMA table_info(ingest_job_documents)"
        ).fetchall()

    primary_key = {
        row[1]: row[5]
        for row in columns
        if row[5] > 0
    }

    assert primary_key == {
        "job_id": 1,
        "document_id": 2,
    }


def test_initialize_creates_default_knowledge_base(tmp_path: Path):
    """测试 MetadataStore.initialize() 方法是否创建了默认知识库。"""
    db_path = tmp_path / "liverag.db"
    store = MetadataStore(db_path, tmp_path / "knowledge_bases")

    store.initialize()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT kb_id, name, description
            FROM knowledge_bases
            WHERE kb_id = ?
            """,
            ("default",),
        ).fetchone()

    assert row == ("default", "默认知识库", "")


def test_default_knowledge_base_cannot_be_deleted(tmp_path: Path):
    """测试默认知识库是否无法被删除。"""
    db_path = tmp_path / "liverag.db"
    store = MetadataStore(db_path, tmp_path / "knowledge_bases")
    store.initialize()

    with pytest.raises(ValueError, match="默认知识库不可删除"):
        store.delete_knowledge_base_metadata("default")

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT kb_id FROM knowledge_bases WHERE kb_id = ?",
            ("default",),
        ).fetchone()

    assert row == ("default",)


def test_reinitialize_does_not_overwrite_default_knowledge_base(tmp_path: Path):
    """测试重新初始化数据库不会覆盖默认知识库的名称和描述。"""
    db_path = tmp_path / "liverag.db"
    store = MetadataStore(db_path, tmp_path / "knowledge_bases")
    store.initialize()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE knowledge_bases
            SET name = ?
            WHERE kb_id = ?
            """,
            ("我的默认知识库", "default"),
        )

    store.initialize()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT name FROM knowledge_bases WHERE kb_id = ?",
            ("default",),
        ).fetchone()

    assert row == ("我的默认知识库",)


@pytest.mark.parametrize(
    "kb_id",
    [
        "default",
        "kb_alpha",
        "kb-beta",
        "KB123",
        "abc_123-test",
        "0",
    ],
)
def test_validate_kb_id_accepts_safe_ids(kb_id: str):
    """测试kb_id验证函数是否接受安全的知识库ID。"""
    MetadataStore.validate_kb_id(kb_id)


@pytest.mark.parametrize(
    "kb_id",
    [
        "",
        "../secret",
        "..",
        ".",
        "kb/secret", #创建子目录
        r"kb\secret",
        "/absolute",
        r"C:\Windows",
        "kb name",
        "知识库",
        "kb.alpha",
        "kb@alpha",
    ],
)
def test_validate_kb_id_rejects_unsafe_ids(kb_id: str):
    """测试kb_id验证函数是否拒绝不安全的知识库ID。"""
    with pytest.raises(ValueError, match="kb_id"):
        MetadataStore.validate_kb_id(kb_id)


def test_unsafe_kb_id_cannot_escape_knowledge_bases_dir(tmp_path: Path):
    """测试不安全的kb_id是否无法逃逸知识库目录。"""
    knowledge_bases_dir = tmp_path / "knowledge_bases"
    store = MetadataStore(
        tmp_path / "liverag.db",
        knowledge_bases_dir,
    )
    store.initialize()

    with pytest.raises(ValueError, match="kb_id"):
        store.knowledge_base_dir("../escaped")

    assert not (tmp_path / "escaped").exists()


def test_two_knowledge_bases_have_isolated_directories(tmp_path: Path):
    """测试两个不同的知识库是否具有隔离的物理目录。"""
    knowledge_bases_dir = tmp_path / "knowledge_bases"
    store = MetadataStore(
        tmp_path / "liverag.db",
        knowledge_bases_dir,
    )
    store.initialize()

    alpha_dir = store.create_knowledge_base(
        kb_id="kb_alpha",
        name="Alpha 知识库",
        description="Alpha 测试数据",
    )
    beta_dir = store.create_knowledge_base(
        kb_id="kb_beta",
        name="Beta 知识库",
        description="Beta 测试数据",
    )

    assert alpha_dir == knowledge_bases_dir / "kb_alpha"
    assert beta_dir == knowledge_bases_dir / "kb_beta"
    assert alpha_dir != beta_dir

    for kb_dir in (alpha_dir, beta_dir):
        assert kb_dir.is_dir()
        assert (kb_dir / "sources").is_dir()
        assert (kb_dir / "storage").is_dir()
        assert (kb_dir / "logs").is_dir()


def test_knowledge_base_files_are_physically_isolated(tmp_path: Path):
    """测试不同知识库的文件是否在物理上隔离，确保一个知识库的文件不会出现在另一个知识库中。"""
    knowledge_bases_dir = tmp_path / "knowledge_bases"
    
    store = MetadataStore(
        tmp_path / "liverag.db",
        knowledge_bases_dir
    )
    store.initialize()

    alpha_dir = store.create_knowledge_base(
        kb_id="kb_alpha",
        name="Alpha",
        description="Alpha 测试数据",
    )
    beta_dir = store.create_knowledge_base(
        kb_id="kb_beta",
        name="Beta",
        description="Beta 测试数据",
    )

    # 验证 SQLite 中分别存在两个知识库
    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            """
            SELECT kb_id
            FROM knowledge_bases
            WHERE kb_id IN (?, ?)
            ORDER BY kb_id
            """,
            ("kb_alpha", "kb_beta"),
        ).fetchall()

    assert rows == [("kb_alpha",), ("kb_beta",)]

    # 验证知识库根目录互不相同
    assert alpha_dir == knowledge_bases_dir / "kb_alpha"
    assert beta_dir == knowledge_bases_dir / "kb_beta"
    assert alpha_dir != beta_dir

    # 验证每个知识库都有自己的三个子目录
    for kb_dir in (alpha_dir, beta_dir):
        assert kb_dir.is_dir()
        assert (kb_dir / "sources").is_dir()
        assert (kb_dir / "storage").is_dir()
        assert (kb_dir / "logs").is_dir()