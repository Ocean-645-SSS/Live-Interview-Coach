"""独立 Interview Worker 的任务参数测试。"""

import inspect

import pytest

from liverag.agent.interview_assistant import LiveKitInterviewAgent
from liverag.interview_main import InterviewJobMetadata


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
