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
import contextlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from liverag.interview.jobs.queue import RedisQueue
from liverag.interview.jobs.repository import JobRepository
from liverag.interview.jobs.tasks import get_handler, registered_types
from liverag.interview.jobs.worker import BackgroundWorker
from liverag.interview.persistence.db import create_session_factory, create_sqlite_engine
from liverag.interview.persistence.models import Base
from liverag.interview.records import JobStatus, generate_id

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
        with contextlib.suppress(PermissionError):
            db_path.unlink(missing_ok=True)


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
    from unittest.mock import AsyncMock, MagicMock

    return BackgroundWorker(
        job_repo=job_repo,
        redis_queue=redis_queue,
        question_bank=MagicMock(),
        llm_client=AsyncMock(),
        profile_source=AsyncMock(),
        llm_model="test-model",
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

    def test_recovers_stale_running_job_for_retry(self, job_repo: JobRepository):
        job = job_repo.create_job(
            job_type="demo",
            idempotency_key="stale_running_job",
            business_resource_id="biz_stale",
        )
        job_repo.mark_running(job.id, lease_seconds=1)

        recovered = job_repo.recover_stale_running_jobs(
            stale_before=datetime.now(timezone.utc) + timedelta(seconds=2),
        )

        assert [item.id for item in recovered] == [job.id]
        restored = job_repo.get_job(job.id)
        assert restored.status is JobStatus.PENDING
        assert "恢复" in (restored.error_message or "")

    def test_expired_lease_rejects_old_worker_result(self, job_repo: JobRepository):
        job = job_repo.create_job(
            job_type="demo",
            idempotency_key="expired_lease_owner",
            business_resource_id="biz_expired_owner",
        )
        first = job_repo.mark_running(job.id, lease_seconds=1)
        assert first.lease_token is not None

        job_repo.recover_stale_running_jobs(
            stale_before=datetime.now(timezone.utc) + timedelta(seconds=2),
        )
        job_repo.mark_queued(job.id)
        second = job_repo.mark_running(job.id, lease_seconds=60)

        with pytest.raises(RuntimeError, match="lease 已失效"):
            job_repo.mark_completed(
                job.id, {"result": "old"}, lease_token=first.lease_token
            )
        assert job_repo.get_job(job.id).lease_token == second.lease_token


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
        lock_token = await redis_queue.acquire_lock(
            job_type="demo", resource_id="res_1", ttl=10
        )
        assert isinstance(lock_token, str)

        # 同一资源不能重复加锁
        acquire_again = await redis_queue.acquire_lock(
            job_type="demo", resource_id="res_1", ttl=10
        )
        assert acquire_again is None

        # 不同资源可以加锁
        other_token = await redis_queue.acquire_lock(
            job_type="demo", resource_id="res_2", ttl=10
        )
        assert isinstance(other_token, str)

        # 用正确的 token 释放
        await redis_queue.release_lock(
            job_type="demo", resource_id="res_1", lock_token=lock_token,
        )
        assert await redis_queue.lock_exists(job_type="demo", resource_id="res_1") is False

        # 释放后可以重新加锁
        acquired_after_release = await redis_queue.acquire_lock(
            job_type="demo", resource_id="res_1", ttl=10
        )
        assert isinstance(acquired_after_release, str)

    @pytest.mark.asyncio
    async def test_renew_lock_requires_current_token(self, redis_queue: RedisQueue):
        lock_token = await redis_queue.acquire_lock(
            job_type="demo", resource_id="renew_resource", ttl=1
        )
        assert isinstance(lock_token, str)

        assert await redis_queue.renew_lock(
            job_type="demo",
            resource_id="renew_resource",
            lock_token=lock_token,
            ttl=10,
        )
        assert not await redis_queue.renew_lock(
            job_type="demo",
            resource_id="renew_resource",
            lock_token="old-token",
            ttl=10,
        )

    @pytest.mark.asyncio
    async def test_keep_lock_alive_prevents_ttl_expiry(self, redis_queue: RedisQueue):
        lock_token = await redis_queue.acquire_lock(
            job_type="demo", resource_id="long_resource", ttl=1
        )
        assert isinstance(lock_token, str)
        stop_event = asyncio.Event()
        heartbeat = asyncio.create_task(
            redis_queue.keep_lock_alive(
                job_type="demo",
                resource_id="long_resource",
                lock_token=lock_token,
                ttl=1,
                stop_event=stop_event,
            )
        )

        await asyncio.sleep(1.1)
        assert await redis_queue.acquire_lock(
            job_type="demo", resource_id="long_resource", ttl=1
        ) is None

        stop_event.set()
        await heartbeat


# ── Worker 端到端测试 ────────────────────────────────────


class TestWorkerEndToEnd:
    @pytest.mark.asyncio
    async def test_worker_renews_lease_while_handler_runs(
        self, job_repo: JobRepository, redis_queue: RedisQueue
    ):
        from unittest.mock import AsyncMock, MagicMock

        worker = BackgroundWorker(
            job_repo=job_repo,
            redis_queue=redis_queue,
            question_bank=MagicMock(),
            llm_client=AsyncMock(),
            profile_source=AsyncMock(),
            llm_model="test-model",
            task_timeout=0.5,
            lease_heartbeat_interval=0.05,
        )
        job = job_repo.create_job(
            job_type="demo",
            idempotency_key=f"lease_heartbeat_{generate_id('key')}",
            business_resource_id="lease_heartbeat_resource",
            payload={"delay_seconds": 0.2},
        )

        execution = asyncio.create_task(worker._execute_job(job.id))
        await asyncio.sleep(0.02)
        first = job_repo.get_job(job.id)
        await asyncio.sleep(0.08)
        running = job_repo.get_job(job.id)
        assert running.status is JobStatus.RUNNING
        assert running.lease_expires_at > first.lease_expires_at
        await execution

        completed = job_repo.get_job(job.id)
        assert completed.status is JobStatus.COMPLETED
        assert completed.lease_token is None
        assert completed.lease_expires_at is None

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
        from unittest.mock import AsyncMock, MagicMock

        worker = BackgroundWorker(
            job_repo=job_repo,
            redis_queue=redis_queue,
            question_bank=MagicMock(),
            llm_client=AsyncMock(),
            profile_source=AsyncMock(),
            llm_model="test-model",
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


# ── 3.2.2 简历事实抽取任务测试 ─────────────────────────


# CandidateFacts 的最小合法 JSON（用于构建 mock LLM 响应，匹配精简后的 Schema）
_MINIMAL_CANDIDATE_FACTS: dict = {
    "kb_id": "default",
    "name": "",
    "work_experience": [],
    "projects": [],
    "skills": [],
    "raw_evidence_refs": [],
}


def _make_fake_profile_source(
    context: str,
    evidence_refs: tuple[str, ...] = (),
):
    """构造假的 profile_source（RagGatewayProfileSource），提供 retrieve() 方法。"""
    from unittest.mock import AsyncMock

    from liverag.interview.application.profile_service import KnowledgeContext

    source = AsyncMock()
    source.retrieve.return_value = KnowledgeContext(
        context=context,
        evidence_refs=evidence_refs,
    )
    return source


def _make_fake_facts_response(facts_dict: dict):
    """构造与 AsyncOpenAI chat.completions.create 返回值一致的假响应。"""
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content=json.dumps(facts_dict, ensure_ascii=False)
            )
        )
    ]
    return mock_response


