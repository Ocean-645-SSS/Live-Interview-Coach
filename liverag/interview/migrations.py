"""Interview 专用 SQLite 数据库迁移。

本模块用有序、不可变的迁移列表管理数据库结构。每个版本只执行一次，
执行成功后会记录到 `schema_migrations` 表。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from liverag.interview.models import utc_now_iso


@dataclass(frozen=True, slots=True)
class Migration:
    """描述一个按顺序执行的数据库结构版本。"""

    version: int    #版本号
    name: str   
    statements: tuple[str, ...]    #本版本需要在同一事务中执行的SQL语句


def _validate_migrations(migrations: Iterable[Migration]) -> tuple[Migration, ...]:
    """检查迁移版本是否从 1 开始连续递增且名称不重复。

    迁移列表在执行前先经过这项检查，能够避免遗漏版本、重复版本或复制名称
    造成不同环境中的数据库结构不一致。

    Args:
        migrations: 等待检查的迁移对象序列。

    Returns:
        固化为 tuple 的迁移序列，供后续执行复用。

    Raises:
        ValueError: 迁移列表为空、版本不连续或名称重复时抛出。
    """

    migration_list = tuple(migrations)
    if not migration_list:
        raise ValueError("数据库迁移列表不能为空")

    actual_versions = [migration.version for migration in migration_list]
    expected_versions = list(range(1, len(migration_list) + 1))
    if actual_versions != expected_versions:
        raise ValueError("数据库迁移版本必须从 1 开始连续递增")

    names = [migration.name for migration in migration_list]
    if len(names) != len(set(names)):
        raise ValueError("数据库迁移名称不能重复")

    return migration_list


def _connect(db_path: Path) -> sqlite3.Connection:
    """创建启用外键、WAL 和忙等待的 SQLite 连接。"""

    expanded_path = db_path.expanduser()
    expanded_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(expanded_path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _ensure_migration_table(connection: sqlite3.Connection) -> None:
    """创建记录已执行版本的迁移表。

    该表必须先于其他业务表存在，因此不属于某个具体业务迁移。建表操作具有
    幂等性，多次启动服务不会覆盖已经记录的版本。
    """

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def _read_applied_versions(connection: sqlite3.Connection) -> set[int]:
    """读取当前数据库已经成功执行的全部迁移版本。"""

    rows = connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {int(row["version"]) for row in rows}


def _apply_single_migration(
    connection: sqlite3.Connection,
    migration: Migration,
) -> None:
    """在同一写事务中执行一个迁移版本并记录执行结果。

    `BEGIN IMMEDIATE` 会在修改 schema 前取得写锁。任意 SQL 失败都会回滚
    当前版本，确保不会出现“部分表已创建，但版本已被误认为成功”的状态。
    """

    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO schema_migrations (version, name, applied_at)
            VALUES (?, ?, ?)
            """,
            (migration.version, migration.name, utc_now_iso()),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def apply_migrations(
    db_path: Path,
    migrations: Iterable[Migration] | None = None,
) -> list[int]: #本次调用实际执行的版本号列表
    """检查数据库已经执行到哪个版本，然后执行 MIGRATIONS 中尚未执行的建表或升级语句"""

    #选择需要使用的迁移列表
    selected_migrations = _validate_migrations(
        MIGRATIONS if migrations is None else migrations
    )
    #创建数据库连接并执行迁移
    connection = _connect(db_path)
    applied_now: list[int] = []

    try:
        #创建迁移记录表
        _ensure_migration_table(connection)
        #遍历迁移记录表
        applied_versions = _read_applied_versions(connection)

        for migration in selected_migrations:
            #跳过已经执行的版本
            if migration.version in applied_versions:
                continue
            #执行建表语句
            _apply_single_migration(connection, migration)
            #记录本次执行的版本号
            applied_now.append(migration.version)
    finally:
        connection.close()

    return applied_now



