"""Redis 队列与短期协调锁。

Redis 只保存队列与可重建协调状态。权威 Job 状态始终在 PostgreSQL 中。
Redis 重启后：
- 队列中的 job_id 可能丢失 → Worker 兜底扫描 PostgreSQL 的 PENDING Job
- 锁自动过期（TTL） → 不会有死锁
- 已完成任务不受影响（结果在 PostgreSQL）

分布式锁安全机制：
- acquire_lock() 使用 SET NX EX 原子获取，value 为随机 UUID token
- release_lock() 使用 Lua 脚本原子比较 value 后删除，防止误删其他进程的锁
- 仅在 token 匹配时删除，避免"旧任务误删新锁"的并发问题
"""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger("liverag.interview.jobs.queue")

# 原子释放锁的 Lua 脚本：仅当 value 匹配时才删除
_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class RedisQueue:
    """基于 Redis List 的 FIFO 任务队列 + String 短期幂等锁。"""

    # Redis 队列前缀
    _QUEUE_PREFIX = "interview:jobs:"
    # Redis 锁前缀
    _LOCK_PREFIX = "interview:lock:"

    def __init__(
        self,
        redis_client,   #redis
        *,
        lock_ttl_seconds: int = 300,    #TTL超时时间
    ) -> None:
        self._redis = redis_client
        self._lock_ttl = lock_ttl_seconds

    # ====================== 队列操作 =============================

    def _queue_key(self, job_type: str) -> str:
        """根据任务类型生成入队key"""

        return f"{self._QUEUE_PREFIX}{job_type}"

    async def enqueue(self, *, job_type: str, job_id: str) -> None:
        """入队：RPUSH job_id 到对应类型的队列。 """

        await self._redis.rpush(self._queue_key(job_type), job_id)
        logger.debug("Job 入队", extra={"job_type": job_type, "job_id": job_id})

    async def dequeue(self, *, job_type: str, timeout: float = 5.0) -> str | None:
        """出队：BLPOP 阻塞等待，超时返回 None。

        BLPOP 三种情况：
        1.队列有任务 -> 立刻返回{key,value}
        2.队列空 + <=timeout -> worker挂起等着，新任务一来就返回
        3.队列空 + >timeout -> redis不等了，返回空
        """

        result = await self._redis.blpop(
            self._queue_key(job_type), timeout=timeout
        )
        if result is None:
            return None

        # BLPOP 返回 (key, value) 元组
        _, job_id = result
        job_id_str = job_id.decode("utf-8") if isinstance(job_id, bytes) else str(job_id)
        logger.debug("Job 出队", extra={"job_type": job_type, "job_id": job_id_str})
        return job_id_str

    async def queue_length(self, *, job_type: str) -> int:
        """查看队列长度，用于监控。"""

        return await self._redis.llen(self._queue_key(job_type))

    # =========== 幂等锁：防止统一业务资源的任务被重复入队 ============

    def _lock_key(self, job_type: str, resource_id: str) -> str:
        """根据 任务类型+业务资源id 生成幂等锁key"""

        return f"{self._LOCK_PREFIX}{job_type}:{resource_id}"

    async def acquire_lock(
        self,
        *,
        job_type: str,
        resource_id: str,
        ttl: int | None = None
    ) -> str | None:
        """获取幂等锁：SET NX PX，value 为随机 UUID token。

        返回：
        - 获取成功 → 返回 lock_token（UUID 字符串），调用方需保存用于释放
        - 锁已被占用 → 返回 None

        调用方释放时必须传入同一个 lock_token，由 Lua 脚本原子验证后删除，
        防止旧任务在锁过期后误删其他进程新获取的锁。
        """

        lock_token = uuid.uuid4().hex
        acquired = await self._redis.set(
            self._lock_key(job_type, resource_id),
            lock_token,
            nx=True,    # key不存在才写（not exist），确保原子性、互斥
            px=(ttl or self._lock_ttl) * 1000,  # 毫秒，兜底过期
        )
        if acquired:
            logger.debug(
                "获取锁",
                extra={
                    "lock_key": self._lock_key(job_type, resource_id),
                    "ttl": ttl or self._lock_ttl,
                },
            )
            return lock_token
        return None

    async def release_lock(
        self,
        *,
        job_type: str,
        resource_id: str,
        lock_token: str,
    ) -> None:
        """原子释放锁：仅当 Redis 中当前 token 与传入 token 一致时才删除。

        使用 Lua 脚本保证"比较 + 删除"的原子性：
        - token 匹配 → DEL 成功，返回 1
        - token 不匹配（锁已过期被其他进程占用）→ 跳过删除，返回 0
        """

        result = await self._redis.eval(
            _RELEASE_LOCK_SCRIPT,
            1,
            self._lock_key(job_type, resource_id),
            lock_token,
        )
        if result == 1:
            logger.debug(
                "释放锁",
                extra={"lock_key": self._lock_key(job_type, resource_id)},
            )
        else:
            logger.debug(
                "释放锁跳过（token 不匹配，锁可能已过期被其他进程占用）",
                extra={"lock_key": self._lock_key(job_type, resource_id)},
            )

    async def lock_exists(self, *, job_type: str, resource_id: str) -> bool:
        """检查锁是否存在。"""

        return bool(await self._redis.exists(self._lock_key(job_type, resource_id)))


__all__ = ["RedisQueue"]