class TestResumeParseTask:
    """resume_parse 任务处理器测试（3.2.2: CandidateFacts 模式）。"""

    @pytest.mark.asyncio
    async def test_resume_parse_is_registered(self):
        """resume_parse 应已注册并可获取。"""
        assert "resume_parse" in registered_types()
        handler = get_handler("resume_parse")
        assert handler is not None
        import inspect

        assert inspect.iscoroutinefunction(handler)

    @pytest.mark.asyncio
    async def test_resume_parse_full_flow(
        self, job_repo: JobRepository
    ):
        """完整流程：创建 Job → RAG 检索 → LLM 事实抽取 → 返回 CandidateFacts。"""
        from unittest.mock import AsyncMock

        # 1. 准备假 profile_source
        fake_source = _make_fake_profile_source(
            context="候选人张三，5年Python开发经验，擅长FastAPI、Redis和PostgreSQL。曾负责电商推荐系统架构设计。",
            evidence_refs=("resume_zhangsan.pdf", "doc_001"),
        )

        # 2. 准备假 LLM 客户端（返回 CandidateFacts 结构）
        fake_llm = AsyncMock()
        fake_llm.chat = AsyncMock()
        fake_llm.chat.completions.create.return_value = _make_fake_facts_response(
            {
                **_MINIMAL_CANDIDATE_FACTS,
                "kb_id": "default",
                "name": "张三",
                "skills": ["Python", "FastAPI", "Redis", "PostgreSQL", "Docker"],
                "projects": [
                    {
                        "name": "电商推荐系统",
                        "role": "架构负责人",
                        "description": "负责架构设计与核心服务开发",
                        "technologies": ["Python", "Redis"],
                    }
                ],
                "work_experience": [
                    {
                        "company": "某科技公司",
                        "role": "高级后端工程师",
                        "description": "负责后端服务开发",
                        "technologies": ["Python", "FastAPI", "Redis"],
                        "start_at": "2021-07-01",
                        "end_at": None,
                    }
                ],
            }
        )

        # 3. 创建 Job
        job = job_repo.create_job(
            job_type="resume_parse",
            idempotency_key=f"resume_parse_test_{generate_id('key')}",
            business_resource_id="default",
            payload={"kb_id": "default", "document_ids": ["doc_001"]},
        )

        # 4. 获取 handler 并执行
        handler = get_handler("resume_parse")
        assert handler is not None

        result = await handler(
            job,
            profile_source=fake_source,
            llm_client=fake_llm,
            llm_model="test-model",
        )

        # 5. 验证结果（CandidateFacts 而非 CandidateProfile）
        assert result["kb_id"] == "default"
        assert result["name"] == "张三"
        assert "Python" in result["skills"]
        assert len(result["projects"]) >= 1
        # raw_evidence_refs 应被 handler 补充了 RAG 来源
        assert len(result["raw_evidence_refs"]) >= 1

        # 6. 验证 profile_source.retrieve() 被调用
        fake_source.retrieve.assert_called_once()
        call_kwargs = fake_source.retrieve.call_args.kwargs
        assert call_kwargs["kb_id"] == "default"

        # 7. 验证 LLM 被调用
        fake_llm.chat.completions.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_resume_parse_empty_context_raises(
        self, job_repo: JobRepository
    ):
        """知识库返回空内容 → 应抛出 ValueError。"""
        from unittest.mock import AsyncMock

        fake_source = _make_fake_profile_source(context="")

        job = job_repo.create_job(
            job_type="resume_parse",
            idempotency_key=f"empty_{generate_id('key')}",
            business_resource_id="default",
            payload={"kb_id": "default"},
        )

        handler = get_handler("resume_parse")
        assert handler is not None

        with pytest.raises(ValueError, match="没有可"):
            await handler(
                job,
                profile_source=fake_source,
                llm_client=AsyncMock(),
                llm_model="test-model",
            )

    def test_resume_parse_declares_required_dependencies(self):
        """Worker 保证的内部依赖应由 handler 签名明确声明。"""
        import inspect

        handler = get_handler("resume_parse")
        assert handler is not None

        parameters = inspect.signature(handler).parameters
        for name in ("profile_source", "llm_client", "llm_model"):
            assert name in parameters
            assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
            assert parameters[name].default is inspect.Parameter.empty

    @pytest.mark.asyncio
    async def test_resume_parse_llm_retry_on_validation_error(
        self, job_repo: JobRepository
    ):
        """LLM 第一次返回非法 JSON → 应重试并最终成功。"""
        from unittest.mock import AsyncMock, MagicMock

        fake_source = _make_fake_profile_source(
            context="候选人李四，3年前端开发经验，React、TypeScript、Vue。",
        )

        fake_llm = AsyncMock()
        fake_llm.chat = AsyncMock()

        # 第一次调用返回非法 JSON（含额外禁止字段 → StrictModel extra="forbid" 触发 ValidationError）
        bad_response = MagicMock()
        bad_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {**_MINIMAL_CANDIDATE_FACTS, "extra_forbidden_field": True}
                    )
                )
            )
        ]
        # 第二次调用返回合法 JSON（CandidateFacts 结构）
        good_response = MagicMock()
        good_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {
                            **_MINIMAL_CANDIDATE_FACTS,
                            "kb_id": "default",
                            "name": "李四",
                            "skills": ["React", "TypeScript", "Vue"],
                        },
                        ensure_ascii=False,
                    )
                )
            )
        ]
        fake_llm.chat.completions.create.side_effect = [bad_response, good_response]

        job = job_repo.create_job(
            job_type="resume_parse",
            idempotency_key=f"retry_{generate_id('key')}",
            business_resource_id="default",
            payload={"kb_id": "default"},
        )

        handler = get_handler("resume_parse")
        assert handler is not None

        result = await handler(
            job,
            profile_source=fake_source,
            llm_client=fake_llm,
            llm_model="test-model",
        )

        assert result["kb_id"] == "default"
        assert "React" in result["skills"]
        # 应调用了两次 LLM
        assert fake_llm.chat.completions.create.call_count == 2

    @pytest.mark.asyncio
    async def test_resume_parse_preserves_kb_id(self, job_repo: JobRepository):
        """LLM 返回的 kb_id 与输入不一致 → ResumeParser 应使用输入值覆盖。"""
        from unittest.mock import AsyncMock

        fake_source = _make_fake_profile_source(context="测试内容")

        fake_llm = AsyncMock()
        fake_llm.chat = AsyncMock()
        fake_llm.chat.completions.create.return_value = _make_fake_facts_response(
            {
                **_MINIMAL_CANDIDATE_FACTS,
                "kb_id": "wrong_kb",  # LLM 返回了错误的 kb_id
                "skills": ["Test"],
            }
        )

        job = job_repo.create_job(
            job_type="resume_parse",
            idempotency_key=f"kb_override_{generate_id('key')}",
            business_resource_id="my_custom_kb",
            payload={"kb_id": "my_custom_kb"},
        )

        handler = get_handler("resume_parse")
        assert handler is not None

        result = await handler(
            job,
            profile_source=fake_source,
            llm_client=fake_llm,
            llm_model="test-model",
        )

        # handler 应覆盖为输入中的 kb_id
        assert result["kb_id"] == "my_custom_kb"


