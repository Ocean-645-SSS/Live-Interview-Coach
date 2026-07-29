"""RagGateway 响应归一化测试。"""

from typing import Any

from liverag.api.rag_gateway import GatewayResponse, RagGateway


def test_map_data_only_changes_success_data() -> None:
    original = GatewayResponse(
        status_code=200,
        body={
            "request_id": "req-1",
            "status": "ok",
            "data": {"value": 1},
            "metrics": {},
            "error": None,
        },
    )

    mapped = RagGateway._map_data(original, lambda data: {"wrapped": data})

    assert mapped.status_code == 200
    assert mapped.body["data"] == {"wrapped": {"value": 1}}
    assert mapped.body["request_id"] == "req-1"
    assert original.body["data"] == {"value": 1}


def test_map_data_does_not_call_mapper_for_error() -> None:
    result = GatewayResponse(
        status_code=503,
        body={
            "request_id": "req-2",
            "status": "error",
            "data": None,
            "metrics": {},
            "error": {"type": "RagUnavailable", "message": "RAG unavailable"},
        },
    )
    called = False

    def mapper(data: Any) -> Any:
        nonlocal called
        called = True
        return data

    mapped = RagGateway._map_data(result, mapper)

    assert mapped is result
    assert called is False


def test_normalize_documents_payload_supplies_stable_defaults() -> None:
    normalized = RagGateway._normalize_documents_payload(
        {
            "documents": [
                {"document_id": "doc-1", "status": "processed"},
                "invalid",
            ]
        }
    )

    assert normalized["total"] == 1
    assert len(normalized["documents"]) == 1
    assert normalized["documents"][0]["document_id"] == "doc-1"
    assert normalized["documents"][0]["status"] == "processed"


def test_normalize_documents_payload_handles_invalid_data() -> None:
    assert RagGateway._normalize_documents_payload(None) == {
        "documents": [],
        "total": 0,
    }
