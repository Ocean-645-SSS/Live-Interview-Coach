"""M3-A 管理 API 测试夹具。"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_server(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """在 pytest 临时用户目录中重新加载管理 API 模块。"""

    import liverag.config.settings as settings_module

    # server.py 中尚未实现的 M3-B 配置接口不属于本测试包；
    # 提供仅用于完成模块导入的占位符，测试不会调用这些名称。
    missing_m3b_names = (
        "public_context_model_config",
        "public_rag_client_config",
        "validate_voice_config_selection",
        "voice_config_for_storage",
        "write_runtime_context_model_config",
        "write_runtime_model_config",
    )
    for name in missing_m3b_names:
        if not hasattr(settings_module, name):
            monkeypatch.setattr(settings_module, name, lambda *args, **kwargs: None, raising=False)

    import liverag.rag.service as service_module
    from liverag.runtime.paths import build_runtime_paths, ensure_runtime_dirs

    # 防止 server 导入时用 .env.local 覆盖根 conftest 设置的 pytest 临时目录。
    monkeypatch.setattr(settings_module, "load_environment", lambda: None)
    monkeypatch.setattr(
        service_module,
        "wait_for_rag_ready",
        lambda **_: SimpleNamespace(
            ready=False,
            status="not_running",
            data=None,
            error="RAG Core is not running in API tests",
        ),
    )
    test_settings = settings_module.load_app_settings()
    ensure_runtime_dirs(build_runtime_paths(test_settings.user_data_dir))
    sys.modules.pop("liverag.api.server", None)
    return importlib.import_module("liverag.api.server")


@pytest.fixture
def api_client(api_server: ModuleType) -> Iterator[TestClient]:
    """返回管理 API 的同步测试客户端。"""

    with TestClient(api_server.app) as client:
        yield client
