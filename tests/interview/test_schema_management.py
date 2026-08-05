from __future__ import annotations

import ast
from pathlib import Path

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
