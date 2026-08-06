from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, select

from liverag.interview.persistence.db import (
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)
from liverag.interview.persistence.models import (
    AnswerEvaluationModel,
    Base,
    InterviewAnswerModel,
    InterviewAttemptModel,
    InterviewEventModel,
    InterviewModel,
    InterviewReportModel,
    InterviewSessionModel,
)
from liverag.interview.records import AnswerState, AttemptState, ReportState
from liverag.interview.schemas import InterviewState

EXPECTED_TABLES = {
    "answer_evaluations",
    "interview_answers",
    "interview_attempts",
    "interview_background_jobs",
    "interview_events",
    "interview_reports",
    "interview_sessions",
    "interviews",
}


def test_metadata_creates_complete_interview_schema(tmp_path: Path) -> None:
    engine = create_sqlite_engine(tmp_path / "interview.db")

    try:
        Base.metadata.create_all(engine)
        database = inspect(engine)

        assert set(database.get_table_names()) == EXPECTED_TABLES
        assert {index["name"] for index in database.get_indexes("interview_sessions")} == {
            "idx_interview_sessions_interview_id"
        }
        assert {index["name"] for index in database.get_indexes("interview_events")} == {
            "idx_interview_events_session_id"
        }

        answer_foreign_keys = {
            foreign_key["referred_table"]: foreign_key["options"].get("ondelete")
            for foreign_key in database.get_foreign_keys("interview_answers")
        }
        assert answer_foreign_keys == {
            "interview_attempts": "RESTRICT",
            "interview_events": "RESTRICT",
            "interview_sessions": "CASCADE",
        }
    finally:
        engine.dispose()


def test_models_persist_domain_graph_with_defaults(tmp_path: Path) -> None:
    engine = create_sqlite_engine(tmp_path / "interview.db")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    answer_started_at = datetime.now(timezone.utc)

    interview = InterviewModel(
        id="interview_1",
        title="后端工程师模拟面试",
        state=InterviewState.READY,
        config_json="{}",
    )
    interview_session = InterviewSessionModel(
        id="session_1",
        interview=interview,
        state=InterviewState.READY,
    )
    attempt = InterviewAttemptModel(
        id="attempt_1",
        session=interview_session,
        room_name="interview-room-1",
    )
    event = InterviewEventModel(
        id="event_1",
        session=interview_session,
        event_type="answer_received",
        payload_json="{}",
        state_before=InterviewState.LISTENING,
        state_after=InterviewState.EVALUATING,
        version_before=1,
        version_after=2,
    )
    answer = InterviewAnswerModel(
        id="answer_1",
        session=interview_session,
        attempt=attempt,
        source_event=event,
        question_id="question_1",
        answer_number=1,
        transcript="这是最终回答。",
        started_at=answer_started_at,
        ended_at=answer_started_at,
    )
    answer.evaluation = AnswerEvaluationModel(
        id="evaluation_1",
        evaluation_json='{"weighted_score": 80}',
    )
    interview_session.report = InterviewReportModel(id="report_1")

    try:
        with session_scope(factory) as database_session:
            database_session.add(interview)

        with session_scope(factory) as database_session:
            stored = database_session.scalars(
                select(InterviewModel).where(InterviewModel.id == "interview_1")
            ).one()
            stored_session = stored.sessions[0]
            stored_answer = stored_session.answers[0]

            assert stored.version == 1
            assert stored_session.current_question_index == 0
            assert stored_session.follow_up_count == 0
            assert stored_session.version == 1
            assert stored_session.attempts[0].state is AttemptState.CREATED
            assert stored_answer.state is AnswerState.RECEIVED
            assert stored_answer.evaluation is not None
            assert stored_answer.evaluation.rubric_version == 1
            assert stored_session.report is not None
            assert stored_session.report.state is ReportState.PENDING
    finally:
        engine.dispose()
