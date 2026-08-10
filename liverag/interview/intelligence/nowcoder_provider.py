"""牛客 Spider Provider，将当前的 MCP Tool 适配成 Interview Coach 需要的业务能力

实现 InterviewIntelligenceProvider 协议，将业务查询适配为确定性搜索关键词
通过 MCP Client 调用 NowcoderSpider，
并将 MCP 返回的原始帖子映射为领域模型 RawInterviewExperience。
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

from liverag.interview.intelligence.mcp.mcp_client import McpNowcoderClient
from liverag.interview.intelligence.provider import (
    InterviewIntelligenceQuery,
    InterviewRound,
    ProviderSearchResult,
    RawInterviewExperience,
)

# ====================== 轮次映射 ======================

_ROUND_LABEL: dict[InterviewRound, str] = {
    InterviewRound.FIRST: "一面",
    InterviewRound.SECOND: "二面",
    InterviewRound.THIRD: "三面",
    InterviewRound.FINAL: "终面",
    InterviewRound.HR: "HR面",
}

def _round_cn(interview_round: InterviewRound | None) -> str | None:
    """将枚举轮次转中文标签，None 返回 None。"""
    if interview_round is None:
        return None
    return _ROUND_LABEL.get(interview_round)


# ====================== 确定性搜索关键词构造 ======================

def _build_queries(query: InterviewIntelligenceQuery) -> list[str]:
    """根据业务查询构造确定性搜索关键词列表。

    同一输入永远产生相同关键词集合。不使用 LLM 动态生成。
    """

    #获得公司名+岗位+地区+轮次
    company = query.company.strip()
    role = query.role.strip()
    region = (query.region or "").strip()
    round_label = _round_cn(query.interview_round)

    queries: list[str] = []

    #基础组合：公司 + 岗位 + 面经（始终生成）
    base = f"{company} {role}"
    queries.append(f"{base} 面经")

    #轮次组合
    if round_label:
        queries.append(f"{base} {round_label}")

    #地区组合
    if region:
        queries.append(f"{company} {region} {role} 面经")
        if round_label:
            queries.append(f"{company} {region} {role} {round_label}")

    return queries


# ====================== 字段映射 ======================

def _compute_content_hash(title: str, content: str) -> str:
    """计算内容指纹：SHA-256(normalized title + content)。"""
    normalized = f"{title.strip()}\n{content.strip()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _to_raw_experience(item, provider: str, source: str) -> RawInterviewExperience:
    """将 MCP 返回的 NowcoderPostItem 映射为领域模型 RawInterviewExperience。"""
    now = datetime.now(timezone.utc)
    return RawInterviewExperience(
        provider=provider,
        source=source,
        source_id=item.source_id,
        source_type=item.source_type,
        title=item.title,
        content=item.content,
        source_url=item.url,
        matched_query=item.matched_query,
        retrieved_at=now,
        content_hash=_compute_content_hash(item.title, item.content),
    )


# ====================== Provider ======================

class NowcoderSpiderProvider:
    """牛客 Spider Provider，实现 InterviewIntelligenceProvider 协议（provider.InterviewIntelligenceProvider）

    通过 MCP stdio Client 调用 NowcoderSpider，将结果映射为领域模型。
    """

    def __init__(self, timeout: float = 30) -> None:
        self._client = McpNowcoderClient(timeout=timeout)

    async def search_experiences(
        self,
        query: InterviewIntelligenceQuery,
    ) -> ProviderSearchResult:
        """根据业务查询搜索牛客面经。

        Args:
            query: 业务查询条件。
        Returns:
            ProviderSearchResult 包含 RawInterviewExperience 列表及统计信息。
        """

        provider = "community_nowcoder_spider"
        source = "nowcoder"

        #记录开始时间
        start = time.monotonic()

        #构造确定性搜索关键词
        queries = _build_queries(query)

        #通过 MCP Client 调用 Spider
        mcp_result = await self._client.search(
            queries=queries,
            max_results=query.limit,
        )

        #记录总耗时
        latency_ms = (time.monotonic() - start) * 1000

        #逐条映射 RawNowcoderPost → RawInterviewExperience
        items = [
            _to_raw_experience(item, provider=provider, source=source)
            for item in mcp_result.items
        ]

        return ProviderSearchResult(
            items=items,
            provider=provider,
            fetched_at=datetime.now(timezone.utc),
            latency_ms=round(latency_ms, 1),
            discovered_count=mcp_result.discovered_count,
            collected_count=mcp_result.collected_count,
            failed_count=mcp_result.failed_count,
        )


__all__ = [
    "NowcoderSpiderProvider",
    "_build_queries",
    "_compute_content_hash",
    "_to_raw_experience",
]
