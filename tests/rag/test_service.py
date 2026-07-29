"""测试 RAG Core ready 等待逻辑及服务启动边界。"""

import json
import urllib.error
from types import SimpleNamespace
from typing import Any

import pytest

import liverag.rag.service as service


class FakeResponse:
    """提供 urllib 响应所需的最小上下文管理器。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class FakeClock:
    """让超时和重试测试无需真实等待。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """固定 ready URL，避免读取开发机环境配置。"""

    settings = SimpleNamespace(
        host="127.0.0.1",
        port=9721,
        api_key="",
        absolute_working_dir="C:/tmp/liverag-test",
    )
    monkeypatch.setattr(service, "RAGSettings", lambda: settings)
    return settings


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """替换单调时钟和 sleep，使重试测试立即结束。"""

    clock = FakeClock()
    monkeypatch.setattr(service.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(service.time, "sleep", clock.sleep)
    return clock


def ready_response(ready: bool) -> FakeResponse:
    """构造符合 RAG Core envelope 的 readyz 响应。"""

    return FakeResponse({"status": "ok", "data": {"ready": ready, "initialized": True}})


@pytest.fixture(autouse=True)
def reset_embedded_service_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试都从没有后台启动线程的状态开始。"""

    monkeypatch.setattr(service, "_START_THREAD", None)
    monkeypatch.setenv("LIGHTRAG_ENABLED", "true")


def assume_service_is_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """ready 轮询测试只模拟已有服务，不创建真实线程。"""

    monkeypatch.setattr(
        service,
        "start_embedded_rag_service",
        lambda: service.RagServiceStartStatus.ALREADY_RUNNING,
    )


def test_returns_immediately_when_first_request_is_ready(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: SimpleNamespace,
    fake_clock: FakeClock,
) -> None:
    """第一次 readyz 返回 true 时立即成功，不执行 sleep。"""

    assume_service_is_running(monkeypatch)
    monkeypatch.setattr(service.urllib.request, "urlopen", lambda *_args, **_kwargs: ready_response(True))

    state = service.wait_for_rag_ready(timeout_ms=1000, interval_ms=10)

    assert state.ready is True
    assert state.status == "external"
    assert state.data == {"ready": True, "initialized": True}
    assert state.error is None
    assert fake_clock.sleeps == []


def test_retries_until_service_becomes_ready(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: SimpleNamespace,
    fake_clock: FakeClock,
) -> None:
    """第一次未就绪时等待，然后再次请求并成功。"""

    assume_service_is_running(monkeypatch)
    responses = iter([ready_response(False), ready_response(True)])
    monkeypatch.setattr(service.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses))

    state = service.wait_for_rag_ready(timeout_ms=1000, interval_ms=10)

    assert state.ready is True
    assert state.status == "external"
    assert fake_clock.sleeps == [0.01]


def test_connection_failure_retries_until_timeout(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: SimpleNamespace,
    fake_clock: FakeClock,
) -> None:
    """连接被拒绝时不抛异常，重试至总超时并返回失败。"""

    assume_service_is_running(monkeypatch)

    def refuse_connection(*_args: object, **_kwargs: object) -> FakeResponse:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(service.urllib.request, "urlopen", refuse_connection)

    state = service.wait_for_rag_ready(timeout_ms=25, interval_ms=10)

    assert state.ready is False
    assert state.status == "external"
    assert state.error is not None
    assert "URLError" in state.error
    assert fake_clock.now == pytest.approx(0.025)


@pytest.mark.parametrize("status_code", [401, 500])
def test_http_error_retries_until_timeout(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: SimpleNamespace,
    fake_clock: FakeClock,
) -> None:
    """readyz 返回 HTTP 错误时记录最近错误并重试至超时。"""

    assume_service_is_running(monkeypatch)

    def raise_http_error(*_args: object, **_kwargs: object) -> FakeResponse:
        raise urllib.error.HTTPError(
            url="http://127.0.0.1:9721/v1/readyz",
            code=status_code,
            msg="error",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(service.urllib.request, "urlopen", raise_http_error)

    state = service.wait_for_rag_ready(timeout_ms=2, interval_ms=1)

    assert state.ready is False
    assert state.status == "external"
    assert state.error == f"readyz 返回 HTTP {status_code}"


def test_sends_configured_api_key_header(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: SimpleNamespace,
    fake_clock: FakeClock,
) -> None:
    """配置 API Key 时，通过 X-API-Key 请求头发送。"""

    fake_settings.api_key = "test-secret"
    assume_service_is_running(monkeypatch)
    captured_headers: list[str | None] = []

    def capture_request(request: Any, **_kwargs: object) -> FakeResponse:
        headers = {key.lower(): value for key, value in request.header_items()}
        captured_headers.append(headers.get("x-api-key"))
        return ready_response(True)

    monkeypatch.setattr(service.urllib.request, "urlopen", capture_request)

    state = service.wait_for_rag_ready(timeout_ms=100, interval_ms=1)

    assert state.ready is True
    assert captured_headers == ["test-secret"]


def test_can_explicitly_start_embedded_service(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: SimpleNamespace,
    fake_clock: FakeClock,
) -> None:
    """显式允许时保留旧的嵌入式 RAG 启动行为。"""

    assume_service_is_running(monkeypatch)
    monkeypatch.setattr(
        service.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: ready_response(True),
    )

    state = service.wait_for_rag_ready(
        timeout_ms=100,
        interval_ms=1,
        start_if_missing=True,
    )

    assert state.ready is True
    assert state.status == "already_running"


def test_service_module_exposes_embedded_start_capability() -> None:
    """service.py 只能等待，不能提供线程、子进程或内嵌启动入口。"""

    assert hasattr(service, "start_embedded_rag_service")
    assert hasattr(service, "RagServiceStartStatus")
    assert hasattr(service, "uvicorn")
    assert hasattr(service, "Thread")


def test_start_is_disabled_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式禁用 RAG 时不读取配置，也不创建后台线程。"""

    monkeypatch.setenv("LIGHTRAG_ENABLED", "false")
    monkeypatch.setattr(service, "RAGSettings", lambda: pytest.fail("不应读取配置"))

    status = service.start_embedded_rag_service()

    assert status == service.RagServiceStartStatus.DISABLED
    assert service._START_THREAD is None


def test_reuses_service_when_port_is_already_open(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: SimpleNamespace,
) -> None:
    """目标端口已有服务时直接复用，不能再创建线程。"""

    monkeypatch.setattr(service, "port_is_open", lambda *_args: True)
    monkeypatch.setattr(service, "Thread", lambda **_kwargs: pytest.fail("不应创建线程"))

    status = service.start_embedded_rag_service()

    assert status == service.RagServiceStartStatus.ALREADY_RUNNING
    assert service._START_THREAD is None


def test_starts_uvicorn_in_daemon_thread(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: SimpleNamespace,
) -> None:
    """端口未开放时创建守护线程，并在其中启动当前 RAG 服务。"""

    calls: dict[str, Any] = {}

    class FakeThread:
        def __init__(self, *, target: Any, name: str, daemon: bool) -> None:
            calls.update(target=target, name=name, daemon=daemon)
            self.started = False

        def start(self) -> None:
            self.started = True
            calls["target"]()

        def is_alive(self) -> bool:
            return self.started

    monkeypatch.setattr(service, "port_is_open", lambda *_args: False)
    monkeypatch.setattr(service, "Thread", FakeThread)
    monkeypatch.setattr(service.uvicorn, "run", lambda app, **kwargs: calls.update(app=app, kwargs=kwargs))

    status = service.start_embedded_rag_service()

    assert status == service.RagServiceStartStatus.STARTED
    assert calls["name"] == "rag-service"
    assert calls["daemon"] is True
    assert calls["app"] == "liverag.rag.server:app"
    assert calls["kwargs"]["host"] == fake_settings.host
    assert calls["kwargs"]["port"] == fake_settings.port
    assert service._START_THREAD.started is True


def test_does_not_start_duplicate_thread(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: SimpleNamespace,
) -> None:
    """已有存活线程时返回 starting，不重复创建 Uvicorn。"""

    running_thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(service, "_START_THREAD", running_thread)
    monkeypatch.setattr(service, "port_is_open", lambda *_args: False)
    monkeypatch.setattr(service, "Thread", lambda **_kwargs: pytest.fail("不应重复创建线程"))

    status = service.start_embedded_rag_service()

    assert status == service.RagServiceStartStatus.STARTING
    assert service._START_THREAD is running_thread
