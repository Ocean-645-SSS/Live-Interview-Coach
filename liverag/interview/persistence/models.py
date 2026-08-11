"""Interview 领域的 SQLAlchemy ORM 模型。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from liverag.interview.records import AnswerState, AttemptState, JobStatus, ReportState
from liverag.interview.schemas import InterviewState


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Interview ORM metadata 的统一基类。"""


class CandidateProfileModel(Base):
    """以个人资料库 ID 为稳定键的长期训练候选人主体。"""

    __tablename__ = "candidate_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    latest_profile_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    interviews: Mapped[list[InterviewModel]] = relationship(
        back_populates="candidate_profile",
        passive_deletes=True,
    )
    skill_progress: Mapped[list[SkillProgressModel]] = relationship(
        back_populates="candidate_profile",
        passive_deletes=True,
    )


class InterviewModel(Base):
    """一场面试的配置、计划和顶层状态。"""

    __tablename__ = "interviews"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_interviews_version"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[InterviewState] = mapped_column(
        SqlEnum(
            InterviewState,
            name="ck_interviews_state_values",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=InterviewState.CREATED,
        nullable=False,
    )
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_profile_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    plan_json: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    candidate_profile: Mapped[CandidateProfileModel] = relationship(
        back_populates="interviews"
    )
    sessions: Mapped[list[InterviewSessionModel]] = relationship(
        back_populates="interview",
        passive_deletes=True,
    )


