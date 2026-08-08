"""Interview Intelligence Service — 业务编排入口。

Preparation Workflow 只调用：
    await intelligence_service.get_company_profile(query)

Service 内部管理：
- Feature Flag 检查
- Cache（Redis fresh/stale）
- Provider → Normalizer → Extractor → Aggregator 全链路
- 降级策略（STALE_FALLBACK / DEGRADED）

安全边界（3.3.10）：
- 日志只记录 metadata（status / count / hash / error_code），禁止记录帖子全文
- 第三方内容经 sanitize_pii 脱敏后才进入 Extractor
- Raw content 不离开 Normalizer → Extractor → Aggregator 管道
- CompanyInterviewProfile 不含 PII、不含原始正文
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from liverag.interview.intelligence.aggregator import aggregate
from liverag.interview.intelligence.cache import (
    IntelligenceCache,
    build_cache_key,
)

import redis.asyncio as _aredis

from liverag.interview.intelligence.extractor import (
    ExperienceExtractor,
    extract_batch as extract_batch_rule,
)
from liverag.interview.intelligence.normalizer import (
    normalize_batch,
    normalize_company,
    normalize_region,
    normalize_role,
)
from liverag.interview.intelligence.provider import (
    IntelligenceEnrichmentResult,
    IntelligenceStatus,
    InterviewIntelligenceProvider,
    InterviewIntelligenceQuery,
    ProviderError,
    ProviderSearchResult,
)

logger = logging.getLogger(__name__)


# ====================== Service 配置 ======================

@dataclass
class IntelligenceServiceConfig:
    """Intelligence Service 配置。"""

    enabled: bool = False  # 是否开启该业务：INTERVIEW_INTELLIGENCE_ENABLED
    fresh_ttl_seconds: int = 3600  # 1 小时内视为 fresh
    stale_ttl_seconds: int = 86400  # 24 小时后视为彻底过期stale


# ====================== Service ======================

class IntelligenceService:
    """Company Intelligence 业务入口。

    使用方式:
        redis_conn = aredis.from_url("redis://127.0.0.1:6379/0")
        provider = NowcoderSpiderProvider()
        config = IntelligenceServiceConfig(enabled=True)
        service = IntelligenceService(
            redis_client=redis_conn,
            provider=provider,
            config=config,
        )
        result = await service.get_company_profile(query)
    """

    def __init__(
        self,
        redis_client: _aredis.Redis,
        provider: InterviewIntelligenceProvider,
        config: IntelligenceServiceConfig | None = None,
        *,
        extractor: ExperienceExtractor | None = None,
    ) -> None:
        self._provider = provider
        self._config = config or IntelligenceServiceConfig()
        self._extractor = extractor  # None 则使用纯规则提取
        self._cache = IntelligenceCache(
            redis_client,
            fresh_ttl_seconds=self._config.fresh_ttl_seconds,
            stale_ttl_seconds=self._config.stale_ttl_seconds,
        )

    # ---- 公开 API ----
    async def get_company_profile(
        self,
        query: InterviewIntelligenceQuery,
    ) -> IntelligenceEnrichmentResult:
        """获取公司面试情报画像。

        编排流程：
        1. Feature flag / query 检查
        2. 读缓存 → fresh 命中直接 CACHE_HIT
        3. 缓存未命中 → Provider 搜索
        4. Normalizer → Extractor → Aggregator → 写缓存 → FRESH / PARTIAL
        5. Provider 失败 → stale cache 降级 → STALE_FALLBACK / DEGRADED
        """

        # 1. Feature flag
        if not self._config.enabled:
            logger.info("Intelligence Service: DISABLED")
            return IntelligenceEnrichmentResult(
                status=IntelligenceStatus.DISABLED,
            )

        # 2. Query 检查 — 缺 company 直接 SKIPPED
        company = (query.company or "").strip()
        if not company:
            logger.info("Intelligence Service: SKIPPED (无 company)")
            return IntelligenceEnrichmentResult(
                status=IntelligenceStatus.SKIPPED,
                degradation_reasons=["COMPANY_NOT_PROVIDED"],
            )

        # 标准化查询参数
        norm_company = normalize_company(company)
        norm_role = normalize_role(query.role or "")
        norm_region = normalize_region(query.region or "")
        provider_name = "community_nowcoder_spider"

        # 3. 先读缓存
        cache_key = build_cache_key(
            provider=provider_name,
            company=norm_company,
            role=norm_role,
            region=norm_region,
            interview_round=None,  # 不按轮次细分缓存
        )

        cached = await self._cache.get(cache_key)
        if cached is not None and cached.is_fresh:
            cache_age = int(
                datetime.now(timezone.utc).timestamp()
                - cached.fetched_at_timestamp
            )
            logger.info("CACHE_HIT: age=%ds", cache_age)
            return IntelligenceEnrichmentResult(
                status=IntelligenceStatus.CACHE_HIT,
                profile=cached.profile,
                provider=cached.provider,
                snapshot_hash=cached.snapshot_hash,
                cache_age_seconds=cache_age,
            )

        # 4. 缓存未命中 → 构造标准化 query → Provider 搜索
        normalized_query = InterviewIntelligenceQuery(
            company=norm_company,
            role=norm_role,
            region=norm_region,
            interview_round=query.interview_round,
            limit=query.limit,
        )

        provider_error: ProviderError | None = None
        try:
            search_result = await self._provider.search_experiences(normalized_query)
            provider_name = search_result.provider
        except ProviderError as exc:
            logger.warning("Provider 失败: %s", exc)
            provider_error = exc
            search_result = ProviderSearchResult()

        # 5. 有数据 → 处理 + 写缓存
        if search_result.items:
            return await self._process_and_cache(
                result=search_result,
                company=norm_company,
                role=norm_role,
                region=norm_region,
            )

        # 6. 无数据 → 降级
        error_code = provider_error.code.value if provider_error else "NO_USABLE_DATA"

        if cached is not None:
            cache_age = int(
                datetime.now(timezone.utc).timestamp()
                - cached.fetched_at_timestamp
            )
            logger.info("STALE_FALLBACK: %s, age=%ds", error_code, cache_age)
            return IntelligenceEnrichmentResult(
                status=IntelligenceStatus.STALE_FALLBACK,
                profile=cached.profile,
                provider=cached.provider,
                degraded=True,
                degradation_reasons=[
                    f"Provider {error_code}，使用过期缓存 (age={cache_age}s)"
                ],
                snapshot_hash=cached.snapshot_hash,
                cache_age_seconds=cache_age,
            )

        # 7. 彻底无数据
        logger.warning("DEGRADED: Provider %s 且无可用缓存", error_code)
        return IntelligenceEnrichmentResult(
            status=IntelligenceStatus.DEGRADED,
            degraded=True,
            degradation_reasons=[f"Provider {error_code}, 无缓存"],
        )

    # ---- 内部流程 ----

    async def _process_and_cache(
        self,
        result: ProviderSearchResult,
        company: str,
        role: str,
        region: str,
    ) -> IntelligenceEnrichmentResult:
        """处理 Provider 数据：Normalizer → Extractor → Aggregator → Cache。"""

        degradation_reasons: list[str] = []

        # 1. Normalizer — 清洗 + URL 去重
        normed = normalize_batch(result.items)

        # 2. Extractor — LLM 优先，规则降级
        if self._extractor is not None:
            extracted = await self._extractor.extract_batch(
                normed, company=company, role=role, region=region,
            )
        else:
            extracted = extract_batch_rule(
                normed, company=company, role=role, region=region,
            )

        # 3. Aggregator
        profile = aggregate(
            extracted,
            company=company,
            role=role,
            region=region,
        )

        # 4. 判断状态
        if result.partial:
            status = IntelligenceStatus.PARTIAL
            degradation_reasons.append(
                f"Provider 部分失败: collected={result.collected_count}, "
                f"failed={result.failed_count}"
            )
        else:
            status = IntelligenceStatus.FRESH

        # 5. 写缓存
        cache_key = build_cache_key(
            provider=result.provider,
            company=company,
            role=role,
            region=region,
            interview_round=None,  # 不按轮次细分缓存
        )
        await self._cache.set(
            cache_key,
            profile=profile,
            provider=result.provider,
        )

        logger.info(
            "Intelligence Service OK: status=%s, company=%s, role=%s, "
            "samples=%d, topics=%d",
            status.value, company, role, profile.sample_count,
            len(profile.top_topics),
        )

        return IntelligenceEnrichmentResult(
            status=status,
            profile=profile,
            provider=result.provider,
            degraded=bool(degradation_reasons),
            degradation_reasons=degradation_reasons,
            snapshot_hash=profile.snapshot_hash,
        )



# ====================== 导出 ======================

__all__ = [
    "IntelligenceService",
    "IntelligenceServiceConfig",
]
