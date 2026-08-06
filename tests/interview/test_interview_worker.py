"""独立 Interview Worker 的任务参数测试。"""

import asyncio
import inspect
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from liverag.agent.interview_assistant import (
    ActiveAnswerBuffer,
    AnswerSubmitReason,
    InterviewAudioNotReadyError,
    InterviewAudioReadiness,
    LiveKitInterviewAgent,
)
from liverag.interview.schemas import InterviewConfig, InterviewState
from liverag.interview.application.controller import InterviewSpeech, InterviewSpeechKind
from liverag.interview_main import (
    InterviewJobMetadata,
    InterviewWorkerDiagnostics,
    parse_interview_control,
)


def test_interview_job_metadata_reads_required_ids() -> None:
    metadata = InterviewJobMetadata.from_json(
        '{"session_id":"session-1","attempt_id":"attempt-1","participant_identity":"user-1"}'
    )

    assert metadata.session_id == "session-1"
    assert metadata.attempt_id == "attempt-1"
    assert metadata.participant_identity == "user-1"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "[]",
        "{broken",
        '{"session_id":"session-1"}',
        '{"session_id":"session-1","attempt_id":"attempt-1"}',
    ],
)
def test_interview_job_metadata_rejects_invalid_input(value: str) -> None:
    with pytest.raises(ValueError):
        InterviewJobMetadata.from_json(value)


def test_interview_agent_disables_default_llm_asynchronously() -> None:
    assert inspect.iscoroutinefunction(LiveKitInterviewAgent.llm_node)


def test_new_interview_answer_timeout_defaults_to_90_seconds() -> None:
    assert InterviewConfig().answer_timeout_seconds == 90


def test_answer_buffer_merges_multiple_final_segments_without_duplicates() -> None:
    opened_at = datetime.now(timezone.utc)
    buffer = ActiveAnswerBuffer(
        session_id="session-1",
        question_id="question-1",
        attempt_id="attempt-1",
        opened_at=opened_at,
        deadline_at=opened_at + timedelta(seconds=90),
    )

    assert buffer.append_final("第一句话。") is True
    assert buffer.append_final("第一句话。") is False
    assert buffer.append_final("第二句话。") is True
    buffer.current_interim = "还没有 final 的最后一句"

    assert buffer.merged_text() == "第一句话。 第二句话。 还没有 final 的最后一句"


class _FakeInterviewController:
    def __init__(self, state: InterviewState) -> None:
        self.state = state
        self.received: list[str] = []
        self.submitted: list[tuple[str, dict[str, object]]] = []

    def get_session(self) -> SimpleNamespace:
        return SimpleNamespace(
            state=self.state,
            current_question_id="question-1",
            current_question_index=0,
            version=1,
            interview_id="interview-1",
        )

    async def receive_final_answer(self, transcript: str, **_kwargs: object) -> None:
        self.received.append(transcript)

    def submit_answer(self, transcript: str, **kwargs: object) -> SimpleNamespace:
        self.submitted.append((transcript, kwargs))
        self.state = InterviewState.EVALUATING
        return SimpleNamespace(
            answer=SimpleNamespace(id="answer-1"),
            transition=SimpleNamespace(session=self.get_session()),
        )

    async def evaluate_submitted_answer(self, _received: object) -> None:
        raise RuntimeError("evaluation intentionally omitted in worker unit test")


class _FakeEmitter:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def on(self, event: str):
        def register(callback: object) -> object:
            self.handlers[event] = callback
            return callback

        return register

    def emit(self, event: str, value: object) -> None:
        callback = self.handlers[event]
        assert callable(callback)
        callback(value)


class _FakeRoom(_FakeEmitter):
    name = "room-1"

    def __init__(self) -> None:
        super().__init__()
        self.remote_participants: dict[str, object] = {}

    @staticmethod
    def isconnected() -> bool:
        return False


class _FakeSession(_FakeEmitter):
    agent_state = "listening"


def _build_agent(controller: _FakeInterviewController) -> LiveKitInterviewAgent:
    readiness = InterviewAudioReadiness()
    readiness.update(**dict.fromkeys(readiness.missing_conditions(), True))
    return LiveKitInterviewAgent(
        controller,  # type: ignore[arg-type]
        session_id="session-1",
        attempt_id="attempt-1",
        room_name="room-1",
        audio_readiness=readiness,
    )


def test_interview_diagnostics_registers_room_and_session_pipeline_events() -> None:
    room = _FakeRoom()
    session = _FakeSession()
    diagnostics = InterviewWorkerDiagnostics(
        room=room,  # type: ignore[arg-type]
        session=session,
        controller=_FakeInterviewController(InterviewState.LISTENING),  # type: ignore[arg-type]
        metadata=InterviewJobMetadata("session-1", "attempt-1", "user-1"),
        readiness=InterviewAudioReadiness(),
    )

    diagnostics.register()

    assert {
        "participant_connected",
        "participant_disconnected",
        "connection_state_changed",
        "track_published",
        "track_subscribed",
        "track_unsubscribed",
        "track_subscription_failed",
    } <= room.handlers.keys()
    assert {
        "user_state_changed",
        "user_input_transcribed",
        "agent_state_changed",
        "speech_created",
        "error",
    } <= session.handlers.keys()


