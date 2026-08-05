"""把 Alembic 接到当前 Interview Coach 的真实 SQLite 数据库和 SQLAlchemy ORM 模型上。"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import URL, engine_from_config, pool

from liverag.interview.models import Base
from liverag.runtime.paths import build_runtime_paths


#Alembic Config 对象，对应项目根目录的 alembic.ini
config = context.config
#使用alembic.ini中的日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def get_database_url() -> str:
    """获取当前环境实际使用的 Interview 数据库 URL"""

    #规范路径
    database_url = os.getenv("INTERVIEW_DATABASE_URL", "").strip()
    if database_url:
        return database_url

    database_path = build_runtime_paths().db_file.expanduser().resolve()

    #构造真实 SQLAlchemy URL 路径：sqlite+pysqlite:///E:/CS/project/LiveRAG/my-LiveRAG/data/liverag.db
    return URL.create(
        "sqlite+pysqlite",
        database=str(database_path),
    ).render_as_string(hide_password=False)


#现有 ORM 结构
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：不真正创建数据库连接，直接根据 URL 生成 SQL"""

    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    #生成迁移SQL
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接真实数据库并执行迁移，更常用"""

    #获取ini配置
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = get_database_url()

    #创见engine
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    #执行迁移：
    #连接数据库→ 将 connection 和 Base.metadata 交给 Alembic→
    #开启事务→ 执行 migration→ 提交或回滚→ 关闭连接
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
