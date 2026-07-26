"""根据配置创建一条完整的实时语音处理流水线，并包装为LiveKit的AgentSession
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

from livekit.agents import AgentSession
from livekit.plugins import openai, silero, volcengine

from liverag.agent.dashscope_tts import DashScopeRealtimeTTS
from liverag.config.settings import AppSettings


def build_agent_session(settings: AppSettings) -> AgentSession:
    """创建实时语音会话，保留当前线上链路调优参数。
    实现AppSettings
        ↓
    创建 STT + LLM + TTS + VAD
        ↓
    配置打断和轮次检测参数
        ↓
    返回 AgentSession"""

    voice = settings.voice
    if voice.stt_provider != "volcengine_bigmodel":
        raise ValueError(f"当前只支持 volcengine_bigmodel STT，实际配置为：{voice.stt_provider}")

    return AgentSession(
        # STT:用户语音转为文字
        stt=volcengine.BigModelSTT(
            # STT基础配置
            app_id=voice.stt_app_id,
            access_token=voice.stt_access_token,
            model_name=voice.stt_model,
            # 文本后处理
            enable_itn=False,  # 是否进行数字、日期、金额等文本格式规整
            enable_punc=True,  # 是否自动添加标点
            enable_ddc=False,  # 是否开启语义顺滑
            # 语音切句/判停
            vad_segment_duration=1200,  # VAD语音分句的最大静音阈值
            end_window_size=900,  # 连续禁音多久强制判定用户说完
            force_to_speech_time=1000,  # 音频持续多久后，才允许按照静音阈值强制判停
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
        min_endpointing_delay=0.1,  # STT判断说完，等待多久提交给LLM
        max_endpointing_delay=0.5,
        turn_detection="stt",  # 让STT provider的结束事件作为主要回合结束依赖
        vad=silero.VAD.load(),  # 加载VAD模型
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
