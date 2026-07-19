"""测试 RagEngineManager 的单知识库缓存与 workspace 隔离。"""

from pathlib import Path

import pytest

import liverag.rag.engine_manager as engine_manager_module
from liverag.rag.engine_manager import RagEngineManager
from liverag.rag.rag_settings import RAGSettings


class FakeRagEngine:
    """避免测试调用真实 LightRAG、LLM 和 Embedding。"""

    def __init__(self, settings: RAGSettings) -> None:
        self.settings = settings
        self.initialize_calls = 0
        self.finalize_calls = 0

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def finalize(self) -> None:
        self.finalize_calls += 1


@pytest.fixture
def manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> RagEngineManager:
    """创建使用临时知识库目录和 FakeRagEngine 的 Manager。"""

    monkeypatch.setattr(engine_manager_module, "RagEngine", FakeRagEngine)
    settings = RAGSettings(
        user_data_dir=str(tmp_path),
        knowledge_bases_dir=str(tmp_path / "knowledge_bases"),
    )
    instance = RagEngineManager(settings)
    instance.kb_store.initialize()
    return instance


@pytest.mark.asyncio
async def test_get_engine_uses_kb_id_as_cache_key_and_reuses_instance(
    manager: RagEngineManager,
) -> None:
    """同一个 kb_id 重用已经初始化的同一个 Engine。"""

    manager.kb_store.create(name="Alpha", kb_id="kb_alpha")

    first = await manager.get_engine("kb_alpha")
    second = await manager.get_engine("kb_alpha")

    assert first is second
    assert first.initialize_calls == 1
    assert manager._engines == {"kb_alpha": first}


@pytest.mark.asyncio
async def test_two_kbs_use_different_engines_and_storage_directories(
    manager: RagEngineManager,
) -> None:
    """不同 kb_id 使用不同 Engine，并绑定各自的 storage/workspace。"""

    alpha_meta = manager.kb_store.create(name="Alpha", kb_id="kb_alpha")
    beta_meta = manager.kb_store.create(name="Beta", kb_id="kb_beta")

    alpha_engine = await manager.get_engine("kb_alpha")
    beta_engine = await manager.get_engine("kb_beta")

    assert alpha_engine is not beta_engine
    assert set(manager._engines) == {"kb_alpha", "kb_beta"}

    assert Path(alpha_engine.settings.working_dir).resolve() == alpha_meta.storage_dir.resolve()
    assert Path(beta_engine.settings.working_dir).resolve() == beta_meta.storage_dir.resolve()
    assert alpha_engine.settings.workspace == "kb_alpha"
    assert beta_engine.settings.workspace == "kb_beta"

