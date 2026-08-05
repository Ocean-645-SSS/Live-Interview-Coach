"""独立的 LiveKit 面试 Worker 启动入口。

启动方式：python -m liverag.interview_main dev

LiveKit 分配任务时，需要在 job metadata 中提供：
{"session_id": "session_xxx", "attempt_id": "attempt_xxx"}
这个 Worker 会读取对应的面试计划，播放问题，接收最终转写，然后调用
InterviewService 完成回答保存、评价、追问和报告生成。
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from dataclasses import dataclass

from livekit import rtc
from livekit.agents import AgentServer, JobContext, cli, room_io

from liverag.agent.interview_assistant import LiveKitInterviewAgent
from liverag.agent.providers import build_agent_session
from liverag.config.settings import load_app_settings, load_environment
from liverag.interview.controller import InterviewAgentController
from liverag.interview.db import create_session_factory, create_sqlite_engine
from liverag.interview.evaluator import (
    AnswerEvaluator,
    OpenAIAnswerEvaluationProvider,
    OpenAIAnswerEvaluationSettings,
)
from liverag.interview.models import Base as InterviewBase
from liverag.interview.records import AttemptState
from liverag.interview.service import InterviewService
from liverag.interview.sqlalchemy_repository import SQLAlchemyInterviewRepository
from liverag.runtime.paths import build_runtime_paths

logger = logging.getLogger("liverag.interview.worker")
server = AgentServer()  # 语音agent的任务服务器
INTERVIEW_CONTROL_TOPIC = "interview-control"


@dataclass(frozen=True, slots=True)
class InterviewJobMetadata:
    """说明这次 LiveKit 房间连接属于哪个 Session 和 Attempt。"""

    session_id: str
    attempt_id: str

    @classmethod
    def from_json(cls, value: str) -> InterviewJobMetadata:
        """读取 LiveKit job metadata，并检查两个必填 ID 是否存在。
        把字符串 -> json -> InterviewJobMetadata"""

        try:
            payload = json.loads(value or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Interview job metadata 必须是 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Interview job metadata 必须是 JSON 对象")

        session_id = str(payload.get("session_id") or "").strip()
        attempt_id = str(payload.get("attempt_id") or "").strip()
        if not session_id or not attempt_id:
            raise ValueError("Interview job metadata 缺少 session_id 或 attempt_id")
        return cls(session_id=session_id, attempt_id=attempt_id)


def parse_interview_control(packet: rtc.DataPacket) -> str | None:
    """读取前端控制消息，只接受本面试约定的两种按钮动作。"""

    if packet.topic != INTERVIEW_CONTROL_TOPIC:
        return None
    try:
        payload = json.loads(packet.data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    action = str(payload.get("type") or "").strip()
    if action not in {"commit_answer", "unknown_answer"}:
        return None
    return action


def build_interview_service() -> InterviewService:
    """创建 Worker 使用的 SQLite Repository、评价 Provider 和 Service。"""

    #加载AppSettings配置
    settings = load_app_settings()
    #派生运行路径
    paths = build_runtime_paths(settings.user_data_dir)
    #数据库引擎
    engine = create_sqlite_engine(paths.db_file)
    #创建interview相关数据库表
    InterviewBase.metadata.create_all(engine)

    #SQLite Repository
    repository = SQLAlchemyInterviewRepository(create_session_factory(engine))

    #评价Provider
    voice = settings.voice
    if not voice.llm_api_key.strip():
        engine.dispose()
        raise RuntimeError("缺少 VOICE_LLM_API_KEY，Interview Worker 无法评价回答")
    provider = OpenAIAnswerEvaluationProvider(
        OpenAIAnswerEvaluationSettings.from_voice_settings(voice)
    )

    #Interview Service层
    evaluator = AnswerEvaluator(repository, provider)
    return InterviewService(repository, evaluator=evaluator)


@server.rtc_session(agent_name="interview-agent")
async def interview_agent_entrypoint(ctx: JobContext) -> None:
    """处理一场实时面试：连接房间、启动语音 Agent，并记录连接结果。"""

    #获取session+attempt元数据
    metadata = InterviewJobMetadata.from_json(ctx.job.metadata if ctx.job else "")

    #获取Interview Service
    service = build_interview_service()

    #获取repository层
    repository = service.repository

    #从repository获取attempt
    attempt = repository.get_attempt(metadata.attempt_id)
    if attempt.session_id != metadata.session_id:
        raise ValueError("attempt_id 不属于 metadata 中的 session_id")
    if ctx.room.name and attempt.room_name != ctx.room.name:
        raise ValueError("当前 LiveKit 房间与 Attempt 记录不一致")

    #获得controller层
    controller = InterviewAgentController(
        service=service,
        session_id=metadata.session_id,
        attempt_id=metadata.attempt_id,
    )

    settings = load_app_settings()
    #创建实时语音会话链路
    session = build_agent_session(settings)
    #创建顶层面试agent
    agent = LiveKitInterviewAgent(controller)

    async def finish_attempt(reason: str = "") -> None:
        """房间关闭时，把本次连接标记成已断开。"""

        del reason
        #获取当前连接情况
        current = repository.get_attempt(metadata.attempt_id)
        #正常连接中 -> 断开连接
        if current.state is AttemptState.CONNECTED:
            repository.update_attempt_state(
                attempt_id=metadata.attempt_id,
                state=AttemptState.DISCONNECTED,
            )

    #回调
    ctx.add_shutdown_callback(finish_attempt)

    try:
        #连接成功：LiveKit加入房间
        await ctx.connect()
        #更新attempt状态
        repository.update_attempt_state(
            attempt_id=metadata.attempt_id,
            state=AttemptState.CONNECTED,
        )
        #正式启动LiveKit实时语音处理流水线：
        #开启麦克风输入；启动VAD/SST/TTS/LLM；监听用户说话
        control_tasks: set[asyncio.Task[None]] = set()

        async def handle_interview_control(packet: rtc.DataPacket) -> None:
            """把网页上的回答按钮转交给当前 LiveKit 面试 Agent。"""

            action = parse_interview_control(packet)
            if action is None:
                return
            try:
                if action == "commit_answer":
                    await agent.commit_current_answer()
                else:
                    await agent.submit_unknown_answer()
            except Exception:
                logger.exception(
                    "interview control failed",
                    extra={
                        "session_id": metadata.session_id,
                        "attempt_id": metadata.attempt_id,
                        "action": action,
                    },
                )

        def on_data_received(packet: rtc.DataPacket) -> None:
            task = asyncio.create_task(handle_interview_control(packet))
            control_tasks.add(task)
            task.add_done_callback(control_tasks.discard)

        ctx.room.on("data_received", on_data_received)

        async def handle_interview_rpc(data: rtc.RpcInvocationData) -> str:
            """处理带回执的网页按钮请求，让前端能显示识别失败原因。"""

            try:
                payload = json.loads(data.payload)
            except json.JSONDecodeError as exc:
                raise ValueError("面试控制消息不是合法 JSON") from exc
            action = str(payload.get("type") or "") if isinstance(payload, dict) else ""
            logger.info(
                "interview control received",
                extra={"session_id": metadata.session_id, "action": action},
            )
            try:
                if action == "commit_answer":
                    transcript = await agent.commit_current_answer()
                    return json.dumps(
                        {"status": "accepted", "transcript": transcript},
                        ensure_ascii=False,
                    )
                if action == "unknown_answer":
                    await agent.submit_unknown_answer()
                    return json.dumps({"status": "accepted"})
                raise ValueError(f"不支持的面试控制动作：{action}")
            except Exception as exc:
                logger.exception(
                    "interview control failed",
                    extra={"session_id": metadata.session_id, "action": action},
                )
                return json.dumps(
                    {
                        "status": "error",
                        "detail": str(exc) or type(exc).__name__,
                    },
                    ensure_ascii=False,
                )

        ctx.room.local_participant.register_rpc_method(
            "interview.control",
            handle_interview_rpc,
        )

        await session.start(
            agent=agent,    #面试agent
            room=ctx.room,  #绑定当前LiveKit房间
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(sample_rate=16000)
            ),
        )
    except Exception as exc:
        #连接房间失败
        with suppress(Exception):
            #更新attempt为异常
            repository.update_attempt_state(
                attempt_id=metadata.attempt_id,
                state=AttemptState.FAILED,
                error_message=f"{type(exc).__name__}: {exc}",
            )
        #写异常日志
        logger.exception(
            "interview worker failed",
            extra={"session_id": metadata.session_id, "attempt_id": metadata.attempt_id},
        )
        raise


def main() -> None:
    """启动名为 interview-agent 的 LiveKit Worker。"""

    load_environment()
    cli.run_app(server)


if __name__ == "__main__":
    main()


__all__ = [
    "INTERVIEW_CONTROL_TOPIC",
    "InterviewJobMetadata",
    "build_interview_service",
    "interview_agent_entrypoint",
    "main",
    "parse_interview_control",
    "server",
]
