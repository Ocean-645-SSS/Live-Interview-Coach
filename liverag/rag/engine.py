"""把一个独立知识库的LigthRAG封装为LiveRAG能稳定使用的接口，负责：
初始化整个RAG系统
文档入库insert
查询query
管理整个生命周期
控制并发写入
"""

import asyncio
import re
import time
from asyncio.log import logger
from collections import Counter
from dataclasses import asdict, is_dataclass
from functools import partial
from math import ceil
from pathlib import Path
from typing import Any

from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import openai_complete_if_cache, openai_embed
from lightrag.utils import EmbeddingFunc

from liverag.rag.rag_settings import RAGSettings
from liverag.rag.schemas import ConversationOptions, QueryOptions, QueryResult


def _to_jsonable(value: Any) -> Any:
    """转换为JSON/FastAPI序列化的数据"""
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if hasattr(value, "value") and value.__class__.__name__.endswith("Status"):
        return value.value
    return value


FOLLOWUP_PHRASES = {
    "接着说",
    "继续",
    "继续说",
    "详细说",
    "详细说说",
    "展开说说",
    "展开讲讲",
    "然后呢",
    "还有呢",
    "再说说",
    "再讲讲",
    "具体点",
    "讲详细点",
    "说详细点",
}

NO_EVIDENCE_ANSWER = "知识库中没有足够依据"

CODE_LIKE_ENTITY_RE = re.compile(
    r"(^src/|^chunk-|^doc-|https?://|[/\\].+\.(ts|tsx|js|jsx|py|md|json|ya?ml|toml|sh|go|rs|java|kt|swift|rb|php|c|cpp|h|hpp)$)",
    re.IGNORECASE,
)

def _is_followup_query(text: str) -> bool:
    """判断是否为追问"""
    stripped = text.strip()
    return bool(stripped) and len(stripped) <= 12 and any(p in stripped for p in FOLLOWUP_PHRASES)


def rewrite_query(query: str, conversation: ConversationOptions)->tuple[str,bool]:
    """改写问题"""
    if not conversation.rewrite_followup or not _is_followup_query(query):
        return query, False
    last_query = (conversation.last_query or "").strip()
    if not last_query:
        return query, False
    rewritten = f"上一轮问题：{last_query}\n当前追问：{query}\n请围绕上一轮主题继续补充。"
    return rewritten, True


def _strip_chunk_content(chunks, include_content: bool):
    """是否保留chunk正文"""
    if include_content:
        return chunks
    # 复制每个chunk，但是删除"content"字段内容
    return [{k: v for k, v in chunk.items() if k != "content"} for chunk in chunks]

def _is_topic_entity(name: str) -> bool:
    """判断一个实体名是否适合作为知识库主题展示。"""

    text = name.strip()
    if len(text) < 2 or len(text) > 80:
        return False
    if CODE_LIKE_ENTITY_RE.search(text):
        return False
    if text.count("/") or text.count("\\"):
        return False
    if sum(ch in "*`{}[]<>" for ch in text) >= 2:
        return False
    return sum(ch.isalpha() for ch in text) != 0


def _build_topic_preview(candidates: list[str]) -> str:
    """把一组实体名压缩成文档主题预览。"""

    filtered = [item for item in candidates if _is_topic_entity(item)]
    picked = filtered[:3] if filtered else candidates[:3]
    return " / ".join(picked)

#================异常类===============
class RagEngineError(RuntimeError):
    """LightRAG 初始化、入库或查询失败。"""


class RagQueryTimeoutError(RagEngineError):
    """LightRAG 查询超过允许时间。"""




