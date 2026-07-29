"""知识库元数据与物理隔离目录管理，业务操作层"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from liverag.rag.metadata_store import DEFAULT_KB_ID
from liverag.rag.metadata_store import KnowledgeBaseMeta, MetadataStore


class KnowledgeBaseStore:
    """为 HTTP 闭环提供知识库领域操作，只暴露KB相关业务操作
    注意与metadata_store中MetadataStore的区别：
    RagEngineManager / HTTP API
            ↓
    KnowledgeBaseStore
            ↓
      MetadataStore
            ↓
          SQLite
          """

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata
        self.root_dir = metadata.knowledge_bases_dir

    def initialize(self) -> None:
        """初始化元数据表和默认知识库。"""
        self.metadata.initialize()

    def ensure_default(self) -> KnowledgeBaseMeta:
        """确保默认知识库及其物理目录存在。"""
        return self.metadata.ensure_default_knowledge_base()

    def list(self) -> list[dict[str, Any]]:
        """返回知识库公开信息列表。"""
        return self.metadata.list_knowledge_bases()

    def create(
        self,
        *,
        name: str,
        description: str = "",
        kb_id: str | None = None,
    ) -> KnowledgeBaseMeta:
        """创建知识库元数据和隔离目录。"""
        return self.metadata.create_knowledge_base(
            name=name,
            description=description,
            kb_id=kb_id,
        )

    def get(self, kb_id: str) -> KnowledgeBaseMeta:
        """读取知识库内部元数据。"""
        return self.metadata.get_knowledge_base(kb_id)

    def update(self, kb_id: str, *, name: str | None = None, description: str | None = None) -> KnowledgeBaseMeta:
        """更新知识库元数据。"""

        return self.metadata.update_knowledge_base(kb_id, name=name, description=description)
    
    def delete(self, kb_id: str) -> None:
        """删除知识库目录和元数据。"""

        if kb_id == DEFAULT_KB_ID:
            raise ValueError("默认知识库不能被删除！")
        
        meta = self.get(kb_id)
        #确保meta.root_dir确实在根目录里
        root = Path(meta.root_dir).resolve()
        kb_root = Path(self.root_dir).resolve()
        if root.parent != kb_root: 
            raise ValueError("知识库目录越过安全边界")
    
        self.metadata.delete_knowledge_base_metadata(kb_id)
        #递归删除该知识库的整个物理目录
        shutil.rmtree(root)

    def public_detail(self, kb_id: str) -> dict[str, Any]:
        """返回适合 HTTP 响应的知识库详情。"""
        return self.metadata.public_knowledge_base_detail(kb_id)

    def source_document_dir(self, kb_id: str, document_id: str) -> Path:
        """返回文档在所属知识库中的原文件目录。"""
        return self.metadata.source_document_dir(kb_id, document_id)
