"""M2-D LiveKit job 生命周期的最小测试。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import liverag.main as main


class FakeStore:
    """在内存中记录 Session 生命周期。"""

    def __init__(self, paths: Any) -> None:
        self.paths = paths
        self.initialized = False
        self.started: list[tuple[str, str]] = []
        self.ended: list[tuple[str, str]] = []
        self.runtime: dict[str, dict[str, Any]] = {}

    def initialize(self) -> None:
        self.initialized = True

    def start_session(self, session_id: str, kb_id: str) -> None:
        self.started.append((session_id, kb_id))
        self.runtime[session_id] = {
            "session_id": session_id,
            "kb_id": kb_id,
            "state": "active",
        }

    def end_session(self, session_id: str, state: str = "ended") -> None:
        self.ended.append((session_id, state))
        self.runtime[session_id]["state"] = state

    def read_runtime_state(self, session_id: str) -> dict[str, Any]:
        return dict(self.runtime[session_id])

    def write_runtime_state(self, session_id: str, state: dict[str, Any]) -> None:
        self.runtime[session_id] = dict(state)

    def read_knowledge_overview_meta(self, kb_id: str) -> dict[str, Any]:
        return {"kb_id": kb_id, "stale": False}


class FakeSession:
    """记录 AgentSession.start() 参数，并可模拟启动失败。"""

    def __init__(self, *, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.start_calls: list[dict[str, Any]] = []

    async def start(self, **kwargs: Any) -> None:
        self.start_calls.append(kwargs)
        if self.fail_start:
            raise RuntimeError("session start failed")


class FakeJobContext:
    """提供 my_agent() 使用的最小 LiveKit JobContext 接口。"""

    def __init__(self) -> None:
        room_info = SimpleNamespace(sid="room-id")
        self.job = SimpleNamespace(id="job-id", room=room_info)
        self.room = SimpleNamespace(name="room-name")
        self.connected = False
        self.shutdown_callbacks: list[Any] = []

    async def connect(self) -> None:
        self.connected = True

    def add_shutdown_callback(self, callback: Any) -> None:
        self.shutdown_callbacks.append(callback)


def fake_settings(tmp_path: Path) -> SimpleNamespace:
    """构造 main.py 实际读取的最小配置。"""

    return SimpleNamespace(
        user_data_dir=tmp_path,
        history_limit=8,
        api=SimpleNamespace(rag_ready_timeout_ms=100),
        rag=SimpleNamespace(
            rag_tool_mode="auto",
            base_url="http://127.0.0.1:9721",
            api_key="",
        ),
        voice=SimpleNamespace(livekit_url="ws://127.0.0.1:7880"),
        context_model=SimpleNamespace(),
    )


def install_job_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    ready: bool = True,
    fail_start: bool = False,
) -> SimpleNamespace:
    """替换所有外部依赖，让 my_agent() 完全在内存中执行。"""

    paths = SimpleNamespace(
        user_data_dir=tmp_path,
        db_file=tmp_path / "metadata.db",
        rag_knowledge_bases_dir=tmp_path / "knowledge-bases",
        logs_dir=tmp_path / "logs",
    )
    store = FakeStore(paths)
    session = FakeSession(fail_start=fail_start)
    events: list[tuple[str, dict[str, Any]]] = []
    history_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(main, "load_app_settings", lambda: fake_settings(tmp_path))
    monkeypatch.setattr(main, "build_runtime_paths", lambda _root: paths)
    monkeypatch.setattr(main, "ContextStore", lambda paths: store)
    monkeypatch.setattr(
        main,
        "wait_for_rag_ready",
        lambda **_kwargs: SimpleNamespace(
            ready=ready,
            error=None if ready else "not ready",
            status="ready" if ready else "timeout",
        ),
    )

    class FakeMetadataStore:
        def __init__(self, *_args: Any) -> None:
            pass

        def initialize(self) -> None:
            pass

    async def resolve_knowledge_base(
        _settings: Any,
        _metadata_store: Any,
    ) -> dict[str, str]:
        return {"kb_id": "kb-1", "name": "测试知识库"}

    class FakeRenderer:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def render(self, **kwargs: Any) -> SimpleNamespace:
            assert kwargs["session_id"] == "job-id"
            assert kwargs["kb_id"] == "kb-1"
            return SimpleNamespace(
                prompt="固定系统提示词",
                prompt_chars=7,
                history_count=0,
                kb_id="kb-1",
                kb_name="测试知识库",
                rag_tool_mode="auto",
            )

    class FakeEventLogger:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def append(self, event_name: str, payload: dict[str, Any]) -> None:
            events.append((event_name, payload))

    class FakeHistoryCompactor:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def compact_after_call(self, **kwargs: Any) -> dict[str, Any]:
            history_calls.append(kwargs)
            return {"updated": True}

    monkeypatch.setattr(main, "MetadataStore", FakeMetadataStore)
    monkeypatch.setattr(main, "_resolve_knowledge_base", resolve_knowledge_base)
    monkeypatch.setattr(main, "build_agent_session", lambda **_kwargs: session)
    monkeypatch.setattr(main, "RagClient", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(main, "SessionPromptRenderer", FakeRenderer)
    monkeypatch.setattr(main, "public_voice_config", lambda **_kwargs: {"model": "test"})
    monkeypatch.setattr(main, "EventLogger", FakeEventLogger)
    monkeypatch.setattr(main, "ContextManager", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(main, "VoiceAssistant", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(main, "HistoryCompactor", FakeHistoryCompactor)
    monkeypatch.setattr(main, "MetricsState", lambda: SimpleNamespace())
    monkeypatch.setattr(main, "register_session_metrics_hooks", lambda *_args: None)
    monkeypatch.setattr(main, "start_network_probe_task", lambda **_kwargs: None)

    return SimpleNamespace(
        store=store,
        session=session,
        events=events,
        history_calls=history_calls,
    )


@pytest.mark.asyncio
async def test_agent_job_starts_and_finalizes_one_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """正常任务绑定一个 KB，启动通话，并在挂断后保存结束状态。"""

    dependencies = install_job_dependencies(monkeypatch, tmp_path)
    ctx = FakeJobContext()

    await main.my_agent(ctx)

    assert dependencies.store.initialized is True
    assert dependencies.store.started == [("job-id", "kb-1")]
    assert ctx.connected is True
    assert len(dependencies.session.start_calls) == 1
    assert dependencies.session.start_calls[0]["room"] is ctx.room
    assert len(ctx.shutdown_callbacks) == 1

    await ctx.shutdown_callbacks[0]("normal shutdown")

    assert dependencies.history_calls == [
        {
            "session_id": "job-id",
            "kb_id": "kb-1",
            "kb_name": "测试知识库",
        }
    ]
    assert dependencies.store.ended == [("job-id", "ended")]
    assert dependencies.store.runtime["job-id"]["state"] == "ended"


@pytest.mark.asyncio
async def test_agent_job_stops_before_session_when_rag_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """RAG Core 未就绪时，不创建 Session，也不连接 LiveKit。"""

    dependencies = install_job_dependencies(
        monkeypatch,
        tmp_path,
        ready=False,
    )
    ctx = FakeJobContext()

    with pytest.raises(RuntimeError, match="RAG Core"):
        await main.my_agent(ctx)

    assert dependencies.store.started == []
    assert dependencies.session.start_calls == []
    assert ctx.connected is False


@pytest.mark.asyncio
async def test_agent_job_marks_session_failed_when_start_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AgentSession 启动失败时，将已创建的 Session 标记为 failed。"""

    dependencies = install_job_dependencies(
        monkeypatch,
        tmp_path,
        fail_start=True,
    )
    ctx = FakeJobContext()

    with pytest.raises(RuntimeError, match="session start failed"):
        await main.my_agent(ctx)

    assert dependencies.store.started == [("job-id", "kb-1")]
    assert dependencies.store.ended == [("job-id", "failed")]
    assert dependencies.store.runtime["job-id"]["state"] == "failed"

