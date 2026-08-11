"""验证 SQLAlchemy Repository 的核心持久化契约。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from liverag.interview.persistence.db import create_session_factory, create_sqlite_engine
from liverag.interview.persistence.models import Base
from liverag.interview.persistence.repository import (
    AnswerTransitionResult,
    ConcurrentUpdateError,
    DuplicateEventError,
    InterviewRepository,
    RecordNotFoundError,
)
from liverag.interview.persistence.sqlalchemy_repository import (
    SQLAlchemyInterviewRepository,
)
from liverag.interview.records import (
    AnswerState,
    AttemptState,
    InterviewAttemptRecord,
    InterviewSessionRecord,
    ReportState,
)
from liverag.interview.schemas import (
    AnswerEvaluation,
    CandidateProfile,
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
    SkillProgressEvidence,
    TranscriptCorrection,
)


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


def _listening_session_with_attempt(
    repository: InterviewRepository,
) -> tuple[InterviewSessionRecord, InterviewAttemptRecord]:
    config = InterviewConfig(question_count=1)
    interview = repository.create_interview(
        interview_id="interview-atomic",
        title="原子回答事务测试",
        config=config,
    )
    repository.save_interview_plan(
        interview_id=interview.id,
        plan=_plan(config),
        expected_version=interview.version,
    )
    created_session = repository.create_session(
        interview_id=interview.id,
        session_id="session-atomic",
    )
    listening_session = repository.update_session_snapshot(
        session_id=created_session.id,
        expected_version=created_session.version,
        state=InterviewState.LISTENING,
        resume_state=None,
        current_question_index=0,
        current_question_id="question-1",
        follow_up_count=0,
        started_at=datetime.now(timezone.utc).isoformat(),
        ended_at=None,
    )
    attempt = repository.create_attempt(
        session_id=listening_session.id,
        room_name="room-atomic",
        attempt_id="attempt-atomic",
    )
    return listening_session, attempt


def _record_final_answer(
    repository: InterviewRepository,
    *,
    event_id: str,
    session: InterviewSessionRecord,
    attempt: InterviewAttemptRecord,
    expected_version: int,
    answer_id: str,
    answer_number: int = 1,
) -> AnswerTransitionResult:
    now = datetime.now(timezone.utc).isoformat()
    return repository.record_answer_transition(
        event_id=event_id,
        session_id=session.id,
        event_type="answer_received",
        payload={"question_id": "question-1"},
        expected_version=expected_version,
        state_before=InterviewState.LISTENING,
        state_after=InterviewState.EVALUATING,
        resume_state=None,
        current_question_index=0,
        current_question_id="question-1",
        follow_up_count=0,
        session_started_at=session.started_at,
        session_ended_at=None,
        question_id="question-1",
        attempt_id=attempt.id,
        answer_number=answer_number,
        transcript=" 先召回候选内容，再执行排序。 ",
        answer_started_at=now,
        answer_ended_at=now,
        answer_id=answer_id,
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
        transcript=" 我们用卡夫卡处理消息。 ",
        source_event_id=event.id,
        started_at=now,
        ended_at=now,
    )
    assert answer.transcript == "我们用卡夫卡处理消息。"
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
        normalized_transcript="我们用Kafka处理消息。",
        transcript_corrections=[
            TranscriptCorrection(
                original="卡夫卡",
                replacement="Kafka",
                confidence=0.97,
                reason="homophone",
            )
        ],
        summary="回答覆盖了基本流程。",
        next_action=FollowUpAction.NEXT_QUESTION,
    )
    repository.save_evaluation(
        evaluation_id="evaluation-1",
        evaluation=evaluation,
    )
    stored_answer = repository.get_answer(answer.id)
    assert stored_answer.state is AnswerState.EVALUATED
    assert stored_answer.transcript == "我们用卡夫卡处理消息。"
    assert stored_answer.normalized_transcript == "我们用Kafka处理消息。"
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


def test_answer_transition_atomically_persists_three_records(
    repository: InterviewRepository,
) -> None:
    session, attempt = _listening_session_with_attempt(repository)

    with pytest.raises(ConcurrentUpdateError, match="已发生变化"):
        _record_final_answer(
            repository,
            event_id="event-stale",
            session=session,
            attempt=attempt,
            expected_version=session.version - 1,
            answer_id="answer-stale",
        )

    assert repository.get_session(session.id) == session
    assert not repository.event_exists("event-stale")
    assert repository.list_answers(session_id=session.id) == []

    result = _record_final_answer(
        repository,
        event_id="event-answer",
        session=session,
        attempt=attempt,
        expected_version=session.version,
        answer_id="answer-atomic",
    )

    assert result.session.state is InterviewState.EVALUATING
    assert result.session.version == session.version + 1
    assert result.event.id == "event-answer"
    assert result.event.version_after == result.session.version
    assert result.answer.id == "answer-atomic"
    assert result.answer.source_event_id == result.event.id
    assert result.answer.transcript == "先召回候选内容，再执行排序。"
    assert repository.get_session(session.id) == result.session
    assert repository.list_events(session_id=session.id) == [result.event]
    assert repository.list_answers(session_id=session.id) == [result.answer]

    with pytest.raises(DuplicateEventError, match="已经处理"):
        _record_final_answer(
            repository,
            event_id="event-answer",
            session=session,
            attempt=attempt,
            expected_version=session.version,
            answer_id="answer-duplicate",
        )

    assert len(repository.list_events(session_id=session.id)) == 1
    assert len(repository.list_answers(session_id=session.id)) == 1


def test_answer_constraint_failure_rolls_back_session_event_and_answer(
    repository: InterviewRepository,
) -> None:
    session, attempt = _listening_session_with_attempt(repository)
    now = datetime.now(timezone.utc).isoformat()
    existing_event = repository.record_transition(
        event_id="event-existing",
        session_id=session.id,
        event_type="answer_seeded",
        payload={},
        expected_version=session.version,
        state_before=InterviewState.LISTENING,
        state_after=InterviewState.LISTENING,
        resume_state=None,
        current_question_index=0,
        current_question_id="question-1",
        follow_up_count=0,
        started_at=session.started_at,
        ended_at=None,
    )
    repository.create_answer(
        answer_id="answer-existing",
        session_id=session.id,
        question_id="question-1",
        attempt_id=attempt.id,
        answer_number=1,
        transcript="已有回答",
        source_event_id=existing_event.id,
        started_at=now,
        ended_at=now,
    )
    before = repository.get_session(session.id)

    with pytest.raises(IntegrityError):
        _record_final_answer(
            repository,
            event_id="event-rollback",
            session=before,
            attempt=attempt,
            expected_version=before.version,
            answer_id="answer-rollback",
            answer_number=1,
        )

    assert repository.get_session(session.id) == before
    assert not repository.event_exists("event-rollback")
    assert [event.id for event in repository.list_events(session_id=session.id)] == [
        "event-existing"
    ]
    assert [answer.id for answer in repository.list_answers(session_id=session.id)] == [
        "answer-existing"
    ]


def test_candidate_profile_and_evaluation_metadata_are_traceable(
    repository: InterviewRepository,
) -> None:
    session, attempt = _listening_session_with_attempt(repository)
    result = _record_final_answer(
        repository,
        event_id="event-metadata",
        session=session,
        attempt=attempt,
        expected_version=session.version,
        answer_id="answer-metadata",
    )
    evaluation = AnswerEvaluation(
        answer_id=result.answer.id,
        question_id=result.answer.question_id,
        scores=DimensionScores(
            technical_accuracy=3,
            completeness=3,
            clarity_and_structure=4,
            job_relevance=4,
        ),
        weighted_score=70,
        missing_points=["重排序"],
        summary="缺少重排序说明。",
        next_action=FollowUpAction.NEXT_QUESTION,
    )
    repository.save_evaluation(
        evaluation_id="evaluation-metadata",
        evaluation=evaluation,
        rubric_version=2,
    )

    interview = repository.get_interview("interview-atomic")
    candidate = repository.get_candidate_profile(interview.candidate_profile_id)
    assert candidate == repository.get_candidate_profile_by_kb("default")
    updated = repository.update_candidate_profile_snapshot(
        candidate_profile_id=candidate.id,
        profile=CandidateProfile(kb_id="default", summary="后端候选人"),
    )
    assert updated.latest_profile_json is not None

    record = repository.get_evaluation_record(result.answer.id)
    assert record.id == "evaluation-metadata"
    assert record.rubric_version == 2
    assert record.evaluation == evaluation
    assert record.session_id == session.id
    assert record.interview_id == interview.id
    assert repository.list_evaluation_records_for_candidate(candidate.id) == [record]


def test_duplicate_evidence_is_idempotent(repository: InterviewRepository) -> None:
    session, attempt = _listening_session_with_attempt(repository)
    result = _record_final_answer(
        repository,
        event_id="event-evidence",
        session=session,
        attempt=attempt,
        expected_version=session.version,
        answer_id="answer-evidence",
    )
    evaluation = AnswerEvaluation(
        answer_id=result.answer.id,
        question_id=result.answer.question_id,
        scores=DimensionScores(
            technical_accuracy=2,
            completeness=3,
            clarity_and_structure=3,
            job_relevance=3,
        ),
        weighted_score=55,
        missing_points=["  重排序。  "],
        summary="需要补充。",
        next_action=FollowUpAction.NEXT_QUESTION,
    )
    repository.save_evaluation(evaluation_id="evaluation-evidence", evaluation=evaluation)
    candidate_id = repository.get_interview("interview-atomic").candidate_profile_id
    evaluated_at = datetime.now(timezone.utc)
    evidence = SkillProgressEvidence(
        id="evidence-1",
        candidate_profile_id=candidate_id,
        skill_key="skill-rag-retrieval",
        evaluation_id="evaluation-evidence",
        session_id=session.id,
        interview_id="interview-atomic",
        question_id="question-1",
        taxonomy_version=1,
        rubric_version=1,
        score=55,
        weak_points=["  重排序。  "],
        evaluated_at=evaluated_at,
        created_at=evaluated_at,
    )

    first = repository.apply_skill_evidence(evidence)
    second = repository.apply_skill_evidence(evidence)

    assert second == first
    assert second.attempts == 1
    assert second.source_evaluation_ids == [evidence.evaluation_id]
    assert second.weak_points[0].text == "重排序"
    persisted = repository.list_skill_evidence(
        candidate_profile_id=candidate_id,
        skill_key=evidence.skill_key,
    )
    assert len(persisted) == 1
    assert persisted[0].evaluation_id == evidence.evaluation_id
