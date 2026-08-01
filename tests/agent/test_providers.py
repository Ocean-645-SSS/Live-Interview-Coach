"""Voice pipeline construction tests."""

from types import SimpleNamespace
from typing import Any

import liverag.agent.providers as providers


def test_build_agent_session_uses_semantic_turn_detection(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeTurnDetector:
        pass

    class FakeAgentSession:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(providers, "SemanticTurnDetector", FakeTurnDetector)
    monkeypatch.setattr(providers, "AgentSession", FakeAgentSession)
    monkeypatch.setattr(
        providers.volcengine,
        "BigModelSTT",
        lambda **kwargs: SimpleNamespace(kind="stt", options=kwargs),
    )
    monkeypatch.setattr(
        providers.openai,
        "LLM",
        lambda **kwargs: SimpleNamespace(kind="llm", options=kwargs),
    )
    monkeypatch.setattr(
        providers.silero.VAD,
        "load",
        lambda: SimpleNamespace(kind="vad"),
    )
    monkeypatch.setattr(
        providers,
        "_build_tts",
        lambda settings: SimpleNamespace(kind="tts"),
    )

    voice = SimpleNamespace(
        stt_provider="volcengine_bigmodel",
        stt_app_id="app-id",
        stt_access_token="token",
        stt_model="bigmodel",
        llm_model="test-llm",
        llm_api_key="llm-key",
        llm_base_url="https://llm.invalid/v1",
    )

    result = providers.build_agent_session(SimpleNamespace(voice=voice))

    assert isinstance(result, FakeAgentSession)
    assert isinstance(captured["turn_detection"], FakeTurnDetector)
    assert captured["preemptive_generation"] is False
    assert captured["min_endpointing_delay"] == 0.8
    assert captured["max_endpointing_delay"] == 2.5
    assert captured["stt"].options["end_window_size"] == 1200
