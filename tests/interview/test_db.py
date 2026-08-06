from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from liverag.interview.persistence.db import (
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)


def test_create_sqlite_engine_configures_database(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "interview.db"
    engine = create_sqlite_engine(database_path)

    try:
        with engine.connect() as connection:
            foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
            journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
            busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()

        assert database_path.is_file()
        assert foreign_keys == 1
        assert journal_mode == "wal"
        assert busy_timeout == 10_000
    finally:
        engine.dispose()


def test_session_scope_commits_and_rolls_back(tmp_path: Path) -> None:
    engine = create_sqlite_engine(tmp_path / "interview.db")
    factory = create_session_factory(engine)

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE probe ("
                    "id INTEGER PRIMARY KEY, "
                    "value TEXT NOT NULL"
                    ")"
                )
            )

        with session_scope(factory) as session:
            session.execute(
                text("INSERT INTO probe (value) VALUES (:value)"),
                {"value": "committed"},
            )

        with (
            pytest.raises(RuntimeError, match="rollback"),
            session_scope(factory) as session,
        ):
            session.execute(
                text("INSERT INTO probe (value) VALUES (:value)"),
                {"value": "rolled back"},
            )
            raise RuntimeError("rollback")

        with engine.connect() as connection:
            values = connection.execute(
                text("SELECT value FROM probe ORDER BY id")
            ).scalars()

            assert list(values) == ["committed"]
    finally:
        engine.dispose()
