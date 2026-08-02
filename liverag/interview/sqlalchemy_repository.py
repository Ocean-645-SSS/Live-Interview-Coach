"""新版本：Interview Repository 的 SQLAlchemy 实现。
相当于 Spring 中的 Mapper XML实现类，使用短生命周期 Session 进行持久化操作。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, TypeVar, cast

from pydantic import BaseModel as PydanticModel
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from liverag.interview.db import session_scope
from liverag.interview.models import (
    AnswerEvaluationModel,
    Base,
    InterviewAnswerModel,
    InterviewAttemptModel,
    InterviewEventModel,
    InterviewModel,
    InterviewReportModel,
    InterviewSessionModel,
)
from liverag.interview.records import (
    AnswerState,
    AttemptState,
    InterviewAnswerRecord,
    InterviewAttemptRecord,
    InterviewEventRecord,
    InterviewRecord,
    InterviewReportRecord,
    InterviewSessionRecord,
    ReportState,
    generate_id,
)
from liverag.interview.repository import (
    ConcurrentUpdateError,
    DuplicateEventError,
    RecordNotFoundError,
)
from liverag.interview.schemas import (
    AnswerEvaluation,
    InterviewConfig,
    InterviewPlan,
    InterviewState,
)

ModelT = TypeVar("ModelT", bound=Base)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _required_iso(value: datetime) -> str:
    result = _to_iso(value)
    if result is None:  # pragma: no cover - 非空 ORM 字段的类型保护
        raise TypeError("必填时间字段不能为 None")
    return result


def _model_to_json(model: PydanticModel) -> str:
    return model.model_dump_json(exclude_none=False)


def _dict_to_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _validate_page(limit: int, offset: int) -> None:
    if not 1 <= limit <= 200:
        raise ValueError("limit 必须在 1 到 200 之间")
    if offset < 0:
        raise ValueError("offset 不能为负数")


def _require_model(
    database_session: Session,
    model_type: type[ModelT],
    record_id: str,
    message: str,
) -> ModelT:
    model = database_session.get(model_type, record_id)
    if model is None:
        raise RecordNotFoundError(message)
    return model


#============================ 转换字段 =============================
def _interview_record(model: InterviewModel) -> InterviewRecord:
    return InterviewRecord(
        id=model.id,
        title=model.title,
        state=model.state,
        config_json=model.config_json,
        plan_json=model.plan_json,
        version=model.version,
        created_at=_required_iso(model.created_at),
        updated_at=_required_iso(model.updated_at),
    )


def _session_record(model: InterviewSessionModel) -> InterviewSessionRecord:
    return InterviewSessionRecord(
        id=model.id,
        interview_id=model.interview_id,
        state=model.state,
        resume_state=model.resume_state,
        current_question_index=model.current_question_index,
        current_question_id=model.current_question_id,
        follow_up_count=model.follow_up_count,
        version=model.version,
        started_at=_to_iso(model.started_at),
        ended_at=_to_iso(model.ended_at),
        created_at=_required_iso(model.created_at),
        updated_at=_required_iso(model.updated_at),
    )


def _attempt_record(model: InterviewAttemptModel) -> InterviewAttemptRecord:
    return InterviewAttemptRecord(
        id=model.id,
        session_id=model.session_id,
        room_name=model.room_name,
        state=model.state,
        connected_at=_to_iso(model.connected_at),
        disconnected_at=_to_iso(model.disconnected_at),
        error_message=model.error_message,
        created_at=_required_iso(model.created_at),
        updated_at=_required_iso(model.updated_at),
    )


def _event_record(model: InterviewEventModel) -> InterviewEventRecord:
    return InterviewEventRecord(
        id=model.id,
        session_id=model.session_id,
        event_type=model.event_type,
        payload_json=model.payload_json,
        state_before=model.state_before,
        state_after=model.state_after,
        version_before=model.version_before,
        version_after=model.version_after,
        created_at=_required_iso(model.created_at),
    )


def _answer_record(model: InterviewAnswerModel) -> InterviewAnswerRecord:
    return InterviewAnswerRecord(
        id=model.id,
        session_id=model.session_id,
        question_id=model.question_id,
        attempt_id=model.attempt_id,
        answer_number=model.answer_number,
        transcript=model.transcript,
        state=model.state,
        source_event_id=model.source_event_id,
        started_at=_required_iso(model.started_at),
        ended_at=_required_iso(model.ended_at),
        created_at=_required_iso(model.created_at),
        updated_at=_required_iso(model.updated_at),
    )


def _report_record(model: InterviewReportModel) -> InterviewReportRecord:
    return InterviewReportRecord(
        id=model.id,
        session_id=model.session_id,
        state=model.state,
        content_json=model.content_json,
        error_message=model.error_message,
        created_at=_required_iso(model.created_at),
        updated_at=_required_iso(model.updated_at),
        completed_at=_to_iso(model.completed_at),
    )
#============================================================================


class SQLAlchemyInterviewRepository:
    """使用短生命周期 SQLAlchemy Session 持久化 Interview 领域记录。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_interview(
        self,
        *,
        title: str,
        config: InterviewConfig,
        interview_id: str | None = None,
    ) -> InterviewRecord:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("面试标题不能为空")

        model = InterviewModel(
            id=interview_id or generate_id("interview"),
            title=clean_title,
            state=InterviewState.CREATED,
            config_json=_model_to_json(config),
        )
        with session_scope(self._session_factory) as database_session:
            database_session.add(model)
            database_session.flush()
            return _interview_record(model)

    def get_interview(self, interview_id: str) -> InterviewRecord:
        with self._session_factory() as database_session:
            model = _require_model(
                database_session,
                InterviewModel,
                interview_id,
                f"模拟面试不存在：{interview_id}",
            )
            return _interview_record(model)

    def list_interviews(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InterviewRecord]:
        _validate_page(limit, offset)
        statement = (
            select(InterviewModel)
            .order_by(InterviewModel.updated_at.desc(), InterviewModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        with self._session_factory() as database_session:
            return [
                _interview_record(model)
                for model in database_session.scalars(statement).all()
            ]

    def get_interview_config(self, interview_id: str) -> InterviewConfig:
        return InterviewConfig.model_validate_json(
            self.get_interview(interview_id).config_json
        )

    def get_interview_plan(self, interview_id: str) -> InterviewPlan | None:
        plan_json = self.get_interview(interview_id).plan_json
        if plan_json is None:
            return None
        return InterviewPlan.model_validate_json(plan_json)

    def save_interview_plan(
        self,
        *,
        interview_id: str,
        plan: InterviewPlan,
        expected_version: int,
    ) -> InterviewRecord:
        with session_scope(self._session_factory) as database_session:
            result = database_session.execute(
                update(InterviewModel)
                .where(
                    InterviewModel.id == interview_id,
                    InterviewModel.version == expected_version,
                )
                .values(
                    plan_json=_model_to_json(plan),
                    state=InterviewState.READY,
                    version=InterviewModel.version + 1,
                    updated_at=_utc_now(),
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                self._raise_interview_conflict(
                    database_session,
                    interview_id=interview_id,
                    expected_version=expected_version,
                )
            model = _require_model(
                database_session,
                InterviewModel,
                interview_id,
                f"模拟面试不存在：{interview_id}",
            )
            return _interview_record(model)

    def update_interview_state(
        self,
        *,
        interview_id: str,
        state: InterviewState,
        expected_version: int,
    ) -> InterviewRecord:
        with session_scope(self._session_factory) as database_session:
            result = database_session.execute(
                update(InterviewModel)
                .where(
                    InterviewModel.id == interview_id,
                    InterviewModel.version == expected_version,
                )
                .values(
                    state=state,
                    version=InterviewModel.version + 1,
                    updated_at=_utc_now(),
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                self._raise_interview_conflict(
                    database_session,
                    interview_id=interview_id,
                    expected_version=expected_version,
                )
            model = _require_model(
                database_session,
                InterviewModel,
                interview_id,
                f"模拟面试不存在：{interview_id}",
            )
            return _interview_record(model)

    @staticmethod
    def _raise_interview_conflict(
        database_session: Session,
        *,
        interview_id: str,
        expected_version: int,
    ) -> None:
        actual_version = database_session.scalar(
            select(InterviewModel.version).where(InterviewModel.id == interview_id)
        )
        if actual_version is None:
            raise RecordNotFoundError(f"模拟面试不存在：{interview_id}")
        raise ConcurrentUpdateError(
            f"模拟面试版本已变化：期望 {expected_version}，实际 {actual_version}"
        )

    def create_session(
        self,
        *,
        interview_id: str,
        session_id: str | None = None,
    ) -> InterviewSessionRecord:
        with session_scope(self._session_factory) as database_session:
            interview = _require_model(
                database_session,
                InterviewModel,
                interview_id,
                f"模拟面试不存在：{interview_id}",
            )
            if (
                interview.state is not InterviewState.READY
                or interview.plan_json is None
            ):
                raise ValueError("只有已生成并冻结计划的面试才能创建 Session")
            model = InterviewSessionModel(
                id=session_id or generate_id("session"),
                interview_id=interview_id,
                state=InterviewState.READY,
            )
            database_session.add(model)
            database_session.flush()
            return _session_record(model)

    def get_session(self, session_id: str) -> InterviewSessionRecord:
        with self._session_factory() as database_session:
            model = _require_model(
                database_session,
                InterviewSessionModel,
                session_id,
                f"面试 Session 不存在：{session_id}",
            )
            return _session_record(model)

    def list_sessions(
        self,
        *,
        interview_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InterviewSessionRecord]:
        _validate_page(limit, offset)
        with self._session_factory() as database_session:
            _require_model(
                database_session,
                InterviewModel,
                interview_id,
                f"模拟面试不存在：{interview_id}",
            )
            statement = (
                select(InterviewSessionModel)
                .where(InterviewSessionModel.interview_id == interview_id)
                .order_by(InterviewSessionModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [
                _session_record(model)
                for model in database_session.scalars(statement).all()
            ]

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
    ) -> InterviewSessionRecord:
        if current_question_index < 0:
            raise ValueError("当前题目索引不能为负数")
        if follow_up_count < 0:
            raise ValueError("追问次数不能为负数")

        with session_scope(self._session_factory) as database_session:
            result = database_session.execute(
                update(InterviewSessionModel)
                .where(
                    InterviewSessionModel.id == session_id,
                    InterviewSessionModel.version == expected_version,
                )
                .values(
                    state=state,
                    resume_state=resume_state,
                    current_question_index=current_question_index,
                    current_question_id=current_question_id,
                    follow_up_count=follow_up_count,
                    version=InterviewSessionModel.version + 1,
                    started_at=_parse_datetime(started_at),
                    ended_at=_parse_datetime(ended_at),
                    updated_at=_utc_now(),
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                self._raise_session_conflict(
                    database_session,
                    session_id=session_id,
                    expected_version=expected_version,
                )
            model = _require_model(
                database_session,
                InterviewSessionModel,
                session_id,
                f"面试 Session 不存在：{session_id}",
            )
            return _session_record(model)

    @staticmethod
    def _raise_session_conflict(
        database_session: Session,
        *,
        session_id: str,
        expected_version: int,
    ) -> None:
        actual_version = database_session.scalar(
            select(InterviewSessionModel.version).where(
                InterviewSessionModel.id == session_id
            )
        )
        if actual_version is None:
            raise RecordNotFoundError(f"面试 Session 不存在：{session_id}")
        raise ConcurrentUpdateError(
            f"Session 版本已变化：期望 {expected_version}，实际 {actual_version}"
        )

    def create_attempt(
        self,
        *,
        session_id: str,
        room_name: str,
        attempt_id: str | None = None,
    ) -> InterviewAttemptRecord:
        clean_room_name = room_name.strip()
        if not clean_room_name:
            raise ValueError("LiveKit 房间名称不能为空")
        with session_scope(self._session_factory) as database_session:
            _require_model(
                database_session,
                InterviewSessionModel,
                session_id,
                f"面试 Session 不存在：{session_id}",
            )
            model = InterviewAttemptModel(
                id=attempt_id or generate_id("attempt"),
                session_id=session_id,
                room_name=clean_room_name,
                state=AttemptState.CREATED,
            )
            database_session.add(model)
            database_session.flush()
            return _attempt_record(model)

    def get_attempt(self, attempt_id: str) -> InterviewAttemptRecord:
        with self._session_factory() as database_session:
            model = _require_model(
                database_session,
                InterviewAttemptModel,
                attempt_id,
                f"面试连接 Attempt 不存在：{attempt_id}",
            )
            return _attempt_record(model)

    def list_attempts(self, session_id: str) -> list[InterviewAttemptRecord]:
        with self._session_factory() as database_session:
            _require_model(
                database_session,
                InterviewSessionModel,
                session_id,
                f"面试 Session 不存在：{session_id}",
            )
            statement = (
                select(InterviewAttemptModel)
                .where(InterviewAttemptModel.session_id == session_id)
                .order_by(InterviewAttemptModel.created_at.asc())
            )
            return [
                _attempt_record(model)
                for model in database_session.scalars(statement).all()
            ]

    def update_attempt_state(
        self,
        *,
        attempt_id: str,
        state: AttemptState,
        error_message: str | None = None,
    ) -> InterviewAttemptRecord:
        with session_scope(self._session_factory) as database_session:
            model = _require_model(
                database_session,
                InterviewAttemptModel,
                attempt_id,
                f"面试连接 Attempt 不存在：{attempt_id}",
            )
            now = _utc_now()
            model.state = state
            if state is AttemptState.CONNECTED and model.connected_at is None:
                model.connected_at = now
            if state in {AttemptState.DISCONNECTED, AttemptState.FAILED}:
                model.disconnected_at = now
            model.error_message = error_message
            model.updated_at = now
            database_session.flush()
            return _attempt_record(model)

    def event_exists(self, event_id: str) -> bool:
        with self._session_factory() as database_session:
            return database_session.get(InterviewEventModel, event_id) is not None

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
    ) -> InterviewEventRecord:
        clean_event_type = event_type.strip()
        if not clean_event_type:
            raise ValueError("事件类型不能为空")
        if current_question_index < 0 or follow_up_count < 0:
            raise ValueError("题目索引和追问次数不能为负数")

        with session_scope(self._session_factory) as database_session:
            if database_session.get(InterviewEventModel, event_id) is not None:
                raise DuplicateEventError(f"事件已经处理：{event_id}")

            version_after = expected_version + 1
            result = database_session.execute(
                update(InterviewSessionModel)
                .where(
                    InterviewSessionModel.id == session_id,
                    InterviewSessionModel.version == expected_version,
                    InterviewSessionModel.state == state_before,
                )
                .values(
                    state=state_after,
                    resume_state=resume_state,
                    current_question_index=current_question_index,
                    current_question_id=current_question_id,
                    follow_up_count=follow_up_count,
                    version=version_after,
                    started_at=_parse_datetime(started_at),
                    ended_at=_parse_datetime(ended_at),
                    updated_at=_utc_now(),
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                current = database_session.execute(
                    select(
                        InterviewSessionModel.state,
                        InterviewSessionModel.version,
                    ).where(InterviewSessionModel.id == session_id)
                ).one_or_none()
                if current is None:
                    raise RecordNotFoundError(f"面试 Session 不存在：{session_id}")
                raise ConcurrentUpdateError(
                    "Session 已发生变化："
                    f"期望状态/版本 {state_before.value}/{expected_version}，"
                    f"实际为 {current.state.value}/{current.version}"
                )

            model = InterviewEventModel(
                id=event_id,
                session_id=session_id,
                event_type=clean_event_type,
                payload_json=_dict_to_json(payload),
                state_before=state_before,
                state_after=state_after,
                version_before=expected_version,
                version_after=version_after,
            )
            database_session.add(model)
            try:
                database_session.flush()
            except IntegrityError as exc:
                raise DuplicateEventError(f"事件已经处理：{event_id}") from exc
            return _event_record(model)

    def list_events(
        self,
        *,
        session_id: str,
        after_version: int = 0,
        limit: int = 200,
    ) -> list[InterviewEventRecord]:
        if after_version < 0:
            raise ValueError("after_version 不能为负数")
        _validate_page(limit, 0)
        with self._session_factory() as database_session:
            _require_model(
                database_session,
                InterviewSessionModel,
                session_id,
                f"面试 Session 不存在：{session_id}",
            )
            statement = (
                select(InterviewEventModel)
                .where(
                    InterviewEventModel.session_id == session_id,
                    InterviewEventModel.version_after > after_version,
                )
                .order_by(
                    InterviewEventModel.version_after.asc(),
                    InterviewEventModel.created_at.asc(),
                )
                .limit(limit)
            )
            return [
                _event_record(model)
                for model in database_session.scalars(statement).all()
            ]

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
    ) -> InterviewAnswerRecord:
        clean_transcript = transcript.strip()
        if not clean_transcript:
            raise ValueError("最终回答文本不能为空")
        if answer_number < 1:
            raise ValueError("回答序号必须从 1 开始")

        model = InterviewAnswerModel(
            id=answer_id or generate_id("answer"),
            session_id=session_id,
            question_id=question_id,
            attempt_id=attempt_id,
            answer_number=answer_number,
            transcript=clean_transcript,
            state=AnswerState.RECEIVED,
            source_event_id=source_event_id,
            started_at=_parse_datetime(started_at),
            ended_at=_parse_datetime(ended_at),
        )
        try:
            with session_scope(self._session_factory) as database_session:
                database_session.add(model)
                database_session.flush()
                return _answer_record(model)
        except IntegrityError as exc:
            with self._session_factory() as database_session:
                duplicate = database_session.scalar(
                    select(InterviewAnswerModel.id).where(
                        InterviewAnswerModel.source_event_id == source_event_id
                    )
                )
            if duplicate is not None:
                raise DuplicateEventError(
                    f"回答事件已经处理：{source_event_id}"
                ) from exc
            raise

    def get_answer(self, answer_id: str) -> InterviewAnswerRecord:
        with self._session_factory() as database_session:
            model = _require_model(
                database_session,
                InterviewAnswerModel,
                answer_id,
                f"面试回答不存在：{answer_id}",
            )
            return _answer_record(model)

    def list_answers(
        self,
        *,
        session_id: str,
        question_id: str | None = None,
    ) -> list[InterviewAnswerRecord]:
        with self._session_factory() as database_session:
            _require_model(
                database_session,
                InterviewSessionModel,
                session_id,
                f"面试 Session 不存在：{session_id}",
            )
            statement = select(InterviewAnswerModel).where(
                InterviewAnswerModel.session_id == session_id
            )
            if question_id is None:
                statement = statement.order_by(
                    InterviewAnswerModel.created_at.asc(),
                    InterviewAnswerModel.answer_number.asc(),
                )
            else:
                statement = statement.where(
                    InterviewAnswerModel.question_id == question_id
                ).order_by(
                    InterviewAnswerModel.answer_number.asc(),
                    InterviewAnswerModel.created_at.asc(),
                )
            return [
                _answer_record(model)
                for model in database_session.scalars(statement).all()
            ]

    def update_answer_state(
        self,
        *,
        answer_id: str,
        state: AnswerState,
    ) -> InterviewAnswerRecord:
        with session_scope(self._session_factory) as database_session:
            model = _require_model(
                database_session,
                InterviewAnswerModel,
                answer_id,
                f"面试回答不存在：{answer_id}",
            )
            model.state = state
            model.updated_at = _utc_now()
            database_session.flush()
            return _answer_record(model)

    def save_evaluation(
        self,
        *,
        evaluation_id: str,
        evaluation: AnswerEvaluation,
        rubric_version: int = 1,
    ) -> AnswerEvaluation:
        if rubric_version < 1:
            raise ValueError("评分规则版本必须从 1 开始")
        with session_scope(self._session_factory) as database_session:
            answer = _require_model(
                database_session,
                InterviewAnswerModel,
                evaluation.answer_id,
                f"面试回答不存在：{evaluation.answer_id}",
            )
            answer.state = AnswerState.EVALUATED
            answer.updated_at = _utc_now()
            database_session.add(
                AnswerEvaluationModel(
                    id=evaluation_id,
                    answer_id=evaluation.answer_id,
                    rubric_version=rubric_version,
                    evaluation_json=_model_to_json(evaluation),
                )
            )
            database_session.flush()
        return evaluation

    def get_evaluation(self, answer_id: str) -> AnswerEvaluation:
        with self._session_factory() as database_session:
            evaluation_json = database_session.scalar(
                select(AnswerEvaluationModel.evaluation_json).where(
                    AnswerEvaluationModel.answer_id == answer_id
                )
            )
        if evaluation_json is None:
            raise RecordNotFoundError(f"回答评价不存在：{answer_id}")
        return AnswerEvaluation.model_validate_json(evaluation_json)

    def list_evaluations(self, session_id: str) -> list[AnswerEvaluation]:
        with self._session_factory() as database_session:
            _require_model(
                database_session,
                InterviewSessionModel,
                session_id,
                f"面试 Session 不存在：{session_id}",
            )
            statement = (
                select(AnswerEvaluationModel.evaluation_json)
                .join(
                    InterviewAnswerModel,
                    InterviewAnswerModel.id == AnswerEvaluationModel.answer_id,
                )
                .where(InterviewAnswerModel.session_id == session_id)
                .order_by(
                    InterviewAnswerModel.created_at.asc(),
                    InterviewAnswerModel.answer_number.asc(),
                )
            )
            values = database_session.scalars(statement).all()
        return [AnswerEvaluation.model_validate_json(value) for value in values]

    def create_report(
        self,
        *,
        session_id: str,
        report_id: str | None = None,
    ) -> InterviewReportRecord:
        with session_scope(self._session_factory) as database_session:
            _require_model(
                database_session,
                InterviewSessionModel,
                session_id,
                f"面试 Session 不存在：{session_id}",
            )
            model = InterviewReportModel(
                id=report_id or generate_id("report"),
                session_id=session_id,
                state=ReportState.PENDING,
            )
            database_session.add(model)
            database_session.flush()
            return _report_record(model)

    def get_report(self, report_id: str) -> InterviewReportRecord:
        with self._session_factory() as database_session:
            model = _require_model(
                database_session,
                InterviewReportModel,
                report_id,
                f"面试报告不存在：{report_id}",
            )
            return _report_record(model)

    def get_report_by_session(
        self, session_id: str
    ) -> InterviewReportRecord | None:
        with self._session_factory() as database_session:
            _require_model(
                database_session,
                InterviewSessionModel,
                session_id,
                f"面试 Session 不存在：{session_id}",
            )
            model = database_session.scalar(
                select(InterviewReportModel).where(
                    InterviewReportModel.session_id == session_id
                )
            )
            return _report_record(model) if model is not None else None

    def start_report_generation(self, report_id: str) -> InterviewReportRecord:
        with session_scope(self._session_factory) as database_session:
            result = database_session.execute(
                update(InterviewReportModel)
                .where(
                    InterviewReportModel.id == report_id,
                    InterviewReportModel.state.in_(
                        (ReportState.PENDING, ReportState.FAILED)
                    ),
                )
                .values(
                    state=ReportState.GENERATING,
                    error_message=None,
                    updated_at=_utc_now(),
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                current_state = database_session.scalar(
                    select(InterviewReportModel.state).where(
                        InterviewReportModel.id == report_id
                    )
                )
                if current_state is None:
                    raise RecordNotFoundError(f"面试报告不存在：{report_id}")
                raise ValueError(f"当前报告状态不允许开始生成：{current_state.value}")
            model = _require_model(
                database_session,
                InterviewReportModel,
                report_id,
                f"面试报告不存在：{report_id}",
            )
            return _report_record(model)

    def fail_report(
        self,
        *,
        report_id: str,
        error_message: str,
    ) -> InterviewReportRecord:
        clean_error = error_message.strip()
        if not clean_error:
            raise ValueError("报告失败原因不能为空")
        with session_scope(self._session_factory) as database_session:
            model = _require_model(
                database_session,
                InterviewReportModel,
                report_id,
                f"面试报告不存在：{report_id}",
            )
            model.state = ReportState.FAILED
            model.error_message = clean_error
            model.completed_at = None
            model.updated_at = _utc_now()
            database_session.flush()
            return _report_record(model)

    def complete_report(
        self,
        *,
        report_id: str,
        content: dict[str, Any],
    ) -> InterviewReportRecord:
        with session_scope(self._session_factory) as database_session:
            model = _require_model(
                database_session,
                InterviewReportModel,
                report_id,
                f"面试报告不存在：{report_id}",
            )
            now = _utc_now()
            model.state = ReportState.COMPLETED
            model.content_json = _dict_to_json(content)
            model.error_message = None
            model.updated_at = now
            model.completed_at = now
            database_session.flush()
            return _report_record(model)


__all__ = ["SQLAlchemyInterviewRepository"]
