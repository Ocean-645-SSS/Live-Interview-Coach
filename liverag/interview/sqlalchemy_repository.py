"""新版本：Interview Repository 的 SQLAlchemy 实现。
相当于 Spring 中的 Mapper XML实现类，使用短生命周期 Session 进行持久化操作。
ORM Model -> Record 的转换函数都在这里实现，避免在业务层直接操作 ORM 模型。

with self._session_factory()：只读使用
with session_scope(self._session_factory)：涉及写数据库使用，与事务相关
"""

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
    AnswerTransitionResult,
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
    """检验分页查询两个参数：limit和offset 是否合法"""

    if not 1 <= limit <= 200:
        raise ValueError("limit 必须在 1 到 200 之间")
    if offset < 0:
        raise ValueError("offset 不能为负数")


def _require_model(
    database_session: Session,  #SQLAlchemy Session
    model_type: type[ModelT],   #查哪张表的ORM类
    record_id: str,   #这条记录的主键id
    message: str,   #找不到记录时的报错信息
) -> ModelT:
    """根据主键查找 ORM 模型，如果不存在就抛出 RecordNotFoundError。"""

    model = database_session.get(model_type, record_id)
    if model is None:
        raise RecordNotFoundError(message)
    return model


#========================== 把 ORM 模型转换为持久化模型记录 ===================================
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


def _validate_transition_values(
    event_type: str,
    current_question_index: int,
    follow_up_count: int,
) -> str:
    """校验event/question/follow_up参数"""
    clean_event_type = event_type.strip()
    if not clean_event_type:
        raise ValueError("事件类型不能为空")
    if current_question_index < 0 or follow_up_count < 0:
        raise ValueError("题目索引和追问次数不能为负数")
    return clean_event_type


