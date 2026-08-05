"""独立 Interview Worker 的任务参数测试。"""

import inspect
from types import SimpleNamespace

import pytest

from liverag.agent.interview_assistant import LiveKitInterviewAgent
from liverag.interview_main import InterviewJobMetadata, parse_interview_control


def test_interview_job_metadata_reads_required_ids() -> None:
    metadata = InterviewJobMetadata.from_json('{"session_id":"session-1","attempt_id":"attempt-1"}')

    assert metadata.session_id == "session-1"
    assert metadata.attempt_id == "attempt-1"


@pytest.mark.parametrize("value", ["", "[]", "{broken", '{"session_id":"session-1"}'])
def test_interview_job_metadata_rejects_invalid_input(value: str) -> None:
    with pytest.raises(ValueError):
        InterviewJobMetadata.from_json(value)


def test_interview_agent_disables_default_llm_asynchronously() -> None:
    assert inspect.iscoroutinefunction(LiveKitInterviewAgent.llm_node)


@pytest.mark.parametrize("action", ["commit_answer", "unknown_answer"])
def test_parse_interview_control_accepts_known_actions(action: str) -> None:
    packet = SimpleNamespace(
        topic="interview-control",
        data=f'{{"type":"{action}"}}'.encode(),
    )

    assert parse_interview_control(packet) == action


@pytest.mark.parametrize(
    ("topic", "data"),
    [
        ("another-topic", b'{"type":"commit_answer"}'),
        ("interview-control", b'{"type":"unsupported"}'),
        ("interview-control", b"not-json"),
    ],
)
def test_parse_interview_control_ignores_unknown_messages(topic: str, data: bytes) -> None:
    packet = SimpleNamespace(topic=topic, data=data)

    assert parse_interview_control(packet) is None
