"""M3-B SOUL Prompt 管理接口测试。"""

from fastapi.testclient import TestClient


def test_soul_can_be_replaced_and_read_back(api_client: TestClient) -> None:
    updated = api_client.put(
        "/prompt/soul",
        json={"content": "你是一位简洁、可靠的学习助手。"},
    )

    assert updated.status_code == 200
    assert updated.json() == {"status": "ok"}

    fetched = api_client.get("/prompt/soul")

    assert fetched.status_code == 200
    assert fetched.json() == {
        "content": "你是一位简洁、可靠的学习助手。\n"
    }
