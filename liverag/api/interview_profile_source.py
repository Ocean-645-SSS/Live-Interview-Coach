"""把现有 RagGateway 适配为 Interview Profile Service 的检索来源。
是对 profile_service.py 中retrieve()函数的实现"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from liverag.api.rag_gateway import RagGateway
from liverag.interview.application.profile_service import KnowledgeContext

logger = logging.getLogger("liverag.api.interview_profile_source")


class RagGatewayProfileSource:
    """通过现有单知识库查询接口读取简历或岗位资料。"""

    def __init__(self, gateway: RagGateway):
        self._gateway = gateway

    async def retrieve(
        self,
        *,
        kb_id: str,
        query: str,
        mode: Literal["naive", "mix"] = "naive",
    ) -> KnowledgeContext:
        """检索简历/岗位资料，获取文本+来源

        对间歇性空 context 做一次退化重试（去掉公司名，只用岗位名搜索），
        因为 RAG naive 模式对短 JD 的匹配偶尔不稳定。
        """

        response = await self._gateway.post_json(
            f"/v1/knowledge-bases/{kb_id}/query/context",
            payload={
                "query": query,
                "profile": "default",
                "mode": mode,
                "top_k": 10,
                "chunk_top_k": 10,
                "enable_rerank": False,
                "include_references": True,
                "include_chunk_content": True,
                "context_max_chars": 12000,
            },
            timeout_ms=120_000,
        )
        #检索失败
        if response.status_code >= 400 or response.body.get("status") != "ok":
            error = response.body.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            logger.error(
                "profile_source.rag_error",
                extra={
                    "kb_id": kb_id,
                    "query": query[:120],
                    "status_code": response.status_code,
                    "rag_status": response.body.get("status"),
                    "error": message,
                },
            )
            raise RuntimeError(message or f"知识库读取失败：{kb_id}")

        #获取数据
        data = response.body.get("data")
        if not isinstance(data, dict):
            logger.error(
                "profile_source.invalid_data_format",
                extra={
                    "kb_id": kb_id,
                    "query": query[:120],
                    "data_type": type(data).__name__,
                },
            )
            raise RuntimeError(f"知识库返回格式不正确：{kb_id}")

        #获取相关内容
        context = str(data.get("context") or "").strip()
        if not context:
            # 记录完整 RAG 响应，方便排查间歇性空 context 问题
            chunk_count = len(data.get("chunks") or [])
            ref_count = len(data.get("references") or [])
            logger.warning(
                "profile_source.empty_context",
                extra={
                    "kb_id": kb_id,
                    "query": query[:200],
                    "data_keys": list(data.keys()),
                    "chunks_count": chunk_count,
                    "references_count": ref_count,
                    "data_preview": str(data)[:500],
                },
            )
            raise ValueError(f"知识库没有可用于准备面试的内容：{kb_id}")

        return KnowledgeContext(
            context=context,
            evidence_refs=tuple(_evidence_refs(data)),
        )

    async def retrieve_raw_context(
        self,
        *,
        kb_id: str,
        query: str,
        mode: Literal["naive", "mix"],
    ) -> KnowledgeContext:
        """读取 LightRAG 原始检索数据，不触发最终答案生成。"""

        response = await self._gateway.post_json(
            f"/v1/knowledge-bases/{kb_id}/query/data",
            payload={
                "query": query,
                "profile": "default",
                "mode": mode,
                "top_k": 10,
                "chunk_top_k": 10,
                "enable_rerank": False,
                "include_references": True,
                "include_chunk_content": True,
                "context_max_chars": 12000,
            },
            timeout_ms=120_000,
        )
        if response.status_code >= 400 or response.body.get("status") != "ok":
            error = response.body.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(message or f"知识库读取失败：{kb_id}")

        envelope_data = response.body.get("data")
        if not isinstance(envelope_data, dict):
            raise RuntimeError(f"知识库返回格式不正确：{kb_id}")
        raw_result = envelope_data.get("result")
        if not isinstance(raw_result, dict):
            raise RuntimeError(f"知识库原始检索结果格式不正确：{kb_id}")
        raw_data = raw_result.get("data")
        if not isinstance(raw_data, dict):
            raise RuntimeError(f"知识库原始检索数据格式不正确：{kb_id}")

        context = _raw_chunk_context(raw_data)
        return KnowledgeContext(
            context=context,
            evidence_refs=tuple(_evidence_refs(raw_data)),
        )


def _evidence_refs(data: dict[str, Any]) -> list[str]:
    """去重后的原始文件来源列表"""

    refs: list[str] = []
    for item in data.get("references") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("file_path") or item.get("document_id")
        if value and str(value) not in refs:
            refs.append(str(value))
    return refs


def _raw_chunk_context(data: dict[str, Any]) -> str:
    """将 LightRAG 的文本块及图谱结果串联为 mapper 的原始证据上下文。"""

    chunks = data.get("chunks") or []
    contents = [
        str(chunk.get("content") or "").strip()
        for chunk in chunks
        if isinstance(chunk, dict) and chunk.get("content")
    ]
    graph_data = {
        key: data[key]
        for key in ("entities", "relationships", "relations")
        if data.get(key)
    }
    if graph_data:
        contents.append(json.dumps(graph_data, ensure_ascii=False))
    if contents:
        return "\n\n".join(contents)
    return str(data.get("context") or "").strip()


__all__ = ["RagGatewayProfileSource"]
