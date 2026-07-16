import pytest


@pytest.fixture(autouse=True)
def isolate_user_data_dir(tmp_path, monkeypatch):
    """隔离真实用户数据目录和测试数据目录
    tmp_path: pytest提供的临时目录，用于存放测试数据
    monkeypatch: pytest提供的monkeypatch工具，用于修改环境变量"""

    # 测试期间把用户数据目录环境变量指向临时目录，结束之后自动恢复
    monkeypatch.setenv("LIVERAG_USER_DATA_DIR", str(tmp_path))
