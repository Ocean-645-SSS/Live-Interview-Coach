"""独立的 LiveKit 面试 Worker 启动入口。

启动方式：python -m liverag.interview_main dev

LiveKit 分配任务时，需要在 job metadata 中提供：
{"session_id": "session_xxx", "attempt_id": "attempt_xxx", "participant_identity": "user_xxx"}
这个 Worker 会读取对应的面试计划，播放问题，接收最终转写，然后调用
InterviewService 完成回答保存、评价、追问和报告生成。
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livekit import rtc
from livekit.agents import AgentServer, JobContext, cli, room_io

from liverag.agent.interview_assistant import (
    InterviewAudioNotReadyError,
    InterviewAudioReadiness,
    LiveKitInterviewAgent,
)
from liverag.agent.providers import build_agent_session
from liverag.config.settings import load_app_settings, load_environment
from liverag.interview.application.controller import InterviewAgentController
from liverag.interview.application.evaluator import (
    AnswerEvaluator,
    OpenAIAnswerEvaluationProvider,
    OpenAIAnswerEvaluationSettings,
)
from liverag.interview.application.service import InterviewService
from liverag.interview.persistence.db import create_database_engine, create_session_factory
from liverag.interview.persistence.sqlalchemy_repository import SQLAlchemyInterviewRepository
from liverag.interview.records import AttemptState
from liverag.interview.schemas import InterviewState
from liverag.interview.skill_progress.service import SkillProgressService
from liverag.interview.skill_progress.taxonomy import SkillTaxonomy

logger = logging.getLogger("liverag.interview.worker")
server = AgentServer()  # 语音agent的任务服务器
INTERVIEW_CONTROL_TOPIC = "interview-control"


@dataclass(frozen=True, slots=True)
class InterviewJobMetadata:
    """说明这次 LiveKit 房间连接属于哪个 Session 和 Attempt。"""

    session_id: str
    attempt_id: str
    participant_identity: str

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
        participant_identity = str(payload.get("participant_identity") or "").strip()
        if not session_id or not attempt_id or not participant_identity:
            raise ValueError(
                "Interview job metadata 缺少 session_id、attempt_id 或 participant_identity"
            )
        return cls(
            session_id=session_id,
            attempt_id=attempt_id,
            participant_identity=participant_identity,
        )


class InterviewWorkerDiagnostics:
    """记录一次 Interview 房间的音频链路，并向开发前端发布离散状态。"""

    _ATTRIBUTE_PREFIX = "liverag.interview."

    def __init__(
        self,
        *,
        room: rtc.Room,
        session: Any,
        controller: InterviewAgentController,
        metadata: InterviewJobMetadata,
        readiness: InterviewAudioReadiness,
        agent: LiveKitInterviewAgent | None = None,
    ) -> None:
        self._room = room
        self._session = session
        self._controller = controller
        self._metadata = metadata
        self._readiness = readiness
        self._agent = agent
        self._tasks: set[asyncio.Task[None]] = set()
        self._last_interim = ""
        self._status = {
            "audio_subscription": "inactive",
            "vad": "idle",
            "stt": "idle",
            "readiness": "waiting",
            "readiness_missing": ",".join(readiness.missing_conditions()),
        }

    def _refresh_readiness_status(self) -> None:
        missing = self._readiness.missing_conditions()
        self._set_status(
            readiness="ready" if not missing else "waiting",
            readiness_missing=",".join(missing),
        )

    def mark_room_connected(self) -> None:
        self._readiness.update(room_connected=True)
        for participant in self._room.remote_participants.values():
            if participant.identity != self._metadata.participant_identity:
                continue
            self._readiness.update(participant_joined=True)
            for publication in participant.track_publications.values():
                if not self._is_expected_microphone(publication, participant):
                    continue
                self._readiness.update(
                    microphone_published=True,
                    microphone_subscribed=publication.track is not None,
                    microphone_unmuted=not publication.muted,
                    microphone_live=publication.track is not None,
                )
        self._refresh_readiness_status()

    def mark_agent_session_started(self) -> None:
        # AgentSession.start has created AudioRecognition/STT and attached RoomIO input here.
        self._readiness.update(agent_session_started=True, stt_input_ready=True)
        self._set_status(stt="ready")
        self._refresh_readiness_status()

    def _context(self, **extra: object) -> dict[str, object]:
        try:
            interview_state = self._controller.get_session().state.value
        except Exception:
            interview_state = "UNKNOWN"
        return {
            "session_id": self._metadata.session_id,
            "attempt_id": self._metadata.attempt_id,
            "room_name": self._room.name,
            "participant_identity": self._metadata.participant_identity,
            "interview_state": interview_state,
            "agent_state": getattr(self._session, "agent_state", "UNKNOWN"),
            **extra,
        }

    @staticmethod
    def _track_context(publication: Any) -> dict[str, object]:
        track_kind = (
            publication.kind if hasattr(publication, "kind") else rtc.TrackKind.KIND_UNKNOWN
        )
        track_source = (
            publication.source if hasattr(publication, "source") else rtc.TrackSource.SOURCE_UNKNOWN
        )
        return {
            "track_sid": getattr(publication, "sid", ""),
            "track_kind": rtc.TrackKind.Name(track_kind),
            "track_source": rtc.TrackSource.Name(track_source),
            "track_muted": bool(getattr(publication, "muted", False)),
        }

    def _is_expected_microphone(self, publication: Any, participant: Any) -> bool:
        return (
            getattr(participant, "identity", None) == self._metadata.participant_identity
            and getattr(publication, "kind", None) == rtc.TrackKind.KIND_AUDIO
            and getattr(publication, "source", None) == rtc.TrackSource.SOURCE_MICROPHONE
        )

    def _set_status(self, **values: str) -> None:
        self._status.update(values)
        if not self._room.isconnected():
            return

        async def update() -> None:
            try:
                await self._room.local_participant.set_attributes(
                    {f"{self._ATTRIBUTE_PREFIX}{key}": value for key, value in values.items()}
                )
            except Exception as exc:
                logger.warning(
                    "interview.diagnostics.publish_failed",
                    extra=self._context(error=f"{type(exc).__name__}: {exc}"),
                )

        task = asyncio.create_task(update())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def publish_initial_status(self) -> None:
        try:
            await self._room.local_participant.set_attributes(
                {
                    f"{self._ATTRIBUTE_PREFIX}session_id": self._metadata.session_id,
                    f"{self._ATTRIBUTE_PREFIX}attempt_id": self._metadata.attempt_id,
                    f"{self._ATTRIBUTE_PREFIX}room_name": self._room.name,
                    **{
                        f"{self._ATTRIBUTE_PREFIX}{key}": value
                        for key, value in self._status.items()
                    },
                }
            )
        except Exception as exc:
            logger.warning(
                "interview.diagnostics.publish_failed",
                extra=self._context(error=f"{type(exc).__name__}: {exc}"),
            )

    async def aclose(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    def monitor_readiness(self) -> None:
        async def monitor() -> None:
            try:
                await self._readiness.wait(timeout_seconds=10.0)
            except InterviewAudioNotReadyError as exc:
                self._set_status(
                    readiness="timeout",
                    readiness_missing=",".join(exc.missing_conditions),
                )
                logger.error(
                    "interview.audio_readiness.timeout",
                    extra=self._context(missing_conditions=list(exc.missing_conditions)),
                )
            else:
                self._set_status(readiness="ready", readiness_missing="")
                logger.info("interview.audio_readiness.ready", extra=self._context())

        task = asyncio.create_task(monitor())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def register(self) -> None:
        """必须在连接房间和启动 AgentSession 前注册，避免漏掉首个音轨事件。"""

        @self._room.on("connection_state_changed")
        def on_connection_state_changed(state: rtc.ConnectionState.ValueType) -> None:
            connected = state == rtc.ConnectionState.CONN_CONNECTED
            logger.info(
                "interview.room.connected" if connected else "interview.room.disconnected",
                extra=self._context(connection_state=rtc.ConnectionState.Name(state)),
            )
            self._readiness.update(room_connected=connected)
            if not connected:
                self._readiness.update(
                    participant_joined=False,
                    microphone_published=False,
                    microphone_subscribed=False,
                    microphone_unmuted=False,
                    microphone_live=False,
                )
            self._refresh_readiness_status()

        @self._room.on("participant_connected")
        def on_participant_connected(participant: rtc.RemoteParticipant) -> None:
            logger.info(
                "interview.participant.joined",
                extra=self._context(
                    remote_identity=participant.identity,
                    expected=participant.identity == self._metadata.participant_identity,
                ),
            )
            if participant.identity == self._metadata.participant_identity:
                self._readiness.update(participant_joined=True)
                self._refresh_readiness_status()

        @self._room.on("participant_disconnected")
        def on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
            logger.info(
                "interview.participant.left",
                extra=self._context(remote_identity=participant.identity),
            )
            if participant.identity == self._metadata.participant_identity:
                self._readiness.update(
                    participant_joined=False,
                    microphone_published=False,
                    microphone_subscribed=False,
                    microphone_unmuted=False,
                    microphone_live=False,
                )
                self._set_status(audio_subscription="inactive", vad="idle")
                self._refresh_readiness_status()

        @self._room.on("track_published")
        def on_track_published(publication: Any, participant: rtc.RemoteParticipant) -> None:
            logger.info(
                "interview.audio_track.published",
                extra=self._context(
                    remote_identity=participant.identity,
                    **self._track_context(publication),
                ),
            )
            if self._is_expected_microphone(publication, participant):
                self._readiness.update(
                    microphone_published=True,
                    microphone_unmuted=not publication.muted,
                )
                self._refresh_readiness_status()

        @self._room.on("track_unpublished")
        def on_track_unpublished(publication: Any, participant: rtc.RemoteParticipant) -> None:
            logger.info(
                "interview.audio_track.unpublished",
                extra=self._context(
                    remote_identity=participant.identity,
                    **self._track_context(publication),
                ),
            )
            if self._is_expected_microphone(publication, participant):
                logger.info(
                    "interview.audio_track.stopped",
                    extra=self._context(
                        remote_identity=participant.identity,
                        **self._track_context(publication),
                    ),
                )
                self._readiness.update(
                    microphone_published=False,
                    microphone_subscribed=False,
                    microphone_unmuted=False,
                    microphone_live=False,
                )
                self._set_status(audio_subscription="inactive")
                self._refresh_readiness_status()

        @self._room.on("track_subscribed")
        def on_track_subscribed(
            _track: Any,
            publication: Any,
            participant: rtc.RemoteParticipant,
        ) -> None:
            logger.info(
                "interview.audio_track.subscribed",
                extra=self._context(
                    remote_identity=participant.identity,
                    **self._track_context(publication),
                ),
            )
            if self._is_expected_microphone(publication, participant):
                self._readiness.update(
                    microphone_published=True,
                    microphone_subscribed=True,
                    microphone_unmuted=not publication.muted,
                    microphone_live=True,
                )
                self._set_status(audio_subscription="active")
                self._refresh_readiness_status()

        @self._room.on("track_unsubscribed")
        def on_track_unsubscribed(
            _track: Any,
            publication: Any,
            participant: rtc.RemoteParticipant,
        ) -> None:
            logger.warning(
                "interview.audio_track.unsubscribed",
                extra=self._context(
                    remote_identity=participant.identity,
                    **self._track_context(publication),
                ),
            )
            if self._is_expected_microphone(publication, participant):
                self._readiness.update(microphone_subscribed=False, microphone_live=False)
                self._set_status(audio_subscription="inactive")
                self._refresh_readiness_status()

        @self._room.on("track_subscription_failed")
        def on_track_subscription_failed(
            participant: rtc.RemoteParticipant,
            track_sid: str,
            error: str,
        ) -> None:
            logger.error(
                "interview.audio_track.subscription_failed",
                extra=self._context(
                    remote_identity=participant.identity,
                    track_sid=track_sid,
                    error=error,
                ),
            )
            if participant.identity == self._metadata.participant_identity:
                self._readiness.update(microphone_subscribed=False, microphone_live=False)
                self._set_status(audio_subscription="inactive", stt="error")
                self._refresh_readiness_status()

        @self._room.on("track_muted")
        def on_track_muted(participant: Any, publication: Any) -> None:
            if self._is_expected_microphone(publication, participant):
                self._readiness.update(microphone_unmuted=False)
                logger.info(
                    "interview.audio_track.muted",
                    extra=self._context(**self._track_context(publication)),
                )
                self._refresh_readiness_status()

        @self._room.on("track_unmuted")
        def on_track_unmuted(participant: Any, publication: Any) -> None:
            if self._is_expected_microphone(publication, participant):
                self._readiness.update(microphone_unmuted=True)
                logger.info(
                    "interview.audio_track.unmuted",
                    extra=self._context(**self._track_context(publication)),
                )
                self._refresh_readiness_status()

        @self._session.on("user_state_changed")
        def on_user_state_changed(event: Any) -> None:
            if event.new_state == "speaking":
                audio_discarded = getattr(self._session, "agent_state", None) == "speaking"
                logger.info(
                    "interview.vad.user_started_speaking",
                    extra=self._context(audio_discarded_by_uninterruptible_tts=audio_discarded),
                )
                self._set_status(
                    vad="speaking",
                    stt="blocked_by_tts" if audio_discarded else "receiving",
                )
            elif event.old_state == "speaking":
                logger.info("interview.vad.user_stopped_speaking", extra=self._context())
                self._set_status(vad="idle")

        @self._session.on("user_input_transcribed")
        def on_user_input_transcribed(event: Any) -> None:
            transcript = str(event.transcript or "").strip()
            if self._agent is not None:
                self._agent.record_transcript_segment(transcript, is_final=bool(event.is_final))
            if event.is_final:
                logger.info(
                    "interview.stt.final",
                    extra=self._context(
                        transcript=transcript,
                        transcript_len=len(transcript),
                    ),
                )
                self._last_interim = ""
                self._set_status(stt="final_received")
            elif transcript and transcript != self._last_interim:
                self._last_interim = transcript
                logger.info(
                    "interview.stt.interim",
                    extra=self._context(
                        transcript=transcript,
                        transcript_len=len(transcript),
                    ),
                )
                self._set_status(stt="receiving")
            elif not transcript:
                logger.warning(
                    "interview.transcript.ignored",
                    extra=self._context(reason="empty_stt_event", is_final=event.is_final),
                )

        @self._session.on("agent_state_changed")
        def on_agent_state_changed(event: Any) -> None:
            logger.info(
                "interview.agent.state_changed",
                extra=self._context(old_state=event.old_state, new_state=event.new_state),
            )

        @self._session.on("speech_created")
        def on_speech_created(event: Any) -> None:
            handle = event.speech_handle
            logger.info(
                "interview.tts.speech_created",
                extra=self._context(
                    source=event.source,
                    allow_interruptions=handle.allow_interruptions,
                ),
            )

            def on_speech_done(done_handle: Any) -> None:
                logger.info(
                    "interview.tts.speech_finished",
                    extra=self._context(interrupted=done_handle.interrupted),
                )

            handle.add_done_callback(on_speech_done)

        @self._session.on("agent_false_interruption")
        def on_agent_false_interruption(event: Any) -> None:
            logger.info(
                "interview.agent.false_interruption",
                extra=self._context(resumed=event.resumed),
            )

        @self._session.on("error")
        def on_session_error(event: Any) -> None:
            logger.error(
                "interview.audio_pipeline.error",
                extra=self._context(
                    source=type(event.source).__name__,
                    error=f"{type(event.error).__name__}: {event.error}",
                    recoverable=bool(getattr(event.error, "recoverable", False)),
                ),
            )
            self._set_status(stt="error")


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
    #数据库引擎
    engine = create_database_engine(
        settings.interview_database.url
    )

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
    skill_progress_service = SkillProgressService(
        repository,
        SkillTaxonomy.from_file(
            Path(__file__).resolve().parent
            / "interview"
            / "skill_progress"
            / "data"
            / "skill_taxonomy.v1.json"
        ),
    )
    return InterviewService(
        repository,
        evaluator=evaluator,
        skill_progress_service=skill_progress_service,
    )


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
    logger.info(
        "interview.stt.instance_created",
        extra={
            "session_id": metadata.session_id,
            "attempt_id": metadata.attempt_id,
            "room_name": attempt.room_name,
            "stt_type": type(session.stt).__name__,
        },
    )
    #创建顶层面试agent
    readiness = InterviewAudioReadiness()
    agent = LiveKitInterviewAgent(
        controller,
        session_id=metadata.session_id,
        attempt_id=metadata.attempt_id,
        room_name=attempt.room_name,
        audio_readiness=readiness,
    )
    diagnostics = InterviewWorkerDiagnostics(
        room=ctx.room,
        session=session,
        controller=controller,
        metadata=metadata,
        readiness=readiness,
        agent=agent,
    )
    diagnostics.register()

    async def finish_attempt(reason: str = "") -> None:
        """房间关闭时，把本次连接标记成已断开。"""

        del reason
        await diagnostics.aclose()
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
        diagnostics.mark_room_connected()
        logger.info(
            "interview.worker.joined_room",
            extra={
                "job_id": ctx.job.id if ctx.job else "",
                "agent_name": ctx.job.agent_name if ctx.job else "interview-agent",
                "session_id": metadata.session_id,
                "attempt_id": metadata.attempt_id,
                "room_name": ctx.room.name,
                "participant_identity": metadata.participant_identity,
            },
        )
        await diagnostics.publish_initial_status()
        diagnostics.monitor_readiness()
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
            """接收网页按钮请求；耗时的评价/TTS 流程在回执后继续执行。"""

            try:
                payload = json.loads(data.payload)
            except json.JSONDecodeError as exc:
                raise ValueError("面试控制消息不是合法 JSON") from exc
            action = str(payload.get("type") or "") if isinstance(payload, dict) else ""
            reason = str(payload.get("reason") or "").upper() if isinstance(payload, dict) else ""
            if not reason:
                reason = "UNKNOWN" if action == "unknown_answer" else "MANUAL"
            logger.info(
                "interview control received",
                extra={"session_id": metadata.session_id, "action": action},
            )
            if action not in {"commit_answer", "answer_submit_requested", "unknown_answer"}:
                raise ValueError(f"不支持的面试控制动作：{action}")
            if reason not in {"MANUAL", "UNKNOWN"}:
                raise ValueError(f"不支持的回答提交原因：{reason}")
            requested_session_id = str(payload.get("session_id") or "")
            requested_attempt_id = str(payload.get("attempt_id") or "")
            requested_question_id = str(payload.get("question_id") or "")
            current_session = controller.get_session()
            if requested_session_id != metadata.session_id:
                raise ValueError("面试控制消息的 session_id 与当前连接不匹配")
            if requested_attempt_id != metadata.attempt_id:
                raise ValueError("面试控制消息的 attempt_id 与当前连接不匹配")
            if requested_question_id != current_session.current_question_id:
                raise ValueError("面试控制消息的 question_id 与当前题目不匹配")
            if current_session.state is not InterviewState.LISTENING:
                raise ValueError("当前回答窗口尚未打开或已经关闭")
            if reason == "MANUAL":
                logger.info(
                    "MANUAL_SUBMIT_REQUEST_RECEIVED",
                    extra={
                        "session_id": metadata.session_id,
                        "question_id": requested_question_id,
                        "state": current_session.state.value,
                        "version": current_session.version,
                    },
                )
            from liverag.agent.interview_assistant import AnswerSubmitReason

            result = await agent.submit_active_answer(
                session_id=requested_session_id,
                question_id=requested_question_id,
                reason=AnswerSubmitReason(reason),
                event_id=str(payload.get("client_event_id") or ""),
                attempt_id=requested_attempt_id,
            )
            return json.dumps(
                {
                    "status": "accepted" if result.success else "rejected",
                    "processing": result.new_state is InterviewState.EVALUATING,
                    "state": result.new_state.value,
                    "error_code": result.error_code,
                    "client_event_id": payload.get("client_event_id"),
                },
                ensure_ascii=False,
            )

        ctx.room.local_participant.register_rpc_method(
            "interview.control",
            handle_interview_rpc,
        )

        logger.info(
            "interview.agent_session.starting",
            extra={
                "session_id": metadata.session_id,
                "attempt_id": metadata.attempt_id,
                "room_name": ctx.room.name,
                "participant_identity": metadata.participant_identity,
            },
        )
        await session.start(
            agent=agent,    #面试agent
            room=ctx.room,  #绑定当前LiveKit房间
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(sample_rate=16000),
                participant_identity=metadata.participant_identity,
            ),
        )
        diagnostics.mark_agent_session_started()
        logger.info(
            "interview.stt.input_ready",
            extra={
                "session_id": metadata.session_id,
                "attempt_id": metadata.attempt_id,
                "room_name": ctx.room.name,
                "participant_identity": metadata.participant_identity,
            },
        )
        logger.info(
            "interview.agent_session.started",
            extra={
                "session_id": metadata.session_id,
                "attempt_id": metadata.attempt_id,
                "room_name": ctx.room.name,
                "participant_identity": metadata.participant_identity,
                "interview_state": controller.get_session().state.value,
            },
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
    "InterviewWorkerDiagnostics",
    "build_interview_service",
    "interview_agent_entrypoint",
    "main",
    "parse_interview_control",
    "server",
]
