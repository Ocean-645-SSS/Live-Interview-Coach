from typing import Any

import pytest

from liverag.api.interview_profile_source import RagGatewayProfileSource
from liverag.api.rag_gateway import GatewayResponse


class _Gateway:
    def __init__(self) -> None:
        self.timeout_ms: int | None = None

    async def post_json(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> GatewayResponse:
        assert path == "/v1/knowledge-bases/default/query/context"
        assert payload["context_max_chars"] == 12000
        self.timeout_ms = timeout_ms
        return GatewayResponse(
            status_code=200,
            body={
                "status": "ok",
                "data": {
                    "context": "候选人使用 Python 开发 RAG 项目。",
                    "references": [{"file_path": "resume.pdf"}],
                },
            },
        )


@pytest.mark.asyncio
async def test_profile_source_uses_long_rag_query_timeout() -> None:
    gateway = _Gateway()

    result = await RagGatewayProfileSource(gateway).retrieve(
        kb_id="default",
        query="提取候选人画像",
    )

    assert gateway.timeout_ms == 120_000
    assert result.context == "候选人使用 Python 开发 RAG 项目。"
    assert result.evidence_refs == ("resume.pdf",)
