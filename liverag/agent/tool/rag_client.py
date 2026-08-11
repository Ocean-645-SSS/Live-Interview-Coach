"""Agent 与 RAG Core之间的 HTTP 适配层
它在构造时锁定单个知识库，查询前再次校验 Session Runtime 中的 kb_id，防止跨会话或跨库污染；
语音 Agent 只调用 M1 的 Context 接口，由 RAG Core 返回证据；M2 Agent LLM 负责最终生成。
所有网络结果都会统一转换成 RagQueryResult，并与当前 session_id + turn_index 一起落入 Evidence 审计日志

在全局链路中：
VoiceAssistant
→ ContextManager
→ RagClient*
→ HTTP
→ M1 RAG Core /query/context"""

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import aiohttp
from pydantic import BaseModel, Field

from liverag.config.settings import RagClientSettings
from liverag.context.store import ContextStore

RagErrorType = Literal["timeout", "http_4xx", "http_5xx", "transport", "protocol"]


@dataclass(slots=True)
class RagQueryError:
    """RAG查询时候的错误原因
    timeout：超时未完成

    http_4xx：服务器成功接受请求，但是请求存在问题：
    401：API Key错误    404：KB不存在   422：请求参数不合法

    http_5xx：RAG Core或者上游服务内部异常
    502：RAG Core上游失败   504：服务端报告查询超时

    transport：DNS、拒绝连接、连接中断等网络问题

    protocol：获得HTTP响应，但是返回内容不符合约定"""

    type: RagErrorType
    message: str  # 具体错误信息
    status_code: int | None = None


@dataclass(slots=True)
class RagEvidenceDocument:
    """命中了哪些文档"""

    document_id: str | None = None
    file_path: str | None = None
    original_filename: str | None = None


@dataclass(slots=True)
class RagEvidenceChunk:
    """具体命中了文档中哪些内容"""

    chunk_id: str | None = None
    document_id: str | None = None
    content: str | None = None  # 片段内容
    score: float | None = None  # 相关性分数


class RagQueryResult(BaseModel):
    """一次 RAG 检索的结果"""

    request_id: str | None = None
    kb_id: str | None = None
    query: str
    effective_query: str
    rewritten: bool = False

    hit: bool = False
    has_context: bool = False
    context: str = ""

    evidence_documents: list[RagEvidenceDocument] = Field(default_factory=list)
    evidence_chunks: list[RagEvidenceChunk] = Field(default_factory=list)

    metrics: dict[str, Any]
    error: RagQueryError | None = None

    @classmethod
    def failed(
        cls,
        *,
        kb_id: str,
        query: str,
        duration: float,
        error_type: RagErrorType,
        message: str,
        request_id: str | None = None,
        status_code: int | None = None,
    ) -> "RagQueryResult":
        """创建一个查询失败的RagQueryResult快捷方法"""

        return cls(
            request_id=request_id,
            kb_id=kb_id,
            query=query,
            effective_query=query,
            rewritten=False,
            hit=False,  # 未命中
            has_context=False,  # 未找到上下文
            context="",
            evidence_documents=[],
            evidence_chunks=[],
            metrics={"latency_ms": max(0.0, duration) * 1000.0},
            error=RagQueryError(
                type=error_type,
                message=message,
                status_code=status_code,
            ),
        )

    def to_tool_payload(self) -> dict[str, Any]:
        """转换为工具输出，只保留Agent需要看到的信息"""

        if self.error is not None:
            return {
                "status": "failed",
                "error": {
                    "type": self.error.type,
                    "message": self.error.message,
                },
                "instruction": "检索失败，不得声称已经查到知识库依据，也不得编造回答。",
            }
        if not self.hit or not self.has_context:
            return {
                "status": "miss",
                "context": "",
                "documents": [],
                "chunks": [],
                "instruction": "知识库中没有找到足够依据，不得编造。",
            }
        return {
            "status": "hit",
            "context": self.context,
            "documents": [asdict(document) for document in self.evidence_documents],
            "chunks": [asdict(chunk) for chunk in self.evidence_chunks],
            "instruction": "请仅依据返回的知识库上下文回答。",
        }


