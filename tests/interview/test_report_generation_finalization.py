"""异步报告生成的投递与面试完成状态测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from liverag.interview.application.controller import InterviewAgentController
from liverag.interview.application.orchestrator import AnswerReceivedCommand
from liverag.interview.application.service import InterviewService
from liverag.interview.jobs.report_generation import (
    enqueue_report_generation,
    finalize_report_generation,
)
from liverag.interview.jobs.repository import JobRepository
from liverag.interview.jobs.tasks import report_generation_task
from liverag.interview.persistence.db import create_session_factory, create_sqlite_engine
from liverag.interview.persistence.models import Base
from liverag.interview.persistence.sqlalchemy_repository import SQLAlchemyInterviewRepository
from liverag.interview.records import JobStatus, utc_now_iso
from liverag.interview.schemas import (
    InterviewConfig,
    InterviewDifficulty,
    InterviewPlan,
    InterviewQuestion,
    InterviewState,
    QuestionRubric,
    QuestionSource,
    QuestionType,
    RubricPoint,
)
from liverag.interview.state_machine import InterviewEventType


def _plan(config: InterviewConfig) -> InterviewPlan:
    return InterviewPlan(
        id="plan-report-async",
        title="报告异步测试",
        introduction="欢迎。",
        config=config,
        questions=[
            InterviewQuestion(
                id="question-report-async",
                order=1,
                type=QuestionType.TECHNICAL_KNOWLEDGE,
                source=QuestionSource.QUESTION_BANK,
                difficulty=InterviewDifficulty.INTERMEDIATE,
                category="后端",
                subcategory="缓存",
                topics=["Redis"],
                question_text="Redis 有什么作用？",
                objective="测试报告异步收尾",
                rubric=QuestionRubric(
                    expected_points=[RubricPoint(id="redis", content="说明缓存用途")]
                ),
            )
        ],
        closing_message="本次面试结束。",
    )


@pytest.fixture
def repositories(tmp_path: Path):
    engine = create_sqlite_engine(tmp_path / "report-generation.db")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    interview_repo = SQLAlchemyInterviewRepository(session_factory)
    job_repo = JobRepository(session_factory)
    try:
        yield interview_repo, job_repo
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_report_generation_is_idempotent(repositories) -> None:
    interview_repo, job_repo = repositories
    service = InterviewService(interview_repo)
    config = InterviewConfig(question_count=1)
    interview = service.create_interview(title="报告任务", config=config)
    interview_repo.save_interview_plan(
        interview_id=interview.id,
        plan=_plan(config),
        expected_version=interview.version,
    )
    session = service.create_session(interview.id)
    queue = AsyncMock()

    first = await enqueue_report_generation(
        interview_repo=interview_repo,
        job_repo=job_repo,
        redis_queue=queue,
        session_id=session.id,
    )
    second = await enqueue_report_generation(
        interview_repo=interview_repo,
        job_repo=job_repo,
        redis_queue=queue,
        session_id=session.id,
    )

    assert first["status"] == JobStatus.PENDING.value
    assert second == first
    assert job_repo.find_by_idempotency(
        job_type="report_generation", idempotency_key=f"report:{session.id}"
    ) is not None
    queue.enqueue.assert_awaited_once()


def _create_completing_session(interview_repo: SQLAlchemyInterviewRepository) -> str:
    service = InterviewService(interview_repo)
    config = InterviewConfig(question_count=1)
    interview = service.create_interview(title="报告完成", config=config)
    interview_repo.save_interview_plan(
        interview_id=interview.id,
        plan=_plan(config),
        expected_version=interview.version,
    )
    session = service.create_session(interview.id)
    attempt = interview_repo.create_attempt(session_id=session.id, room_name="room-report-async")
    for number, event_type in enumerate(
        (
            InterviewEventType.START,
            InterviewEventType.INTRODUCTION_FINISHED,
            InterviewEventType.QUESTION_ASKED,
        ),
        start=1,
    ):
        service.transition(
            session_id=session.id,
            event_id=f"event-report-{number}",
            event_type=event_type,
        )
    now = utc_now_iso()
    service.receive_answer(
        AnswerReceivedCommand(
            session_id=session.id,
            attempt_id=attempt.id,
            event_id="event-report-answer",
            transcript="用于完成报告的回答。",
            answer_number=1,
            started_at=now,
            ended_at=now,
        )
    )
    service.transition(
        session_id=session.id,
        event_id="event-report-finish",
        event_type=InterviewEventType.FINISH,
    )
    return session.id


def test_finalize_report_generation_completes_a_completing_session(repositories) -> None:
    interview_repo, _ = repositories
    session_id = _create_completing_session(interview_repo)

    completed = finalize_report_generation(interview_repo=interview_repo, session_id=session_id)
    repeated = finalize_report_generation(interview_repo=interview_repo, session_id=session_id)

    assert completed.state is InterviewState.COMPLETED
    assert repeated.state is InterviewState.COMPLETED


@pytest.mark.asyncio
async def test_controller_enqueues_report_without_marking_session_completed(repositories) -> None:
    interview_repo, _ = repositories
    session_id = _create_completing_session(interview_repo)
    enqueue = AsyncMock()
    controller = InterviewAgentController(
        service=InterviewService(interview_repo),
        session_id=session_id,
        attempt_id="attempt-report-async",
        enqueue_report_generation=enqueue,
    )

    session = await controller.complete()

    enqueue.assert_awaited_once_with(session_id)
    assert session.state is InterviewState.COMPLETING


@pytest.mark.asyncio
async def test_report_generation_task_finalizes_session_after_persisting_report(repositories) -> None:
    interview_repo, job_repo = repositories
    session_id = _create_completing_session(interview_repo)
    job = job_repo.create_job(
        job_type="report_generation",
        idempotency_key=f"report:{session_id}",
        business_resource_id=session_id,
        payload={"session_id": session_id},
    )

    result = await report_generation_task(job, interview_repo=interview_repo)

    assert result["state"] == "COMPLETED"
    assert interview_repo.get_report_by_session(session_id) is not None
    assert interview_repo.get_session(session_id).state is InterviewState.COMPLETED


@pytest.mark.asyncio
async def test_report_generation_recovers_stale_generating_report(repositories) -> None:
    interview_repo, job_repo = repositories
    session_id = _create_completing_session(interview_repo)
    report = interview_repo.create_report(session_id=session_id)
    interview_repo.start_report_generation(report.id)
    job = job_repo.create_job(
        job_type="report_generation",
        idempotency_key=f"report:{session_id}",
        business_resource_id=session_id,
        payload={"session_id": session_id},
    )
    queue = AsyncMock()
    queue.acquire_lock.return_value = "recovery-token"

    result = await report_generation_task(
        job,
        interview_repo=interview_repo,
        redis_queue=queue,
        stale_after_seconds=0,
    )

    assert result["state"] == "COMPLETED"
    assert interview_repo.get_report_by_session(session_id).state.value == "COMPLETED"
    queue.acquire_lock.assert_awaited_once()
    queue.release_lock.assert_awaited_once()