def _apply_session_transition(
    database_session: Session,
    *,
    session_id: str,
    expected_version: int,
    state_before: InterviewState,
    state_after: InterviewState,
    resume_state: InterviewState | None,
    current_question_index: int,
    current_question_id: str | None,
    follow_up_count: int,
    started_at: str | None,
    ended_at: str | None,
) -> int:
    """按state_before和expected_version条件更新 Session，并返回递增后的版本号。
    只负责session"""

    version_after = expected_version + 1
    result = database_session.execute(
        update(InterviewSessionModel)
        .where( #乐观锁+状态前置检查
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
    #更新成功，返回最新版本号
    if cast(CursorResult[Any], result).rowcount == 1:
        return version_after

    #更新失败：session不存在/并发冲突：session存在，但状态/版本号不对
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


class SQLAlchemyInterviewRepository:
    """使用短生命周期 SQLAlchemy Session 持久化 Interview 领域记录。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    #================================== Interview =====================================
    def create_interview(
        self,
        *,
        title: str,
        config: InterviewConfig,
        interview_id: str | None = None,
    ) -> InterviewRecord:
        """创建一条面试"""

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
            database_session.flush()  #把待执行的SQL提交到数据库，但不提交事务
            return _interview_record(model)

    def get_interview(self, interview_id: str) -> InterviewRecord:
        """根据面试 id 获取一条面试，并转换为record"""

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
        """分页列出面试"""

        #检验分页参数是否合理
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
                #scalars().all()：查询全部model，返回列表
                for model in database_session.scalars(statement).all()
            ]

    def get_interview_config(self, interview_id: str) -> InterviewConfig:
        """获取面试配置。"""

        config_json = self.get_interview(interview_id).config_json
        # model_validate_json：解析校验 JSON 字符串，并将其转换为模型对象。
        return InterviewConfig.model_validate_json(config_json)

    def get_interview_plan(self, interview_id: str) -> InterviewPlan | None:
        """获取面试计划"""

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
        """冻结最新面试计划"""

        with session_scope(self._session_factory) as database_session:
            #更新计划、版本号、状态、更新时间
            result = database_session.execute(
                update(InterviewModel)
                .values(
                    plan_json=_model_to_json(plan),
                    state=InterviewState.READY,
                    version=InterviewModel.version + 1,
                    updated_at=_utc_now(),
                )
                .where(
                    InterviewModel.id == interview_id,
                    InterviewModel.version == expected_version,
                )
            )
            #确保更新成功
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
        """更新面试状态"""

        with session_scope(self._session_factory) as database_session:
            #更新面试状态
            result = database_session.execute(
                update(InterviewModel)
                .values(
                    state=state,
                    version=InterviewModel.version + 1,
                    updated_at=_utc_now(),
                )
                .where(
                    InterviewModel.id == interview_id,
                    InterviewModel.version == expected_version,
                )
            )
            #确保更新成功
            if cast(CursorResult[Any], result).rowcount != 1:
                self._raise_interview_conflict(
                    database_session,
                    interview_id=interview_id,
                    expected_version=expected_version,
                )
            #查询数据库，并确保记录真实存在，返回model
            model = _require_model(
                database_session,
                InterviewModel,
                interview_id,
                f"模拟面试不存在：{interview_id}",
            )
            #把 ORM model 转换为持久化业务层 record
            return _interview_record(model)

    @staticmethod
    def _raise_interview_conflict(
        database_session: Session,
        *,
        interview_id: str,
        expected_version: int,
    ) -> None:
        """面试内容更新失败：面试不存在 / 记录存在，但面试版本已经被其他请求更新过"""
        actual_version = database_session.scalar(
            select(InterviewModel.version).where(InterviewModel.id == interview_id)
        )
        if actual_version is None:
            raise RecordNotFoundError(f"模拟面试不存在：{interview_id}")
        raise ConcurrentUpdateError(
            f"模拟面试版本已变化：期望 {expected_version}，实际 {actual_version}"
        )

    #=============================== Interview Session =================================
    def create_session(
        self,
        *,
        interview_id: str,
        session_id: str | None = None,
    ) -> InterviewSessionRecord:
        """创建新的面试会话"""

        with session_scope(self._session_factory) as database_session:
            #查询对应的顶层面试
            interview = _require_model(
                database_session,
                InterviewModel,
                interview_id,
                f"模拟面试不存在：{interview_id}",
            )
            #面试状态不为ready/面试计划未冻结，抛出错误
            if (
                interview.state is not InterviewState.READY
                or interview.plan_json is None
            ):
                raise ValueError("只有已生成并冻结计划的面试才能创建 Session")

            #新的面试会话model
            model = InterviewSessionModel(
                id=session_id or generate_id("session"),
                interview_id=interview_id,
                state=InterviewState.READY,
            )
            database_session.add(model)
            database_session.flush()
            return _session_record(model)

    def get_session(self, session_id: str) -> InterviewSessionRecord:
        """获取一条面试会话"""

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
        """分页获取面试会话"""

        #检验分页参数
        _validate_page(limit, offset)

        with self._session_factory() as database_session:
            #查询对应的顶层面试
            _require_model(
                database_session,
                InterviewModel,
                interview_id,
                f"模拟面试不存在：{interview_id}",
            )
            statement=(
                select(InterviewSessionModel)
                .where(InterviewSessionModel.interview_id == interview_id)
                .order_by(InterviewSessionModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [_session_record(model)
                    for model in database_session.scalars(statement).all()]

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
        """更新会话快照"""

        if current_question_index < 0:
            raise ValueError("当前题目索引不能为负数")
        if follow_up_count < 0:
            raise ValueError("追问次数不能为负数")

        with session_scope(self._session_factory) as database_session:
            #更新最新session
            result = database_session.execute(
                update(InterviewSessionModel)
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
                .where(
                    InterviewSessionModel.id == session_id,
                    InterviewSessionModel.version == expected_version,
                )
            )
            #确保更新成功
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
        """会话内容更新失败：面试不存在 / 记录存在，但会话版本已经被其他请求更新过"""

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

    #============================== Interview Attempt ============================
    def create_attempt(
        self,
        *,
        session_id: str,
        room_name: str,
        attempt_id: str | None = None,
    ) -> InterviewAttemptRecord:
        """更新LiveKit房间连接"""

        clean_room_name = room_name.strip()
        if not clean_room_name:
            raise ValueError("LiveKit 房间名称不能为空")

        with session_scope(self._session_factory) as database_session:
            #确保顶层面试存在
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
        """根据 attempt_id 获得对应的attempt"""

        with self._session_factory() as database_session:
            model = _require_model(
                database_session,
                InterviewAttemptModel,
                attempt_id,
                f"面试连接 Attempt 不存在：{attempt_id}",
            )
            return _attempt_record(model)

    def list_attempts(self, session_id: str) -> list[InterviewAttemptRecord]:
        """根据 session_id 获取所有 attempts """

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
        """更新 attempt 状态"""

        with session_scope(self._session_factory) as database_session:
            #获得当前attempt
            model = _require_model(
                database_session,
                InterviewAttemptModel,
                attempt_id,
                f"面试连接 Attempt 不存在：{attempt_id}",
            )
            now = _utc_now()
            #更新状态
            model.state = state
            #更新连接时间
            if state is AttemptState.CONNECTED and model.connected_at is None:
                model.connected_at = now
            #更新断开连接时间
            if state in {AttemptState.DISCONNECTED, AttemptState.FAILED}:
                model.disconnected_at = now
            #更新错误信息
            model.error_message = error_message
            #更新时间
            model.updated_at = now

            #提交到数据库
            database_session.flush()
            return _attempt_record(model)

    #=============================== Interview Event ==============================
    def event_exists(self, event_id: str) -> bool:
        """确保当前事件存在"""

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
        """在同一事务中更新 Session 快照并追加状态事件。
        用于不涉及用户回答的普通场景：更新 session+event"""

        #校验参数
        clean_event_type = _validate_transition_values(
            event_type,
            current_question_index,
            follow_up_count,
        )
        try:
            with session_scope(self._session_factory) as database_session:
                #防止重复处理事件
                if database_session.get(InterviewEventModel, event_id) is not None:
                    raise DuplicateEventError(f"事件已经处理：{event_id}")

                #更新session快照，返回最新版本号
                version_after = _apply_session_transition(
                    database_session,
                    session_id=session_id,
                    expected_version=expected_version,
                    state_before=state_before,
                    state_after=state_after,
                    resume_state=resume_state,
                    current_question_index=current_question_index,
                    current_question_id=current_question_id,
                    follow_up_count=follow_up_count,
                    started_at=started_at,
                    ended_at=ended_at,
                )

                #最新事件model
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
                #写入event
                database_session.add(model)
                database_session.flush()
                return _event_record(model)
        except IntegrityError as exc:
            with self._session_factory() as database_session:
                duplicate = database_session.get(InterviewEventModel, event_id)
            if duplicate is not None:
                raise DuplicateEventError(f"事件已经处理：{event_id}") from exc
            raise

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
    ) -> AnswerTransitionResult:
        """原子更新 Session、追加 Event，并保存一条 STT final Answer。
        用于用户回答引发的状态变化：更新 session+event+answer"""

        #校验参数
        clean_event_type = _validate_transition_values(
            event_type,
            current_question_index,
            follow_up_count,
        )
        clean_question_id = question_id.strip()
        if not clean_question_id:
            raise ValueError("题目标识不能为空")
        clean_transcript = transcript.strip()
        if not clean_transcript:
            raise ValueError("最终回答文本不能为空")
        if answer_number < 1:
            raise ValueError("回答序号必须从 1 开始")

        try:
            with session_scope(self._session_factory) as database_session:
                #防止重复处理事件
                if database_session.get(InterviewEventModel, event_id) is not None:
                    raise DuplicateEventError(f"事件已经处理：{event_id}")

                #确保attempt存在，且属于当前session
                attempt = _require_model(
                    database_session,
                    InterviewAttemptModel,
                    attempt_id,
                    f"面试连接 Attempt 不存在：{attempt_id}",
                )
                if attempt.session_id != session_id:
                    raise ValueError("Answer 的 Attempt 不属于当前 Session")

                #乐观锁更新Session
                version_after = _apply_session_transition(
                    database_session,
                    session_id=session_id,
                    expected_version=expected_version,
                    state_before=state_before,
                    state_after=state_after,
                    resume_state=resume_state,
                    current_question_index=current_question_index,
                    current_question_id=current_question_id,
                    follow_up_count=follow_up_count,
                    started_at=session_started_at,
                    ended_at=session_ended_at,
                )
                #事件model
                event_model = InterviewEventModel(
                    id=event_id,
                    session_id=session_id,
                    event_type=clean_event_type,
                    payload_json=_dict_to_json(payload),
                    state_before=state_before,
                    state_after=state_after,
                    version_before=expected_version,
                    version_after=version_after,
                )
                #答案model
                answer_model = InterviewAnswerModel(
                    id=answer_id or generate_id("answer"),
                    session_id=session_id,
                    question_id=clean_question_id,
                    attempt_id=attempt_id,
                    answer_number=answer_number,
                    transcript=clean_transcript,
                    state=AnswerState.RECEIVED,
                    source_event_id=event_id,
                    started_at=_parse_datetime(answer_started_at),
                    ended_at=_parse_datetime(answer_ended_at),
                )
                #一次性add
                database_session.add_all((event_model, answer_model))
                #执行sql
                database_session.flush()

                #重新读取session
                session_model = _require_model(
                    database_session,
                    InterviewSessionModel,
                    session_id,
                    f"面试 Session 不存在：{session_id}",
                )
                #返回session+answer+event
                return AnswerTransitionResult(
                    session=_session_record(session_model),
                    event=_event_record(event_model),
                    answer=_answer_record(answer_model),
                )
        except IntegrityError as exc:
            with self._session_factory() as database_session:
                duplicate = database_session.get(InterviewEventModel, event_id)
            if duplicate is not None:
                raise DuplicateEventError(f"事件已经处理：{event_id}") from exc
            raise

    def list_events(
        self,
        *,
        session_id: str,
        after_version: int = 0,
        limit: int = 200,
    ) -> list[InterviewEventRecord]:
        """根据 session_id 分页获取会话对应的事件"""

        if after_version < 0:
            raise ValueError("after_version 不能为负数")
        _validate_page(limit, 0)

        with self._session_factory() as database_session:
            #确保session存在
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

    #================================== Interview Answer ==================================
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
        """新增一条答案"""

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
        #违反数据库完整性约束：重复处理事件
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
        """根据answer_id获取answer"""

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
        """根据session_id和question_id列出answers"""

        with self._session_factory() as database_session:
            #确保session存在
            _require_model(
                database_session,
                InterviewSessionModel,
                session_id,
                f"面试 Session 不存在：{session_id}",
            )

            #查找当前answer对应的session
            statement = select(InterviewAnswerModel).where(
                InterviewAnswerModel.session_id == session_id
            )
            #没传入question_id
            if question_id is None:
                statement = statement.order_by(
                    InterviewAnswerModel.created_at.asc(),
                    InterviewAnswerModel.answer_number.asc(),
                )
            #传入question_id，可加以限制
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
        """更新答案状态"""

        with session_scope(self._session_factory) as database_session:
            #确保answer存在
            model = _require_model(
                database_session,
                InterviewAnswerModel,
                answer_id,
                f"面试回答不存在：{answer_id}",
            )
            #更新state
            model.state = state
            #更新时间
            model.updated_at = _utc_now()
            #提交更新
            database_session.flush()
            return _answer_record(model)

    #============================ Interview Evaluation =============================
    def save_evaluation(
        self,
        *,
        evaluation_id: str,
        evaluation: AnswerEvaluation,
        rubric_version: int = 1,
    ) -> AnswerEvaluation:
        """保存最新答案评价"""

        if rubric_version < 1:
            raise ValueError("评分规则版本必须从 1 开始")

        with session_scope(self._session_factory) as database_session:
            #获取当前评价对应的answer
            answer = _require_model(
                database_session,
                InterviewAnswerModel,
                evaluation.answer_id,
                f"面试回答不存在：{evaluation.answer_id}",
            )

            #更新答案状态为已评价
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
        """根据answer_id获取对应的evaluation"""

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
        """根据session_id列出所有answers对应的评价
        一个：interview_session
        │ id
        └── 多个：interview_answers.session_id
                    │ id
                    └── 每个answer最多1个：answer_evaluation.answer_id"""

        with self._session_factory() as database_session:
            #确保当前session存在
            _require_model(
                database_session,
                InterviewSessionModel,
                session_id,
                f"面试 Session 不存在：{session_id}",
            )
            #查询评价
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

    #============================== Interview Report ==============================
    def create_report(
        self,
        *,
        session_id: str,
        report_id: str | None = None,
    ) -> InterviewReportRecord:
        """创建面试报告任务记录"""

        with session_scope(self._session_factory) as database_session:
            #确保session存在
            _require_model(
                database_session,
                InterviewSessionModel,
                session_id,
                f"面试 Session 不存在：{session_id}",
            )
            model = InterviewReportModel(
                id=report_id or generate_id("report"),
                session_id=session_id,
                state=ReportState.PENDING,  #待处理
            )
            database_session.add(model)
            database_session.flush()
            return _report_record(model)

    def get_report(self, report_id: str) -> InterviewReportRecord:
        """根据report_id获取对应的报告"""

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
        """通过session_id获取对应的报告"""

        with self._session_factory() as database_session:
            #确保session存在
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
        """标记开始生成报告"""

        with session_scope(self._session_factory) as database_session:
            #把当前状态为pending/failed的报告状态更新为generating
            result = database_session.execute(
                update(InterviewReportModel)
                .where(
                    InterviewReportModel.id == report_id,
                    InterviewReportModel.state.in_(
                        (ReportState.PENDING, ReportState.FAILED)
                    ),
                )
                .values(
                    state=ReportState.GENERATING,   #生成中
                    error_message=None,
                    updated_at=_utc_now(),
                )
            )

            #确保更新成功
            if cast(CursorResult[Any], result).rowcount != 1:
                current_state = database_session.scalar(
                    select(InterviewReportModel.state).where(
                        InterviewReportModel.id == report_id
                    )
                )
                if current_state is None:
                    raise RecordNotFoundError(f"面试报告不存在：{report_id}")
                raise ValueError(f"当前报告状态不允许开始生成：{current_state.value}")

            #确保面试报告存在
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
        """更新报告生成失败"""

        clean_error = error_message.strip()
        if not clean_error:
            raise ValueError("报告失败原因不能为空")

        with session_scope(self._session_factory) as database_session:
            #确保报告存在
            model = _require_model(
                database_session,
                InterviewReportModel,
                report_id,
                f"面试报告不存在：{report_id}",
            )
            #更新状态为failed
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
        """更新报告生成完毕"""
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