@pytest.mark.asyncio
async def test_interview_audio_readiness_waits_for_every_condition() -> None:
    readiness = InterviewAudioReadiness()
    waiter = asyncio.create_task(readiness.wait(timeout_seconds=0.5))
    await asyncio.sleep(0)
    assert not waiter.done()

    readiness.update(**dict.fromkeys(readiness.missing_conditions(), True))

    await waiter


@pytest.mark.asyncio
async def test_interview_audio_readiness_is_attempt_scoped_after_first_success() -> None:
    readiness = InterviewAudioReadiness()
    readiness.update(**dict.fromkeys(readiness.missing_conditions(), True))

    await asyncio.wait_for(readiness.wait(), timeout=0.05)
    # Opening the next question reuses the established attempt readiness.  It must
    # not clear the event and wait for another one-shot track_subscribed callback.
    await asyncio.wait_for(readiness.wait(), timeout=0.05)


@pytest.mark.asyncio
async def test_interview_audio_readiness_reports_exact_missing_conditions() -> None:
    readiness = InterviewAudioReadiness()
    readiness.update(
        room_connected=True,
        participant_joined=True,
        microphone_published=True,
    )

    with pytest.raises(InterviewAudioNotReadyError) as captured:
        await readiness.wait(timeout_seconds=0.01)

    assert captured.value.missing_conditions == (
        "microphone_subscribed",
        "microphone_unmuted",
        "microphone_live",
        "agent_session_started",
        "stt_input_ready",
    )


