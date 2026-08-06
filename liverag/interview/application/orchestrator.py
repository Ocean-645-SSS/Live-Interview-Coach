"""实时面试用例的统一编排入口，属于应用层、业务流程编排层"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from liverag.interview.records import (
    InterviewAnswerRecord,
    InterviewEventRecord,
    InterviewSessionRecord,
)
from liverag.interview.persistence.repository import DuplicateEventError, InterviewRepository
from liverag.interview.state_machine import (
    InterviewEventType,
    InterviewStateMachine,
    InterviewTransitionError,
    SessionTransition,
)

logger = logging.getLogger("liverag.interview.state_machine")


@dataclass(frozen=True, slots=True)
class InterviewTransitionResult:
    """一次事件持久化后的 Event 和最新 Session。"""

    event: InterviewEventRecord
    session: InterviewSessionRecord


@dataclass(frozen=True, slots=True)
class AnswerReceivedCommand:
    """STT 产生最终文本后，记录回答所需的完整输入。"""

    session_id: str
    attempt_id: str
    event_id: str
    transcript: str
    answer_number: int
    started_at: str
    ended_at: str
    answer_id: str | None = None
    payload: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AnswerReceivedResult:
    """回答事件完成后返回给实时 Agent 的权威记录：session+event+answer"""

    transition: InterviewTransitionResult
    answer: InterviewAnswerRecord


class InterviewOrchestrator:
    """协调状态机state_machine与持久化，是实时面试状态变化的应用层入口。"""

    def __init__(self, repository: InterviewRepository):
        self._repository = repository
        self._state_machine = InterviewStateMachine()

    def transition(
        self,
        *,
        session_id: str,
        event_id: str,
        event_type: InterviewEventType,
        payload: dict[str, Any] | None = None,
    ) -> InterviewTransitionResult:
        """处理不创建 Answer 的普通面试事件。"""

        #禁止处理和answer相关的事件
        if event_type is InterviewEventType.ANSWER_RECEIVED:
            raise InterviewTransitionError(
                "ANSWER_RECEIVED 必须通过 receive_answer() 提交最终回答"
            )

        #获取更新前的session+更新目标快照
        session, snapshot = self._calculate_transition(
            session_id=session_id,
            event_id=event_id,
            event_type=event_type,
        )
        #记录事件
        transition_payload = dict(payload or {})
        try:
            event = self._repository.record_transition(
                event_id=event_id,
                session_id=session.id,
                event_type=event_type.value,
                payload=transition_payload,
                expected_version=session.version,
                state_before=session.state,
                state_after=snapshot.state,
                resume_state=snapshot.resume_state,
                current_question_index=snapshot.current_question_index,
                current_question_id=snapshot.current_question_id,
                follow_up_count=snapshot.follow_up_count,
                started_at=snapshot.started_at,
                ended_at=snapshot.ended_at,
            )
        except Exception:
            logger.exception(
                "interview.state_transition.persistence_failed",
                extra=self._transition_log_context(
                    session=session,
                    event_type=event_type,
                    to_state=snapshot.state,
                    attempt_id=str(transition_payload.get("attempt_id") or ""),
                ),
            )
            raise
        result = InterviewTransitionResult(
            event=event,
            session=self._repository.get_session(session.id),
        )
        self._log_transition(
            before=session,
            result=result,
            event_type=event_type,
            attempt_id=str(transition_payload.get("attempt_id") or ""),
        )
        return result

    def receive_answer(self, command: AnswerReceivedCommand) -> AnswerReceivedResult:
        """原子更新 Session，并同时创建 Event 与最终 Answer。"""

        session, snapshot = self._calculate_transition(
            session_id=command.session_id,
            event_id=command.event_id,
            event_type=InterviewEventType.ANSWER_RECEIVED,
        )
        if session.current_question_id is None:
            raise InterviewTransitionError("LISTENING Session 缺少当前题目标识")

        try:
            result = self._repository.record_answer_transition(
                event_id=command.event_id,
                session_id=session.id,
                event_type=InterviewEventType.ANSWER_RECEIVED.value,
                payload=dict(command.payload or {}),
                expected_version=session.version,
                state_before=session.state,
                state_after=snapshot.state,
                resume_state=snapshot.resume_state,
                current_question_index=snapshot.current_question_index,
                current_question_id=snapshot.current_question_id,
                follow_up_count=snapshot.follow_up_count,
                session_started_at=snapshot.started_at,
                session_ended_at=snapshot.ended_at,
                question_id=session.current_question_id,
                attempt_id=command.attempt_id,
                answer_number=command.answer_number,
                transcript=command.transcript,
                answer_started_at=command.started_at,
                answer_ended_at=command.ended_at,
                answer_id=command.answer_id,
            )
        except Exception:
            logger.exception(
                "interview.state_transition.persistence_failed",
                extra=self._transition_log_context(
                    session=session,
                    event_type=InterviewEventType.ANSWER_RECEIVED,
                    to_state=snapshot.state,
                    attempt_id=command.attempt_id,
                ),
            )
            raise

        transition = InterviewTransitionResult(
            event=result.event,
            session=result.session,
        )
        self._log_transition(
            before=session,
            result=transition,
            event_type=InterviewEventType.ANSWER_RECEIVED,
            attempt_id=command.attempt_id,
        )
        return AnswerReceivedResult(
            transition=transition,
            answer=result.answer,
        )

    @staticmethod
    def _transition_log_context(
        *,
        session: InterviewSessionRecord,
        event_type: InterviewEventType,
        to_state: object,
        attempt_id: str,
        version: int | None = None,
    ) -> dict[str, object]:
        return {
            "interview_id": session.interview_id,
            "session_id": session.id,
            "attempt_id": attempt_id,
            "question_id": session.current_question_id,
            "from_state": session.state.value,
            "event": event_type.value,
            "to_state": getattr(to_state, "value", str(to_state)),
            "version": version if version is not None else session.version,
        }

    def _log_transition(
        self,
        *,
        before: InterviewSessionRecord,
        result: InterviewTransitionResult,
        event_type: InterviewEventType,
        attempt_id: str,
    ) -> None:
        logger.info(
            "interview.state_transition.persisted",
            extra=self._transition_log_context(
                session=before,
                event_type=event_type,
                to_state=result.session.state,
                attempt_id=attempt_id,
                version=result.session.version,
            )
            | {"question_id": result.session.current_question_id},
        )

    def _calculate_transition(
        self,
        *,
        session_id: str,
        event_id: str,
        event_type: InterviewEventType,
    ) -> tuple[InterviewSessionRecord, SessionTransition]:
        """计算出：更新前的数据库记录+更新目标"""

        #幂等性处理
        if self._repository.event_exists(event_id):
            raise DuplicateEventError(f"事件已经处理：{event_id}")

        #获取更新前的session
        session = self._repository.get_session(session_id)

        #获取面试计划
        plan = self._repository.get_interview_plan(session.interview_id)
        if plan is None:
            raise InterviewTransitionError("Session 对应的面试计划不存在")

        #计算目标状态快照
        snapshot = self._state_machine.calculate_transition(
            session=session,
            plan=plan,
            event_type=event_type,
        )

        return session, snapshot


__all__ = [
    "AnswerReceivedCommand",
    "AnswerReceivedResult",
    "InterviewOrchestrator",
    "InterviewTransitionResult",
]
