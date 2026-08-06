"""Redis 队列与短期协调锁。

Redis 只保存队列与可重建协调状态。权威 Job 状态始终在 PostgreSQL 中。
Redis 重启后：
- 队列中的 job_id 可能丢失 → Worker 兜底扫描 PostgreSQL 的 PENDING Job
- 锁自动过期（TTL） → 不会有死锁
- 已完成任务不受影响（结果在 PostgreSQL）
"""

from __future__ import annotations

import logging

logger = logging.getLogger("liverag.interview.jobs.queue")


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
    ) -> bool:
        """获取幂等锁：SET NX PX，返回是否获取成功。"""

        #相当于：set key value NX PX 300000 
        acquired = await self._redis.set(
            self._lock_key(job_type, resource_id),  #锁的key
            "1",    #锁的value（只是占位）
            nx=True,    #key不存在才写（not exist），确保原子性、互斥
            px=(ttl or self._lock_ttl) * 1000,  #5分钟后销毁，作为兜底
        )
        #写成功了没
        return bool(acquired)

    async def release_lock(self, *, job_type: str, resource_id: str) -> None:
        """主动释放锁。"""

        await self._redis.delete(self._lock_key(job_type, resource_id))

    async def lock_exists(self, *, job_type: str, resource_id: str) -> bool:
        """检查锁是否存在。"""
        
        return bool(await self._redis.exists(self._lock_key(job_type, resource_id)))


__all__ = ["RedisQueue"]
