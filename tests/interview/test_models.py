from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from liverag.interview.persistence.db import (
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)
from liverag.interview.persistence.models import (
    AnswerEvaluationModel,
    Base,
    CandidateProfileModel,
    InterviewAnswerModel,
    InterviewAttemptModel,
    InterviewEventModel,
    InterviewModel,
    InterviewReportModel,
    InterviewSessionModel,
    SkillProgressModel,
)
from liverag.interview.records import AnswerState, AttemptState, ReportState
from liverag.interview.schemas import InterviewState, SkillProgress

EXPECTED_TABLES = {
    "answer_evaluations",
    "candidate_profiles",
    "interview_answers",
    "interview_attempts",
    "interview_background_jobs",
    "interview_events",
    "interview_reports",
    "interview_sessions",
    "interviews",
    "skill_progress",
    "skill_progress_evidence",
}

NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)


def test_skill_progress_requires_traceable_sources() -> None:
    progress = SkillProgress(
        candidate_profile_id="candidate_profile_abc",
        skill_key="skill_python",
        taxonomy_version=1,
        attempts=2,
        average_score=70,
        current_score=75,
        latest_score=80,
        confidence=0.55,
        weak_points=[],
        source_evaluation_ids=["evaluation_1", "evaluation_2"],
        first_evaluated_at=NOW,
        last_evaluated_at=NOW,
        updated_at=NOW,
    )

    assert progress.attempts == len(progress.source_evaluation_ids)

    with pytest.raises(ValidationError, match="评价来源"):
        SkillProgress(
            candidate_profile_id="candidate_profile_abc",
            skill_key="skill_python",
            taxonomy_version=1,
            attempts=2,
            average_score=70,
            current_score=75,
            latest_score=80,
            confidence=0.55,
            weak_points=[],
            source_evaluation_ids=["evaluation_1", "evaluation_1"],
            first_evaluated_at=NOW,
            last_evaluated_at=NOW,
            updated_at=NOW,
        )

    with pytest.raises(ValidationError, match="attempts"):
        SkillProgress(
            candidate_profile_id="candidate_profile_abc",
            skill_key="skill_python",
            taxonomy_version=1,
            attempts=2,
            average_score=70,
            current_score=75,
            latest_score=80,
            confidence=0.55,
            weak_points=[],
            source_evaluation_ids=["evaluation_1"],
            first_evaluated_at=NOW,
            last_evaluated_at=NOW,
            updated_at=NOW,
        )


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
        assert {
            index["name"] for index in database.get_indexes("interviews")
        } == {"ix_interviews_candidate_profile_id"}

        interview_candidate_fk = next(
            foreign_key
            for foreign_key in database.get_foreign_keys("interviews")
            if foreign_key["referred_table"] == "candidate_profiles"
        )
        assert interview_candidate_fk["options"].get("ondelete") == "RESTRICT"
        assert next(
            column
            for column in database.get_columns("interviews")
            if column["name"] == "candidate_profile_id"
        )["nullable"] is False

        assert {constraint["name"] for constraint in database.get_unique_constraints(
            "skill_progress"
        )} == {"uq_skill_progress_candidate_skill"}
        assert {constraint["name"] for constraint in database.get_unique_constraints(
            "skill_progress_evidence"
        )} == {"uq_skill_progress_evidence_candidate_skill_evaluation"}
        progress_checks = {
            constraint["name"]
            for constraint in database.get_check_constraints("skill_progress")
        }
        assert {
            "ck_skill_progress_attempts",
            "ck_skill_progress_average_score",
            "ck_skill_progress_current_score",
            "ck_skill_progress_latest_score",
            "ck_skill_progress_confidence",
        } <= progress_checks

        answer_foreign_keys = {
            foreign_key["referred_table"]: foreign_key["options"].get("ondelete")
            for foreign_key in database.get_foreign_keys("interview_answers")
        }
        assert answer_foreign_keys == {
            "interview_attempts": "RESTRICT",
            "interview_events": "RESTRICT",
            "interview_sessions": "CASCADE",
        }
        normalized_transcript_column = next(
            column
            for column in database.get_columns("interview_answers")
            if column["name"] == "normalized_transcript"
        )
        assert normalized_transcript_column["nullable"] is True
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
        candidate_profile=CandidateProfileModel(
            id="candidate_profile_default",
            kb_id="default",
        ),
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


def test_long_term_skill_models_enforce_database_constraints(tmp_path: Path) -> None:
    engine = create_sqlite_engine(tmp_path / "constraints.db")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    try:
        with session_scope(factory) as database_session:
            candidate = CandidateProfileModel(
                id="candidate_profile_abc",
                kb_id="default",
            )
            database_session.add(candidate)

        with pytest.raises(IntegrityError), session_scope(factory) as database_session:
            database_session.add(
                CandidateProfileModel(
                    id="candidate_profile_duplicate",
                    kb_id="default",
                )
            )

        with pytest.raises(IntegrityError), session_scope(factory) as database_session:
            database_session.add(
                SkillProgressModel(
                    candidate_profile_id="candidate_profile_abc",
                    skill_key="skill_python",
                    taxonomy_version=1,
                    attempts=0,
                    average_score=70,
                    current_score=70,
                    latest_score=70,
                    confidence=0.5,
                    weak_points_json="[]",
                    source_evaluation_ids_json='["evaluation_1"]',
                    first_evaluated_at=NOW,
                    last_evaluated_at=NOW,
                )
            )
    finally:
        engine.dispose()
