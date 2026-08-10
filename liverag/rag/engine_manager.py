"""多个知识库对应多个RagEngine的缓存管理层，即：
HTTP/API
   ↓ 传入单个 kb_id
RagEngineManager
   ↓ 查找或创建该 KB 对应的 Engine
RagEngine
   ↓
该 KB 独立的 storage/workspace
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from liverag.rag.engine import RagEngine
from liverag.rag.knowledge_base import KnowledgeBaseMeta, KnowledgeBaseStore
from liverag.rag.metadata_store import DEFAULT_KB_ID, MetadataStore
from liverag.rag.rag_settings import RAGSettings


class RagEngineManager:
    """按 kb_id 管理多个独立 LightRAG engine。"""

    def __init__(self,settings:RAGSettings):
        self.settings=settings
        self.metadata= MetadataStore(
            Path(settings.user_data_dir).expanduser() / "liverag.db",
            Path(settings.knowledge_bases_dir),
        )
        self.kb_store=KnowledgeBaseStore(self.metadata)
        self._engines:dict[str,RagEngine]={}
        self._locks:dict[str,asyncio.Lock]={} #防止一个engine被并发初始化多次
        self._initialized=False


    async def initialize(self):
        """初始化默认知识库，预热默认engine"""

        self.kb_store.initialize() #准备产品元数据数据库（四张表）+确保默认知识库存在
        await self.get_engine("default") #预热engine：生成source/storage/logs文件库，封装LLM+Embedding，初始化LightRAG存储
        self._initialized=True


    async def finalize(self):
        """服务退出时关闭所有engine"""
        engines=list(self._engines.values())
        self._engines.clear()
        if engines:
            await asyncio.gather(*(engine.finalize() for engine in engines),return_exceptions=True) #某个engine清除失败，不影响其他engine清除
        self._initialized=False


    async def close_engine(self,kb_id:str):
        """删除或重载某个KB前关闭engine"""
        engine=self._engines.pop(kb_id,None)
        if engine is not None:
            await engine.finalize()


    async def delete_knowledge_base(self,kb_id:str):
        """关闭engine之后，安全删除KB"""
        if kb_id==DEFAULT_KB_ID:
            raise ValueError("默认知识库不能删除！")
        #确认知识库存在
        self.kb_store.get(kb_id)

        #必须先关闭引擎！防止向量库或图谱存储仍然持有文件句柄就直接删除目录，导致资源错误或残留状态
        await self.close_engine(kb_id)

        #删除KB
        self.kb_store.delete(kb_id)


    async def get_engine(self,kb_id:str)->RagEngine:
        """获取或创建单个KB的engine

        必须保证：
        同一个 kb_id 重用同一个 Engine；
        不同 kb_id 使用不同 Engine；
        同一 KB 不会被并发重复初始化；
        不同 KB 可以并行初始化；
        初始化失败的 Engine 不会进入缓存；
        每个 KB 使用独立的 working_dir 和 workspace

        需要的情况：
        涉及 storage、向量、图谱、检索或 LLM
        不需要的情况：
        只涉及 SQLite 或 sources 原文件
        """

        #如果缓存中有engine，直接返回
        if kb_id in self._engines:
            return self._engines[kb_id]

        #缓存中没有engine，获取kb_id专属的异步锁
        lock=self._locks.setdefault(kb_id,asyncio.Lock())
        async with lock:
            #双重检索
            if kb_id in self._engines:
                return self._engines[kb_id]
            #获取元数据
            meta=self.kb_store.get(kb_id)
            #获取engine
            engine=RagEngine(self._settings_for(meta))
            #初始化engine：创建目录，配置llm+embedding+lightRAG
            await engine.initialize()
            #关联kb_id（必须先初始化再缓存）
            self._engines[kb_id]=engine
            return engine



    def _settings_for(self,meta:KnowledgeBaseMeta)->RAGSettings:
        """绑定KB独立目录和workspace！！！
        确保了Engine A.settings ≠ Engine B.settings
        共享全局模型配置，隔离知识库数据"""

        return replace( #基于全局配置绑定新值
            self.settings,
            kb_id=meta.kb_id,
            kb_name=meta.name,
            working_dir=str(meta.storage_dir),
            upload_dir=str(meta.sources_dir),
            rag_log_dir=str(meta.logs_dir),
            workspace=meta.kb_id
        )



    async def ready_state(self)->dict[str,Any]:
        """汇总RAG服务当前的就绪状态"""
        return {
            "initialized":self._initialized,
            "provider_configured":self.settings.provider_ready(), #LLM+Embedding是否配置好了
            "llm_model":self.settings.llm_model,
            "embedding_model":self.settings.embedding_model,
            "embedding_dim":self.settings.embedding_dim,
            "user_data_dir":self.settings.absolute_user_data_dir,
            "knowledge_bases_dir":self.settings.absolute_knowledge_bases_dir,
            "cached_kb_ids":sorted(self._engines.keys()), #已经缓存了哪些engines
        }
