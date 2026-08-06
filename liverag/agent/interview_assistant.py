"""LiveKit 实时语音事件与面试业务控制器之间的适配层
接收LiveKit事件，播放TTS"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from enum import Enum

from livekit.agents import Agent, ModelSettings, llm

from liverag.interview.application.controller import (
    InterviewAgentController,
    InterviewSpeech,
    InterviewSpeechKind,
)
from liverag.interview.application.orchestrator import AnswerReceivedResult
from liverag.interview.schemas import InterviewState

logger = logging.getLogger("liverag.interview.agent")


class InterviewAudioNotReadyError(RuntimeError):
    def __init__(self, missing_conditions: tuple[str, ...]) -> None:
        self.missing_conditions = missing_conditions
        super().__init__("Interview audio is not ready: " + ", ".join(missing_conditions))


@dataclass(slots=True)
class InterviewAudioReadiness:
    """Event-driven readiness barrier for the Interview worker only."""

    room_connected: bool = False
    participant_joined: bool = False
    microphone_published: bool = False
    microphone_subscribed: bool = False
    microphone_unmuted: bool = False
    microphone_live: bool = False
    agent_session_started: bool = False
    stt_input_ready: bool = False
    _changed: asyncio.Event = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._changed = asyncio.Event()

    def update(self, **values: bool) -> None:
        for name, value in values.items():
            if not hasattr(self, name):
                raise ValueError(f"Unknown Interview readiness condition: {name}")
            setattr(self, name, value)
        self._changed.set()

    def missing_conditions(self) -> tuple[str, ...]:
        return tuple(
            item.name
            for item in fields(self)
            if not item.name.startswith("_") and not getattr(self, item.name)
        )

    async def wait(self, *, timeout_seconds: float = 10.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while missing := self.missing_conditions():
            self._changed.clear()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise InterviewAudioNotReadyError(missing)
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise InterviewAudioNotReadyError(self.missing_conditions()) from exc


@dataclass(slots=True)
class ActiveAnswerBuffer:
    session_id: str
    question_id: str
    attempt_id: str
    opened_at: datetime
    deadline_at: datetime
    answer_window_id: str = ""
    final_segments: list[str] = field(default_factory=list)
    current_interim: str = ""
    is_open: bool = False
    submitted: bool = False
    first_transcript_received: bool = False

    def append_final(self, transcript: str) -> bool:
        clean = " ".join(transcript.split())
        if not clean or clean in self.final_segments:
            self.current_interim = ""
            return False
        self.final_segments.append(clean)
        self.current_interim = ""
        return True

    def merged_text(self) -> str:
        parts = [*self.final_segments]
        interim = " ".join(self.current_interim.split())
        if interim and interim not in parts:
            parts.append(interim)
        return " ".join(parts).strip()


class AnswerSubmitReason(str, Enum):
    MANUAL = "MANUAL"
    UNKNOWN = "UNKNOWN"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True, slots=True)
class SubmitAnswerResult:
    reason: AnswerSubmitReason
    success: bool
    error_code: str | None
    old_state: InterviewState
    new_state: InterviewState
    transcript: str = ""


class LiveKitInterviewAgent(Agent):
    """把 LiveKit 的播放和转写回调交给 InterviewAgentController。"""

    def __init__(
        self,
        controller: InterviewAgentController,
        *,
        session_id: str,
        attempt_id: str,
        room_name: str,
        audio_readiness: InterviewAudioReadiness,
    ) -> None:
        # 面试问题由已经冻结的 InterviewPlan 决定，不让通用 LLM 自由聊天。
        super().__init__(instructions="按照面试计划逐题进行模拟面试。")
        self._controller = controller
        self._session_id = session_id
        self._attempt_id = attempt_id
        self._room_name = room_name
        self._audio_readiness = audio_readiness
        self._turn_lock = asyncio.Lock()  # 确保同一时间只处理一个面试流程事件
        self._answer_buffer: ActiveAnswerBuffer | None = None
        self._answer_timeout_task: asyncio.Task[None] | None = None
        self._processed_submit_event_ids: set[str] = set()
        self._continuation_tasks: set[asyncio.Task[None]] = set()

    def _log_context(self, **extra: object) -> dict[str, object]:
        """为语音层日志补齐一次 Interview 连接的稳定业务标识。"""

        try:
            session = self._controller.get_session()
            interview_state = session.state.value
            question_id = getattr(session, "current_question_id", None)
            session_version = getattr(session, "version", None)
            interview_id = getattr(session, "interview_id", None)
            question_cursor = getattr(session, "current_question_index", None)
        except Exception:
            interview_state = "UNKNOWN"
            question_id = None
            session_version = None
            interview_id = None
            question_cursor = None
        answer_buffer = self._answer_buffer
        return {
            "interview_id": interview_id,
            "session_id": self._session_id,
            "attempt_id": self._attempt_id,
            "room_name": self._room_name,
            "interview_state": interview_state,
            "state": interview_state,
            "question_id": question_id,
            "question_cursor": question_cursor,
            "session_version": session_version,
            "version": session_version,
            "answer_window_open": bool(answer_buffer and answer_buffer.is_open),
            "buffer_question_id": answer_buffer.question_id if answer_buffer else None,
            "answer_window_id": (
                answer_buffer.answer_window_id if answer_buffer is not None else None
            ),
            "answer_deadline_at": (
                answer_buffer.deadline_at.isoformat() if answer_buffer is not None else None
            ),
            **extra,
        }

    async def on_enter(self) -> None:
        """加入房间后从数据库记录的位置开始或恢复面试。"""

        # LiveKit invokes on_enter only after AgentSession has installed its input/STT nodes.
        self._audio_readiness.update(agent_session_started=True, stt_input_ready=True)
        # 加锁，防止进入房间的同时受到用户回答
        async with self._turn_lock:
            await self._wait_for_audio_ready()
            # 获得旧状态
            state_before = self._controller.get_session().state
            # 决定进入房间后说的第一句话：开场白/当前题目/追问/结束语
            first_speech = self._controller.start()

            # 如果是第一次进入
            if first_speech.kind is InterviewSpeechKind.INTRODUCTION:
                await self._play(first_speech)
                # 返回第一题
                question = self._controller.introduction_spoken()
                # 播放第一题
                await self._deliver_prompt_and_open_answer_window(question)
                # 进入listening状态，等待回答
            # 如果是面试准备结束
            elif first_speech.kind is InterviewSpeechKind.CLOSING:
                await self._play(first_speech)
                # 生成面试报告，面试结束
                self._controller.complete()
            # 判断用于恢复题目还是追问
            elif state_before is not InterviewState.LISTENING:
                await self._deliver_prompt_and_open_answer_window(first_speech)
            else:
                # A reconnected LISTENING session remains authoritative; replay only.
                await self._play(first_speech)

    async def on_user_turn_completed(
        self,
        turn_ctx: llm.ChatContext,  # 完整对话上下文
        new_message: llm.ChatMessage,  # 用户最新消息
    ) -> None:
        """用户说完一轮后，处理最终文字并播放追问、下一题或结束语。"""

        del turn_ctx
        # 获取最终转写
        transcript = (new_message.text_content or "").strip()
        if not transcript:
            logger.warning(
                "interview.transcript.ignored",
                extra=self._log_context(
                    ignored_reason="empty_final_transcript",
                    reason="empty_final_transcript",
                    is_final=True,
                    transcript="",
                ),
            )
            return

        self.record_transcript_segment(transcript, is_final=True)

    def record_transcript_segment(self, transcript: str, *, is_final: bool) -> None:
        """Collect STT segments; segment final never closes the business answer."""

        clean = " ".join(transcript.split())
        buffer = self._answer_buffer
        session = self._controller.get_session()
        state = session.state
        if (
            not clean
            or buffer is None
            or not buffer.is_open
            or state is not InterviewState.LISTENING
            or buffer.question_id != session.current_question_id
        ):
            if not clean:
                ignored_reason = "empty_transcript"
            elif state is not InterviewState.LISTENING:
                ignored_reason = "interview_state_not_listening"
            elif buffer is None or not buffer.is_open:
                ignored_reason = "answer_window_not_open"
            else:
                ignored_reason = "answer_buffer_question_mismatch"
            logger.info(
                "interview.transcript.ignored",
                extra=self._log_context(
                    reason=ignored_reason,
                    ignored_reason=ignored_reason,
                    is_final=is_final,
                    transcript=clean,
                ),
            )
            return
        if not buffer.first_transcript_received:
            buffer.first_transcript_received = True
            logger.info("STT_FIRST_TRANSCRIPT_RECEIVED", extra=self._log_context())
        if is_final:
            appended = buffer.append_final(clean)
            logger.info(
                "interview.stt.segment_final.buffered",
                extra=self._log_context(transcript=clean, duplicate=not appended),
            )
        else:
            buffer.current_interim = clean
            logger.info("interview.stt.interim.buffered", extra=self._log_context(transcript=clean))

    async def commit_current_answer(
        self,
        *,
        event_id: str | None = None,
        question_id: str | None = None,
        attempt_id: str | None = None,
    ) -> str:
        """Compatibility wrapper for the MANUAL business submission."""

        result = await self.submit_active_answer(
            session_id=self._session_id,
            question_id=question_id or (self._controller.get_session().current_question_id or ""),
            reason=AnswerSubmitReason.MANUAL,
            event_id=event_id or f"legacy-manual:{datetime.now(timezone.utc).isoformat()}",
            attempt_id=attempt_id,
        )
        return result.transcript

    async def submit_unknown_answer(
        self,
        *,
        event_id: str | None = None,
        question_id: str | None = None,
        attempt_id: str | None = None,
    ) -> str:
        """Compatibility wrapper for UNKNOWN; it never mutates the STT pipeline."""

        result = await self.submit_active_answer(
            session_id=self._session_id,
            question_id=question_id or (self._controller.get_session().current_question_id or ""),
            reason=AnswerSubmitReason.UNKNOWN,
            event_id=event_id or f"legacy-unknown:{datetime.now(timezone.utc).isoformat()}",
            attempt_id=attempt_id,
        )
        return result.transcript

    async def _process_answer(
        self,
        transcript: str,
        *,
        answer_disposition: str = "ANSWERED",
    ) -> None:
        """串行完成保存、评价、播放下一句话和更新 Session 状态。"""

        async with self._turn_lock:
            state = self._controller.get_session().state
            if state is not InterviewState.LISTENING:
                logger.info(
                    "interview.transcript.ignored",
                    extra=self._log_context(
                        ignored_reason="interview_state_not_listening",
                        reason="interview_state_not_listening",
                        expected_state=InterviewState.LISTENING.value,
                        is_final=True,
                        transcript=transcript,
                        transcript_len=len(transcript),
                    ),
                )
                return

            logger.info(
                "interview.transcript.accepted",
                extra=self._log_context(
                    answer_disposition=answer_disposition,
                    transcript_len=len(transcript),
                ),
            )
            result = await self._controller.receive_final_answer(
                transcript,
                answer_disposition=answer_disposition,
            )
            speech = result.next_speech
            if speech.kind is InterviewSpeechKind.CLOSING:
                await self._play(speech)
                self._controller.complete()
            else:
                await self._deliver_prompt_and_open_answer_window(speech)

    async def submit_active_answer(
        self,
        *,
        session_id: str,
        question_id: str,
        reason: AnswerSubmitReason,
        event_id: str,
        attempt_id: str | None = None,
    ) -> SubmitAnswerResult:
        """Authoritative, idempotent close-and-persist entry for all three reasons."""

        async with self._turn_lock:
            buffer = self._answer_buffer
            session = self._controller.get_session()
            old_state = session.state
            if event_id and event_id in self._processed_submit_event_ids:
                return SubmitAnswerResult(reason, True, None, old_state, old_state)
            if session_id != self._session_id:
                raise ValueError("answer submit session_id does not match current session")
            if buffer is None or not buffer.is_open or buffer.submitted:
                result = SubmitAnswerResult(
                    reason,
                    False,
                    "ANSWER_WINDOW_NOT_OPEN",
                    old_state,
                    old_state,
                )
                logger.info(
                    "ANSWER_SUBMIT_RESULT",
                    extra=self._log_context(
                        reason=reason.value,
                        success=False,
                        error_code=result.error_code,
                        old_state=old_state.value,
                        new_state=old_state.value,
                    ),
                )
                return result
            if session.state is not InterviewState.LISTENING:
                raise ValueError("server state is not LISTENING")
            if question_id != buffer.question_id or question_id != session.current_question_id:
                raise ValueError("answer submit question_id does not match current question")
            if attempt_id and attempt_id != buffer.attempt_id:
                raise ValueError("answer submit attempt_id does not match current attempt")
            if event_id:
                self._processed_submit_event_ids.add(event_id)
            # 提前标记防并发：防止 DB 写入期间重复提交通过检查
            buffer.submitted = True
            buffer.is_open = False
            timeout_task = self._answer_timeout_task
            if timeout_task and timeout_task is not asyncio.current_task():
                timeout_task.cancel()
                try:
                    await timeout_task
                except asyncio.CancelledError:
                    logger.info(
                        "interview.answer_timeout_task.cancelled",
                        extra=self._log_context(
                            answer_window_id=buffer.answer_window_id,
                            question_id=buffer.question_id,
                        ),
                    )
            transcript = buffer.merged_text()
            answer_text = "UNKNOWN" if reason is AnswerSubmitReason.UNKNOWN else transcript or "（未作答）"
            disposition = (
                "UNKNOWN"
                if reason is AnswerSubmitReason.UNKNOWN
                else "ANSWERED" if transcript else "NO_ANSWER"
            )
            logger.info(
                "ANSWER_WINDOW_CLOSED",
                extra=self._log_context(reason=reason.value, transcript_len=len(transcript)),
            )
            # 使用 generate_id 确保 event_id 不超 interview_events.id 的 64 字符限制
            from liverag.interview.records import generate_id as _gen_event_id

            submit_event_id = _gen_event_id("event")
            try:
                received = self._controller.submit_answer(
                    answer_text,
                    started_at=buffer.opened_at.isoformat(),
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    answer_disposition=disposition,
                    event_id=submit_event_id,
                )
            except Exception:
                # 数据库写入失败 → 清理残留 buffer 状态
                # 标记为未提交，但将 buffer 置空以阻止死循环重试
                buffer.submitted = False
                buffer.is_open = False
                self._answer_buffer = None
                logger.exception(
                    "interview.answer_persist.failed",
                    extra=self._log_context(
                        reason=reason.value,
                        answer_window_id=buffer.answer_window_id,
                    ),
                )
                raise
            self._answer_buffer = None
            new_state = received.transition.session.state
            logger.info("ANSWER_PERSISTED", extra=self._log_context(reason=reason.value))
            logger.info("STATE_EVALUATING", extra=self._log_context(reason=reason.value))
            logger.info(
                "ANSWER_SUBMIT_RESULT",
                extra=self._log_context(
                    reason=reason.value,
                    success=True,
                    error_code=None,
                    old_state=old_state.value,
                    new_state=new_state.value,
                ),
            )
            task = asyncio.create_task(self._continue_after_submission(received))
            self._continuation_tasks.add(task)
            task.add_done_callback(self._continuation_tasks.discard)
            return SubmitAnswerResult(reason, True, None, old_state, new_state, transcript)

    async def _continue_after_submission(self, received: AnswerReceivedResult) -> None:
        try:
            result = await self._controller.evaluate_submitted_answer(received)
            speech = result.next_speech
            if speech.kind is InterviewSpeechKind.CLOSING:
                await self._play(speech)
                self._controller.complete()
            else:
                await self._deliver_prompt_and_open_answer_window(speech)
        except Exception:
            logger.exception("interview.answer_evaluation.failed", extra=self._log_context())

    async def _submit_answer_after_timeout(self, buffer: ActiveAnswerBuffer) -> None:
        delay = max(0.0, (buffer.deadline_at - datetime.now(timezone.utc)).total_seconds())
        try:
            await asyncio.sleep(delay)
            current = self._answer_buffer
            if current is not buffer:
                return
            assert current is not None
            if current.answer_window_id != buffer.answer_window_id:
                return
            from liverag.interview.records import generate_id as _gen_event_id

            # 使用短唯一 ID 避免超过 interview_events.id 的 64 字符限制
            # answer_window_id 已在日志和 buffer 中记录，无需放入 event_id
            timeout_event_id = _gen_event_id("event")
            logger.info(
                "interview.answer_timeout.triggered",
                extra=self._log_context(
                    answer_window_id=buffer.answer_window_id,
                    timeout_event_id=timeout_event_id,
                ),
            )
            await self.submit_active_answer(
                session_id=buffer.session_id,
                question_id=buffer.question_id,
                reason=AnswerSubmitReason.TIMEOUT,
                event_id=timeout_event_id,
                attempt_id=buffer.attempt_id,
            )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception(
                "interview.answer_timeout.crashed",
                extra=self._log_context(
                    answer_window_id=buffer.answer_window_id,
                ),
            )

    async def _wait_for_audio_ready(self) -> None:
        try:
            await self._audio_readiness.wait(timeout_seconds=10.0)
        except InterviewAudioNotReadyError as exc:
            logger.error(
                "interview.audio_readiness.timeout",
                extra=self._log_context(missing_conditions=list(exc.missing_conditions)),
            )
            raise

    async def _deliver_prompt_and_open_answer_window(
        self,
        speech: InterviewSpeech,
    ) -> None:
        """Use one readiness/state path for first, resumed, follow-up, and later prompts."""

        await self._wait_for_audio_ready()
        await self._play(speech)
        logger.info("QUESTION_TTS_FINISHED", extra=self._log_context())
        await self._mark_prompt_spoken_and_verify(speech.kind)

    async def _mark_prompt_spoken_and_verify(self, kind: InterviewSpeechKind) -> None:
        opened_at = datetime.now(timezone.utc)
        deadline_at = opened_at + timedelta(seconds=self._controller.answer_timeout_seconds())
        asking_session = self._controller.get_session()
        question_id = asking_session.current_question_id
        if question_id is None:
            raise RuntimeError("Question prompt is missing current_question_id")
        answer_window_id = f"{self._attempt_id}:{question_id}:{opened_at.isoformat()}"
        buffer = ActiveAnswerBuffer(
            session_id=self._session_id,
            question_id=question_id,
            attempt_id=self._attempt_id,
            opened_at=opened_at,
            deadline_at=deadline_at,
            answer_window_id=answer_window_id,
        )
        self._answer_buffer = buffer
        logger.info("ANSWER_BUFFER_CREATED", extra=self._log_context())
        session = self._controller.prompt_spoken(
            kind,
            answer_started_at=opened_at.isoformat(),
            answer_deadline_at=deadline_at.isoformat(),
        )
        if session.state is not InterviewState.LISTENING:
            raise RuntimeError(
                "Prompt playback completed but persisted Interview state is "
                f"{session.state.value}, expected LISTENING"
            )
        if session.current_question_id is None:
            raise RuntimeError("LISTENING Session is missing current_question_id")
        if session.current_question_id != buffer.question_id:
            self._answer_buffer = None
            raise RuntimeError("LISTENING question_id does not match answer buffer")
        buffer.is_open = True
        logger.info("SERVER_STATE_LISTENING", extra=self._log_context())
        logger.info(
            "ANSWER_WINDOW_OPENED",
            extra=self._log_context(
                question_id=session.current_question_id,
                session_version=session.version,
            ),
        )
        self._answer_timeout_task = asyncio.create_task(
            self._submit_answer_after_timeout(buffer)
        )
        if session.current_question_index == 0:
            logger.info(
                "FIRST_QUESTION_READY",
                extra=self._log_context(
                    audio_ready=not self._audio_readiness.missing_conditions(),
                    server_state=session.state.value,
                    answer_window_open=buffer.is_open,
                    buffer_created=True,
                ),
            )

    @staticmethod
    def _answer_window_id(buffer: ActiveAnswerBuffer) -> str:
        return buffer.answer_window_id

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: ModelSettings,
    ) -> None:
        """关闭默认自由回答：不让LiveKit自带的LLM发挥，要说的话已经由面试计划和评价结果确定"""

        # 不需要上下文、工具、模型配置
        del chat_ctx, tools, model_settings
        return None

    async def _play(self, speech: InterviewSpeech) -> None:
        """通过 TTS 播放一段文字，并等到声音真正播放完毕。"""

        handle = self.session.say(
            speech.text,
            allow_interruptions=False,  # 不允许被打断
            add_to_chat_ctx=True,  # 加入对话内容
        )
        await handle.wait_for_playout()
        logger.info(
            "interview.tts.playout_completed",
            extra=self._log_context(
                speech_kind=speech.kind.value,
                interrupted=handle.interrupted,
            ),
        )


__all__ = [
    "ActiveAnswerBuffer",
    "AnswerSubmitReason",
    "InterviewAudioNotReadyError",
    "InterviewAudioReadiness",
    "LiveKitInterviewAgent",
    "SubmitAnswerResult",
]