# ── 3.2.3 画像生成任务测试 ─────────────────────────────


class TestProfileGenerationTask:
    """profile_generation 任务处理器测试。"""

    @staticmethod
    def _make_fake_profile_source(
        context: str,
        evidence_refs: tuple[str, ...] = (),
    ):
        """构造假的 profile_source（提供 retrieve() 方法）。"""
        from unittest.mock import AsyncMock

        from liverag.interview.application.profile_service import KnowledgeContext

        source = AsyncMock()
        source.retrieve.return_value = KnowledgeContext(
            context=context,
            evidence_refs=evidence_refs,
        )
        return source

    @staticmethod
    def _make_fake_question_bank(labels: list[str] | None = None):
        """构造假的 QuestionBank，包含指定标签作为一级分类。"""
        from liverag.interview.question_bank.catalog import QuestionBank, QuestionBankDocument
        from liverag.interview.schemas import (
            InterviewDifficulty,
            InterviewQuestion,
            QuestionRubric,
            QuestionSource,
            QuestionType,
            RubricPoint,
        )

        if labels is None:
            labels = ["Python", "Redis", "PostgreSQL", "FastAPI"]

        questions = []
        for idx, label in enumerate(labels, start=1):
            questions.append(
                InterviewQuestion(
                    id=f"q-{idx:03d}",
                    order=idx,
                    type=QuestionType.TECHNICAL_KNOWLEDGE,
                    source=QuestionSource.QUESTION_BANK,
                    difficulty=InterviewDifficulty.INTERMEDIATE,
                    category=label,
                    topics=[f"{label} 应用"],
                    question_text=f"请描述 {label} 的核心概念。",
                    objective=f"考察 {label} 基础知识",
                    rubric=QuestionRubric(
                        expected_points=[
                            RubricPoint(id=f"rp-{idx}", content=f"{label} 概念")
                        ]
                    ),
                    reference_answer=f"{label} 的核心是……",
                    source_reference=f"test.md#{label.lower()}",
                )
            )

        return QuestionBank(QuestionBankDocument(version=1, questions=questions))

    @pytest.mark.asyncio
    async def test_profile_generation_is_registered(self):
        """profile_generation 应已注册并可获取。"""
        assert "profile_generation" in registered_types()
        handler = get_handler("profile_generation")
        assert handler is not None
        import inspect

        assert inspect.iscoroutinefunction(handler)

    def test_profile_generation_declares_required_dependencies(self):
        """Worker 保证的内部依赖应由 handler 签名明确声明。"""
        import inspect

        handler = get_handler("profile_generation")
        assert handler is not None

        parameters = inspect.signature(handler).parameters
        for name in ("profile_source", "job_repo"):
            assert name in parameters
            assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
            assert parameters[name].default is inspect.Parameter.empty

    @pytest.mark.asyncio
    async def test_candidate_profile_generation(self, job_repo: JobRepository):
        """完整流程：创建 Job → 检索 RAG → 返回 CandidateProfile（skills 来自 CandidateFacts）。"""
        fake_source = self._make_fake_profile_source(
            context="项目：使用 Python 实现异步编程服务，负责 Redis 缓存和 PostgreSQL 数据库设计。",
            evidence_refs=("resume.pdf",),
        )

        job = job_repo.create_job(
            job_type="profile_generation",
            idempotency_key=f"profile_candidate_{generate_id('key')}",
            business_resource_id="default",
            payload={"profile_type": "candidate_profile", "kb_id": "default"},
        )

        handler = get_handler("profile_generation")
        assert handler is not None

        result = await handler(
            job,
            profile_source=fake_source,
            job_repo=job_repo,
        )

        # 无 CandidateFacts → skills 为空
        assert result["kb_id"] == "default"
        assert result["skills"] == []
        assert len(result["projects"]) >= 1
        assert len(result["evidence_refs"]) >= 1
        assert "resume.pdf" in result["evidence_refs"]

    @pytest.mark.asyncio
    async def test_job_profile_generation(self, job_repo: JobRepository):
        """完整流程：创建 Job → 检索 RAG → 返回 JobProfile。"""
        fake_source = self._make_fake_profile_source(
            context="岗位要求熟悉 Python 和异步编程，负责后端服务开发。",
            evidence_refs=("jd.txt",),
        )

        job = job_repo.create_job(
            job_type="profile_generation",
            idempotency_key=f"profile_job_{generate_id('key')}",
            business_resource_id="target_kb",
            payload={
                "profile_type": "job_profile",
                "kb_id": "target_kb",
                "company": "测试公司",
                "role": "后端工程师",
            },
        )

        handler = get_handler("profile_generation")
        assert handler is not None

        result = await handler(
            job,
            profile_source=fake_source,
            job_repo=job_repo,
        )

        assert result["kb_id"] == "target_kb"
        assert result["company"] == "测试公司"
        assert result["role"] == "后端工程师"
        assert result["required_skills"] == []
        assert "jd.txt" in result["evidence_refs"]

    @pytest.mark.asyncio
    async def test_job_profile_missing_role_raises(self, job_repo: JobRepository):
        """job_profile 类型缺少 role → 应抛出 ValueError。"""
        fake_source = self._make_fake_profile_source(context="测试内容")

        job = job_repo.create_job(
            job_type="profile_generation",
            idempotency_key=f"no_role_{generate_id('key')}",
            business_resource_id="target_kb",
            payload={
                "profile_type": "job_profile",
                "kb_id": "target_kb",
                # 缺少 role
            },
        )

        handler = get_handler("profile_generation")
        assert handler is not None

        with pytest.raises(ValueError, match="必须提供 role"):
            await handler(
                job,
                profile_source=fake_source,
                job_repo=job_repo,
            )

    @pytest.mark.asyncio
    async def test_invalid_profile_type_raises(self, job_repo: JobRepository):
        """不支持的 profile_type → 应抛出 ValueError。"""
        fake_source = self._make_fake_profile_source(context="测试内容")

        job = job_repo.create_job(
            job_type="profile_generation",
            idempotency_key=f"invalid_type_{generate_id('key')}",
            business_resource_id="default",
            payload={
                "profile_type": "unknown_type",
                "kb_id": "default",
            },
        )

        handler = get_handler("profile_generation")
        assert handler is not None

        with pytest.raises(ValueError, match="不支持的画像类型"):
            await handler(
                job,
                profile_source=fake_source,
                job_repo=job_repo,
            )

    @pytest.mark.asyncio
    async def test_candidate_profile_with_facts_enhancement(
        self, job_repo: JobRepository
    ):
        """3.2.3: 提供 CandidateFacts → experience_level 应从工作经历中推理。"""
        from datetime import date

        fake_source = self._make_fake_profile_source(
            context="候选人张三，5年Python后端开发经验。",
            evidence_refs=("resume.pdf",),
        )

        # 1. 创建一个"已完成"的 resume_parse Job（模拟 CandidateFacts 产出）
        resume_job = job_repo.create_job(
            job_type="resume_parse",
            idempotency_key=f"facts_src_{generate_id('key')}",
            business_resource_id="default",
        )
        job_repo.mark_running(resume_job.id)
        from liverag.interview.schemas import CandidateFacts, ProjectFact, WorkExperienceFact

        facts = CandidateFacts(
            kb_id="default",
            name="张三",
            work_experience=[
                WorkExperienceFact(
                    company="A公司",
                    role="初级工程师",
                    start_at=date(2018, 7, 1),
                    end_at=date(2021, 6, 30),
                    technologies=["Python"],
                ),
                WorkExperienceFact(
                    company="B公司",
                    role="高级工程师",
                    start_at=date(2021, 7, 1),
                    end_at=None,  # 至今
                    technologies=["Python", "Redis"],
                ),
            ],
            projects=[
                ProjectFact(name="推荐系统", role="负责人", technologies=["Python"]),
            ],
            skills=["Python", "Redis"],
        )
        job_repo.mark_completed(resume_job.id, facts.model_dump(mode="json"))

        # 2. 创建 profile_generation Job，引用 CandidateFacts
        job = job_repo.create_job(
            job_type="profile_generation",
            idempotency_key=f"profile_facts_{generate_id('key')}",
            business_resource_id="default",
            payload={
                "profile_type": "candidate_profile",
                "kb_id": "default",
                "candidate_facts_job_id": resume_job.id,
            },
        )

        # 3. 执行 handler（注入 job_repo 以读取 CandidateFacts）
        handler = get_handler("profile_generation")
        assert handler is not None

        result = await handler(
            job,
            profile_source=fake_source,
            job_repo=job_repo,
        )

        # 4. 验证 experience_level 被推理（2018至今 ≈ 8+ 年 → EXPERT）
        assert result["kb_id"] == "default"
        assert result["experience_level"] != ""
        assert result["experience_level"] in ("INTERMEDIATE", "SENIOR", "EXPERT")

    @pytest.mark.asyncio
    async def test_candidate_profile_without_facts_uses_rules(
        self, job_repo: JobRepository
    ):
        """无 CandidateFacts → experience_level 为空，skills 为空。"""
        fake_source = self._make_fake_profile_source(
            context="候选人，熟悉 Python。",
            evidence_refs=("resume.pdf",),
        )

        job = job_repo.create_job(
            job_type="profile_generation",
            idempotency_key=f"profile_nofacts_{generate_id('key')}",
            business_resource_id="default",
            payload={"profile_type": "candidate_profile", "kb_id": "default"},
        )

        handler = get_handler("profile_generation")
        assert handler is not None

        result = await handler(
            job,
            profile_source=fake_source,
            job_repo=job_repo,
        )

        assert result["kb_id"] == "default"
        assert result["skills"] == []
        assert result["experience_level"] == ""  # 无 CandidateFacts → 不推理


