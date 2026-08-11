"""报告异步任务的共享投递和完成状态处理。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException, status

from liverag.interview.application.orchestrator import InterviewOrchestrator
from liverag.interview.jobs.queue import RedisQueue
from liverag.interview.jobs.repository import JobRepository
from liverag.interview.persistence.repository import InterviewRepository
from liverag.interview.records import JobStatus
from liverag.interview.schemas import InterviewState
from liverag.interview.state_machine import InterviewEventType

REPORT_GENERATION_LOCK_TTL_SECONDS = 60


async def enqueue_report_generation(
    *,
    interview_repo: InterviewRepository,
    job_repo: JobRepository,
    redis_queue: RedisQueue,
    session_id: str,
) -> dict[str, Any]:
    """创建或复用 Session 对应的报告生成任务，并将其投递到 Redis。"""

    interview_repo.get_session(session_id)
    idempotency_key = f"report:{session_id}"
    existing = job_repo.find_by_idempotency(
        job_type="report_generation",
        idempotency_key=idempotency_key,
    )
    if existing is not None and existing.status in {
        JobStatus.COMPLETED,
        JobStatus.PENDING,
        JobStatus.QUEUED,
        JobStatus.RUNNING,
    }:
        return {"job_id": existing.id, "status": existing.status.value}

    lock_token = await redis_queue.acquire_lock(
        job_type="report_generation_job",
        resource_id=session_id,
        ttl=10,
    )
    if lock_token is None:
        await asyncio.sleep(0.1)
        retry_existing = job_repo.find_by_idempotency(
            job_type="report_generation",
            idempotency_key=idempotency_key,
        )
        if retry_existing is not None:
            return {"job_id": retry_existing.id, "status": retry_existing.status.value}
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="报告生成任务正在创建中，请稍后重试",
        )

    try:
        job = job_repo.create_job(
            job_type="report_generation",
            idempotency_key=idempotency_key,
            business_resource_id=session_id,
            payload={"session_id": session_id},
            max_attempts=3,
        )
        await redis_queue.enqueue(job_type="report_generation", job_id=job.id)
        return {"job_id": job.id, "status": job.status.value}
    finally:
        await redis_queue.release_lock(
            job_type="report_generation_job",
            resource_id=session_id,
            lock_token=lock_token,
        )


def finalize_report_generation(
    *,
    interview_repo: InterviewRepository,
    session_id: str,
):
    """仅在报告已持久化后，将实时面试从 COMPLETING 标记为 COMPLETED。"""

    session = interview_repo.get_session(session_id)
    if session.state is InterviewState.COMPLETED:
        return session
    if session.state is not InterviewState.COMPLETING:
        return session
    return InterviewOrchestrator(interview_repo).transition(
        session_id=session_id,
        event_id=f"report_completed:{session_id}",
        event_type=InterviewEventType.REPORT_COMPLETED,
    ).session


__all__ = [
    "REPORT_GENERATION_LOCK_TTL_SECONDS",
    "enqueue_report_generation",
    "finalize_report_generation",
]
