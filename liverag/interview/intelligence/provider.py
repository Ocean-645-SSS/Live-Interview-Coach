"""Interview Intelligence 领域契约。

定义 Provider 接口、数据模型和错误类型。业务层只依赖本模块定义的
Protocol 和 Schema，不直接耦合具体 Provider 实现（MCP Client、Spider 等）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from pydantic import Field

from liverag.interview.schemas import StrictModel


class InterviewRound(str, Enum):
    """面试轮次。
    不能识别的轮次使用 None，不强行推断。
    """

    FIRST = "first"  # 一面
    SECOND = "second"  # 二面
    THIRD = "third"  # 三面
    FINAL = "final"  # 终面
    HR = "hr"  # HR 面


class ProviderErrorCode(str, Enum):
    """Provider 统一错误码。

    屏蔽 MCP exception、subprocess exception、HTTP Spider exception、
    Pydantic validation error 等底层实现细节。上层只处理 ProviderError。
    """

    TIMEOUT = "TIMEOUT"  # 超时
    UNAVAILABLE = "UNAVAILABLE"  # Provider 整体不可用
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"  # MCP Server 没有预期 Tool
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"  # 接口契约和代码不一致
    INVALID_RESPONSE = "INVALID_RESPONSE"  # 返回数据不合法
    RATE_LIMITED = "RATE_LIMITED"  # 被限流
    NO_USABLE_DATA = "NO_USABLE_DATA"  # 没有可用数据


# ====================== 查询模型 ======================


class InterviewIntelligenceQuery(StrictModel):
    """业务希望查询什么面经"""

    company: str
    role: str
    region: str | None = None
    interview_round: InterviewRound | None = None
    limit: int = Field(default=10, ge=1, le=20, description="期望获取的面经数量，默认 10，最多 20")


# ====================== 原始面经数据 ======================

class RawInterviewExperience(StrictModel):
    """Provider 真正获得的原始面经。"""

    provider: str = ""  #默认community_nowcoder_spider
    source: str = ""    #默认nowcoder
    source_id: str = "" #牛客 uuid+content_id
    source_type: str = ""   #feed/discuss
    title: str = ""
    content: str = ""
    source_url: str = ""
    matched_query: str = ""
    published_at: datetime | None = None    #无法获取语义默认None
    retrieved_at: datetime | None = None
    content_hash: str = ""  #normalize(title + content) 后计算 SHA-256


# ====================== 标准化面经 ======================

class NormalizedInterviewExperience(StrictModel):
    """经过 Interview Coach 理解后的业务数据。

    注意：topics、questions、interview_round 属于 Normalizer / Extractor
    的产物，不再宣称是 Provider 原始字段。
    该模型不再携带完整 content，避免后续 Planner 意外接触第三方原文。
    """

    provider: str = ""
    source: str = ""
    source_id: str = ""
    source_url: str = ""
    company: str = ""
    role: str = ""
    region: str = ""
    interview_round: InterviewRound | None = None
    topics: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    content_hash: str = ""


# ====================== Provider 返回 envelope ======================

class ProviderSearchResult(StrictModel):
    """Provider 的统一返回 Envelope。

    部分失败时不丢弃已经获得的有效数据
    """

    items: list[RawInterviewExperience] = Field(default_factory=list)
    provider: str = ""
    fetched_at: datetime | None = None  #生成时间
    latency_ms: float = 0.0 #查询耗时
    discovered_count: int = 0   #总共查询个数
    collected_count: int = 0    #有效查询个数
    failed_count: int = 0   #查询失败数量

    @property
    def partial(self) -> bool:
        """部分失败：有失败但仍有成功数据可消费。"""
        return self.failed_count > 0 and self.collected_count > 0


# ====================== Provider 接口 ======================

class InterviewIntelligenceProvider(Protocol):
    """业务层只依赖此 Protocol，不直接耦合具体 Provider 实现。

    具体实现由 NowcoderSpiderProvider（nowcoder_provider.py）提供，
    通过 MCP stdio Client 调用牛客 Spider。
    """

    async def search_experiences(
        self,
        query: InterviewIntelligenceQuery,
    ) -> ProviderSearchResult:
        """根据业务查询搜索面经。"""
        ...


# ====================== Provider 能力描述 ======================

@dataclass(frozen=True, slots=True)
class ProviderCapability:
    """Provider 的静态能力描述。

    只记录 Provider 的静态能力，不为未来未知 Provider 设计复杂
    capability framework。V1 仅 community_nowcoder_spider。
    """

    provider_name: str  # e.g. "community_nowcoder_spider"
    transport: str  # V1: "stdio"
    tool_name: str  # V1: "search_nowcoder_experiences"
    schema_version: int = 1
    supports_partial_results: bool = False


# ====================== Provider 错误 ======================

class ProviderError(Exception):
    """统一的 Provider 异常。

    可降级错误码：TIMEOUT / UNAVAILABLE / TOOL_NOT_FOUND /
    CONTRACT_MISMATCH / INVALID_RESPONSE / RATE_LIMITED / NO_USABLE_DATA
    """

    def __init__(
        self,
        code: ProviderErrorCode,
        provider: str = "",
        message: str = "",
        retryable: bool = False,    #是否可重试
    ) -> None:
        self.code = code
        self.provider = provider
        self.message = message
        self.retryable = retryable
        super().__init__(str(self))

    def __str__(self) -> str:
        return (
            f"ProviderError(code={self.code.value}, "
            f"provider={self.provider!r}, "
            f"message={self.message!r}, "
            f"retryable={self.retryable})"
        )

    def __repr__(self) -> str:
        return str(self)


# ====================== 聚合结果模型 ======================

class TopicFrequency(StrictModel):
    """单个技术主题的统计频次。"""

    topic: str = ""
    count: int = 0
    ratio: float = 0.0


class EvidenceRef(StrictModel):
    """面经数据来源标识，不嵌入全文。"""

    provider: str = ""
    source_id: str = ""
    content_hash: str = ""


class RoundPattern(StrictModel):
    """单个面试轮次的考察模式。"""

    round: InterviewRound | None = None
    common_topics: list[str] = Field(default_factory=list)
    common_questions: list[str] = Field(default_factory=list)
    description: str = ""


class CompanyInterviewProfile(StrictModel):
    """公司面试情报画像 — 基于外部面经来 源归纳的面试参考信息。

    由 Aggregator从 NormalizedInterviewExperience[]聚合生成。
    为 None 时 Planner 正常降级工作，不影响 Plan 生成。
    """

    company: str = ""
    role: str = ""
    region: str = ""

    # 样本统计
    sample_count: int = 0
    usable_sample_count: int = 0

    # 高频主题
    top_topics: list[TopicFrequency] = Field(default_factory=list)

    # 代表性题目（有限数量，不全量塞给 Planner）
    representative_questions: list[str] = Field(default_factory=list)

    # 不同轮次考察模式
    round_patterns: list[RoundPattern] = Field(default_factory=list)

    # 来源证据（只保存 provider + source_id + content_hash）
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    # 生成元数据
    generated_at: datetime | None = None
    snapshot_hash: str = ""

    @property
    def frequent_topics(self) -> list[str]:
        """高频考察主题名（纯字符串列表，供 Planner 使用）。"""
        return [t.topic for t in self.top_topics]


# ====================== Service 返回模型 ======================

class IntelligenceStatus(str, Enum):
    """Intelligence Service 的状态枚举。"""

    FRESH = "FRESH"  # provider 成功获取并生成新 profile
    DISABLED = "DISABLED"  # feature flag 未开启
    SKIPPED = "SKIPPED"  # 无 company 等不满足查询条件
    CACHE_HIT = "CACHE_HIT"  # fresh cache 直接命中
    PARTIAL = "PARTIAL"  # provider 部分失败，但有效数据足够生成 profile
    STALE_FALLBACK = "STALE_FALLBACK"  # provider 失败，使用过期缓存
    DEGRADED = "DEGRADED"  # provider 失败且无可用缓存


class IntelligenceEnrichmentResult(StrictModel):
    """Intelligence Service 的统一返回 envelope。"""

    status: IntelligenceStatus
    profile: CompanyInterviewProfile | None = None
    provider: str | None = None
    degraded: bool = False  # 是否降级
    degradation_reasons: list[str] = Field(default_factory=list)  # 降级理由
    snapshot_hash: str | None = None
    cache_age_seconds: int | None = None  # 缓存年龄（秒），仅 CACHE_HIT / STALE_FALLBACK 时有值


__all__ = [
    "CompanyInterviewProfile",
    "EvidenceRef",
    "IntelligenceEnrichmentResult",
    "IntelligenceStatus",
    "InterviewIntelligenceProvider",
    "InterviewIntelligenceQuery",
    "InterviewRound",
    "NormalizedInterviewExperience",
    "ProviderCapability",
    "ProviderError",
    "ProviderErrorCode",
    "ProviderSearchResult",
    "RawInterviewExperience",
    "RoundPattern",
    "TopicFrequency",
]
