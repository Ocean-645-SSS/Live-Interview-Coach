"""Interview 持久化层的稳定接口与共享异常。
相当于 Spring 里的 mapper 接口，不真正操作数据库"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from liverag.interview.records import (
    AnswerState,
    AttemptState,
    InterviewAnswerRecord,
    InterviewAttemptRecord,
    InterviewEventRecord,
    InterviewRecord,
    InterviewReportRecord,
    InterviewSessionRecord,
)
from liverag.interview.schemas import (
    AnswerEvaluation,
    InterviewConfig,
    InterviewPlan,
    InterviewState,
)


#======================三种异常======================
class RecordNotFoundError(LookupError):
    """请求的 Interview 持久化记录不存在。"""


class ConcurrentUpdateError(RuntimeError):
    """记录已被其他请求更新，调用方持有的版本已经过期。"""


class DuplicateEventError(RuntimeError):
    """相同事件标识已经处理，不能再次产生业务效果。"""


@dataclass(frozen=True, slots=True)
class AnswerTransitionResult:
    """一次回答事件原子落库后产生的三份权威记录。"""

    session: InterviewSessionRecord
    event: InterviewEventRecord
    answer: InterviewAnswerRecord


@runtime_checkable
class InterviewRepository(Protocol):
    """状态机和应用服务依赖的 Interview 持久化契约。"""

    #======================Interview 顶层记录======================
    def create_interview(
        self,
        *,
        title: str,
        config: InterviewConfig,
        interview_id: str | None = None,
    ) -> InterviewRecord: ...

    def get_interview(self, interview_id: str) -> InterviewRecord: ...

    def list_interviews(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InterviewRecord]: ...

    def get_interview_config(self, interview_id: str) -> InterviewConfig: ...

    def get_interview_plan(self, interview_id: str) -> InterviewPlan | None: ...

    def save_interview_plan(
        self,
        *,
        interview_id: str,
        plan: InterviewPlan,
        expected_version: int,
    ) -> InterviewRecord: ...

    def update_interview_state(
        self,
        *,
        interview_id: str,
        state: InterviewState,
        expected_version: int,
    ) -> InterviewRecord: ...

    #======================Interview Session 记录======================
    def create_session(
        self,
        *,
        interview_id: str,
        session_id: str | None = None,
    ) -> InterviewSessionRecord: ...

    def get_session(self, session_id: str) -> InterviewSessionRecord: ...

    def list_sessions(
        self,
        *,
        interview_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InterviewSessionRecord]: ...

    def update_session_snapshot(
        self,
        *,
        session_id: str,
        expected_version: int,
        state: InterviewState,
        resume_state: InterviewState | None,
        current_question_index: int,
        current_question_id: str | None,
        follow_up_count: int,
        started_at: str | None,
        ended_at: str | None,
    ) -> InterviewSessionRecord: ...

    #======================Interview Attempt 记录======================
    def create_attempt(
        self,
        *,
        session_id: str,
        room_name: str,
        attempt_id: str | None = None,
    ) -> InterviewAttemptRecord: ...

    def get_attempt(self, attempt_id: str) -> InterviewAttemptRecord: ...

    def list_attempts(self, session_id: str) -> list[InterviewAttemptRecord]: ...

    def update_attempt_state(
        self,
        *,
        attempt_id: str,
        state: AttemptState,
        error_message: str | None = None,
    ) -> InterviewAttemptRecord: ...

    #======================Interview Event 记录======================
    def event_exists(self, event_id: str) -> bool: ...

    def record_transition(
        self,
        *,
        event_id: str,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        expected_version: int,
        state_before: InterviewState,
        state_after: InterviewState,
        resume_state: InterviewState | None,
        current_question_index: int,
        current_question_id: str | None,
        follow_up_count: int,
        started_at: str | None,
        ended_at: str | None,
    ) -> InterviewEventRecord: ...

    def record_answer_transition(
        self,
        *,
        event_id: str,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        expected_version: int,
        state_before: InterviewState,
        state_after: InterviewState,
        resume_state: InterviewState | None,
        current_question_index: int,
        current_question_id: str | None,
        follow_up_count: int,
        session_started_at: str | None,
        session_ended_at: str | None,
        question_id: str,
        attempt_id: str,
        answer_number: int,
        transcript: str,
        answer_started_at: str,
        answer_ended_at: str,
        answer_id: str | None = None,
    ) -> AnswerTransitionResult: ...

    def list_events(
        self,
        *,
        session_id: str,
        after_version: int = 0,
        limit: int = 200,
    ) -> list[InterviewEventRecord]: ...

    #======================Interview Answer 记录======================
    def create_answer(
        self,
        *,
        session_id: str,
        question_id: str,
        attempt_id: str,
        answer_number: int,
        transcript: str,
        source_event_id: str,
        started_at: str,
        ended_at: str,
        answer_id: str | None = None,
    ) -> InterviewAnswerRecord: ...

    def get_answer(self, answer_id: str) -> InterviewAnswerRecord: ...

    def list_answers(
        self,
        *,
        session_id: str,
        question_id: str | None = None,
    ) -> list[InterviewAnswerRecord]: ...

    def update_answer_state(
        self,
        *,
        answer_id: str,
        state: AnswerState,
    ) -> InterviewAnswerRecord: ...

    #=====================Interview Answer Evaluation 记录======================
    def save_evaluation(
        self,
        *,
        evaluation_id: str,
        evaluation: AnswerEvaluation,
        rubric_version: int = 1,
    ) -> AnswerEvaluation: ...

    def get_evaluation(self, answer_id: str) -> AnswerEvaluation: ...

    def list_evaluations(self, session_id: str) -> list[AnswerEvaluation]: ...

    #=====================Interview Report 记录======================
    def create_report(
        self,
        *,
        session_id: str,
        report_id: str | None = None,
    ) -> InterviewReportRecord: ...

    def get_report(self, report_id: str) -> InterviewReportRecord: ...

    def get_report_by_session(
        self, session_id: str
    ) -> InterviewReportRecord | None: ...

    def start_report_generation(self, report_id: str) -> InterviewReportRecord: ...

    def fail_report(
        self,
        *,
        report_id: str,
        error_message: str,
    ) -> InterviewReportRecord: ...

    def complete_report(
        self,
        *,
        report_id: str,
        content: dict[str, Any],
    ) -> InterviewReportRecord: ...


__all__ = [
    "AnswerTransitionResult",
    "ConcurrentUpdateError",
    "DuplicateEventError",
    "InterviewRepository",
    "RecordNotFoundError",
]
