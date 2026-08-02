"""面试模块的 SQLAlchemy 与 SQLite 基础设施。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

__all__ = ["create_session_factory", "create_sqlite_engine", "session_scope"]


def create_sqlite_engine(database_path: Path, *, echo: bool = False) -> Engine:
    """创建面向 FastAPI 使用场景的 SQLite engine：管理数据库连接"""

    #规范化数据库路径
    resolved_path = database_path.expanduser().resolve()
    #创建数据库目录
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    #创建SQLAlchemy engine，使用 WAL 模式和 10 秒超时
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(resolved_path)),
        connect_args={"check_same_thread": False, "timeout": 10.0}, #允许多线程访问
        echo=echo,
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA busy_timeout = 10000")
        finally:
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """创建每次调用都会产生独立 Session 的工厂：保存session如何创建的统一配置"""

    return sessionmaker(
        bind=engine,    #绑定数据库引擎
        class_=Session,    #创建标准SQLAlchemy Session
        autoflush=False,    #关闭自动刷新，避免在事务中意外触发查询
        expire_on_commit=False,    #关闭提交后过期，避免在事务中意外触发查询
    )


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """提供自动提交、异常回滚并始终关闭 Session 的事务边界。"""

    session = factory()
    try:
        with session.begin():
            yield session
    finally:
        session.close()
