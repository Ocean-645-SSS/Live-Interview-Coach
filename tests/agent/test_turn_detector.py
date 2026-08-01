"""Logical user-turn boundary tests."""

import pytest
from livekit.agents import llm

from liverag.agent.turn_detector import SemanticTurnDetector


@pytest.mark.parametrize(
    "text",
    [
        "我想问一下这个项目",
        "这个项目主要涉及",
        "它使用了视觉和",
        "如果需要进一步分析，",
    ],
)
def test_incomplete_fragments_use_long_endpoint_window(text: str) -> None:
    assert SemanticTurnDetector.is_incomplete(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "这个项目涉及哪些技术？",
        "请介绍一下这个项目的技术架构。",
        "你好",
    ],
)
def test_complete_utterances_can_commit_normally(text: str) -> None:
    assert SemanticTurnDetector.is_incomplete(text) is False


@pytest.mark.asyncio
async def test_detector_scores_latest_incomplete_user_message_below_threshold() -> None:
    detector = SemanticTurnDetector()
    context = llm.ChatContext.empty()
    context.add_message(role="user", content="我想问一下这个项目")

    probability = await detector.predict_end_of_turn(context)

    assert probability < await detector.unlikely_threshold(None)