class RagEngine:
    def __init__(self, settings: RAGSettings):
        self.settings = settings  # LightRAG配置类
        self.rag: LightRAG | None = None  # LightRAG
        self._write_lock = asyncio.Lock()  # 异步写锁
        self._background_jobs: set[asyncio.Task[None]] = set()  # engine启动的后台异步任务


    async def initialize(self):
        """初始化engine，变为可以插入和查询的LightRAG引擎"""

        # 幂等判断，防止重复创建LigthRAG实例
        if self.rag is not None:
            return

        # 创建知识库运行目录，如果父目录不存在就一起创建，如果当前目录已经存在不抛出异常
        # 用户根数据
        Path(self.settings.absolute_user_data_dir).mkdir(parents=True, exist_ok=True)
        # 存储文本块、向量索引、图谱数据、文档状态...
        Path(self.settings.absolute_working_dir).mkdir(parents=True, exist_ok=True)
        # 存储知识库原文件
        Path(self.settings.absolute_upload_dir).mkdir(parents=True, exist_ok=True)

        # 封装LLM
        async def llm_model_func(
            prompt: str,
            system_prompt: str | None = None,
            history_messages: list[dict[str, str]] | None = None,
            keyword_extraction: bool = False,  # 是不是关键词提取调用，从而使用不同缓存或处理方式
            **kwargs,
        ) -> str:
            return await openai_complete_if_cache(
                model=self.settings.llm_model,
                prompt=prompt,
                system_prompt=system_prompt,
                history_messages=history_messages or [],
                keyword_extraction=keyword_extraction,
                api_key=self.settings.llm_api_key or None,
                base_url=self.settings.llm_base_url or None,
                **kwargs,
            )

        # 封装embedding
        embedding_func = EmbeddingFunc(
            embedding_dim=self.settings.embedding_dim,
            max_token_size=self.settings.max_embed_tokens,
            func=partial(  # 固定参数，每次调用不会变化
                openai_embed.func,
                model=self.settings.embedding_model,
                base_url=self.settings.embedding_base_url or None,
                api_key=self.settings.embedding_api_key or None,
            ),
        )

        # 创建LightRAG实例,负责：
        # 文档切块，Embedding，向量检索，实体抽取，关系抽取，知识图谱，上下文组装，答案生成
        rag = LightRAG(
            working_dir=self.settings.absolute_working_dir,  # 工作目录，物理隔离的关键
            workspace=self.settings.workspace,  # 通常为kb_id
            # ==================存储后端==================
            kv_storage=self.settings.kv_storage,  # 键值数据、完整文档、文本块...
            vector_storage=self.settings.vector_storage,  # 向量索引
            graph_storage=self.settings.graph_storage,  # 实体关系图谱
            doc_status_storage=self.settings.doc_status_storage,  # 文档处理状态
            # ==================LLM=======================
            llm_model_func=llm_model_func,
            llm_model_name=self.settings.llm_model,
            # ==================Embedding================
            embedding_func=embedding_func,
            # =================文本切块参数===============
            chunk_token_size=self.settings.chunk_token_size,
            chunk_overlap_token_size=self.settings.chunk_overlap_token_size,
            # =================Embedding批处理和并发================
            embedding_batch_num=self.settings.embedding_batch_num,
            embedding_func_max_async=self.settings.embedding_func_max_async,
            # =================LLM并发=======================
            llm_model_max_async=self.settings.llm_model_max_async,
            # =================并行插入数量=====================
            max_parallel_insert=self.settings.max_parallel_insert,
            # ================实体抽取追加次=======================
            entity_extract_max_gleaning=self.settings.entity_extract_max_gleaning,
            # ================LLM缓存=======================
            enable_llm_cache=self.settings.enable_llm_cache,
            enable_llm_cache_for_entity_extract=self.settings.enable_llm_cache_for_entity_extract,
        )

        # 初始化LightRAG存储:KV storage,Vector storage,Graph storage,Document status storage
        await rag.initialize_storages()
        self.rag = rag

    def ensure_ready(self) -> LightRAG:
        """确保当前rag初始化好了，否则拒绝操作"""
        if self.rag is None:
            raise RuntimeError("当前LigthRAG尚未初始化")
        return self.rag

    def ready_state(self) -> dict[str, Any]:
        """RAG,LLM,Embedding，三个目录+workspace已准备就绪"""
        return {
            "initialized": self.rag is not None,
            "provider_configured": self.settings.provider_ready(),
            "llm_model": self.settings.llm_model,
            "embedding_model": self.settings.embedding_model,
            "embedding_dim": self.settings.embedding_dim,
            "working_dir": self.settings.absolute_working_dir,
            "user_data_dir": self.settings.absolute_user_data_dir,
            "upload_dir": self.settings.absolute_upload_dir,
            "workspace": self.settings.workspace,
            "kb_id": self.settings.kb_id,
            "kb_name": self.settings.kb_name,
        }

    async def finalize(self):
        """释放LiveRAG存储资源"""
        for task in list(self._background_jobs):
            task.cancel()  # 发送任务取消请求，不是立即消灭任务
        if self._background_jobs:
            # 并发运行所有后台任务
            await asyncio.gather(
                *self._background_jobs,
                return_exceptions=True,
            )
        # 任务执行好了之后，清除
        self._background_jobs.clear()

        # 关闭LightRAG资源
        if self.rag is not None:
            await self.rag.finalize_storages()
            self.rag = None



    async def enqueue_documents(
        self,
        *,
        texts: list[str],
        file_sources: list[str],
        document_ids: list[str],
        track_id: str,
    ) -> dict[str, Any]:
        """将解析好的文档入队给LightRAG
        执行时序：
        1. 获取写锁
        2. 把文档放进 LightRAG 队列
        3. 释放写锁
        4. 创建后台索引任务
        5. 立即返回 track_id
        6. 后台任务获取写锁
        7. 处理队列
        8. 任务完成并从集合移除
        """

        rag = self.ensure_ready()

        # 检查文本是否为空
        if not texts:
            raise ValueError("至少需要一个document！")
        if any(not text.strip() for text in texts):
            raise ValueError("document文本不能为空！")
        # 检查文档id是否为空
        if any(not document_id.strip() for document_id in document_ids):
            raise ValueError("document_id不能为空！")
        # 检查trackid是否为空
        if not track_id.strip():
            raise ValueError("track_id不能为空！")
        # 检查文件来源不为空
        if any(not source.strip() for source in file_sources):
            raise ValueError("file_source不能为空！")

        # 确保批量提交时，每篇正文都能通过相同下标找到唯一的来源路径和文档 ID，
        # 防止文档信息错位或部分丢失。
        if not (len(texts) == len(file_sources) == len(document_ids)):
            raise ValueError("文档正文、文档来源路径、文档唯一ID三者包含的内容数量必须一致！")

        # 文档异步入队
        async with self._write_lock:
            await rag.apipeline_enqueue_documents(
                texts,
                file_paths=file_sources,
                ids=document_ids,
                track_id=track_id,
            )

        # 启动知识库后台索引任务，跟踪生命周期
        self._schedule_background_pipeline()

        return {
            "track_id": track_id,
            "processing_mode": "async",
            "count": len(texts),
            "kb_id": self.settings.kb_id,
            "kb_name": self.settings.kb_name,
        }

    def _schedule_background_pipeline(self) -> None:
        """启动当前知识库的后台索引任务并跟踪其生命周期。"""
        # 创建后台任务，不阻塞当前入队请求
        task = asyncio.create_task(self._run_background_pipeline())
        self._background_jobs.add(task)
        # 任务结束之后，自动清除，并记录异常
        task.add_done_callback(self._on_background_job_done)

    async def _run_background_pipeline(self) -> None:
        """串行执行当前知识库的 LightRAG 入队文档处理。"""
        rag = self.ensure_ready()
        async with self._write_lock:  # 获取写锁
            """处理队列中文档：
            → 读取待处理文档
            → 更新文档状态
            → 文本切块
            → 调用 Embedding
            → 保存向量
            → 调用 LLM 抽取实体和关系
            → 更新知识图谱
            → 更新文档处理状态
            """
            await rag.apipeline_process_enqueue_documents() #后台真正处理索引

    def _on_background_job_done(
    self,
    task: asyncio.Task[None],
    ) -> None:
        """清理已完成的后台任务，并记录任务异常。"""

        self._background_jobs.discard(task)

        if task.cancelled():
            return

        exception = task.exception()
        if exception is not None:
            logger.error(
                "LightRAG background indexing pipeline failed",
                exc_info=(
                    type(exception),
                    exception,
                    exception.__traceback__,
                ),
            )



    def build_query_param(self, options: QueryOptions, *, only_need_context: bool) -> QueryParam:
        """把对外请求模型QueryOptions（自定义）转换为内部查询参数QueryParam（原生）
        only_need_context:
        True表示只检索上下文，不生成答案 -> query_context()
        False表示检索并生成答案 -> query_answer()
        """

        # 参数字典
        values: dict[str, Any] = {
            "mode": options.mode,
            "only_need_context": only_need_context,
            "only_need_prompt": False,
            "stream": False,
            "hl_keywords": options.hl_keywords,
            "ll_keywords": options.ll_keywords,
            "enable_rerank": options.enable_rerank,
            "include_references": options.include_references,
        }

        # 动态添加可选参数
        for field_name in (
            "top_k",
            "chunk_top_k",
            "max_entity_tokens",
            "max_relation_tokens",
            "max_total_tokens",
            "response_type",
        ):
            value = getattr(options, field_name)

            if value is not None:
                values[field_name] = value

        # 展开字典，过滤掉为空的键值对，返回QueryParam标准格式
        return QueryParam(**{key: value for key, value in values.items() if value is not None})



    async def _evidence_is_relevant(self,query: str,context: str,) -> bool:
        """判断当前context是否能真正回答query"""

        if not context.strip():
            return False

        rag = self.ensure_ready()
        prompt = f"""
        判断下面的证据是否包含能够直接回答问题的信息。

        严格规则：
        - 仅仅主题相似不算相关。
        - 不能依靠常识补充缺失信息。
        - 如果证据不能直接支持答案，返回 false。
        - 只能返回 true 或 false。

        问题：
        {query}

        证据：
        {context}
        """.strip()

        response = await rag.llm_model_func(prompt) #调用LLM：判断证据是否真的能回答问题
        text = str(response).strip().lower()
        return text == "true"


    async def query_context(
        self, query: str, profile: str, options: QueryOptions, conversation: ConversationOptions
    ) -> tuple[QueryResult, dict[str, Any]]:
        """从知识库检索出相关证据，但不让答案生成LLM输出最终回答"""

        # 检查engine是否就绪
        rag = self.ensure_ready()

        # 合并profile默认参数和请求参数
        request_started = time.perf_counter()
        resolved = self.resolve_options(profile, options)

        # 根据上一轮对话改写追问
        rewrite_started = time.perf_counter()
        effective_query, rewritten = rewrite_query(query, conversation)
        rewrite_ms = round((time.perf_counter() - rewrite_started) * 1000, 1)

        # 开始时间
        started = request_started

        # 检查问题是否过短:减少无异议调用，避免浪费tokens
        if len(effective_query.strip()) < 3:
            result = QueryResult(
                kb_id=self.settings.kb_id,
                hit=False,
                query=query,
                effective_query=effective_query,
                rewritten=rewritten,
                has_context=False,
                context="",
                context_truncated=False,
                answer=None,
                references=[],
                chunks=[],
                duration=time.perf_counter()-started
            )
            metrics = self._query_metrics(started, resolved, cache_hit=False)
            return result, metrics | {
                "chunks_count": 0,
                "rewrite_ms": rewrite_ms,
                "retrieval_ms": 0.0,
                "extraction_ms": 0.0,
                "evidence_gate_ms": 0.0,
                "request_total_ms": metrics["latency_ms"],
            }

        # bypass:不使用RAG检索，直接回答
        if resolved.mode == "bypass":
            result = QueryResult(
                kb_id=self.settings.kb_id,
                hit=False,
                query=query,
                effective_query=effective_query,
                rewritten=rewritten,
                has_context=False,
                context="",
                context_truncated=False,
                answer=None,
                references=[],
                chunks=[],
                duration=time.perf_counter() - started,
            )
            metrics = self._query_metrics(started, resolved, cache_hit=False)
            return result, metrics | {
                "chunks_count": 0,
                "rewrite_ms": rewrite_ms,
                "retrieval_ms": 0.0,
                "extraction_ms": 0.0,
                "evidence_gate_ms": 0.0,
                "request_total_ms": metrics["latency_ms"],
            }

        # 构造QueryParam
        param = self.build_query_param(resolved, only_need_context=True)

        # 调用LigthRAG检索，增加timeout
        retrieval_started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                rag.aquery_llm(effective_query, param=param),
                timeout=self.settings.query_timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            raise RagQueryTimeoutError(
                f"LightRAG 查询超时！ "
                f"{self.settings.query_timeout_seconds:g} 秒"
            )from exc
        except Exception as exc:
            raise RagEngineError(
                "LightRAG初始化、入库或查询失败！"
            ) from exc

        # 提取context/references/chunks结构化上下文
        retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 1)
        extraction_started = time.perf_counter()
        payload = self._extract_context_payload(result, resolved)
        extraction_ms = round((time.perf_counter() - extraction_started) * 1000, 1)

        #判断当前context是否能回答query
        evidence_gate_started = time.perf_counter()
        relevant = await self._evidence_is_relevant(
            effective_query,
            payload["context"],
        )
        if not relevant:
            payload["hit"] = False
            payload["context"] = ""
            payload["context_truncated"] = False
            payload["references"] = []
            payload["chunks"] = []

        evidence_gate_ms = round((time.perf_counter() - evidence_gate_started) * 1000, 1)
        payload.update({"query": query, "effective_query": effective_query, "rewritten": rewritten})
        query_result = QueryResult(
            kb_id=self.settings.kb_id,
            hit=payload["hit"],
            query=query,
            effective_query=effective_query,
            rewritten=rewritten,
            has_context=bool(payload["context"]),
            context=payload["context"],
            context_truncated=payload["context_truncated"],
            answer=None,
            references=payload["references"],
            chunks=payload["chunks"],
            duration=time.perf_counter() - started,
        )
        # 计算查询指标
        metrics = self._query_metrics(started, resolved, cache_hit=False)
        metrics.update({
            "rewrite_ms": rewrite_ms,
            "retrieval_ms": retrieval_ms,
            "extraction_ms": extraction_ms,
            "evidence_gate_ms": evidence_gate_ms,
            "request_total_ms": metrics["latency_ms"],
        })
        data = result.get("data") or {}
        chunks = data.get("chunks") or []
        metrics["chunks_count"] = len(chunks)

        # 返回检索结果和metrics
        return query_result, metrics

    def _extract_context_payload(
        self, result: dict[str, Any], resolved: QueryOptions
    ) -> dict[str, Any]:
        """把LightRAG原始结果转换为LiveRAG稳定结构"""
        data = result.get("data", {}) or {}
        llm_response = result.get("llm_response", {}) or {}
        context = str(llm_response.get("content") or "").strip()

        # 识别无上下文占位符
        if self._is_empty_context_text(context):
            context = ""

        # 读取上下文长度限制
        limit = resolved.context_max_chars
        truncated = False  # 没有截断

        if limit and len(context) > limit:
            context = context[:limit].strip()
            truncated = True  # 发生截断

        # 提取chunks
        chunks = data.get("chunks", []) or []
        # 提取references
        references = data.get("references", []) or []

        # 提取结果部分
        return {
            "hit": (
                bool(context) and result.get("status") == "success"
            ),  # context不为空且响应结果为success才行
            "context": context,
            "context_truncated": truncated,
            "references": (
                self._with_kb_list(  # 每项增加kb信息
                    _to_jsonable(references)
                )
                if resolved.include_references
                else []
            ),
            "chunks": (
                self._with_kb_list(
                    _to_jsonable(_strip_chunk_content(chunks, resolved.include_chunk_content))
                )
                if resolved.include_references
                else []
            ),
        }

    @staticmethod
    def _is_empty_context_text(context: str) -> bool:
        """识别无上下文占位文本"""
        normalized = context.strip().lower()
        return not normalized or "[no-context]" in normalized

    def _with_kb(self, item: dict[str, Any]) -> dict[str, Any]:
        """给返回对象增加知识来源"""
        return {
            **item,
            "kb_id": self.settings.kb_id,
            "kb_name": self.settings.kb_name,
        }

    def _with_kb_list(self, items: Any):
        """给列表里的字典追加知识来源"""
        if not isinstance(items, list):
            return []
        return [self._with_kb(item) if isinstance(item, dict) else item for item in items]



    async def query_answer(
        self,
        query: str,  # 用户原始问题
        profile: str,  # default:普通文本查询  voice：实时语音查询
        options: QueryOptions,
        conversation: ConversationOptions,  # 处理缺乏独立语义的追问
    ) -> tuple[QueryResult, dict[str, Any]]:
        """在当前知识库里检索相关证据，让LightRAG基于证据生成答案，
        并返回答案、检索原始结果和性能指标"""

        # 获得准备好的rag
        rag = self.ensure_ready()

        # 合并查询参数得到最终生效的参数：profile 中的默认配置+本次 options 中显式传入的配置
        # 先加载预设，再用本次请求参数覆盖预设。
        resolved = self.resolve_options(profile, options)

        # 改写多轮追问
        # effective_query:真正提交给LightRAG的问题，rewritten:是否发生了改写
        effective_query, rewritten = rewrite_query(query, conversation)

        # 计时
        started = time.perf_counter()

        # 构造QueryParam
        param = self.build_query_param(resolved, only_need_context=False)

        # LightRAG调用LLM检索:
        # 查询分析→ 关键词提取→ 向量/图谱检索→
        # 选择相关 chunks→ 组装上下文→ 调用 LLM→ 返回答案与检索数据
        try:
            result = await asyncio.wait_for(
                rag.aquery_llm(effective_query, param=param), # 第一次调用LLM，次数取决于schemas.QueryMode
                timeout=self.settings.query_timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            raise RagQueryTimeoutError(
                f"LightRAG 查询超时！ "
                f"{self.settings.query_timeout_seconds:g} 秒"
            )from exc
        except Exception as exc:
            raise RagEngineError(
                "LightRAG初始化、入库或查询失败！"
            ) from exc

        # 提取答案、检索结果和耗时
        llm_response = result.get("llm_response", {}) or {}

        # 增加 evidence 门控
        data = result.get("data") or {}
        chunks = data.get("chunks") or []
        references = data.get("references") or []
        query_succeeded=result.get("status")=="success"
        has_evidence = bool(chunks or references)
        candidate_hit = query_succeeded and has_evidence

        context = str(data.get("context") or "").strip()
        if not context:
            context = "\n\n".join(
                str(chunk.get("content") or "").strip()
                for chunk in chunks
                if isinstance(chunk, dict) and chunk.get("content")
            )

        # 截断 context
        context_truncated = False
        if resolved.context_max_chars and len(context) > resolved.context_max_chars:
            context = context[: resolved.context_max_chars].strip()
            context_truncated = True

        # 第二次调用LLM：判断证据是否真的能回答问题，有检索结果不代表证据真的能回答问题
        relevant = (
            await self._evidence_is_relevant(effective_query, context)
            if candidate_hit
            else False
        )
        hit = candidate_hit and relevant

        # 不相关时必须清空全部证据，禁止模型答案泄漏出去
        if not hit:
            context = ""
            context_truncated = False
            references = []
            chunks = []

        has_context = hit and bool(context)

        evidence_references = (
            self._with_kb_list(_to_jsonable(references)) if resolved.include_references else []
        )
        evidence_chunks = (
            self._with_kb_list(
                _to_jsonable(_strip_chunk_content(chunks, resolved.include_chunk_content))
            )
            if resolved.include_references
            else []
        )

        metrics = self._query_metrics(started, resolved, cache_hit=False)
        metrics["chunks_count"] = len(chunks)

        #无证据固定答案
        answer = (
            llm_response.get("content") or ""
            if hit
            else NO_EVIDENCE_ANSWER
        )

        # 返回业务数据+metrics
        query_result = QueryResult(
            kb_id=self.settings.kb_id,
            hit=hit,
            query=query,
            effective_query=effective_query,
            rewritten=rewritten,
            has_context=has_context,
            context=context,
            context_truncated=context_truncated,
            answer=answer,
            references=evidence_references,
            chunks=evidence_chunks,
            duration=time.perf_counter() - started,
        )
        return query_result, metrics


    def resolve_options(self, profile: str, options: QueryOptions) -> QueryOptions:
        """读取profile默认值，获取调用方显式填写的值，让显式值覆盖默认值"""

        # 获取profile默认参数
        base = self._profile_defaults(profile).model_dump()
        # 获取本次请求显式提供的参数
        incoming = options.model_dump(
            exclude_none=True, exclude_unset=True
        )  # 不覆盖调用方没填写的字段
        # 用请求值覆盖默认值：键不存在就新增，存在就覆盖
        base.update(incoming)
        # 重新构造
        return QueryOptions(**base)


    def _profile_defaults(self, profile: str) -> QueryOptions:
        """"""
        if profile == "voice":
            return QueryOptions(  # 低延迟
                mode=self.settings.voice_mode,  # naive：文本块向量检索，不处理复杂图谱检索
                top_k=self.settings.voice_top_k,
                chunk_top_k=self.settings.voice_chunk_top_k,
                enable_rerank=self.settings.voice_enable_rerank,
                include_references=False,
                include_chunk_content=False,
                context_max_chars=self.settings.voice_context_max_chars,  # 限制上下文长度
            )
        return QueryOptions(  # 完整检索
            mode=self.settings.default_mode,  # mix：结合多种检索来源
            top_k=self.settings.top_k,
            chunk_top_k=self.settings.chunk_top_k,
            enable_rerank=self.settings.enable_rerank,  # 再打分，结果更准确
            include_references=True,  # 返回来源
            include_chunk_content=False,
        )

    def _query_metrics(self, started: float, options: QueryOptions, cache_hit: bool):
        """获取一次RAG查询的性能和实际参数"""
        return {
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),  # 计算耗时，
            "mode": options.mode,
            "top_k": options.top_k,
            "chunk_top_k": options.chunk_top_k,
            "enable_rerank": options.enable_rerank,
            "cache_hit": cache_hit,
            "kb_id": self.settings.kb_id,
            "kb_name": self.settings.kb_name,
        }

    async def query_data(
        self,
        query:str,
        profile:str,
        options: QueryOptions,
        conversation:ConversationOptions
    )->tuple[QueryResult, dict[str, Any]]:
        """查询结构化数据"""

        rag=self.ensure_ready()
        resolved=self.resolve_options(profile=profile,options=options)
        effective_query , rewritten=rewrite_query(query,conversation=conversation)
        param = self.build_query_param(resolved, only_need_context=True)
        result=await rag.aquery_data(effective_query,param=param)

        started=time.perf_counter()
        metrics=self._query_metrics(started, resolved, cache_hit=False)
        metrics["chunks_count"] = len(result.get("data", {}).get("chunks", []) or [])

        return {
            "query": query,
            "effective_query": effective_query,
            "rewritten": rewritten,
            "result": _to_jsonable(result),
        }, metrics


    async def documents(self,page:int=1,page_size:int=50)->dict[str,Any]:
        """读取documents状态列侬阿婆"""

        rag = self.ensure_ready()
        #分页读取LightRAG文档状态
        docs, total = await rag.doc_status.get_docs_paginated(page=page, page_size=page_size) #查询当前知识库中的全部 LightRAG 文档，但一次只返回一页
        #统计不同文档数量
        counts = await rag.doc_status.get_all_status_counts()
        #计算总页数
        total_pages = ceil(total / page_size) if total else 0
        return { #组装当前页文档
            "documents": [
                self._with_kb({"document_id": doc_id, **_to_jsonable(status)}) for doc_id, status in docs
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1 and total_pages > 0,
            "status_counts": counts,
        }


    async def document_detail(self, document_id: str) -> dict[str, Any]:
        """读取单个文档状态status、原文content和文本块chunks。"""

        rag = self.ensure_ready()
        #读取文档状态
        status = await rag.doc_status.get_by_id(document_id)
        if not status:
            raise KeyError(f"document not found: {document_id}")

        #读取完整正文
        full_doc = await rag.full_docs.get_by_id(document_id)
        #将文档状态转换为json格式
        json_status = _to_jsonable(status)
        #获得chunk ID
        chunks_list = json_status.get("chunks_list") or []
        chunks = []
        #批量读取chunk内容
        if chunks_list:
            raw_chunks = await rag.text_chunks.get_by_ids(chunks_list)
            chunks = _to_jsonable(raw_chunks)

        return self._with_kb({
            "document_id": document_id,
            "status": json_status, #rag.doc_status
            "content": (full_doc or {}).get("content", ""), #rag.full_docs
            "file_path": json_status.get("file_path") or (full_doc or {}).get("file_path"),
            "chunks": chunks, #rag.text_chunks
            "chunks_count": len(chunks),
        })

    async def delete_document(
        self,
        document_id: str,
        *,
        delete_llm_cache: bool = False,
    ) -> dict[str, Any]:
        """删除单个文档及其派生文本块、实体、关系和向量数据。"""

        rag = self.ensure_ready()
        async with self._write_lock:
            result = await rag.adelete_by_doc_id(
                document_id,
                delete_llm_cache=delete_llm_cache,
            )
        return _to_jsonable(result)


    async def job(self, job_id: str) -> dict[str, Any]:
        """按照track_id(job_id)异步获取入库任务"""
        rag = self.ensure_ready()
        docs = await rag.aget_docs_by_track_id(job_id) #根据track_id,查询某次批量上传任务包含的文档及其最新状态
        return {
            "job_id": job_id,
            "documents": [
                self._with_kb({"document_id": doc_id, **_to_jsonable(status)}) for doc_id, status in docs.items()
            ],
            "total": len(docs),
            "kb_id": self.settings.kb_id,
            "kb_name": self.settings.kb_name,
        }

    async def knowledge_overview(
        self,
        *,
        entity_limit: int = 20,
        relation_limit: int = 12,
        document_limit: int = 10,
        topic_limit: int = 8,
    ) -> dict[str, Any]:
        """返回知识库的实体、关系和文档主题概览。
        获取全部文档状态
            ↓
        筛选已完成索引的文档
            ↓
        读取这些文档的实体和关系
            ↓
        统计并排序实体、主题和关系
            ↓
        生成每个文档的简要介绍
            ↓
        返回结构化 JSON"""

        #读取知识库有多少文档、哪些processed、哪些失败、哪些等待
        rag = self.ensure_ready()
        status_counts = await rag.doc_status.get_all_status_counts()
        total_documents = int(status_counts.get("all", 0) or 0)
        docs, _ = await rag.doc_status.get_docs_paginated(
            page=1,
            page_size=max(total_documents or 0, 1),
        )
        if total_documents == 0:
            total_documents = len(docs)
        documents = [(doc_id, _to_jsonable(status)) for doc_id, status in docs]
        processed = [(doc_id, status) for doc_id, status in documents if status.get("status") == "processed"]
        processed_ids = [doc_id for doc_id, _ in processed]

        if not processed_ids:
            return {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "summary": {
                    "total_documents": total_documents,
                    "processed_documents": status_counts.get("processed", 0),
                    "failed_documents": status_counts.get("failed", 0),
                    "pending_documents": total_documents
                    - status_counts.get("processed", 0)
                    - status_counts.get("failed", 0),
                    "total_chunks": 0,
                    "total_entities": 0,
                    "total_relationships": 0,
                },
                "topics": [],
                "top_entities": [],
                "top_relationships": [],
                "documents": [
                    {
                        "document_id": doc_id,
                        "kb_id": self.settings.kb_id,
                        "kb_name": self.settings.kb_name,
                        "file_path": status.get("file_path"),
                        "status": status.get("status"),
                        "chunks_count": status.get("chunks_count", 0),
                        "updated_at": status.get("updated_at") or status.get("created_at"),
                        "topic_preview": "",
                        "top_entities": [],
                    }
                    for doc_id, status in documents[:document_limit]
                ],
            }

        full_entities_raw = await rag.full_entities.get_by_ids(processed_ids)
        full_relations_raw = await rag.full_relations.get_by_ids(processed_ids)
        full_entities = [_to_jsonable(item or {}) for item in full_entities_raw]
        full_relations = [_to_jsonable(item or {}) for item in full_relations_raw]

        #文档包含哪些实体、关系
        entity_mentions = Counter[str]()
        entity_documents = Counter[str]()
        relation_mentions = Counter[tuple[str, str]]()
        relation_documents = Counter[tuple[str, str]]()
        doc_entities: dict[str, list[str]] = {}

        for (doc_id, _status), entity_payload in zip(processed, full_entities, strict=False):
            unique_entities: list[str] = []
            seen_entities: set[str] = set()
            for raw_name in entity_payload.get("entity_names") or []:
                name = str(raw_name).strip()
                if not name:
                    continue
                entity_mentions[name] += 1
                if name in seen_entities:
                    continue
                seen_entities.add(name)
                entity_documents[name] += 1
                unique_entities.append(name)
            doc_entities[doc_id] = unique_entities

        for relation_payload in full_relations:
            seen_pairs: set[tuple[str, str]] = set()
            for raw_pair in relation_payload.get("relation_pairs") or []:
                if not isinstance(raw_pair, (list, tuple)) or len(raw_pair) != 2:
                    continue
                source = str(raw_pair[0]).strip()
                target = str(raw_pair[1]).strip()
                if not source or not target:
                    continue
                pair = (source, target)
                relation_mentions[pair] += 1
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                relation_documents[pair] += 1

        unique_entities = list(entity_documents.keys())
        entity_chunk_payloads = await rag.entity_chunks.get_by_ids(unique_entities) if unique_entities else []
        entity_chunk_map = {
            name: _to_jsonable(payload or {})
            for name, payload in zip(unique_entities, entity_chunk_payloads, strict=False)
        }

        #统计并且排序：出现次数、关联多少文本块、覆盖多少文档
        ranked_entities = sorted(
            unique_entities,
            key=lambda name: (
                entity_documents[name],
                entity_chunk_map.get(name, {}).get("count", 0),
                entity_mentions[name],
                name.lower(),
            ),
            reverse=True,
        )

        #最重要的实体
        top_entities: list[dict[str, Any]] = []
        for name in ranked_entities[:entity_limit]:
            try:
                degree = await rag.chunk_entity_relation_graph.node_degree(name)
            except Exception:
                degree = 0
            top_entities.append(
                {
                    "name": name,
                    "mention_count": entity_mentions[name],
                    "document_count": entity_documents[name],
                    "chunk_count": entity_chunk_map.get(name, {}).get("count", 0),
                    "degree": degree,
                    "is_topic_like": _is_topic_entity(name),
                }
            )

        ranked_topics = [
            name
            for name in ranked_entities
            if _is_topic_entity(name)
        ]
        topics = [
            {
                "name": name,
                "document_count": entity_documents[name],
                "chunk_count": entity_chunk_map.get(name, {}).get("count", 0),
                "mention_count": entity_mentions[name],
            }
            for name in ranked_topics[:topic_limit]
        ]

        ranked_relations = sorted(
            relation_documents.keys(),
            key=lambda pair: (
                relation_documents[pair],
                relation_mentions[pair],
                pair[0].lower(),
                pair[1].lower(),
            ),
            reverse=True,
        )

        #最重要的关系
        top_relationships = [
            {
                "source": source,
                "target": target,
                "document_count": relation_documents[(source, target)],
                "mention_count": relation_mentions[(source, target)],
            }
            for source, target in ranked_relations[:relation_limit]
        ]

        document_items: list[dict[str, Any]] = []
        for doc_id, status in documents[:document_limit]:
            entities = doc_entities.get(doc_id, [])
            ranked_doc_entities = sorted(
                entities,
                key=lambda name: (
                    entity_documents[name],
                    entity_chunk_map.get(name, {}).get("count", 0),
                    entity_mentions[name],
                    name.lower(),
                ),
                reverse=True,
            )
            topic_preview = _build_topic_preview(ranked_doc_entities)
            document_items.append(
                {
                    "document_id": doc_id,
                    "kb_id": self.settings.kb_id,
                    "kb_name": self.settings.kb_name,
                    "file_path": status.get("file_path"),
                    "status": status.get("status"),
                    "chunks_count": status.get("chunks_count", 0),
                    "updated_at": status.get("updated_at") or status.get("created_at"),
                    "topic_preview": topic_preview,
                    "top_entities": ranked_doc_entities[:5],
                }
            )

        pending_documents = total_documents - status_counts.get("processed", 0) - status_counts.get("failed", 0)
        total_chunks = sum((status.get("chunks_count") or 0) for _, status in processed)

        return {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "summary": {
                "total_documents": total_documents,
                "processed_documents": status_counts.get("processed", 0),
                "failed_documents": status_counts.get("failed", 0),
                "pending_documents": pending_documents,
                "total_chunks": total_chunks,
                "total_entities": len(unique_entities),
                "total_relationships": len(relation_documents),
            },
            "topics": topics,
            "top_entities": top_entities,
            "top_relationships": top_relationships,
            "documents": document_items,
        }
