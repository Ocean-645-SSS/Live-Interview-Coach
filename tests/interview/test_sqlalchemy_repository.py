"""验证 SQLAlchemy Repository 的核心持久化契约。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from liverag.interview.db import create_session_factory, create_sqlite_engine
from liverag.interview.models import Base
from liverag.interview.records import AnswerState, AttemptState, ReportState
from liverag.interview.repository import (
    ConcurrentUpdateError,
    DuplicateEventError,
    InterviewRepository,
    RecordNotFoundError,
)
from liverag.interview.schemas import (
    AnswerEvaluation,
    DimensionScores,
    FollowUpAction,
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
from liverag.interview.sqlalchemy_repository import SQLAlchemyInterviewRepository


def _plan(config: InterviewConfig) -> InterviewPlan:
    question = InterviewQuestion(
        id="question-1",
        order=1,
        type=QuestionType.TECHNICAL_KNOWLEDGE,
        source=QuestionSource.QUESTION_BANK,
        difficulty=InterviewDifficulty.INTERMEDIATE,
        category="RAG",
        subcategory="检索",
        topics=["向量检索"],
        question_text="向量检索的基本流程是什么？",
        objective="验证候选人对检索流程的理解",
        rubric=QuestionRubric(
            expected_points=[
                RubricPoint(id="flow", content="说明召回与排序", required=True)
            ]
        ),
        reference_answer="先召回，再排序。",
    )
    return InterviewPlan(
        id="plan-1",
        title="后端面试计划",
        introduction="面试开始。",
        config=config,
        questions=[question],
        closing_message="面试结束。",
    )


@pytest.fixture
def repository(
    tmp_path: Path,
) -> Iterator[InterviewRepository]:
    engine = create_sqlite_engine(tmp_path / "interview.db")
    Base.metadata.create_all(engine)
    instance = SQLAlchemyInterviewRepository(create_session_factory(engine))
    try:
        yield instance
    finally:
        engine.dispose()


def test_repository_implements_protocol_and_interview_lifecycle(
    repository: InterviewRepository,
) -> None:
    assert isinstance(repository, InterviewRepository)

    config = InterviewConfig(question_count=1)
    interview = repository.create_interview(
        interview_id="interview-1",
        title="  后端工程师模拟面试  ",
        config=config,
    )
    assert interview.title == "后端工程师模拟面试"
    assert repository.get_interview_config(interview.id) == config
    assert repository.get_interview_plan(interview.id) is None

    ready = repository.save_interview_plan(
        interview_id=interview.id,
        plan=_plan(config),
        expected_version=interview.version,
    )
    assert ready.state is InterviewState.READY
    assert ready.version == 2
    assert repository.get_interview_plan(interview.id) == _plan(config)
    assert repository.list_interviews() == [ready]

    with pytest.raises(ConcurrentUpdateError, match="版本已变化"):
        repository.update_interview_state(
            interview_id=interview.id,
            state=InterviewState.FAILED,
            expected_version=1,
        )

    interview_session = repository.create_session(
        interview_id=interview.id,
        session_id="session-1",
    )
    assert interview_session.state is InterviewState.READY
    assert repository.list_sessions(interview_id=interview.id) == [interview_session]

    updated_session = repository.update_session_snapshot(
        session_id=interview_session.id,
        expected_version=interview_session.version,
        state=InterviewState.INTRODUCTION,
        resume_state=None,
        current_question_index=0,
        current_question_id="question-1",
        follow_up_count=0,
        started_at=datetime.now(timezone.utc).isoformat(),
        ended_at=None,
    )
    assert updated_session.version == 2
    assert updated_session.state is InterviewState.INTRODUCTION

    with pytest.raises(ConcurrentUpdateError, match="版本已变化"):
        repository.update_session_snapshot(
            session_id=interview_session.id,
            expected_version=interview_session.version,
            state=InterviewState.FAILED,
            resume_state=None,
            current_question_index=0,
            current_question_id=None,
            follow_up_count=0,
            started_at=None,
            ended_at=None,
        )

    with pytest.raises(RecordNotFoundError, match="不存在"):
        repository.get_interview("missing-interview")

    with pytest.raises(ValueError, match="limit"):
        repository.list_interviews(limit=0)


def test_repository_persists_attempt_event_answer_evaluation_and_report(
    repository: InterviewRepository,
) -> None:
    config = InterviewConfig(question_count=1)
    interview = repository.create_interview(
        interview_id="interview-1",
        title="后端工程师模拟面试",
        config=config,
    )
    repository.save_interview_plan(
        interview_id=interview.id,
        plan=_plan(config),
        expected_version=interview.version,
    )
    interview_session = repository.create_session(
        interview_id=interview.id,
        session_id="session-1",
    )

    attempt = repository.create_attempt(
        session_id=interview_session.id,
        room_name="room-1",
        attempt_id="attempt-1",
    )
    connected = repository.update_attempt_state(
        attempt_id=attempt.id,
        state=AttemptState.CONNECTED,
    )
    assert connected.connected_at is not None
    assert repository.get_attempt(attempt.id) == connected
    assert repository.list_attempts(interview_session.id) == [connected]

    event = repository.record_transition(
        event_id="event-1",
        session_id=interview_session.id,
        event_type="start",
        payload={"source": "test"},
        expected_version=interview_session.version,
        state_before=InterviewState.READY,
        state_after=InterviewState.INTRODUCTION,
        resume_state=None,
        current_question_index=0,
        current_question_id="question-1",
        follow_up_count=0,
        started_at=datetime.now(timezone.utc).isoformat(),
        ended_at=None,
    )
    assert event.version_after == 2
    assert repository.event_exists(event.id)
    assert repository.list_events(session_id=interview_session.id) == [event]

    with pytest.raises(DuplicateEventError, match="已经处理"):
        repository.record_transition(
            event_id=event.id,
            session_id=interview_session.id,
            event_type="start",
            payload={},
            expected_version=interview_session.version,
            state_before=InterviewState.READY,
            state_after=InterviewState.INTRODUCTION,
            resume_state=None,
            current_question_index=0,
            current_question_id="question-1",
            follow_up_count=0,
            started_at=None,
            ended_at=None,
        )

    now = datetime.now(timezone.utc).isoformat()
    answer = repository.create_answer(
        answer_id="answer-1",
        session_id=interview_session.id,
        question_id="question-1",
        attempt_id=attempt.id,
        answer_number=1,
        transcript=" 先召回候选内容，再执行排序。 ",
        source_event_id=event.id,
        started_at=now,
        ended_at=now,
    )
    assert answer.transcript == "先召回候选内容，再执行排序。"
    assert repository.get_answer(answer.id) == answer
    assert repository.list_answers(session_id=interview_session.id) == [answer]

    evaluating = repository.update_answer_state(
        answer_id=answer.id,
        state=AnswerState.EVALUATING,
    )
    assert evaluating.state is AnswerState.EVALUATING

    with pytest.raises(DuplicateEventError, match="已经处理"):
        repository.create_answer(
            answer_id="answer-duplicate",
            session_id=interview_session.id,
            question_id="question-1",
            attempt_id=attempt.id,
            answer_number=2,
            transcript="重复回答。",
            source_event_id=event.id,
            started_at=now,
            ended_at=now,
        )

    evaluation = AnswerEvaluation(
        answer_id=answer.id,
        question_id=answer.question_id,
        scores=DimensionScores(
            technical_accuracy=4,
            completeness=3,
            clarity_and_structure=3,
            job_relevance=4,
        ),
        weighted_score=87.5,
        covered_points=["召回", "排序"],
        summary="回答覆盖了基本流程。",
        next_action=FollowUpAction.NEXT_QUESTION,
    )
    repository.save_evaluation(
        evaluation_id="evaluation-1",
        evaluation=evaluation,
    )
    assert repository.get_answer(answer.id).state is AnswerState.EVALUATED
    assert repository.get_evaluation(answer.id) == evaluation
    assert repository.list_evaluations(interview_session.id) == [evaluation]

    report = repository.create_report(
        session_id=interview_session.id,
        report_id="report-1",
    )
    assert report.state is ReportState.PENDING
    generating = repository.start_report_generation(report.id)
    assert generating.state is ReportState.GENERATING
    failed = repository.fail_report(
        report_id=report.id,
        error_message="模型暂时不可用",
    )
    assert failed.state is ReportState.FAILED
    assert failed.error_message == "模型暂时不可用"
    restarted = repository.start_report_generation(report.id)
    assert restarted.state is ReportState.GENERATING
    completed = repository.complete_report(
        report_id=report.id,
        content={"score": 87.5},
    )
    assert completed.state is ReportState.COMPLETED
    assert repository.get_report_by_session(interview_session.id) == completed
