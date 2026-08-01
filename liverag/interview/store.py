"""Interview 业务数据的 SQLite 访问层。

`InterviewStore` 只负责持久化，不决定某个状态是否允许迁移，也不调用 LLM
或 LiveKit。上层状态机负责业务规则，Store 负责事务、唯一约束、乐观锁和
数据库记录与 Python 对象之间的转换。
"""

from __future__ import annotations

from datetime import timezone
from time import datetime
import json
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from liverag.interview.migrations import apply_migrations
from liverag.interview.models import (
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
from liverag.interview.schemas import (
    AnswerEvaluation,
    InterviewConfig,
    InterviewPlan,
    InterviewState,
)

#======================== 三种错误 ======================== 
class RecordNotFoundError(LookupError):
    """请求的 Interview 数据库记录不存在。"""


class ConcurrentUpdateError(RuntimeError):
    """记录已被其他请求更新，当前调用持有的版本已经过期。"""


class DuplicateEventError(RuntimeError):
    """相同事件标识已经处理过，不能再次产生业务效果。"""

def utc_now_iso() -> str:
    """返回字符串类型时间"""
    return datetime.now(timezone.utc).isoformat()


def _model_to_json(model: BaseModel) -> str:
    """把 Pydantic 模型序列化为紧凑、稳定的 JSON 字符串。"""

    return model.model_dump_json(exclude_none=False)


def _dict_to_json(value: dict[str, Any]) -> str:
    """把普通字典序列化为支持中文且格式紧凑的 JSON 字符串。"""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _interview_from_row(row: sqlite3.Row) -> InterviewRecord:
    """把 `interviews` 查询结果转换为不可变的面试任务记录。"""

    return InterviewRecord(
        id=str(row["id"]),
        title=str(row["title"]),
        state=InterviewState(str(row["state"])),
        config_json=str(row["config_json"]),
        plan_json=str(row["plan_json"]) if row["plan_json"] is not None else None,
        version=int(row["version"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _session_from_row(row: sqlite3.Row) -> InterviewSessionRecord:
    """把 `interview_sessions` 查询结果转换为不可变的 Session 记录。"""

    resume_state = row["resume_state"]
    return InterviewSessionRecord(
        id=str(row["id"]),
        interview_id=str(row["interview_id"]),
        state=InterviewState(str(row["state"])),
        resume_state=InterviewState(str(resume_state)) if resume_state else None,
        current_question_index=int(row["current_question_index"]),
        current_question_id=(
            str(row["current_question_id"])
            if row["current_question_id"] is not None
            else None
        ),
        follow_up_count=int(row["follow_up_count"]),
        version=int(row["version"]),
        started_at=str(row["started_at"]) if row["started_at"] else None,
        ended_at=str(row["ended_at"]) if row["ended_at"] else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _attempt_from_row(row: sqlite3.Row) -> InterviewAttemptRecord:
    """把 `interview_attempts` 查询结果转换为不可变的连接记录。"""

    return InterviewAttemptRecord(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        room_name=str(row["room_name"]),
        state=AttemptState(str(row["state"])),
        connected_at=str(row["connected_at"]) if row["connected_at"] else None,
        disconnected_at=(
            str(row["disconnected_at"]) if row["disconnected_at"] else None
        ),
        error_message=str(row["error_message"]) if row["error_message"] else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _answer_from_row(row: sqlite3.Row) -> InterviewAnswerRecord:
    """把 `interview_answers` 查询结果转换为不可变的回答记录。"""

    return InterviewAnswerRecord(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        question_id=str(row["question_id"]),
        attempt_id=str(row["attempt_id"]),
        answer_number=int(row["answer_number"]),
        transcript=str(row["transcript"]),
        state=AnswerState(str(row["state"])),
        source_event_id=str(row["source_event_id"]),
        started_at=str(row["started_at"]),
        ended_at=str(row["ended_at"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _report_from_row(row: sqlite3.Row) -> InterviewReportRecord:
    """把 `interview_reports` 查询结果转换为不可变的报告记录。"""

    return InterviewReportRecord(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        state=ReportState(str(row["state"])),
        content_json=str(row["content_json"]) if row["content_json"] else None,
        error_message=str(row["error_message"]) if row["error_message"] else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        completed_at=str(row["completed_at"]) if row["completed_at"] else None,
    )


class InterviewStore:
    """集中管理 Interview SQLite 数据库的连接和读写操作。"""

    def __init__(self, db_path: Path):
        """绑定数据库路径；此时不会创建文件或执行迁移。"""

        self.db_path = db_path.expanduser()

    def initialize(self) -> list[int]:
        """初始化数据库并返回本次实际执行的迁移版本。"""

        return apply_migrations(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        """创建启用外键、WAL 和行名称访问的短生命周期连接。"""

        connection = sqlite3.connect(self.db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

#======================== Interview 任务 ========================
    def create_interview(
        self,
        *,
        title: str,
        config: InterviewConfig,
        interview_id: str | None = None,
    ) -> InterviewRecord:
        """创建一条尚未开始实时执行的模拟面试任务。"""

        clean_title = title.strip()
        if not clean_title:
            raise ValueError("面试标题不能为空")

        record_id = interview_id or generate_id("interview")
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO interviews (
                    id, title, state, config_json, plan_json,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, 1, ?, ?)
                """,
                (
                    record_id,
                    clean_title,
                    InterviewState.CREATED.value,
                    _model_to_json(config),
                    now,
                    now,
                ),
            )
        return self.get_interview(record_id)

    def get_interview(self, interview_id: str) -> InterviewRecord:
        """按标识读取一条模拟面试任务，不存在时抛出明确异常。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM interviews WHERE id = ?",
                (interview_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"模拟面试不存在：{interview_id}")
        return _interview_from_row(row)

    def get_interview_config(self, interview_id: str) -> InterviewConfig:
        """读取并校验某场面试创建时保存的实际采用的配置。"""

        record = self.get_interview(interview_id)
        return InterviewConfig.model_validate_json(record.config_json)

    def get_interview_plan(self, interview_id: str) -> InterviewPlan | None:
        """读取并校验冻结计划；尚未生成计划时返回 None。"""

        record = self.get_interview(interview_id)
        if record.plan_json is None:
            return None
        return InterviewPlan.model_validate_json(record.plan_json)

    def save_interview_plan(
        self,
        *,
        interview_id: str,
        plan: InterviewPlan,
        expected_version: int,
    ) -> InterviewRecord:
        """保存冻结计划，并通过乐观锁拒绝覆盖其他请求的新修改。

        计划保存成功后面试进入 READY，版本号增加 1。调用方必须传入此前
        读取到的版本；如果数据库版本已经变化，本方法不会覆盖新数据。
        """

        now = utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE interviews
                SET plan_json = ?, state = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    _model_to_json(plan),
                    InterviewState.READY.value,
                    now,
                    interview_id,
                    expected_version,
                ),
            )
            #修改的数据行数不为1，说明要么面试不存在，要么版本号不匹配，抛出明确异常
            if cursor.rowcount != 1:
                self._raise_missing_or_concurrent_interview(
                    connection,
                    interview_id=interview_id,
                    expected_version=expected_version,
                )
        return self.get_interview(interview_id)

    def _raise_missing_or_concurrent_interview(
        self,
        connection: sqlite3.Connection,
        *,
        interview_id: str,
        expected_version: int,
    ) -> None:
        """区分面试不存在和乐观锁冲突，向上层提供准确错误。"""

        row = connection.execute(
            "SELECT version FROM interviews WHERE id = ?",
            (interview_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"模拟面试不存在：{interview_id}")
        raise ConcurrentUpdateError(
            f"模拟面试版本已变化：期望 {expected_version}，实际 {row['version']}"
        )

#======================== Interview Session ========================
    def create_session(
        self,
        *,
        interview_id: str,
        session_id: str | None = None,
    ) -> InterviewSessionRecord:
        """为已生成计划的面试创建一次可恢复的实时执行 Session。"""

        interview = self.get_interview(interview_id)
        #如果计划未冻结，提前结束抛出异常
        if interview.state is not InterviewState.READY or interview.plan_json is None:
            raise ValueError("只有已生成并冻结计划的面试才能创建 Session")

        record_id = session_id or generate_id("session")
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO interview_sessions (
                    id, interview_id, state, resume_state,
                    current_question_index, current_question_id,
                    follow_up_count, version, started_at, ended_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, NULL, 0, NULL, 0, 1, NULL, NULL, ?, ?)
                """,
                (record_id, interview_id, InterviewState.READY.value, now, now),
            )
        return self.get_session(record_id)

    def get_session(self, session_id: str) -> InterviewSessionRecord:
        """按标识读取实时面试 Session，不存在时抛出明确异常。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM interview_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"面试 Session 不存在：{session_id}")
        return _session_from_row(row)

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
        """原子更新状态机快照，并使用 version 防止并发覆盖。

        本方法不判断迁移是否合法；合法性由稍后实现的状态机负责。Store 只
        校验计数不为负，并确保调用方更新的是自己读取过的版本。
        """

        if current_question_index < 0:
            raise ValueError("当前题目索引不能为负数")
        if follow_up_count < 0:
            raise ValueError("追问次数不能为负数")

        now = utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE interview_sessions
                SET state = ?, resume_state = ?, current_question_index = ?,
                    current_question_id = ?, follow_up_count = ?,
                    version = version + 1, started_at = ?, ended_at = ?, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    state.value,
                    resume_state.value if resume_state else None,
                    current_question_index,
                    current_question_id,
                    follow_up_count,
                    started_at,
                    ended_at,
                    now,
                    session_id,
                    expected_version,
                ),
            )

            #判断更新是否成功
            if cursor.rowcount != 1:
                #查询session是否存在
                row = connection.execute(
                    "SELECT version FROM interview_sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                #session不存在
                if row is None:
                    raise RecordNotFoundError(f"面试 Session 不存在：{session_id}")
                #session存在，但版本过期
                raise ConcurrentUpdateError(
                    f"Session 版本已变化：期望 {expected_version}，实际 {row['version']}"
                )
        return self.get_session(session_id)

    def create_attempt(
        self,
        *,
        session_id: str,
        room_name: str,
        attempt_id: str | None = None,
    ) -> InterviewAttemptRecord:
        """为 Session 创建一次新的 LiveKit 房间连接记录。"""

        self.get_session(session_id)
        clean_room_name = room_name.strip()
        if not clean_room_name:
            raise ValueError("LiveKit 房间名称不能为空")

        record_id = attempt_id or generate_id("attempt")
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO interview_attempts (
                    id, session_id, room_name, state, connected_at,
                    disconnected_at, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    record_id,
                    session_id,
                    clean_room_name,
                    AttemptState.CREATED.value,
                    now,
                    now,
                ),
            )
        return self.get_attempt(record_id)

    def get_attempt(self, attempt_id: str) -> InterviewAttemptRecord:
        """按标识读取一次 LiveKit 连接记录。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM interview_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"面试连接 Attempt 不存在：{attempt_id}")
        return _attempt_from_row(row)

    def update_attempt_state(
        self,
        *,
        attempt_id: str,
        state: AttemptState,
        error_message: str | None = None,
    ) -> InterviewAttemptRecord:
        """更新 LiveKit 连接状态并自动记录连接或断开时间。"""

        current = self.get_attempt(attempt_id)
        now = utc_now_iso()
        connected_at = current.connected_at
        disconnected_at = current.disconnected_at
        if state is AttemptState.CONNECTED and connected_at is None:
            connected_at = now
        if state in {AttemptState.DISCONNECTED, AttemptState.FAILED}:
            disconnected_at = now

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE interview_attempts
                SET state = ?, connected_at = ?, disconnected_at = ?,
                    error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    state.value,
                    connected_at,
                    disconnected_at,
                    error_message,
                    now,
                    attempt_id,
                ),
            )
        return self.get_attempt(attempt_id)

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
        """在同一事务中写入事件并更新 Session 状态快照。

        `event_id` 保证重复投递不会产生第二次状态变化，`expected_version`
        保证两个并发事件不能互相覆盖。状态是否合法由状态机在调用前判断。
        """

        clean_event_type = event_type.strip()
        if not clean_event_type:
            raise ValueError("事件类型不能为空")
        if current_question_index < 0 or follow_up_count < 0:
            raise ValueError("题目索引和追问次数不能为负数")

        now = utc_now_iso()
        version_after = expected_version + 1
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id FROM interview_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                raise DuplicateEventError(f"事件已经处理：{event_id}")

            cursor = connection.execute(
                """
                UPDATE interview_sessions
                SET state = ?, resume_state = ?, current_question_index = ?,
                    current_question_id = ?, follow_up_count = ?, version = ?,
                    started_at = ?, ended_at = ?, updated_at = ?
                WHERE id = ? AND version = ? AND state = ?
                """,
                (
                    state_after.value,
                    resume_state.value if resume_state else None,
                    current_question_index,
                    current_question_id,
                    follow_up_count,
                    version_after,
                    started_at,
                    ended_at,
                    now,
                    session_id,
                    expected_version,
                    state_before.value,
                ),
            )
            if cursor.rowcount != 1:
                row = connection.execute(
                    "SELECT state, version FROM interview_sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    raise RecordNotFoundError(f"面试 Session 不存在：{session_id}")
                raise ConcurrentUpdateError(
                    "Session 已发生变化："
                    f"期望状态/版本 {state_before.value}/{expected_version}，"
                    f"实际为 {row['state']}/{row['version']}"
                )

            connection.execute(
                """
                INSERT INTO interview_events (
                    id, session_id, event_type, payload_json,
                    state_before, state_after, version_before,
                    version_after, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_id,
                    clean_event_type,
                    _dict_to_json(payload),
                    state_before.value,
                    state_after.value,
                    expected_version,
                    version_after,
                    now,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        return InterviewEventRecord(
            id=event_id,
            session_id=session_id,
            event_type=clean_event_type,
            payload_json=_dict_to_json(payload),
            state_before=state_before,
            state_after=state_after,
            version_before=expected_version,
            version_after=version_after,
            created_at=now,
        )

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
        """保存一条 STT final transcript，并依靠事件唯一键防止重复回答。"""

        clean_transcript = transcript.strip()
        if not clean_transcript:
            raise ValueError("最终回答文本不能为空")
        if answer_number < 1:
            raise ValueError("回答序号必须从 1 开始")

        record_id = answer_id or generate_id("answer")
        now = utc_now_iso()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO interview_answers (
                        id, session_id, question_id, attempt_id,
                        answer_number, transcript, state, source_event_id,
                        started_at, ended_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record_id,
                        session_id,
                        question_id,
                        attempt_id,
                        answer_number,
                        clean_transcript,
                        AnswerState.RECEIVED.value,
                        source_event_id,
                        started_at,
                        ended_at,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if "source_event_id" in str(exc):
                raise DuplicateEventError(f"回答事件已经处理：{source_event_id}") from exc
            raise
        return self.get_answer(record_id)

    def get_answer(self, answer_id: str) -> InterviewAnswerRecord:
        """按标识读取候选人的一条最终回答。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM interview_answers WHERE id = ?",
                (answer_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"面试回答不存在：{answer_id}")
        return _answer_from_row(row)

    def save_evaluation(
        self,
        *,
        evaluation_id: str,
        evaluation: AnswerEvaluation,
        rubric_version: int = 1,
    ) -> AnswerEvaluation:
        """保存一条回答评价，并把对应回答标记为 EVALUATED。"""

        if rubric_version < 1:
            raise ValueError("评分规则版本必须从 1 开始")
        now = utc_now_iso()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE interview_answers
                SET state = ?, updated_at = ?
                WHERE id = ?
                """,
                (AnswerState.EVALUATED.value, now, evaluation.answer_id),
            )
            if cursor.rowcount != 1:
                raise RecordNotFoundError(f"面试回答不存在：{evaluation.answer_id}")
            connection.execute(
                """
                INSERT INTO answer_evaluations (
                    id, answer_id, rubric_version, evaluation_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation_id,
                    evaluation.answer_id,
                    rubric_version,
                    _model_to_json(evaluation),
                    now,
                    now,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return evaluation

    def create_report(
        self,
        *,
        session_id: str,
        report_id: str | None = None,
    ) -> InterviewReportRecord:
        """为已进入收尾阶段的 Session 创建待生成报告记录。"""

        self.get_session(session_id)
        record_id = report_id or generate_id("report")
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO interview_reports (
                    id, session_id, state, content_json, error_message,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, NULL, NULL, ?, ?, NULL)
                """,
                (record_id, session_id, ReportState.PENDING.value, now, now),
            )
        return self.get_report(record_id)

    def get_report(self, report_id: str) -> InterviewReportRecord:
        """按报告标识读取生成状态和内容。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM interview_reports WHERE id = ?",
                (report_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"面试报告不存在：{report_id}")
        return _report_from_row(row)

    def complete_report(
        self,
        *,
        report_id: str,
        content: dict[str, Any],
    ) -> InterviewReportRecord:
        """保存结构化报告内容并将报告标记为完成。"""

        now = utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE interview_reports
                SET state = ?, content_json = ?, error_message = NULL,
                    updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    ReportState.COMPLETED.value,
                    _dict_to_json(content),
                    now,
                    now,
                    report_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RecordNotFoundError(f"面试报告不存在：{report_id}")
        return self.get_report(report_id)


__all__ = [
    "InterviewStore",
    "RecordNotFoundError",
    "ConcurrentUpdateError",
    "DuplicateEventError",
]
