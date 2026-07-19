"""测试 M1 KnowledgeBaseStore 的领域委托和物理隔离。"""

from pathlib import Path

from liverag.rag.knowledge_base import KnowledgeBaseStore
from liverag.rag.metadata_store import DEFAULT_KB_ID, MetadataStore


def build_store(tmp_path: Path) -> KnowledgeBaseStore:
    metadata = MetadataStore(
        tmp_path / "liverag.db",
        tmp_path / "knowledge_bases",
    )
    return KnowledgeBaseStore(metadata)


def test_initialize_creates_default_knowledge_base_directories(tmp_path: Path):
    store = build_store(tmp_path)

    store.initialize()
    default = store.get(DEFAULT_KB_ID)

    assert default.root_dir == store.root_dir / DEFAULT_KB_ID
    assert default.sources_dir.is_dir()
    assert default.storage_dir.is_dir()
    assert default.logs_dir.is_dir()


def test_create_and_get_preserve_isolated_directories(tmp_path: Path):
    store = build_store(tmp_path)
    store.initialize()

    alpha = store.create(name="Alpha", kb_id="kb_alpha")
    beta = store.create(name="Beta", kb_id="kb_beta")

    assert store.get("kb_alpha") == alpha
    assert store.get("kb_beta") == beta
    assert alpha.root_dir != beta.root_dir
    assert alpha.storage_dir != beta.storage_dir
    assert alpha.sources_dir != beta.sources_dir
    assert alpha.logs_dir != beta.logs_dir


def test_list_and_public_detail_return_public_fields(tmp_path: Path):
    store = build_store(tmp_path)
    store.initialize()
    store.create(name="Alpha", description="测试知识库", kb_id="kb_alpha")

    items = store.list()
    detail = store.public_detail("kb_alpha")

    assert [item["kb_id"] for item in items] == ["default", "kb_alpha"]
    assert detail["kb_id"] == "kb_alpha"
    assert detail["name"] == "Alpha"
    assert detail["description"] == "测试知识库"
    assert "root_dir" not in detail


def test_source_document_dir_stays_inside_selected_knowledge_base(tmp_path: Path):
    store = build_store(tmp_path)
    store.initialize()
    alpha = store.create(name="Alpha", kb_id="kb_alpha")
    beta = store.create(name="Beta", kb_id="kb_beta")

    alpha_document_dir = store.source_document_dir("kb_alpha", "doc_001")

    assert alpha_document_dir == alpha.sources_dir / "doc_001"
    assert alpha_document_dir.parent == alpha.sources_dir
    assert alpha_document_dir.parent != beta.sources_dir
