"""Interview Intelligence Redis 缓存层。

生命周期：
【FRESH】新鲜缓存：0 ~ fresh_ttl_second
【STALE】过期但还能救急：fresh_ttl_seconds ~ stale_ttl_seconds
【EXPIRED】彻底不可用：>stale_ttl_seconds

两级过期策略：
- fresh_until（默认 1h）：数据仍新鲜，可直接返回 CACHE_HIT
- stale_until（默认 24h）：数据已过期但可降级使用 STALE_FALLBACK
- 超出 stale_until：彻底过期，Redis TTL 自动删除

Cache Key 基于规范化参数构造 canonical JSON 再 SHA-256，不直接拼接长字符串。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as _aredis

from liverag.interview.intelligence.provider import CompanyInterviewProfile

logger = logging.getLogger(__name__)

# ====================== 版本常量 ======================

_SCHEMA_VERSION = 1
_ADAPTER_VERSION = 1

_CACHE_PREFIX = "interview:intelligence:v1:"    #cache前缀


# ====================== Cache Key ======================

def build_cache_key(
    provider: str,
    company: str,
    role: str,
    region: str,
    interview_round: str | None,
) -> str:
    """基于规范化参数构造缓存 key。

    不直接把很长的 company/role 字符串拼成 Redis Key，而是先构造canonical JSON 再 SHA-256。
    """

    canonical: dict[str, object] = {
        "provider": provider,
        "company": company,
        "role": role,
        "region": region,
        "interview_round": interview_round,
        "schema_version": _SCHEMA_VERSION,
        "adapter_version": _ADAPTER_VERSION,
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{_CACHE_PREFIX}{fingerprint}"


# ====================== Cache Result ======================

@dataclass
class CacheResult:
    """从缓存读取的结果。"""

    profile: CompanyInterviewProfile
    provider: str
    snapshot_hash: str  #CompanyInterviewProfile 聚合快照的指纹
    fetched_at_timestamp: float
    is_fresh: bool  # True = 在 fresh_until 内, False = stale 但仍可用


# ====================== Cache ======================

class IntelligenceCache:
    """面试情报 Redis 缓存。

    使用方式:
        cache = IntelligenceCache(redis_client, fresh_ttl_seconds=3600, stale_ttl_seconds=86400)
        key = build_cache_key(...)
        result = await cache.get(key)
        if result is None:
            await cache.set(key, profile, provider="community_nowcoder_spider")
    """

    def __init__(
        self,
        redis_client: _aredis.Redis,
        *,
        fresh_ttl_seconds: int = 3600,
        stale_ttl_seconds: int = 86400,
    ) -> None:
        self._redis = redis_client
        self._fresh_ttl = fresh_ttl_seconds
        self._stale_ttl = stale_ttl_seconds

    # ---- 读 ----
    async def get(self, cache_key: str) -> CacheResult | None:
        """读取缓存 envelope 并检查 fresh/stale 状态：

        Redis里的JSON -> 解析 -> CompanyInterviewProfile -> 判断fresh/stale -> CacheResult
        """

        #redis存的JSON元数据
        raw = await self._redis.get(cache_key)
        if raw is None:
            return None

        try:
            raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            #解析出envelope
            envelope: dict = json.loads(raw_str)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("缓存 JSON 解析失败, key=%s", cache_key)
            return None

        #现在时间
        now_ts = datetime.now(timezone.utc).timestamp()
        #过期时间
        stale_until = envelope.get("stale_until", 0)
        #如果彻底过期，返回空
        if now_ts > stale_until:
            logger.debug("缓存已过期, key=%s", cache_key)
            return None

        #反序列化envelope -> profile
        profile_json = envelope.get("profile", "")
        if not profile_json:
            return None

        try:
            profile = CompanyInterviewProfile.model_validate_json(profile_json)
        except Exception:
            logger.warning("缓存 profile 反序列化失败", exc_info=True)
            return None

        #解析 fetched_at
        fetched_at_str = envelope.get("fetched_at", "")
        try:
            fetched_at_ts = datetime.fromisoformat(fetched_at_str).timestamp()
        except (ValueError, TypeError):
            fetched_at_ts = envelope.get("fresh_until", now_ts) - self._fresh_ttl

        fresh_until = envelope.get("fresh_until", 0)

        return CacheResult(
            profile=profile,
            provider=envelope.get("provider", ""),
            snapshot_hash=envelope.get("snapshot_hash", ""),
            fetched_at_timestamp=fetched_at_ts,
            is_fresh=now_ts <= fresh_until,
        )

    # ---- 写 ----
    async def set(
        self,
        cache_key: str,
        profile: CompanyInterviewProfile,
        provider: str,
    ) -> None:
        """写入缓存 envelope：
        CompanyInterviewProfile -> 转成 JSON -> 塞进 Redis

        TTL = stale_ttl_seconds，过期后 Redis 自动删除。
        """

        now = datetime.now(timezone.utc)
        envelope: dict[str, Any] = {
            "profile": profile.model_dump_json(),
            "provider": provider,
            "fetched_at": now.isoformat(),  #捕获事件
            "fresh_until": now.timestamp() + self._fresh_ttl,   #不新鲜时间
            "stale_until": now.timestamp() + self._stale_ttl,   #过期时间
            "snapshot_hash": profile.snapshot_hash,
        }

        #SET key value EX TTL_seconds
        await self._redis.set(
            cache_key,  #key
            json.dumps(envelope, ensure_ascii=False),   #value
            ex=self._stale_ttl, #过期时间
        )
        logger.debug("缓存写入成功, key=%s", cache_key)


    # ---- touch：延长 FRESH TTL ----
    async def touch(self, cache_key: str) -> bool:
        """不替换数据，仅将 FRESH 窗口延长一个 fresh_ttl 周期。

        用于：已成功获取但希望在后台预热中保持"新鲜"状态时调用。
        返回 True 表示缓存存在且已续期，False 表示缓存不存在或已彻底过期。

        仅修改 fresh_until，stale_until 和 envelope 内容不变。
        """
        raw = await self._redis.get(cache_key)
        if raw is None:
            return False

        try:
            raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            envelope: dict = json.loads(raw_str)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False

        now_ts = datetime.now(timezone.utc).timestamp()
        stale_until = envelope.get("stale_until", 0)

        if now_ts > stale_until:
            # 已彻底过期，touch 无意义
            return False

        # 延长 fresh_until 但不改变 stale_until
        envelope["fresh_until"] = now_ts + self._fresh_ttl

        # STL 基于当前 envelope 的 stale_until 重新计算（touch 不改变总 stale 周期）
        remaining_ttl = max(1, int(stale_until - now_ts))
        await self._redis.set(
            cache_key,
            json.dumps(envelope, ensure_ascii=False),
            ex=remaining_ttl,
        )
        logger.debug("缓存 FRESH 窗口已续期, key=%s", cache_key)
        return True


__all__ = [
    "CacheResult",
    "IntelligenceCache",
    "build_cache_key",
]