# 在数据库中创建models.py 中对应的几个模型
MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="create_interview_v1_tables",
        statements=(
            """
            CREATE TABLE interviews (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'CREATED', 'PREPARING', 'READY', 'INTRODUCTION',
                    'ASKING', 'LISTENING', 'EVALUATING', 'FOLLOW_UP',
                    'NEXT_QUESTION', 'COMPLETING', 'COMPLETED', 'PAUSED',
                    'ABORTED', 'FAILED'
                )),
                config_json TEXT NOT NULL,
                plan_json TEXT,
                version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE interview_sessions (
                id TEXT PRIMARY KEY,
                interview_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'CREATED', 'PREPARING', 'READY', 'INTRODUCTION',
                    'ASKING', 'LISTENING', 'EVALUATING', 'FOLLOW_UP',
                    'NEXT_QUESTION', 'COMPLETING', 'COMPLETED', 'PAUSED',
                    'ABORTED', 'FAILED'
                )),
                resume_state TEXT CHECK (
                    resume_state IS NULL OR resume_state IN (
                        'INTRODUCTION', 'ASKING', 'LISTENING', 'EVALUATING',
                        'FOLLOW_UP', 'NEXT_QUESTION', 'COMPLETING'
                    )
                ),
                current_question_index INTEGER NOT NULL DEFAULT 0
                    CHECK (current_question_index >= 0),
                current_question_id TEXT,
                follow_up_count INTEGER NOT NULL DEFAULT 0
                    CHECK (follow_up_count >= 0),
                version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
                started_at TEXT,
                ended_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (interview_id)
                    REFERENCES interviews(id)
                    ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE interview_attempts (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                room_name TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK (state IN (
                    'CREATED', 'CONNECTED', 'DISCONNECTED', 'FAILED'
                )),
                connected_at TEXT,
                disconnected_at TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (session_id)
                    REFERENCES interview_sessions(id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE interview_events (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                state_before TEXT NOT NULL,
                state_after TEXT NOT NULL,
                version_before INTEGER NOT NULL CHECK (version_before >= 1),
                version_after INTEGER NOT NULL CHECK (version_after >= version_before),
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id)
                    REFERENCES interview_sessions(id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE interview_answers (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                question_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                answer_number INTEGER NOT NULL CHECK (answer_number >= 1),
                transcript TEXT NOT NULL CHECK (length(trim(transcript)) > 0),
                state TEXT NOT NULL CHECK (state IN (
                    'RECEIVED', 'EVALUATING', 'EVALUATED', 'FAILED'
                )),
                source_event_id TEXT NOT NULL UNIQUE,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (session_id, question_id, answer_number),
                FOREIGN KEY (session_id)
                    REFERENCES interview_sessions(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (attempt_id)
                    REFERENCES interview_attempts(id)
                    ON DELETE RESTRICT,
                FOREIGN KEY (source_event_id)
                    REFERENCES interview_events(id)
                    ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE answer_evaluations (
                id TEXT PRIMARY KEY,
                answer_id TEXT NOT NULL UNIQUE,
                rubric_version INTEGER NOT NULL DEFAULT 1
                    CHECK (rubric_version >= 1),
                evaluation_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (answer_id)
                    REFERENCES interview_answers(id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE interview_reports (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK (state IN (
                    'PENDING', 'GENERATING', 'COMPLETED', 'FAILED'
                )),
                content_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (session_id)
                    REFERENCES interview_sessions(id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX idx_interview_sessions_interview_id
            ON interview_sessions(interview_id, created_at)
            """,
            """
            CREATE INDEX idx_interview_attempts_session_id
            ON interview_attempts(session_id, created_at)
            """,
            """
            CREATE INDEX idx_interview_events_session_id
            ON interview_events(session_id, version_after, created_at)
            """,
            """
            CREATE INDEX idx_interview_answers_session_question
            ON interview_answers(session_id, question_id, answer_number)
            """,
            """
            CREATE INDEX idx_interview_reports_state
            ON interview_reports(state, updated_at)
            """,
        ),
    ),
)


__all__ = ["Migration", "MIGRATIONS", "apply_migrations"]
