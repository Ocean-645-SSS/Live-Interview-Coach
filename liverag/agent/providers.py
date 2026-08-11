"""根据配置创建一条完整的实时语音处理流水线，并包装为LiveKit的AgentSession
AppSettings.voice
├── Volcengine BigModel STT
├── OpenAI-compatible LLM
├── DashScopeRealtimeTTS
├── Silero VAD
└── 回合结束与打断参数
        ↓
LiveKit AgentSession

负责模型和音频的装配，流程如下：
用户语音
  ↓
STT：火山引擎语音识别
  ↓
文字
  ↓
LLM：OpenAI-compatible 对话模型
  ↓
回答文字
  ↓
TTS：DashScope
  ↓
回答语音
"""

from pathlib import Path

from livekit.agents import AgentSession
from livekit.plugins import openai, silero

from liverag.agent.dashscope_tts import DashScopeRealtimeTTS
from liverag.agent.hot_words import load_hot_words
from liverag.agent.turn_detector import SemanticTurnDetector
from liverag.agent.volcengine_stt import AuditedBigModelSTT
from liverag.config.settings import AppSettings


def build_agent_session(
    settings: AppSettings,
    *,
    hot_words_json: str | None = None,
) -> AgentSession:
    """创建实时语音会话，保留当前线上链路调优参数。
    实现AppSettings
        ↓
    创建 STT（动态热词） + LLM + TTS + VAD(检测用户什么时候开始、停止讲话)
        ↓
    配置打断和轮次检测参数
        ↓
    返回 AgentSession"""

    voice = settings.voice
    #目前只支持火山引擎STT
    if voice.stt_provider != "volcengine_bigmodel":
        raise ValueError(f"当前只支持 volcengine_bigmodel STT，实际配置为：{voice.stt_provider}")

    # 未提供 session 级热词时，保留原有的全局文件加载行为。
    if hot_words_json is None:
        hot_words_path = Path(voice.stt_hot_words_path) if voice.stt_hot_words_path else None
        hot_words_json = load_hot_words(hot_words_path)

    return AgentSession(
        # STT:用户语音转为文字
        stt=AuditedBigModelSTT(
            # STT基础配置
            app_id=voice.stt_app_id,
            access_token=voice.stt_access_token,
            model_name=voice.stt_model,
            # 热词
            hot_words_json=hot_words_json,
            # 文本后处理
            enable_itn=False,  # 是否进行数字、日期、金额等文本格式规整
            enable_punc=True,  # 是否自动添加标点
            enable_ddc=False,  # 是否开启语义顺滑
            # 语音切句/判停
            vad_segment_duration=3000,  # VAD语音分句的最大静音阈值
            end_window_size=3000,  # 给短暂停顿留出合并窗口，避免半句话提前提交
            force_to_speech_time=3000,  # 音频持续多久后，才允许按照静音阈值强制判停
            # 流式结果
            interim_results=True,  # 是否持续返回最终尚未确认的中间识别结果
        ),
        # LLM
        llm=openai.LLM(
            model=voice.llm_model,
            api_key=voice.llm_api_key,
            base_url=voice.llm_base_url,
        ),
        # TTS：文字转语音
        tts=_build_tts(settings),
        preemptive_generation=False,  # 本轮完全确认结束前，提前启动LLM生成
        min_interruption_duration=0.3,  # 判断用户发声多久秒才打断助手说话
        min_endpointing_delay=0.8,  # 短暂停顿后继续等待，避免把换气当成新问题
        max_endpointing_delay=2.5,
        # STT final 只是片段边界；语义模型确认完整后才提交逻辑轮次并触发 RAG。
        turn_detection=SemanticTurnDetector(),
        vad=silero.VAD.load(),  # 加载VAD模型:检测用户什么时候开始、停止讲话
    )


def _build_tts(settings: AppSettings) -> DashScopeRealtimeTTS:
    """根据DashScope配置选择 TTS provider"""

    voice = settings.voice

    # 如果TTS服务商不在规定范围内
    if voice.tts_provider not in {"dashscope", "dashscope_realtime", "qwen_realtime"}:
        raise ValueError(f"当前只支持DashScope TTS，实际配置为：{voice.tts_provider}")

    return DashScopeRealtimeTTS(
        model=voice.tts_model,
        voice=voice.tts_voice,
        api_key=voice.tts_api_key,
        base_url=voice.tts_base_url,
        sample_rate=24000,  # 音频采样率
        speech_rate=1.05,  # 语音播放速度
    )