@pytest.mark.asyncio
async def test_interview_agent_logs_empty_final_transcript(
    caplog: pytest.LogCaptureFixture,
) -> None:
    controller = _FakeInterviewController(InterviewState.LISTENING)
    agent = _build_agent(controller)

    with caplog.at_level(logging.INFO, logger="liverag.interview.agent"):
        await agent.on_user_turn_completed(
            None,  # type: ignore[arg-type]
            SimpleNamespace(text_content="  "),  # type: ignore[arg-type]
        )

    assert controller.received == []
    assert any(
        record.message == "interview.transcript.ignored"
        and record.reason == "empty_final_transcript"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_interview_agent_logs_final_transcript_ignored_outside_listening(
    caplog: pytest.LogCaptureFixture,
) -> None:
    controller = _FakeInterviewController(InterviewState.ASKING)
    agent = _build_agent(controller)

    with caplog.at_level(logging.INFO, logger="liverag.interview.agent"):
        await agent.on_user_turn_completed(
            None,  # type: ignore[arg-type]
            SimpleNamespace(text_content="已经识别出的回答"),  # type: ignore[arg-type]
        )

    assert controller.received == []
    assert any(
        record.message == "interview.transcript.ignored"
        and record.reason == "interview_state_not_listening"
        and record.interview_state == "ASKING"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_unknown_answer_only_closes_the_existing_answer_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _FakeInterviewController(InterviewState.LISTENING)
    agent = _build_agent(controller)
    submit_arguments: dict[str, object] = {}

    async def fake_submit(_self: object, **kwargs: object) -> SimpleNamespace:
        submit_arguments.update(kwargs)
        return SimpleNamespace(transcript="")

    monkeypatch.setattr(LiveKitInterviewAgent, "submit_active_answer", fake_submit)

    await agent.submit_unknown_answer(
        event_id="event-1",
        question_id="question-1",
        attempt_id="attempt-1",
    )

    assert submit_arguments == {
        "session_id": "session-1",
        "reason": AnswerSubmitReason.UNKNOWN,
        "event_id": "event-1",
        "question_id": "question-1",
        "attempt_id": "attempt-1",
    }


@pytest.mark.asyncio
async def test_manual_submit_persists_buffer_and_enters_evaluating_once() -> None:
    controller = _FakeInterviewController(InterviewState.LISTENING)
    agent = _build_agent(controller)
    opened_at = datetime.now(timezone.utc)
    agent._answer_buffer = ActiveAnswerBuffer(
        session_id="session-1",
        question_id="question-1",
        attempt_id="attempt-1",
        opened_at=opened_at,
        deadline_at=opened_at + timedelta(seconds=90),
        answer_window_id="window-1",
        final_segments=["first answer"],
        is_open=True,
    )

    first = await agent.submit_active_answer(
        session_id="session-1",
        question_id="question-1",
        reason=AnswerSubmitReason.MANUAL,
        event_id="event-1",
        attempt_id="attempt-1",
    )
    duplicate = await agent.submit_active_answer(
        session_id="session-1",
        question_id="question-1",
        reason=AnswerSubmitReason.MANUAL,
        event_id="event-1",
        attempt_id="attempt-1",
    )

    assert first.new_state is InterviewState.EVALUATING
    assert duplicate.success is True
    assert [item[0] for item in controller.submitted] == ["first answer"]
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_unknown_submit_does_not_touch_stt_and_persists_unknown() -> None:
    controller = _FakeInterviewController(InterviewState.LISTENING)
    agent = _build_agent(controller)
    opened_at = datetime.now(timezone.utc)
    agent._answer_buffer = ActiveAnswerBuffer(
        session_id="session-1",
        question_id="question-1",
        attempt_id="attempt-1",
        opened_at=opened_at,
        deadline_at=opened_at + timedelta(seconds=90),
        answer_window_id="window-1",
        is_open=True,
    )

    result = await agent.submit_active_answer(
        session_id="session-1",
        question_id="question-1",
        reason=AnswerSubmitReason.UNKNOWN,
        event_id="unknown-1",
        attempt_id="attempt-1",
    )

    assert result.new_state is InterviewState.EVALUATING
    assert controller.submitted[0][0] == "UNKNOWN"
    assert controller.submitted[0][1]["answer_disposition"] == "UNKNOWN"
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_manual_and_timeout_compete_but_persist_only_once() -> None:
    controller = _FakeInterviewController(InterviewState.LISTENING)
    agent = _build_agent(controller)
    opened_at = datetime.now(timezone.utc)
    buffer = ActiveAnswerBuffer(
        session_id="session-1",
        question_id="question-1",
        attempt_id="attempt-1",
        opened_at=opened_at,
        deadline_at=opened_at,
        answer_window_id="window-1",
        final_segments=["race answer"],
        is_open=True,
    )
    agent._answer_buffer = buffer

    await asyncio.gather(
        agent.submit_active_answer(
            session_id="session-1",
            question_id="question-1",
            reason=AnswerSubmitReason.MANUAL,
            event_id="manual-1",
            attempt_id="attempt-1",
        ),
        agent._submit_answer_after_timeout(buffer),
    )

    assert len(controller.submitted) == 1
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_stale_timeout_cannot_submit_the_next_question() -> None:
    controller = _FakeInterviewController(InterviewState.LISTENING)
    agent = _build_agent(controller)
    opened_at = datetime.now(timezone.utc)
    stale = ActiveAnswerBuffer(
        session_id="session-1",
        question_id="question-old",
        attempt_id="attempt-1",
        opened_at=opened_at,
        deadline_at=opened_at,
        answer_window_id="window-old",
        is_open=True,
    )
    agent._answer_buffer = ActiveAnswerBuffer(
        session_id="session-1",
        question_id="question-1",
        attempt_id="attempt-1",
        opened_at=opened_at,
        deadline_at=opened_at + timedelta(seconds=90),
        answer_window_id="window-new",
        is_open=True,
    )

    await agent._submit_answer_after_timeout(stale)

    assert controller.submitted == []


@pytest.mark.asyncio
async def test_first_question_creates_buffer_before_publishing_listening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _FakeInterviewController(InterviewState.ASKING)
    agent = _build_agent(controller)
    observed_buffer: ActiveAnswerBuffer | None = None

    def prompt_spoken(_kind: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal observed_buffer
        observed_buffer = agent._answer_buffer
        controller.state = InterviewState.LISTENING
        return controller.get_session()

    controller.prompt_spoken = prompt_spoken  # type: ignore[attr-defined]
    controller.answer_timeout_seconds = lambda: 90  # type: ignore[attr-defined]

    async def no_play(_speech: object) -> None:
        return None

    monkeypatch.setattr(agent, "_play", no_play)
    await agent._deliver_prompt_and_open_answer_window(
        InterviewSpeech(InterviewSpeechKind.QUESTION, "question")
    )

    assert observed_buffer is not None
    assert observed_buffer.question_id == "question-1"
    assert agent._answer_buffer is observed_buffer
    assert observed_buffer.is_open is True
    agent.record_transcript_segment("first question transcript", is_final=True)
    assert observed_buffer.merged_text() == "first question transcript"
    assert agent._answer_timeout_task is not None
    agent._answer_timeout_task.cancel()
    await asyncio.gather(agent._answer_timeout_task, return_exceptions=True)


def test_first_later_and_follow_up_prompts_share_one_delivery_method() -> None:
    source = inspect.getsource(LiveKitInterviewAgent)

    assert source.count("await self._deliver_prompt_and_open_answer_window(") >= 4
    assert "await self._mark_prompt_spoken_and_verify(speech.kind)" in source


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
