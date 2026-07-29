"""M3-A Session 管理接口测试。"""

from pathlib import Path
from types import ModuleType

from fastapi.testclient import TestClient


def _create_session(
    api_server: ModuleType,
    *,
    session_id: str,
    state: str = "active",
) -> None:
    api_server.store.start_session(session_id=session_id, kb_id="default")
    api_server.store.append_message(
        session_id=session_id,
        role="user",
        content="你好",
        turn_index=1,
    )
    api_server.store.append_message(
        session_id=session_id,
        role="assistant",
        content="你好，有什么可以帮助你？",
        turn_index=1,
    )
    if state != "active":
        api_server.store.end_session(session_id=session_id, state=state)


def test_turns_returns_aggregated_session_messages(
    api_client: TestClient,
    api_server: ModuleType,
) -> None:
    _create_session(api_server, session_id="session-turns")

    response = api_client.get("/sessions/session-turns/turns")

    assert response.status_code == 200
    turns = response.json()
    assert len(turns) == 1
    assert turns[0]["user_message"]["content"] == "你好"
    assert turns[0]["assistant_message"]["content"] == "你好，有什么可以帮助你？"


def test_active_session_cannot_be_deleted(
    api_client: TestClient,
    api_server: ModuleType,
) -> None:
    _create_session(api_server, session_id="session-active")

    response = api_client.delete("/sessions/session-active")

    assert response.status_code == 409
    assert api_server.store.read_runtime_state("session-active")["state"] == "active"


def test_ended_session_can_be_deleted(
    api_client: TestClient,
    api_server: ModuleType,
) -> None:
    _create_session(api_server, session_id="session-ended", state="ended")
    session_dir: Path = api_server.paths.sessions_dir / "session-ended"
    assert session_dir.is_dir()

    response = api_client.delete("/sessions/session-ended")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "deleted": True,
        "session_id": "session-ended",
    }
    assert not session_dir.exists()


def test_session_list_contract(
    api_client: TestClient,
    api_server: ModuleType,
) -> None:
    _create_session(api_server, session_id="session-list", state="ended")

    response = api_client.get("/sessions")

    assert response.status_code == 200
    assert any(item["session_id"] == "session-list" for item in response.json()["sessions"])


def test_session_detail_contract(
    api_client: TestClient,
    api_server: ModuleType,
) -> None:
    _create_session(api_server, session_id="session-detail", state="ended")

    response = api_client.get("/sessions/session-detail")

    assert response.status_code == 200
    assert response.json()["session_id"] == "session-detail"
    assert response.json()["state"] == "ended"


def test_session_export_contract(
    api_client: TestClient,
    api_server: ModuleType,
) -> None:
    _create_session(api_server, session_id="session-export", state="ended")

    response = api_client.get("/sessions/session-export/export")

    assert response.status_code == 200
    exported = response.json()
    assert exported["runtime_state"]["session_id"] == "session-export"
    assert len(exported["messages"]) == 2
    assert "rag_context" in exported
    assert "turns" in exported
    assert "session_system_prompt" in exported