# ── 3.2-A: Preparation Workflow 测试 ────────────────────


class TestPreparationStage:
    """PreparationStage 枚举测试。"""

    def test_stage_values_exist(self):
        """所有 stage 枚举值应可访问。"""
        from liverag.interview.schemas import PreparationStage

        assert PreparationStage.PENDING.value == "PENDING"
        assert PreparationStage.RESUME_PARSING.value == "RESUME_PARSING"
        assert PreparationStage.CANDIDATE_PROFILE_GENERATION.value == "CANDIDATE_PROFILE_GENERATION"
        assert PreparationStage.JOB_PROFILE_GENERATION.value == "JOB_PROFILE_GENERATION"
        assert PreparationStage.COMPANY_INTELLIGENCE.value == "COMPANY_INTELLIGENCE"
        assert PreparationStage.PLAN_GENERATION.value == "PLAN_GENERATION"
        assert PreparationStage.READY.value == "READY"

    def test_stage_order(self):
        """Stage 应按准备流程顺序排列。"""
        from liverag.interview.schemas import PreparationStage

        ordered = list(PreparationStage)
        assert ordered[0] is PreparationStage.PENDING
        assert ordered[-1] is PreparationStage.READY


class TestInterviewPreparationTask:
    """interview_preparation 任务处理器测试。"""

    @staticmethod
    def _fake_deps(
        *,
        profile_context: str = "候选人张三，掌握 Python 技术栈。",
        llm_facts: dict | None = None,
        bank_labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """构造 preparation workflow 子 handler 所需的全部 fake 依赖。"""
        from unittest.mock import AsyncMock

        if llm_facts is None:
            llm_facts = {**_MINIMAL_CANDIDATE_FACTS, "skills": ["Python"]}
        if bank_labels is None:
            bank_labels = ["Python"]

        fake_llm = AsyncMock()
        fake_llm.chat = AsyncMock()
        fake_llm.chat.completions.create.return_value = _make_fake_facts_response(llm_facts)

        return {
            "profile_source": _make_fake_profile_source(profile_context),
            "llm_client": fake_llm,
            "llm_model": "test-model",
            "question_bank": TestProfileGenerationTask._make_fake_question_bank(bank_labels),
        }

    @staticmethod
    def _workflow_payload(**overrides: Any) -> dict[str, Any]:
        """构造 workflow Job 的 payload，含必需的 kb/role 字段。"""
        base: dict[str, Any] = {
            "interview_id": "interview_test_001",
            "candidate_kb_id": "default",
            "target_kb_id": "default",
            "target_role": "后端工程师",
            "target_company": "某公司",
            "config_json": '{"question_count":1,"candidate_kb_id":"default","topic_weights":{"Python":1.0}}',
            "current_stage": "PENDING",
            "completed_steps": [],
            "degraded": False,
            "degradation_reasons": [],
            "stage_results": {},
        }
        base.update(overrides)
        return base

    @pytest.mark.asyncio
    async def test_handler_is_registered(self):
        """interview_preparation 应已注册并可获取。"""
        assert "interview_preparation" in registered_types()
        handler = get_handler("interview_preparation")
        assert handler is not None
        import inspect
        assert inspect.iscoroutinefunction(handler)

    @pytest.mark.asyncio
    async def test_full_stage_loop(self, job_repo: JobRepository):
        """完整 stage 循环：5 个 stage 顺序执行，COMPANY_INTELLIGENCE 降级跳过，最终 READY。"""
        deps = self._fake_deps()
        payload = self._workflow_payload()

        job = job_repo.create_job(
            job_type="interview_preparation",
            idempotency_key=f"prep_full_{generate_id('key')}",
            business_resource_id="interview_test_001",
            payload=payload,
        )

        handler = get_handler("interview_preparation")
        assert handler is not None

        result = await handler(job, job_repo=job_repo, **deps)

        assert result["status"] == "READY"
        assert len(result["completed_steps"]) == 5
        assert "RESUME_PARSE" in result["completed_steps"]
        assert "CANDIDATE_PROFILE" in result["completed_steps"]
        assert "JOB_PROFILE" in result["completed_steps"]
        assert "COMPANY_INTELLIGENCE" in result["completed_steps"]
        assert "PLAN_GENERATION" in result["completed_steps"]
        # COMPANY_INTELLIGENCE 无 handler → 降级
        assert result["degraded"] is True
        assert len(result["degradation_reasons"]) == 2  # NOWCODER_MCP + PLAN_QUALITY
        # stage_results 只保存摘要信息（skills_count / plan_id 等）
        assert "skills_count" in result["stage_results"]["resume_parse"]
        assert "skills_count" in result["stage_results"]["candidate_profile"]
        assert "skills_count" in result["stage_results"]["job_profile"]
        assert "plan_id" in result["stage_results"]["plan_generation"]

    @pytest.mark.asyncio
    async def test_skips_completed_stages(self, job_repo: JobRepository):
        """已完成 stage 应在重试时跳过（幂等恢复）。"""
        deps = self._fake_deps()
        payload = self._workflow_payload(
            interview_id="interview_test_002",
            current_stage="CANDIDATE_PROFILE_GENERATION",
            completed_steps=["RESUME_PARSE", "CANDIDATE_PROFILE"],
            stage_results={
                "resume_parse": {
                    "status": "completed",
                    "facts": {"kb_id": "default", "name": "", "work_experience": [],
                              "projects": [], "skills": [], "raw_evidence_refs": []},
                },
                "candidate_profile": {
                    "status": "completed",
                    "profile": {"kb_id": "default", "summary": "test", "skills": ["Python"],
                                "projects": [], "experience_level": "", "evidence_refs": []},
                },
            },
        )

        job = job_repo.create_job(
            job_type="interview_preparation",
            idempotency_key=f"prep_skip_{generate_id('key')}",
            business_resource_id="interview_test_002",
            payload=payload,
        )

        handler = get_handler("interview_preparation")
        assert handler is not None

        result = await handler(job, job_repo=job_repo, **deps)

        assert result["status"] == "READY"
        assert len(result["completed_steps"]) == 5
        # 恢复逻辑清除 RESUME_PARSE/CANDIDATE_PROFILE/JOB_PROFILE，全部重跑
        assert result["completed_steps"][0] == "RESUME_PARSE"
        assert result["completed_steps"][1] == "CANDIDATE_PROFILE"
        assert "skills_count" in result["stage_results"]["resume_parse"]  # 重跑后为摘要
        assert "skills_count" in result["stage_results"]["candidate_profile"]  # 重跑后为摘要

    @pytest.mark.asyncio
    async def test_payload_tracks_current_stage(self, job_repo: JobRepository):
        """执行过程中 payload 应更新 current_stage（通过 update_payload）。"""
        deps = self._fake_deps()
        payload = self._workflow_payload(interview_id="interview_test_003")

        job = job_repo.create_job(
            job_type="interview_preparation",
            idempotency_key=f"prep_track_{generate_id('key')}",
            business_resource_id="interview_test_003",
            payload=payload,
        )

        handler = get_handler("interview_preparation")
        assert handler is not None

        result = await handler(job, job_repo=job_repo, **deps)

        # 执行完成后，payload 应反映最终 stage
        updated_job = job_repo.get_job(job.id)
        updated_payload = json.loads(updated_job.payload_json)
        assert updated_payload["current_stage"] == "READY"
        assert len(updated_payload["completed_steps"]) == 5
        assert result["status"] == "READY"

    def test_handler_declares_job_repo_dependency(self):
        """interview_preparation handler 应声明 job_repo 依赖。"""
        import inspect

        handler = get_handler("interview_preparation")
        assert handler is not None

        parameters = inspect.signature(handler).parameters
        assert "job_repo" in parameters
        assert parameters["job_repo"].kind is inspect.Parameter.KEYWORD_ONLY


class TestJobRepositoryUpdatePayload:
    """JobRepository.update_payload 方法测试。"""

    def test_update_payload_succeeds(self, job_repo: JobRepository):
        """update_payload 应更新 payload_json 并保持其他字段不变。"""
        job = job_repo.create_job(
            job_type="demo",
            idempotency_key=f"upd_pl_{generate_id('key')}",
            business_resource_id="biz_upd",
            payload={"step": 1},
        )

        new_payload = {"step": 2, "stage": "running"}
        updated = job_repo.update_payload(job.id, new_payload)

        assert json.loads(updated.payload_json) == new_payload
        assert updated.status is JobStatus.PENDING  # 状态未变
        assert updated.id == job.id

    def test_update_payload_not_found_raises(self, job_repo: JobRepository):
        """不存在的 job_id 应抛出 LookupError。"""
        with pytest.raises(LookupError):
            job_repo.update_payload("job_nonexistent", {"key": "val"})


class TestPreparationWorkerEndToEnd:
    """Preparation Workflow Worker 端到端测试。"""

    @staticmethod
    def _e2e_deps() -> dict[str, Any]:
        """构造 E2E 测试用的 mock 依赖（子 handler 可真实调用）。"""
        from unittest.mock import AsyncMock

        from liverag.interview.application.profile_service import KnowledgeContext

        # profile_source.retrieve() → 非空 context
        fake_source = AsyncMock()
        fake_source.retrieve.return_value = KnowledgeContext(
            context="测试知识库内容：候选人张三，掌握 Python、Redis 技术栈。",
            evidence_refs=("e2e_doc.pdf",),
        )

        # llm_client.chat.completions.create() → 有效 CandidateFacts JSON
        fake_llm = AsyncMock()
        fake_llm.chat = AsyncMock()
        fake_llm.chat.completions.create.return_value = _make_fake_facts_response(
            {**_MINIMAL_CANDIDATE_FACTS, "skills": ["Python", "Redis"]}
        )

        return {
            "question_bank": TestProfileGenerationTask._make_fake_question_bank(["Python", "Redis"]),
            "llm_client": fake_llm,
            "profile_source": fake_source,
        }

    @staticmethod
    def _e2e_payload(**overrides: Any) -> dict[str, Any]:
        """构造 E2E workflow payload，含必需的 kb/role 字段。"""
        base: dict[str, Any] = {
            "interview_id": "interview_e2e_001",
            "candidate_kb_id": "default",
            "target_kb_id": "default",
            "target_role": "后端工程师",
            "target_company": "某公司",
            "config_json": '{"question_count":2,"candidate_kb_id":"default","topic_weights":{"Python":1.0}}',
            "current_stage": "PENDING",
            "completed_steps": [],
            "degraded": False,
            "degradation_reasons": [],
            "stage_results": {},
        }
        base.update(overrides)
        return base

    @pytest.mark.asyncio
    async def test_worker_executes_preparation_job(
        self, job_repo: JobRepository, redis_queue: RedisQueue
    ):
        """端到端：创建 interview_preparation Job → 入队 → Worker 执行 → COMPLETED。"""
        deps = self._e2e_deps()

        worker = BackgroundWorker(
            job_repo=job_repo,
            redis_queue=redis_queue,
            question_bank=deps["question_bank"],
            llm_client=deps["llm_client"],
            profile_source=deps["profile_source"],
            llm_model="test-model",
            poll_timeout=1.0,
            task_timeout=30.0,
            max_retries=2,
        )

        payload = self._e2e_payload()

        job = job_repo.create_job(
            job_type="interview_preparation",
            idempotency_key=f"prep_e2e_{generate_id('key')}",
            business_resource_id="interview_e2e_001",
            payload=payload,
        )

        await redis_queue.enqueue(job_type="interview_preparation", job_id=job.id)

        worker_task = asyncio.create_task(worker.run())

        # 轮询等待完成
        for _ in range(60):  # 最多等 6 秒
            await asyncio.sleep(0.1)
            updated = job_repo.get_job(job.id)
            if updated.status is JobStatus.COMPLETED:
                break

        worker.request_shutdown()
        await worker_task

        # 验证最终状态
        updated = job_repo.get_job(job.id)
        assert updated.status is JobStatus.COMPLETED
        result = json.loads(updated.result_json)
        assert result["status"] == "READY"
        assert len(result["completed_steps"]) == 5
        assert "RESUME_PARSE" in result["completed_steps"]
        assert "PLAN_GENERATION" in result["completed_steps"]

    @pytest.mark.asyncio
    async def test_preparation_job_idempotent_across_restarts(
        self, job_repo: JobRepository, redis_queue: RedisQueue
    ):
        """Worker 重启后：已完成 Job 不重复执行。"""
        deps = self._e2e_deps()

        worker = BackgroundWorker(
            job_repo=job_repo,
            redis_queue=redis_queue,
            question_bank=deps["question_bank"],
            llm_client=deps["llm_client"],
            profile_source=deps["profile_source"],
            llm_model="test-model",
            poll_timeout=1.0,
            task_timeout=30.0,
            max_retries=2,
        )

        payload = self._e2e_payload(interview_id="interview_idem_001")

        job1 = job_repo.create_job(
            job_type="interview_preparation",
            idempotency_key=f"prep_idem_{generate_id('key')}",
            business_resource_id="interview_idem_001",
            payload=payload,
        )

        await redis_queue.enqueue(job_type="interview_preparation", job_id=job1.id)

        worker_task = asyncio.create_task(worker.run())

        for _ in range(60):
            await asyncio.sleep(0.1)
            updated = job_repo.get_job(job1.id)
            if updated.status is JobStatus.COMPLETED:
                break

        worker.request_shutdown()
        await worker_task

        # 验证已完成
        completed = job_repo.get_job(job1.id)
        assert completed.status is JobStatus.COMPLETED
        json.loads(completed.result_json)

        # 通过唯一约束：同一 idempotency_key 不能创建第二条 Job
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            job_repo.create_job(
                job_type="interview_preparation",
                idempotency_key=job1.idempotency_key,
                business_resource_id="interview_idem_001",
                payload=payload,
            )

    @pytest.mark.asyncio
    async def test_preparation_job_recovery_mid_stage(
        self, job_repo: JobRepository, redis_queue: RedisQueue
    ):
        """模拟中间 stage 后 Worker 重启 → 已完成 stage 应被跳过。"""
        deps = self._e2e_deps()

        # 创建一个"半完成"的 Job（前 2 个 stage 已完成）
        payload = self._e2e_payload(
            interview_id="interview_rec_001",
            current_stage="JOB_PROFILE_GENERATION",
            completed_steps=["RESUME_PARSE", "CANDIDATE_PROFILE"],
            stage_results={
                "resume_parse": {
                    "status": "completed",
                    "facts": {"kb_id": "default", "name": "", "work_experience": [],
                              "projects": [], "skills": [], "raw_evidence_refs": []},
                },
                "candidate_profile": {
                    "status": "completed",
                    "profile": {"kb_id": "default", "summary": "test", "skills": ["Python"],
                                "projects": [], "experience_level": "", "evidence_refs": []},
                },
            },
        )

        job = job_repo.create_job(
            job_type="interview_preparation",
            idempotency_key=f"prep_rec_{generate_id('key')}",
            business_resource_id="interview_rec_001",
            payload=payload,
        )

        worker = BackgroundWorker(
            job_repo=job_repo,
            redis_queue=redis_queue,
            question_bank=deps["question_bank"],
            llm_client=deps["llm_client"],
            profile_source=deps["profile_source"],
            llm_model="test-model",
            poll_timeout=1.0,
            task_timeout=30.0,
            max_retries=2,
        )

        await redis_queue.enqueue(job_type="interview_preparation", job_id=job.id)

        worker_task = asyncio.create_task(worker.run())

        for _ in range(60):
            await asyncio.sleep(0.1)
            updated = job_repo.get_job(job.id)
            if updated.status is JobStatus.COMPLETED:
                break

        worker.request_shutdown()
        await worker_task

        updated = job_repo.get_job(job.id)
        assert updated.status is JobStatus.COMPLETED
        result = json.loads(updated.result_json)
        assert len(result["completed_steps"]) == 5
        # RESUME_PARSE 旧数据保留，CANDIDATE_PROFILE 因无法恢复而重跑
        assert result["stage_results"]["resume_parse"]["status"] == "completed"
        assert result["stage_results"]["candidate_profile"]["status"] == "completed"


__all__ = [
    "TestBackgroundJobModel",
    "TestInterviewPreparationTask",
    "TestJobRepositoryUpdatePayload",
    "TestJobStatusTransitions",
    "TestPreparationStage",
    "TestPreparationWorkerEndToEnd",
    "TestProfileGenerationTask",
    "TestRedisQueue",
    "TestResumeParseTask",
    "TestTaskRegistry",
    "TestWorkerEndToEnd",
]
