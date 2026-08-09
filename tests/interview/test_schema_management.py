from __future__ import annotations

import ast
import json
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from liverag.interview.records import candidate_profile_id_for_kb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_STARTUP_PATHS = (
    PROJECT_ROOT / "liverag" / "api" / "server.py",
    PROJECT_ROOT / "liverag" / "interview_main.py",
)


def _call_name(call: ast.Call) -> str:
    parts: list[str] = []
    node = call.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_production_startup_does_not_manage_interview_schema() -> None:
    forbidden_suffixes = (
        "metadata.create_all",
        "command.upgrade",
        "command.stamp",
    )

    for path in PRODUCTION_STARTUP_PATHS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        forbidden_calls = [
            _call_name(node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _call_name(node).endswith(forbidden_suffixes)
        ]

        assert forbidden_calls == [], f"{path} manages schema: {forbidden_calls}"


def test_migration_backfills_historical_interview_candidate_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "historical.db"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("INTERVIEW_DATABASE_URL", database_url)
    config = Config(PROJECT_ROOT / "alembic.ini")

    command.upgrade(config, "75e3f27927f0")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO interviews (
                    id, title, state, config_json, plan_json, version,
                    created_at, updated_at
                ) VALUES (
                    :id, :title, :state, :config_json, NULL, 1,
                    :created_at, :updated_at
                )
                """
            ),
            {
                "id": "interview_historical",
                "title": "历史面试",
                "state": "CREATED",
                "config_json": json.dumps({"candidate_kb_id": " My   Resume "}),
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            },
        )

    command.upgrade(config, "head")

    expected_id = candidate_profile_id_for_kb(" My   Resume ")
    with engine.connect() as connection:
        interview = connection.execute(
            text(
                "SELECT candidate_profile_id FROM interviews "
                "WHERE id = 'interview_historical'"
            )
        ).one()
        candidate = connection.execute(
            text("SELECT id, kb_id FROM candidate_profiles WHERE id = :id"),
            {"id": expected_id},
        ).one()

    schema = inspect(engine)
    assert interview.candidate_profile_id == expected_id
    assert candidate.id == expected_id
    assert candidate.kb_id == " My   Resume "
    assert any(
        column["name"] == "candidate_profile_id" and not column["nullable"]
        for column in schema.get_columns("interviews")
    )
    engine.dispose()


NOW_ISO = "2026-08-09T00:00:00+00:00"
