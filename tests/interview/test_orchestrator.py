"""InterviewOrchestrator 的普通事件与回答原子落库测试。"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from liverag.interview.persistence.db import create_session_factory, create_sqlite_engine
from liverag.interview.persistence.models import Base
from liverag.interview.application.orchestrator import AnswerReceivedCommand, InterviewOrchestrator
from liverag.interview.records import utc_now_iso
from liverag.interview.persistence.repository import DuplicateEventError, InterviewRepository
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
from liverag.interview.persistence.sqlalchemy_repository import SQLAlchemyInterviewRepository
from liverag.interview.state_machine import InterviewEventType, InterviewTransitionError


def _question() -> InterviewQuestion:
    return InterviewQuestion(
        id="question-1",
        order=1,
        type=QuestionType.TECHNICAL_KNOWLEDGE,
        source=QuestionSource.QUESTION_BANK,
        difficulty=InterviewDifficulty.INTERMEDIATE,
        category="RAG",
        topics=["检索增强生成"],
        question_text="RAG 的基本流程是什么？",
        objective="验证候选人是否理解 RAG",
        rubric=QuestionRubric(
            expected_points=[RubricPoint(id="flow", content="说明检索和生成流程")]
        ),
    )


@pytest.fixture
def orchestrator(
    tmp_path: Path,
) -> Iterator[tuple[InterviewOrchestrator, InterviewRepository, str, str]]:
    engine = create_sqlite_engine(tmp_path / "orchestrator.db")
    Base.metadata.create_all(engine)
    repository = SQLAlchemyInterviewRepository(create_session_factory(engine))
    config = InterviewConfig(question_count=1)
    interview = repository.create_interview(title="编排测试", config=config)
    repository.save_interview_plan(
        interview_id=interview.id,
        plan=InterviewPlan(
            id="plan-1",
            title="编排测试计划",
            introduction="欢迎参加面试。",
            config=config,
            questions=[_question()],
            closing_message="面试结束。",
        ),
        expected_version=interview.version,
    )
    session = repository.create_session(interview_id=interview.id)
    attempt = repository.create_attempt(session_id=session.id, room_name="room-1")
    try:
        yield InterviewOrchestrator(repository), repository, session.id, attempt.id
    finally:
        engine.dispose()


def _enter_listening(orchestrator: InterviewOrchestrator, session_id: str) -> None:
    for number, event_type in enumerate(
        (
            InterviewEventType.START,
            InterviewEventType.INTRODUCTION_FINISHED,
            InterviewEventType.QUESTION_ASKED,
        ),
        start=1,
    ):
        orchestrator.transition(
            session_id=session_id,
            event_id=f"event-{number}",
            event_type=event_type,
        )


def _answer_command(session_id: str, attempt_id: str) -> AnswerReceivedCommand:
    now = utc_now_iso()
    return AnswerReceivedCommand(
        session_id=session_id,
        attempt_id=attempt_id,
        event_id="event-answer-1",
        answer_id="answer-1",
        transcript="先检索相关资料，再把上下文交给模型生成答案。",
        answer_number=1,
        started_at=now,
        ended_at=now,
        payload={"source": "livekit"},
    )


def test_receive_answer_atomically_updates_session_event_and_answer(orchestrator):
    service, repository, session_id, attempt_id = orchestrator
    _enter_listening(service, session_id)

    result = service.receive_answer(_answer_command(session_id, attempt_id))

    assert result.transition.session.state is InterviewState.EVALUATING
    assert result.transition.event.id == "event-answer-1"
    assert result.answer.id == "answer-1"
    assert result.answer.question_id == "question-1"
    assert result.answer.source_event_id == result.transition.event.id
    assert repository.get_session(session_id).version == result.transition.event.version_after
    assert result.transition.event.version_after == result.transition.event.version_before + 1
    assert len(repository.list_events(session_id=session_id)) == 4
    assert repository.list_answers(session_id=session_id) == [result.answer]


def test_answer_event_cannot_bypass_receive_answer(orchestrator):
    service, repository, session_id, _ = orchestrator
    _enter_listening(service, session_id)

    with pytest.raises(InterviewTransitionError, match="receive_answer"):
        service.transition(
            session_id=session_id,
            event_id="event-answer-1",
            event_type=InterviewEventType.ANSWER_RECEIVED,
        )

    assert repository.get_session(session_id).state is InterviewState.LISTENING
    assert repository.list_answers(session_id=session_id) == []


def test_receive_answer_rejects_non_listening_session(orchestrator):
    service, repository, session_id, attempt_id = orchestrator

    with pytest.raises(InterviewTransitionError, match="不允许事件"):
        service.receive_answer(_answer_command(session_id, attempt_id))

    assert repository.list_events(session_id=session_id) == []
    assert repository.list_answers(session_id=session_id) == []


def test_duplicate_answer_event_has_no_second_business_effect(orchestrator):
    service, repository, session_id, attempt_id = orchestrator
    _enter_listening(service, session_id)
    command = _answer_command(session_id, attempt_id)
    service.receive_answer(command)

    with pytest.raises(DuplicateEventError):
        service.receive_answer(command)

    assert len(repository.list_events(session_id=session_id)) == 4
    assert len(repository.list_answers(session_id=session_id)) == 1
