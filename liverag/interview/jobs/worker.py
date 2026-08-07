"""Background Worker 异步主循环。

职责：
1. 从 Redis 队列中 BLPOP 取出 job_id
2. 查询 PostgreSQL 获取完整 Job 信息
3. 幂等检查（已完成则跳过）
4. 执行已注册的任务处理器
5. 更新 PostgreSQL 状态为 COMPLETED 或 FAILED

Worker 不直接访问业务模型，只通过 JobRepository 和任务处理器操作。
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Any

from openai import AsyncOpenAI

from liverag.interview.application.profile_service import KnowledgeContextSource
from liverag.interview.jobs.queue import RedisQueue
from liverag.interview.jobs.repository import BackgroundJobRecord, JobRepository
from liverag.interview.jobs.tasks import get_handler, registered_types
from liverag.interview.persistence.repository import InterviewRepository
from liverag.interview.records import JobStatus
from liverag.interview.question_bank.catalog import QuestionBank
from liverag.agent.tool.rag_client import RagClient

logger = logging.getLogger("liverag.interview.jobs.worker")


class BackgroundWorker:
    """从 Redis 消费 Job 并异步执行的后台 Worker。"""

    def __init__(
        self,
        *,
        job_repo: JobRepository,   #BackgroundJob的repository层
        redis_queue: RedisQueue,    #管理Redis的队列和锁
        question_bank: QuestionBank,
        llm_model: str,
        llm_client: AsyncOpenAI,
        profile_source: KnowledgeContextSource,
        rag_client: RagClient | None = None,
        interview_repo: InterviewRepository | None = None,
        poll_timeout: float = 5.0,  #轮询间隔
        task_timeout: float = 300.0,   #超时时间
        max_retries: int = 3,   #最大轮次
    ) -> None:
        self._repo = job_repo
        self._queue = redis_queue
        self._question_bank = question_bank
        self._llm_client = llm_client
        self._profile_source = profile_source
        self._llm_model = llm_model
        self._rag_client = rag_client
        self._interview_repo = interview_repo
        self._poll_timeout = poll_timeout
        self._task_timeout = task_timeout
        self._max_retries = max_retries
        self._shutdown_event = asyncio.Event()  #初始状态
        self._running = False

    # ================================ 生命周期 =================================
    async def run(self) -> None:
        """启动 Worker 主循环，直到收到关闭信号。"""

        self._running = True
        logger.info(
            "Worker 启动",
            extra={
                "poll_timeout": self._poll_timeout,
                "task_timeout": self._task_timeout,
                "max_retries": self._max_retries,
                "registered_job_types": registered_types(),
            },
        )

        #未收到退出循环的信号
        while not self._shutdown_event.is_set():
            try:
                await self._poll_and_execute()
            except Exception:
                logger.exception("Worker 主循环异常，继续运行")

        #收到退出循环的新号：停止运行worker
        self._running = False
        logger.info("Worker 已停止")

    def request_shutdown(self) -> None:
        """发送关闭信号，Worker 在完成当前任务后退出。"""

        logger.info("收到关闭信号")
        #设置退出循环新号
        self._shutdown_event.set()

    # ================================ 核心循环 =================================
    async def _poll_and_execute(self) -> None:
        """兜底扫描 PostgreSQL PENDING Job + 从 Redis 获取下一个 Job 并执行。"""

        # 兜底扫描：把数据库中 PENDING 但不在 Redis 队列里的 Job 补入队
        await self._backfill_pending_jobs()

        # 从 Redis 阻塞获取下一个 job_id
        acquired = False    #当前队列是否有job
        for job_type in registered_types():
            #出队获取job
            job_id = await self._queue.dequeue(
                job_type=job_type, timeout=self._poll_timeout
            )
            #处理任务
            if job_id is not None:
                acquired = True
                await self._execute_job(job_id)
                # 一次只处理一个，回到循环顶部重新扫描
                break  

        # 所有队列为空，短暂等待后重试（防止忙轮询）
        if not acquired:
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=1.0
                )
            except asyncio.TimeoutError:
                pass

    async def _backfill_pending_jobs(self) -> None:
        """扫描所有已注册 job_type 的 PENDING Job，补入 Redis 队列。

        这保证了 Redis 重启后丢失的队列数据可以从 PostgreSQL 恢复。
        """

        for job_type in registered_types():
            #列出所有PENDING状态的jobs
            pending = self._repo.list_pending_jobs(job_type=job_type, limit=20)
            #遍历jobs
            for job in pending:
                # 通过幂等锁避免重复入队
                #获取幂等锁
                if await self._queue.acquire_lock(
                    job_type=job.job_type,
                    resource_id=job.business_resource_id,
                    ttl=60,
                ):
                    #入队
                    await self._queue.enqueue(
                        job_type=job.job_type, job_id=job.id
                    )
                    #标记job为QUEUED状态
                    self._repo.mark_queued(job.id)
                    #释放锁
                    await self._queue.release_lock(
                        job_type=job.job_type, resource_id=job.business_resource_id
                    )

    async def _execute_job(self, job_id: str) -> None:
        """执行单条 Job：幂等检查 → 获取处理器 → 超时执行 → 写回结果。"""

        #根据job_id获取job
        try:
            job = self._repo.get_job(job_id)
        except LookupError:
            logger.error("Job 记录不存在", extra={"job_id": job_id})
            return

        #幂等检查
        #已完成->跳过
        if job.status is JobStatus.COMPLETED:
            logger.info("Job 已完成，跳过", extra={"job_id": job_id})
            return
        #失败且超过重试次数->跳过
        if job.status is JobStatus.FAILED and job.attempt >= job.max_attempts:
            logger.info(
                "Job 已达最大重试次数，跳过",
                extra={"job_id": job_id, "attempt": job.attempt},
            )
            return

        #获得任务注册器
        handler = get_handler(job.job_type)
        if handler is None:
            logger.error(
                "未注册的任务类型",
                extra={"job_id": job_id, "job_type": job.job_type},
            )
            #数据库标记错误信息状态
            self._repo.mark_failed(job_id, f"未注册的任务类型：{job.job_type}")
            return

        #标记运行中：QUEUE->RUNNING
        self._repo.mark_running(job_id)

        # 组装 handler 依赖注入
        handler_kwargs = {
            "job_repo": self._repo,
            "profile_source": self._profile_source,
            "llm_client": self._llm_client,
            "llm_model": self._llm_model,
            "question_bank": self._question_bank,
        }
        if self._rag_client is not None:
            handler_kwargs["rag_client"] = self._rag_client
        if self._interview_repo is not None:
            handler_kwargs["interview_repo"] = self._interview_repo

        try:
            result = await asyncio.wait_for(
                handler(job, **handler_kwargs),
                timeout=self._task_timeout,
            )
            #标记任务完成
            self._repo.mark_completed(job_id, result)
            logger.info(
                "Job 执行成功",
                extra={"job_id": job_id, "job_type": job.job_type},
            )
        #任务超时报错
        except asyncio.TimeoutError:
            error_msg = f"任务超时（{self._task_timeout}s）"
            self._repo.mark_failed(job_id, error_msg)
            logger.error("Job 超时", extra={"job_id": job_id, "error": error_msg})
        #其他原因失败报错
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            self._repo.mark_failed(job_id, error_msg)
            logger.exception(
                "Job 执行失败",
                extra={"job_id": job_id, "error": error_msg},
            )
            # 未达最大重试次数 → 自动重回 PENDING
            if job.attempt < job.max_attempts - 1:
                try:
                    #重新入队
                    self._repo.retry_job(job_id)
                    logger.info(
                        "Job 已重试入队",
                        extra={"job_id": job_id, "next_attempt": job.attempt + 2},
                    )
                except RuntimeError:
                    pass


__all__ = ["BackgroundWorker"]
