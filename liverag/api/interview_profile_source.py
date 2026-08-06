"""把现有 RagGateway 适配为 Interview Profile Service 的检索来源。
是对 profile_service.py 中retrieve()函数的实现"""

from __future__ import annotations

from typing import Any

from liverag.api.rag_gateway import RagGateway
from liverag.interview.application.profile_service import KnowledgeContext


class RagGatewayProfileSource:
    """通过现有单知识库查询接口读取简历或岗位资料。"""

    def __init__(self, gateway: RagGateway):
        self._gateway = gateway

    async def retrieve(self, *, kb_id: str, query: str) -> KnowledgeContext:
        """检索简历/岗位资料，获取文本+来源"""

        response = await self._gateway.post_json(
            f"/v1/knowledge-bases/{kb_id}/query/context",
            payload={
                "query": query,
                "profile": "default",
                # 画像只需要召回简历/JD原文，不需要走耗时更高的图谱混合查询。
                "mode": "naive",
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
            raise RuntimeError(message or f"知识库读取失败：{kb_id}")

        #获取数据
        data = response.body.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"知识库返回格式不正确：{kb_id}")

        #获取相关内容
        context = str(data.get("context") or "").strip()
        if not context:
            raise ValueError(f"知识库没有可用于准备面试的内容：{kb_id}")

        return KnowledgeContext(
            context=context,
            evidence_refs=tuple(_evidence_refs(data)),
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


__all__ = ["RagGatewayProfileSource"]
