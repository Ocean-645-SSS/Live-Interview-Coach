"""第三步 3.1：Background Job 系统测试。

覆盖：
- BackgroundJobModel ORM 建表与 CRUD
- JobRepository 创建、查询、状态更新
- RedisQueue 入队、出队、锁（fakeredis）
- BackgroundWorker 主循环 + Demo 任务端到端
- 幂等约束（唯一键防重）
- 重试与失败状态
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from liverag.interview.jobs.queue import RedisQueue
from liverag.interview.jobs.repository import JobRepository
from liverag.interview.jobs.tasks import get_handler, registered_types
from liverag.interview.jobs.worker import BackgroundWorker
from liverag.interview.persistence.db import create_session_factory, create_sqlite_engine
from liverag.interview.persistence.models import Base
from liverag.interview.records import BackgroundJobRecord, JobStatus, generate_id


# ── Fixtures ─────────────────────────────────────────────


@pytest.fixture(scope="function")
def engine():
    """每次测试创建独立的 SQLite 内存数据库。"""
    db_path = Path(f"test_jobs_{generate_id('db')}.db")
    eng = create_sqlite_engine(db_path)
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()
        try:
            db_path.unlink(missing_ok=True)
        except PermissionError:
            pass


@pytest.fixture(scope="function")
def job_repo(engine):
    """创建基于内存 SQLite 的 JobRepository。"""
    factory = create_session_factory(engine)
    return JobRepository(factory)


@pytest.fixture(scope="function")
async def redis_queue():
    """使用 fakeredis 模拟 Redis。"""
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis()
    queue = RedisQueue(client, lock_ttl_seconds=10)
    try:
        yield queue
    finally:
        await client.aclose()


@pytest.fixture(scope="function")
def worker(job_repo, redis_queue):
    """创建 BackgroundWorker 实例。"""
    return BackgroundWorker(
        job_repo=job_repo,
        redis_queue=redis_queue,
        poll_timeout=1.0,
        task_timeout=10.0,
        max_retries=2,
    )


# ── 模型测试 ─────────────────────────────────────────────


class TestBackgroundJobModel:
    def test_create_job_succeeds(self, job_repo: JobRepository):
        job = job_repo.create_job(
            job_type="demo",
            idempotency_key="test_key_001",
            business_resource_id="resource_001",
            payload={"key": "value"},
        )
        assert job.id.startswith("job_")
        assert job.status is JobStatus.PENDING
        assert job.job_type == "demo"
        assert job.attempt == 0
        assert job.max_attempts == 3

    def test_get_job(self, job_repo: JobRepository):
        created = job_repo.create_job(
            job_type="demo",
            idempotency_key="test_key_002",
            business_resource_id="resource_002",
        )
        fetched = job_repo.get_job(created.id)
        assert fetched.id == created.id
        assert fetched.status is JobStatus.PENDING

    def test_get_job_not_found_raises(self, job_repo: JobRepository):
        with pytest.raises(LookupError):
            job_repo.get_job("job_nonexistent")

    def test_idempotency_key_unique(self, job_repo: JobRepository):
        job_repo.create_job(
            job_type="demo",
            idempotency_key="same_key",
            business_resource_id="resource_A",
        )
        # 相同 job_type + idempotency_key 应触发数据库唯一约束
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            job_repo.create_job(
                job_type="demo",
                idempotency_key="same_key",
                business_resource_id="resource_B",
            )

    def test_find_by_idempotency(self, job_repo: JobRepository):
        job_repo.create_job(
            job_type="demo",
            idempotency_key="lookup_key",
            business_resource_id="r1",
        )
        found = job_repo.find_by_idempotency(
            job_type="demo", idempotency_key="lookup_key"
        )
        assert found is not None
        assert found.idempotency_key == "lookup_key"

        not_found = job_repo.find_by_idempotency(
            job_type="demo", idempotency_key="nonexistent"
        )
        assert not_found is None

    def test_get_job_by_resource(self, job_repo: JobRepository):
        job_repo.create_job(
            job_type="demo",
            idempotency_key="k1",
            business_resource_id="biz_1",
        )
        job_repo.create_job(
            job_type="demo",
            idempotency_key="k2",
            business_resource_id="biz_1",
        )
        result = job_repo.get_job_by_resource(
            job_type="demo", business_resource_id="biz_1"
        )
        assert result is not None
        # 返回最新一条
        assert result.idempotency_key == "k2"


# ── 状态流转测试 ─────────────────────────────────────────


class TestJobStatusTransitions:
    def test_full_lifecycle(self, job_repo: JobRepository):
        job = job_repo.create_job(
            job_type="demo",
            idempotency_key="lifecycle_test",
            business_resource_id="biz_lifecycle",
        )
        assert job.status is JobStatus.PENDING

        job = job_repo.mark_queued(job.id)
        assert job.status is JobStatus.QUEUED

        job = job_repo.mark_running(job.id)
        assert job.status is JobStatus.RUNNING
        assert job.attempt == 1
        assert job.started_at is not None

        job = job_repo.mark_completed(job.id, {"result": "ok"})
        assert job.status is JobStatus.COMPLETED
        assert job.result_json is not None
        assert json.loads(job.result_json) == {"result": "ok"}
        assert job.completed_at is not None

    def test_failure_and_retry(self, job_repo: JobRepository):
        job = job_repo.create_job(
            job_type="demo",
            idempotency_key="retry_test",
            business_resource_id="biz_retry",
            max_attempts=3,
        )
        job_repo.mark_running(job.id)

        job = job_repo.mark_failed(job.id, "something broke")
        assert job.status is JobStatus.FAILED
        assert job.error_message == "something broke"

        # 未达最大重试 → 可重回 PENDING
        job = job_repo.retry_job(job.id)
        assert job.status is JobStatus.PENDING

    def test_max_retries_exceeded(self, job_repo: JobRepository):
        job = job_repo.create_job(
            job_type="demo",
            idempotency_key="max_retry_test",
            business_resource_id="biz_max",
            max_attempts=2,
        )
        job_repo.mark_running(job.id)
        job_repo.mark_failed(job.id, "fail 1")
        job_repo.retry_job(job.id)
        job_repo.mark_running(job.id)
        job_repo.mark_failed(job.id, "fail 2")

        # 第 3 次 retry 应失败
        with pytest.raises(RuntimeError, match="最大重试次数"):
            job_repo.retry_job(job.id)

    def test_list_pending_jobs(self, job_repo: JobRepository):
        job_repo.create_job(
            job_type="demo",
            idempotency_key="pending_1",
            business_resource_id="biz_p1",
        )
        job_repo.create_job(
            job_type="demo",
            idempotency_key="pending_2",
            business_resource_id="biz_p2",
        )
        pending = job_repo.list_pending_jobs(job_type="demo", limit=10)
        assert len(pending) == 2
        assert all(j.status is JobStatus.PENDING for j in pending)


# ── RedisQueue 测试 ──────────────────────────────────────


class TestRedisQueue:
    @pytest.mark.asyncio
    async def test_enqueue_dequeue(self, redis_queue: RedisQueue):
        await redis_queue.enqueue(job_type="demo", job_id="job_001")
        length = await redis_queue.queue_length(job_type="demo")
        assert length == 1

        job_id = await redis_queue.dequeue(job_type="demo", timeout=1.0)
        assert job_id == "job_001"
        assert await redis_queue.queue_length(job_type="demo") == 0

    @pytest.mark.asyncio
    async def test_dequeue_empty_returns_none(self, redis_queue: RedisQueue):
        job_id = await redis_queue.dequeue(job_type="demo", timeout=0.5)
        assert job_id is None

    @pytest.mark.asyncio
    async def test_acquire_release_lock(self, redis_queue: RedisQueue):
        acquired = await redis_queue.acquire_lock(
            job_type="demo", resource_id="res_1", ttl=10
        )
        assert acquired is True

        # 同一资源不能重复加锁
        acquired_again = await redis_queue.acquire_lock(
            job_type="demo", resource_id="res_1", ttl=10
        )
        assert acquired_again is False

        # 不同资源可以加锁
        acquired_other = await redis_queue.acquire_lock(
            job_type="demo", resource_id="res_2", ttl=10
        )
        assert acquired_other is True

        # 释放后可以重新加锁
        await redis_queue.release_lock(job_type="demo", resource_id="res_1")
        assert await redis_queue.lock_exists(job_type="demo", resource_id="res_1") is False

        acquired_after_release = await redis_queue.acquire_lock(
            job_type="demo", resource_id="res_1", ttl=10
        )
        assert acquired_after_release is True


# ── Worker 端到端测试 ────────────────────────────────────


class TestWorkerEndToEnd:
    @pytest.mark.asyncio
    async def test_worker_executes_demo_job(
        self, job_repo: JobRepository, redis_queue: RedisQueue, worker: BackgroundWorker
    ):
        """端到端：创建 Job → 入队 → Worker 执行 → 状态变为 COMPLETED。"""
        # 1. 创建 Job
        job = job_repo.create_job(
            job_type="demo",
            idempotency_key=f"e2e_{generate_id('key')}",
            business_resource_id="e2e_resource",
            payload={"delay_seconds": 0.1},
        )

        # 2. 入队
        await redis_queue.enqueue(job_type="demo", job_id=job.id)

        # 3. 启动 Worker 主循环（作为一个独立 task）
        worker_task = asyncio.create_task(worker.run())

        # 4. 等待 Worker 处理完成（轮询 PG 状态）
        for _ in range(30):  # 最多等 3 秒
            await asyncio.sleep(0.1)
            updated = job_repo.get_job(job.id)
            if updated.status is JobStatus.COMPLETED:
                break

        # 5. 停止 Worker
        worker.request_shutdown()
        await worker_task

        # 6. 验证结果
        updated = job_repo.get_job(job.id)
        assert updated.status is JobStatus.COMPLETED
        result = json.loads(updated.result_json)
        assert result["message"] == "hello async"
        assert result["job_id"] == job.id
        assert result["slept_seconds"] == 0.1

    @pytest.mark.asyncio
    async def test_worker_handles_task_timeout(
        self, job_repo: JobRepository, redis_queue: RedisQueue
    ):
        """超时任务应被标记为 FAILED。"""
        worker = BackgroundWorker(
            job_repo=job_repo,
            redis_queue=redis_queue,
            poll_timeout=1.0,
            task_timeout=0.3,  # 极短超时
            max_retries=1,
        )

        job = job_repo.create_job(
            job_type="demo",
            idempotency_key=f"timeout_{generate_id('key')}",
            business_resource_id="timeout_resource",
            payload={"delay_seconds": 5.0},  # 任务 sleep 5s 但超时只有 0.3s
            max_attempts=1,
        )
        await redis_queue.enqueue(job_type="demo", job_id=job.id)

        worker_task = asyncio.create_task(worker.run())
        for _ in range(30):
            await asyncio.sleep(0.1)
            updated = job_repo.get_job(job.id)
            if updated.status is JobStatus.FAILED:
                break

        worker.request_shutdown()
        await worker_task

        updated = job_repo.get_job(job.id)
        assert updated.status is JobStatus.FAILED
        assert "超时" in (updated.error_message or "")

    @pytest.mark.asyncio
    async def test_worker_skips_completed_job(
        self, job_repo: JobRepository, redis_queue: RedisQueue, worker: BackgroundWorker
    ):
        """Worker 不应重复执行已完成的 Job。"""
        job = job_repo.create_job(
            job_type="demo",
            idempotency_key=f"skip_{generate_id('key')}",
            business_resource_id="skip_resource",
            payload={"delay_seconds": 0.1},
        )
        # 直接在 PG 中标记为已完成
        job_repo.mark_queued(job.id)
        job_repo.mark_running(job.id)
        job_repo.mark_completed(job.id, {"message": "already done"})
        completed_at_before = job_repo.get_job(job.id).completed_at

        # 入队（模拟重复投递）
        await redis_queue.enqueue(job_type="demo", job_id=job.id)

        worker_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.5)
        worker.request_shutdown()
        await worker_task

        # 状态应保持不变
        updated = job_repo.get_job(job.id)
        assert updated.status is JobStatus.COMPLETED
        assert updated.completed_at == completed_at_before

    @pytest.mark.asyncio
    async def test_worker_idempotent_across_restarts(
        self, job_repo: JobRepository, redis_queue: RedisQueue
    ):
        """同一 idempotency_key 只创建一条 Job 记录（唯一约束校验）。"""
        job_repo.create_job(
            job_type="demo",
            idempotency_key="unique_idempotency",
            business_resource_id="unique_resource",
        )
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            job_repo.create_job(
                job_type="demo",
                idempotency_key="unique_idempotency",
                business_resource_id="unique_resource_2",
            )


# ── 任务注册表测试 ───────────────────────────────────────


class TestTaskRegistry:
    def test_demo_task_is_registered(self):
        assert "demo" in registered_types()
        handler = get_handler("demo")
        assert handler is not None
        assert handler is not None
        import inspect
        assert inspect.iscoroutinefunction(handler)

    def test_unknown_task_returns_none(self):
        assert get_handler("nonexistent_task_type") is None


__all__ = [
    "TestBackgroundJobModel",
    "TestJobStatusTransitions",
    "TestRedisQueue",
    "TestTaskRegistry",
    "TestWorkerEndToEnd",
]
