"""M3-A 管理 API 测试夹具。"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import liverag.config.settings as settings_module


@pytest.fixture
def api_server(
    monkeypatch: pytest.MonkeyPatch,
    isolate_user_data_dir: None,
    tmp_path: Path,
) -> ModuleType:
    """在 pytest 临时用户目录中重新加载管理 API 模块。"""

    import liverag.rag.service as service_module
    from liverag.runtime.paths import build_runtime_paths, ensure_runtime_dirs

    # 防止 server 导入时用 .env.local 覆盖根 conftest 设置的 pytest 临时目录。
    monkeypatch.setenv("LIVERAG_USER_DATA_DIR", str(tmp_path))
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