class InterviewSessionModel(Base):
    """实时面试状态机的权威快照。"""

    #对应的数据库表名
    __tablename__ = "interview_sessions"
    #描述整张表规则
    __table_args__ = (
        CheckConstraint(
            "resume_state IS NULL OR resume_state IN ("
            "'INTRODUCTION', 'ASKING', 'LISTENING', 'EVALUATING', "
            "'FOLLOW_UP', 'NEXT_QUESTION', 'COMPLETING'"
            ")",
            name="ck_interview_sessions_resume_state",
        ),
        CheckConstraint(
            "current_question_index >= 0",
            name="ck_interview_sessions_question_index",
        ),
        CheckConstraint(
            "follow_up_count >= 0",
            name="ck_interview_sessions_follow_up_count",
        ),
        CheckConstraint("version >= 1", name="ck_interview_sessions_version"),
        Index(
            "idx_interview_sessions_interview_id",
            "interview_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[InterviewState] = mapped_column(
        SqlEnum(
            InterviewState,
            name="ck_interview_sessions_state_values",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=InterviewState.CREATED,
        nullable=False,
    )
    resume_state: Mapped[InterviewState | None] = mapped_column(
        SqlEnum(
            InterviewState,
            name="ck_interview_sessions_resume_state_values",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        )
    )
    current_question_index: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    current_question_id: Mapped[str | None] = mapped_column(String(255))
    follow_up_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    interview: Mapped[InterviewModel] = relationship(back_populates="sessions")
    attempts: Mapped[list[InterviewAttemptModel]] = relationship(
        back_populates="session",   #声明关系另外一端的属性名：这个表
        cascade="all, delete-orphan",   #声明级联删除和孤儿删除：删除 session 时删除所有 attempt
        passive_deletes=True,   #声明被动删除：删除 session 时不加载 attempt，而是直接在数据库中删除
    )
    events: Mapped[list[InterviewEventModel]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    answers: Mapped[list[InterviewAnswerModel]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    report: Mapped[InterviewReportModel | None] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    skill_evidence: Mapped[list[SkillProgressEvidenceModel]] = relationship(
        back_populates="session",
        passive_deletes=True,
    )


class InterviewAttemptModel(Base):
    """一次 LiveKit 房间连接尝试。"""

    __tablename__ = "interview_attempts"
    __table_args__ = (
        Index("idx_interview_attempts_session_id", "session_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    room_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    state: Mapped[AttemptState] = mapped_column(
        SqlEnum(
            AttemptState,
            name="ck_interview_attempts_state_values",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=AttemptState.CREATED,
        nullable=False,
    )
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    session: Mapped[InterviewSessionModel] = relationship(back_populates="attempts")
    answers: Mapped[list[InterviewAnswerModel]] = relationship(
        back_populates="attempt",
        passive_deletes=True,
    )


class InterviewEventModel(Base):
    """驱动状态机且只追加写入的业务事件。"""

    __tablename__ = "interview_events"
    __table_args__ = (
        CheckConstraint("version_before >= 1", name="ck_interview_events_version_before"),
        CheckConstraint(
            "version_after >= version_before",
            name="ck_interview_events_version_order",
        ),
        Index(
            "idx_interview_events_session_id",
            "session_id",
            "version_after",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    state_before: Mapped[InterviewState] = mapped_column(
        SqlEnum(
            InterviewState,
            name="ck_interview_events_state_before_values",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    state_after: Mapped[InterviewState] = mapped_column(
        SqlEnum(
            InterviewState,
            name="ck_interview_events_state_after_values",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    version_before: Mapped[int] = mapped_column(Integer, nullable=False)
    version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    session: Mapped[InterviewSessionModel] = relationship(back_populates="events")
    answer: Mapped[InterviewAnswerModel | None] = relationship(
        back_populates="source_event",
        passive_deletes=True,
        uselist=False,
    )


class InterviewAnswerModel(Base):
    """候选人对一道题的一次最终回答。"""

    __tablename__ = "interview_answers"
    __table_args__ = (
        CheckConstraint("answer_number >= 1", name="ck_interview_answers_number"),
        CheckConstraint(
            "length(trim(transcript)) > 0",
            name="ck_interview_answers_transcript",
        ),
        UniqueConstraint(
            "session_id",
            "question_id",
            "answer_number",
            name="uq_interview_answers_session_question_number",
        ),
        Index(
            "idx_interview_answers_session_question",
            "session_id",
            "question_id",
            "answer_number",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("interview_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    answer_number: Mapped[int] = mapped_column(Integer, nullable=False)
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[AnswerState] = mapped_column(
        SqlEnum(
            AnswerState,
            name="ck_interview_answers_state_values",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=AnswerState.RECEIVED,
        nullable=False,
    )
    source_event_id: Mapped[str] = mapped_column(
        ForeignKey("interview_events.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    session: Mapped[InterviewSessionModel] = relationship(back_populates="answers")
    attempt: Mapped[InterviewAttemptModel] = relationship(back_populates="answers")
    source_event: Mapped[InterviewEventModel] = relationship(back_populates="answer")
    evaluation: Mapped[AnswerEvaluationModel | None] = relationship(
        back_populates="answer",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class AnswerEvaluationModel(Base):
    """回答的结构化评价快照。"""

    __tablename__ = "answer_evaluations"
    __table_args__ = (
        CheckConstraint("rubric_version >= 1", name="ck_answer_evaluations_rubric_version"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    answer_id: Mapped[str] = mapped_column(
        ForeignKey("interview_answers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    rubric_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    evaluation_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    answer: Mapped[InterviewAnswerModel] = relationship(back_populates="evaluation")
    skill_evidence: Mapped[list[SkillProgressEvidenceModel]] = relationship(
        back_populates="evaluation",
        passive_deletes=True,
    )


class SkillProgressModel(Base):
    """由全部评价证据重算得到的候选人技能快照。"""

    __tablename__ = "skill_progress"
    __table_args__ = (
        CheckConstraint("taxonomy_version >= 1", name="ck_skill_progress_taxonomy"),
        CheckConstraint("attempts >= 1", name="ck_skill_progress_attempts"),
        CheckConstraint(
            "average_score >= 0 AND average_score <= 100",
            name="ck_skill_progress_average_score",
        ),
        CheckConstraint(
            "current_score >= 0 AND current_score <= 100",
            name="ck_skill_progress_current_score",
        ),
        CheckConstraint(
            "latest_score >= 0 AND latest_score <= 100",
            name="ck_skill_progress_latest_score",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_skill_progress_confidence",
        ),
        UniqueConstraint(
            "candidate_profile_id",
            "skill_key",
            name="uq_skill_progress_candidate_skill",
        ),
    )

    candidate_profile_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    skill_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    taxonomy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    average_score: Mapped[float] = mapped_column(Float, nullable=False)
    current_score: Mapped[float] = mapped_column(Float, nullable=False)
    latest_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    weak_points_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_evaluation_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    first_evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    candidate_profile: Mapped[CandidateProfileModel] = relationship(
        back_populates="skill_progress"
    )
    evidence: Mapped[list[SkillProgressEvidenceModel]] = relationship(
        back_populates="skill_progress",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class SkillProgressEvidenceModel(Base):
    """一条回答评价映射到一个稳定技能后的不可变证据。"""

    __tablename__ = "skill_progress_evidence"
    __table_args__ = (
        CheckConstraint(
            "taxonomy_version >= 1", name="ck_skill_progress_evidence_taxonomy"
        ),
        CheckConstraint(
            "rubric_version >= 1", name="ck_skill_progress_evidence_rubric"
        ),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_skill_progress_evidence_score",
        ),
        ForeignKeyConstraint(
            ["candidate_profile_id", "skill_key"],
            ["skill_progress.candidate_profile_id", "skill_progress.skill_key"],
            ondelete="CASCADE",
            name="fk_skill_progress_evidence_progress",
        ),
        UniqueConstraint(
            "candidate_profile_id",
            "skill_key",
            "evaluation_id",
            name="uq_skill_progress_evidence_candidate_skill_evaluation",
        ),
        Index(
            "idx_skill_progress_evidence_candidate_skill_time",
            "candidate_profile_id",
            "skill_key",
            "evaluated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    skill_key: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("answer_evaluations.id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(255), nullable=False)
    taxonomy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    rubric_version: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    weak_points_json: Mapped[str] = mapped_column(Text, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    skill_progress: Mapped[SkillProgressModel] = relationship(
        back_populates="evidence"
    )
    evaluation: Mapped[AnswerEvaluationModel] = relationship(
        back_populates="skill_evidence"
    )
    session: Mapped[InterviewSessionModel] = relationship(
        back_populates="skill_evidence"
    )


class InterviewReportModel(Base):
    """一场面试最终报告的生成状态和内容。"""

    __tablename__ = "interview_reports"
    __table_args__ = (
        Index("idx_interview_reports_state", "state", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    state: Mapped[ReportState] = mapped_column(
        SqlEnum(
            ReportState,
            name="ck_interview_reports_state_values",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=ReportState.PENDING,
        nullable=False,
    )
    content_json: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[InterviewSessionModel] = relationship(back_populates="report")


class BackgroundJobModel(Base):
    """持久化后台异步任务，由 API 写入、Worker 消费。

    Redis 只保存队列与可重建协调状态，权威状态永远存储在 PostgreSQL 中。
    相同 job_type + idempotency_key 由数据库唯一约束保证幂等。
    """

    __tablename__ = "interview_background_jobs"
    __table_args__ = (
        CheckConstraint("attempt >= 0", name="ck_background_jobs_attempt"),
        CheckConstraint("max_attempts >= 1", name="ck_background_jobs_max_attempts"),
        UniqueConstraint(
            "job_type",
            "idempotency_key",
            name="uq_background_jobs_idempotency",
        ),
        Index("idx_background_jobs_status", "status", "created_at"),
        Index("idx_background_jobs_running_lease", "status", "lease_expires_at"),
        Index(
            "idx_background_jobs_resource",
            "job_type",
            "business_resource_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        SqlEnum(
            JobStatus,
            name="ck_background_jobs_status_values",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=JobStatus.PENDING,
        nullable=False,
    )
    business_resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )


__all__ = [
    "AnswerEvaluationModel",
    "BackgroundJobModel",
    "Base",
    "CandidateProfileModel",
    "InterviewAnswerModel",
    "InterviewAttemptModel",
    "InterviewEventModel",
    "InterviewModel",
    "InterviewReportModel",
    "InterviewSessionModel",
    "SkillProgressEvidenceModel",
    "SkillProgressModel",
]
