"""M2-D 实时语音 provider 装配测试。"""

from types import SimpleNamespace
from typing import Any

import pytest

import liverag.agent.providers as providers
from liverag.config.settings import VoiceSettings


class FakeComponent:
    """记录 provider 构造参数。"""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class FakeAgentSession(FakeComponent):
    """记录 AgentSession 的完整装配参数。"""


class FakeVAD:
    """提供 Silero VAD.load() 所需的最小协议。"""

    loaded = object()

    @classmethod
    def load(cls) -> object:
        return cls.loaded


def app_settings(voice: VoiceSettings) -> SimpleNamespace:
    """构造 provider 工厂所需的最小 AppSettings 协议。"""

    return SimpleNamespace(voice=voice)


def install_fake_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """替换所有在线 provider，保证测试不访问网络。"""

    monkeypatch.setattr(providers, "AgentSession", FakeAgentSession)
    monkeypatch.setattr(providers.volcengine, "BigModelSTT", FakeComponent)
    monkeypatch.setattr(providers.openai, "LLM", FakeComponent)
    monkeypatch.setattr(providers.silero, "VAD", FakeVAD)
    monkeypatch.setattr(providers, "DashScopeRealtimeTTS", FakeComponent)


def test_build_agent_session_wires_expected_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """STT、LLM、TTS、VAD 及回合参数被完整传入 AgentSession。"""

    install_fake_providers(monkeypatch)
    voice = VoiceSettings(
        stt_app_id="stt-app",
        stt_access_token="stt-token",
        llm_model="qwen-flash",
        llm_base_url="https://llm.example/v1",
        llm_api_key="llm-secret",
        tts_api_key="tts-secret",
    )

    session = providers.build_agent_session(app_settings(voice))

    assert isinstance(session, FakeAgentSession)
    assert session.kwargs["stt"].kwargs == {
        "app_id": "stt-app",
        "access_token": "stt-token",
        "model_name": "bigmodel",
        "enable_itn": False,
        "enable_punc": True,
        "enable_ddc": False,
        "vad_segment_duration": 1200,
        "end_window_size": 900,
        "force_to_speech_time": 1000,
        "interim_results": True,
    }
    assert session.kwargs["llm"].kwargs == {
        "model": "qwen-flash",
        "api_key": "llm-secret",
        "base_url": "https://llm.example/v1",
    }
    assert session.kwargs["tts"].kwargs == {
        "model": "qwen3-tts-flash-realtime",
        "voice": "Cherry",
        "api_key": "tts-secret",
        "base_url": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        "sample_rate": 24000,
        "speech_rate": 1.05,
    }
    assert session.kwargs["vad"] is FakeVAD.loaded
    assert session.kwargs["preemptive_generation"] is False
    assert session.kwargs["min_interruption_duration"] == 0.3
    assert session.kwargs["min_endpointing_delay"] == 0.1
    assert session.kwargs["max_endpointing_delay"] == 0.5
    assert session.kwargs["turn_detection"] == "stt"


@pytest.mark.parametrize("provider", ["dashscope", "dashscope_realtime", "qwen_realtime"])
def test_build_tts_accepts_dashscope_aliases(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    """三个既有 DashScope provider 名称都走实时 TTS。"""

    monkeypatch.setattr(providers, "DashScopeRealtimeTTS", FakeComponent)
    result = providers._build_tts(
        app_settings(VoiceSettings(tts_provider=provider, tts_api_key="secret"))
    )

    assert isinstance(result, FakeComponent)
    assert result.kwargs["api_key"] == "secret"


def test_build_tts_rejects_non_dashscope_provider() -> None:
    """MiniMax 等未适配 provider 必须立即失败，不能静默回退。"""

    with pytest.raises(ValueError, match="只支持DashScope TTS"):
        providers._build_tts(app_settings(VoiceSettings(tts_provider="minimax")))


def test_build_agent_session_rejects_non_volcengine_stt() -> None:
    """当前只允许装配已经适配的火山引擎 STT。"""

    voice = VoiceSettings(stt_provider="other-stt")

    with pytest.raises(ValueError, match="只支持 volcengine_bigmodel STT"):
        providers.build_agent_session(app_settings(voice))