class RagClient:
    """封装语音链路需要的 RAG 上下文查询"""

    def __init__(
        self,
        settings: RagClientSettings,
        store: ContextStore,
        *,
        user_data_dir: Path | None = None,
        kb_id: str = "default",
        kb_name: str = "个人简历",
    ):
        self.settings = settings
        self.store = store
        self.user_data_dir = user_data_dir
        self.kb_id = kb_id.strip() or "default"
        self.kb_name = kb_name.strip() or self.kb_id

    async def query_context(
        self,
        *,
        query: str,
        last_query: str | None,
        session_id: str,
        source: str = "api",
        tool_name: str | None = None,
        turn_index: int | None = None,
    ) -> RagQueryResult:
        """检索当前session_id锁定的单个知识库，并且记录完整RAG结果
        参数校验
        ↓
        会话与知识库校验
        ↓
        构造 payload / headers
        ↓
        HTTP 请求 RAG Core
        ↓
        ┌─────────────┬─────────────┐
        │             │             │
        HTTP失败      网络失败       HTTP成功
        │             │             │
        构造错误结果   构造错误结果    _parse_response()
        └─────────────┴─────────────┘
                    ↓
            _record_result()
                    ↓
                返回结果
        """

        # 校验参数
        clean_query = query.strip()
        if not clean_query:
            raise ValueError("query 不得为空")

        if not session_id.strip():
            raise ValueError("session_id 不能为空")

        if isinstance(turn_index, bool) or not isinstance(turn_index, int) or turn_index < 0:
            raise ValueError("turn_index 必须是非负整数")

        runtime = self.store.read_runtime_state(session_id=session_id)
        if not runtime:
            raise ValueError(f"session 不存在:{session_id}")

        #校验session runtime中的kb_id与RAG Client绑定的kb_id的一致条件
        session_kb_id = runtime.get("kb_id")
        if session_kb_id != self.kb_id:
            raise ValueError("RagClient 的 kb_id 与 session 锁定的知识库不一致")

        # 构造请求头(schemas.QueryRequest嵌套参数部分)
        payload = {
            "query": clean_query,
            "profile": "voice",
            "options": {
                "mode": self.settings.query_mode,
                "top_k": self.settings.top_k,
                "chunk_top_k": self.settings.chunk_top_k,
                "enable_rerank": self.settings.enable_rerank,
                "include_references": True,
                "include_chunk_content": True,
                "context_max_chars": self.settings.context_max_chars,
            },
            "conversation": {"last_query": last_query, "rewrite_followup": False},
        }

        # 构造认证头header
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["X-API-Key"] = self.settings.api_key

        # 请求RAG Core
        # 记录调用开始时间
        started = time.perf_counter()

        try:
            # 根据配置创建超时规则
            timeout = aiohttp.ClientTimeout(total=max(self.settings.timeout_ms, 100) / 1000.0)

            # 创建异步 HTTP ClientSession
            async with aiohttp.ClientSession(timeout=timeout) as session:  # noqa: SIM117
                # 向 RAG Core 发送 POST 请求: /v1/knowledge-bases/{kb_id}/query/context
                async with session.post(
                    self._context_url(), json=payload, headers=headers
                ) as response:
                    # 如果响应非200，直接返回
                    if response.status != 200:
                        body = await response.text()
                        # 判断状态码
                        if response.status == 504:
                            error_type = "timeout"
                        elif 400 <= response.status < 500:
                            error_type = "http_4xx"
                        else:
                            error_type = "http_5xx"

                        #响应非200的RagQuery结果
                        result = RagQueryResult(
                            kb_id=self.kb_id,
                            query=clean_query,
                            effective_query=clean_query,
                            context="",
                            hit=False,
                            has_context=False,
                            metrics={
                                "latency_ms": self._elapsed_ms(started),
                                "status": response.status,
                                "cache_hit": False,
                                "kb_id": self.kb_id,
                                "kb_name": self.kb_name,
                            },
                            error=RagQueryError(
                                type=error_type,
                                message=body[:300] or "RAG Core 查询失败",
                                status_code=response.status,
                            ),
                        )
                        #写入rag_context.jsonl
                        return self._record_result(
                            result,
                            session_id=session_id,
                            source=source,
                            tool_name=tool_name,
                            turn_index=turn_index,
                        )
                    # 读取 RAG Core 返回 HTTP 响应，解析为字典
                    data = await response.json()

        # HTTP 成功但响应正文不是合法 JSON。
        except (aiohttp.ContentTypeError, json.JSONDecodeError) as exc:
            result = RagQueryResult.failed(
                kb_id=self.kb_id,
                query=clean_query,
                duration=(time.perf_counter() - started),
                error_type="protocol",
                message=f"RAG Core 响应不是合法 JSON：{type(exc).__name__}",
            )
            return self._record_result(
                result,
                session_id=session_id,
                source=source,
                tool_name=tool_name,
                turn_index=turn_index,
            )

        # 处理查询超时
        except asyncio.TimeoutError:
            result = RagQueryResult(
                kb_id=self.kb_id,
                query=clean_query,
                effective_query=clean_query,
                context="",
                hit=False,
                has_context=False,
                metrics={
                    "latency_ms": self._elapsed_ms(started),
                    "timeout_ms": self.settings.timeout_ms,
                    "kb_id": self.kb_id,
                    "kb_name": self.kb_name,
                },
                error=RagQueryError(
                    type="timeout",
                    message="RAG Core 查询超时",
                ),
            )
            return self._record_result(
                result,
                session_id=session_id,
                source=source,
                tool_name=tool_name,
                turn_index=turn_index,
            )

        # 处理HTTP客户端和网络连接错误
        except aiohttp.ClientError as exc:
            result = RagQueryResult(
                kb_id=self.kb_id,
                query=clean_query,
                effective_query=clean_query,
                context="",
                hit=False,
                has_context=False,
                metrics={
                    "latency_ms": self._elapsed_ms(started),
                    "kb_id": self.kb_id,
                    "kb_name": self.kb_name,
                },
                error=RagQueryError(
                    type="transport",
                    message=f"无法连接 RAG Core：{type(exc).__name__}",
                ),
            )

            return self._record_result(
                result,
                session_id=session_id,
                source=source,
                tool_name=tool_name,
                turn_index=turn_index,
            )

        # 成功连接 RAG Core+没有超时+HTTP 状态码为 200+响应正文是合法 JSON，才能解析成果
        result = self._parse_response(
            data=data,
            query=clean_query,
            start=started,
        )

        # 写入rag_context.jsonl
        return self._record_result(
            result,
            session_id=session_id,
            source=source,
            tool_name=tool_name,
            turn_index=turn_index,
        )

    def _parse_response(
        self,
        data: Any,
        query: str,
        start: float,
    ) -> RagQueryResult:
        """解析所有 HTTP 200、合法 JSON 的 RAG Core 响应，但status=200包括了命中、未命中和业务协议错误(还需进一步排查)
        并解析 context 和 evidence，封装为统一的RagQueryResult"""

        # 初始化默认结果
        request_id: str | None = None
        service_metrics: dict[str, Any] = {}
        hit = False
        has_context = False
        raw_context = ""  # RAG Core返回的原始完整上下文
        context = ""  # 实际给Agent的文本（可能经过截取）
        effective_query = query
        rewritten = False
        evidence_documents: list[RagEvidenceDocument] = []  # 命中的原始文档
        evidence_chunks: list[RagEvidenceChunk] = []  # 命中的文本片段
        error: RagQueryError | None = None

        # 响应必须遵守当前 RAG Core 的统一 envelope。
        # 检查data格式
        if not isinstance(data, dict):
            error = RagQueryError(
                type="protocol",
                message="RAG Core 响应不是 JSON 对象",
            )
        # 检查status和data字段是否在data里
        elif "status" not in data or "data" not in data:
            error = RagQueryError(
                type="protocol",
                message="RAG Core 响应缺少 status 或 data 字段",
            )
        # data/status都合格
        else:
            # 检查request_id
            raw_request_id = data.get("request_id")
            if not isinstance(raw_request_id, str) or not raw_request_id.strip():
                error = RagQueryError(
                    type="protocol",
                    message="RAG Core 响应缺少有效 request_id",
                )
            else:
                request_id = raw_request_id

            # 检查metrics
            raw_metrics = data.get("metrics", {})
            if not isinstance(raw_metrics, dict):
                error = RagQueryError(
                    type="protocol",
                    message="RAG Core 响应中的 metrics 必须是对象",
                )
            else:
                service_metrics = raw_metrics

            # 检查status
            status = data.get("status")
            # HTTP已经200，但是业务处理异常
            if status != "ok":
                raw_error = data.get("error")
                if isinstance(raw_error, dict):
                    message = str(raw_error.get("message") or "RAG 查询失败")
                elif raw_error:
                    message = str(raw_error)
                else:
                    message = "RAG 查询失败"
                error = RagQueryError(
                    type="protocol",
                    message=message,
                )

            elif error is None:
                result_data = data.get("data")
                if not isinstance(result_data, dict):
                    error = RagQueryError(
                        type="protocol",
                        message="RAG Core 响应中的 data 必须是对象",
                    )
                # KB_id和ragcore的不一样
                elif result_data.get("kb_id") != self.kb_id:
                    error = RagQueryError(
                        type="protocol",
                        message=(
                            "RAG Core 返回的 kb_id 不一致："
                            f"期望 {self.kb_id}，实际 {result_data.get('kb_id')}"
                        ),
                    )
                # 正常
                else:
                    raw_hit = result_data.get("hit")
                    raw_has_context = result_data.get("has_context")
                    context_value = result_data.get("context")
                    raw_effective_query = result_data.get("effective_query")
                    raw_rewritten = result_data.get("rewritten")

                    if not isinstance(raw_hit, bool):
                        error = RagQueryError(
                            type="protocol",
                            message="RAG Core 响应中的 hit 必须是布尔值",
                        )
                    elif not isinstance(raw_has_context, bool):
                        error = RagQueryError(
                            type="protocol",
                            message="RAG Core 响应中的 has_context 必须是布尔值",
                        )
                    elif not isinstance(context_value, str):
                        error = RagQueryError(
                            type="protocol",
                            message="RAG Core 响应中的 context 必须是字符串",
                        )
                    elif not isinstance(raw_effective_query, str):
                        error = RagQueryError(
                            type="protocol",
                            message="RAG Core 响应中的 effective_query 必须是字符串",
                        )
                    elif not isinstance(raw_rewritten, bool):
                        error = RagQueryError(
                            type="protocol",
                            message="RAG Core 响应中的 rewritten 必须是布尔值",
                        )
                    # 检查字段之间是否矛盾
                    # 1.有上下文，但是内容为空
                    elif raw_has_context and not context_value.strip():
                        error = RagQueryError(
                            type="protocol",
                            message="RAG Core 声明 has_context=True，但 context 为空",
                        )
                    # 2.有上下文和未命中矛盾
                    elif not raw_hit and raw_has_context:
                        error = RagQueryError(
                            type="protocol",
                            message="RAG Core 返回的 hit 与 has_context 相互矛盾",
                        )
                    # 数据可信
                    else:
                        hit = raw_hit
                        has_context = raw_has_context
                        raw_context = context_value.strip()
                        effective_query = raw_effective_query
                        rewritten = raw_rewritten
                        # 获得文本切片证据
                        evidence_chunks = self._build_evidence_chunks(result_data.get("chunks"))
                        # 获得原始文档证据
                        evidence_documents = self._build_evidence_documents(
                            result_data.get("references"),
                            evidence_chunks=evidence_chunks,
                        )
        # 如果有错误，所有结果清空
        if error is not None:
            hit = False
            has_context = False
            raw_context = ""
            evidence_documents = []
            evidence_chunks = []
        # 检索命中，并且是有效上下文，才截取context
        elif hit and has_context:
            context = raw_context[: self.settings.context_max_chars].strip()

        metrics = {
            **service_metrics,  # 服务端指标
            # 客户端指标
            "network_total_ms": self._elapsed_ms(start),
            "latency_ms": service_metrics.get("request_total_ms", self._elapsed_ms(start)),
            "mode": service_metrics.get(
                "mode",
                self.settings.query_mode,
            ),
            "cache_hit": False,
            "context_len": len(raw_context),
            "context_truncated": (len(raw_context) > self.settings.context_max_chars),
            "kb_id": self.kb_id,
            "kb_name": self.kb_name,
        }

        return RagQueryResult(
            request_id=request_id,
            kb_id=self.kb_id,
            query=query,
            effective_query=effective_query,
            rewritten=rewritten,
            context=context,
            has_context=has_context,
            hit=bool(hit),
            metrics=metrics,
            evidence_documents=evidence_documents,
            evidence_chunks=evidence_chunks,
            error=error,
        )

    def _record_result(
        self,
        result: RagQueryResult,
        *,
        session_id: str,
        source: str,
        tool_name: str | None,
        turn_index: int | None,
    ) -> RagQueryResult:
        """写入rag_context.jsonl"""

        self.store.append_rag_context(
            session_id=session_id,
            record={
                "source": source,
                "tool_name": tool_name,
                "kb_id": self.kb_id,
                "kb_name": self.kb_name,
                "turn_index": turn_index,
                "query": result.query,
                "effective_query": result.effective_query,
                "rewritten": result.rewritten,
                "hit": result.hit,
                "has_context": result.has_context,
                "request_id": result.request_id,
                "metrics": result.metrics,
                "error": asdict(result.error) if result.error is not None else None,
                "context_preview": result.context[:240],
                "evidence_documents": [asdict(document) for document in result.evidence_documents],
                "evidence_chunks": [asdict(chunk) for chunk in result.evidence_chunks],
                "evidence_count": len(result.evidence_chunks),
                "duration": max(
                    0.0,
                    float(result.metrics.get("latency_ms", 0.0)) / 1000.0,
                ),
            },
        )
        return result

    def _build_evidence_chunks(self, raw_chunks: Any) -> list[RagEvidenceChunk]:
        """把 RAG 返回的 chunks 转换成RagEvidenceChunk对象。"""

        if not isinstance(raw_chunks, list):
            return []
        evidence: list[RagEvidenceChunk] = []

        # 最多保留8个片段，避免工具结果过长
        for raw in raw_chunks[:8]:
            if not isinstance(raw, dict):
                continue
            raw_chunk_id = raw.get("chunk_id")
            raw_document_id = raw.get("document_id")
            raw_content = raw.get("content")
            raw_score = raw.get("score")

            chunk_id = str(raw_chunk_id) if raw_chunk_id is not None else None
            document_id = str(raw_document_id) if raw_document_id is not None else None
            content = str(raw_content) if raw_content is not None else None
            score = (
                float(raw_score)
                if (isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool))
                else None
            )

            evidence.append(
                RagEvidenceChunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    content=content,
                    score=score,
                )
            )
        return evidence

    def _build_evidence_documents(
        self,
        raw_references: Any,
        evidence_chunks: list[RagEvidenceChunk],
    ) -> list[RagEvidenceDocument]:
        """聚合文档级证据，优先使用 references，不足时从 chunks 推导。"""

        documents: dict[str, RagEvidenceDocument] = {}

        # 优先读取RAG Core返回的references
        if isinstance(raw_references, list):
            for raw in raw_references:
                if not isinstance(raw, dict):
                    continue

                raw_document_id = raw.get("document_id")
                raw_file_path = raw.get("file_path")
                raw_original_filename = raw.get("original_filename")

                document_id = str(raw_document_id) if raw_document_id is not None else None
                file_path = str(raw_file_path) if raw_file_path is not None else None
                original_filename = (
                    str(raw_original_filename) if raw_original_filename is not None else file_path
                )

                key = document_id or file_path
                if not key:
                    continue
                documents[key] = RagEvidenceDocument(
                    document_id=document_id,
                    file_path=file_path,
                    original_filename=original_filename,
                )

        # references不完整，从chunks补充文档ID
        for chunk in evidence_chunks:
            document_id = str(chunk.document_id or "")
            if not document_id:
                continue

            # 已经从references获得了，不重复添加
            if document_id in documents:
                continue

            documents[document_id] = RagEvidenceDocument(document_id=document_id, file_path=None)

        return list(documents.values())

    def _context_url(self) -> str:
        """返回当前知识库上下文查询地址"""

        return f"{self.settings.base_url.rstrip('/')}/v1/knowledge-bases/{quote(self.kb_id, safe='')}/query/context"

    @staticmethod
    def _elapsed_ms(start: float) -> float:
        """返回从start开始到现在的毫秒数"""

        return round((time.perf_counter() - start) * 1000.0, 1)
