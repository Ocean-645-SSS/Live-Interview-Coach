# Interview Audio P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent every Interview prompt from opening before the exact participant microphone, RoomIO input, AgentSession, and STT pipeline are ready, while making every stage observable without changing the normal VoiceAssistant.

**Architecture:** Keep the shared provider construction unchanged, but add an Interview-only event-driven readiness barrier owned by the worker. `Agent.on_enter()` and every subsequent question/follow-up await the same barrier before playback; the barrier is satisfied only after room connection, expected participant presence, a live subscribed microphone publication, completed `AgentSession.start()`, and initialized STT input. Persisted Interview state remains the sole business-state authority exposed to the frontend.

**Tech Stack:** Python 3.10, LiveKit Agents 1.4.5, React 19, Next.js 15, livekit-client 2.15, pytest, Ruff, TypeScript, ESLint

## Global Constraints

- Do not change normal LiveRAG VoiceAssistant behavior.
- Do not add frameworks or dependencies.
- Do not modify question bank, evaluator, follow-up, report, or normal VoiceAssistant behavior.
- Do not adjust STT/VAD/endpointing thresholds without runtime evidence.
- Do not bulk-delete files or directories.

---

### Task 1: Pin the Interview participant and instrument the worker pipeline

**Files:**
- Modify: `LiveRAG-Fronted/agent-starter-react/app/api/connection-details/route.ts`
- Modify: `liverag/interview_main.py`
- Test: `tests/interview/test_interview_worker.py`

**Interfaces:**
- Consumes: the existing explicit `RoomAgentDispatch` metadata and `AgentSession` event API.
- Produces: `InterviewJobMetadata.participant_identity`, exact RoomIO participant selection, structured room/session logs, and `liverag.interview.{audio_subscription,vad,stt}` agent attributes.

- [ ] **Step 1: Extend the failing metadata test**

```python
metadata = InterviewJobMetadata.from_json(
    '{"session_id":"session-1","attempt_id":"attempt-1",'
    '"participant_identity":"user-1"}'
)
assert metadata.participant_identity == "user-1"
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/interview/test_interview_worker.py -q`

Expected: FAIL because `participant_identity` is not defined.

- [ ] **Step 3: Carry the generated participant identity in dispatch metadata**

Generate `participantName` before `RoomConfiguration`, include it in `dispatchMetadata`, require it in `InterviewJobMetadata`, and pass it to `room_io.RoomOptions(participant_identity=...)`.

- [ ] **Step 4: Register low-noise observability before `ctx.connect()`**

Log room join, participant lifecycle, audio publication/subscription lifecycle, subscription failures, AgentSession start, VAD start/stop, deduplicated interim/final STT, speech interruption outcome, and pipeline errors. Publish only state labels—not audio samples—to agent attributes.

- [ ] **Step 5: Run the focused worker tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/interview/test_interview_worker.py -q`

Expected: PASS.

### Task 2: Make transcript acceptance and rejection explicit

**Files:**
- Modify: `liverag/agent/interview_assistant.py`
- Test: `tests/interview/test_interview_worker.py`

**Interfaces:**
- Consumes: `InterviewAgentController.get_session()` and LiveKit final user-turn callbacks.
- Produces: explicit `interview.transcript.ignored`/`accepted` logs with session, attempt, room, and Interview state.

- [ ] **Step 1: Add tests for empty and invalid-state transcripts**

Use a fake controller to assert empty text and a final transcript received outside `LISTENING` never call `receive_final_answer`, while emitting an ignore reason.

- [ ] **Step 2: Run the tests and verify the missing behavior**

Run: `.venv\\Scripts\\python.exe -m pytest tests/interview/test_interview_worker.py -q`

Expected: FAIL because empty transcripts return silently and state rejection is not explicit.

- [ ] **Step 3: Implement minimal state-gated logging**

Attach immutable session/attempt/room context to `LiveKitInterviewAgent`, log every ignored transcript reason, re-check state inside the turn lock, and log TTS completion/interruption. Do not change controller business logic.

- [ ] **Step 4: Run focused tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/interview/test_interview_worker.py -q`

Expected: PASS.

### Task 3: Add an Interview-only readiness barrier and unified prompt delivery

**Files:**
- Modify: `liverag/interview_main.py`
- Modify: `liverag/agent/interview_assistant.py`
- Test: `tests/interview/test_interview_worker.py`

**Interfaces:**
- Produces: `InterviewAudioReadiness`, `wait_for_interview_audio_ready(timeout_seconds=10)`, and `deliver_prompt_and_open_answer_window()`.
- Consumes: room lifecycle/track events plus the point immediately after `AgentSession.start()` returns.

- [ ] **Step 1: Add readiness tests**

Cover success only when all conditions are true, timeout reporting of the exact missing conditions, mute/unsubscribe clearing readiness, and proof that `on_enter()` does not call the controller or TTS before readiness.

- [ ] **Step 2: Implement event-driven readiness**

Update readiness from existing LiveKit callbacks and an explicit `mark_agent_session_started(stt_input_ready=True)` call after `session.start()`. Await a condition/event with `asyncio.timeout`; do not poll, sleep, retry, or restart the session.

- [ ] **Step 3: Unify prompt delivery**

Route first question, resumed question, follow-up, and later question through one helper that waits for readiness, moves/retains authoritative `ASKING`, plays uninterruptible speech, persists `QUESTION_ASKED`/`FOLLOW_UP_ASKED`, reloads the session, and verifies `LISTENING`.

- [ ] **Step 4: Publish timeout details**

Set agent diagnostic attributes to `readiness=timeout` and `readiness_missing=<comma-separated conditions>` and raise without playing the prompt.

### Task 4: Remove the microphone race and add the development diagnostics panel

**Files:**
- Modify: `LiveRAG-Fronted/agent-starter-react/components/interview/interview-live.tsx`

**Interfaces:**
- Consumes: `LiveKitRoom audio`, `useLocalParticipant().microphoneTrack`, `MediaStreamTrack.readyState`, agent participant attributes, room/track events, and existing transcriptions.
- Produces: a truthful microphone readiness predicate and the requested collapsible development diagnostics.

- [ ] **Step 1: Remove the pre-connect duplicate microphone effect**

Keep `audio` on `LiveKitRoom`, whose installed implementation publishes on `RoomEvent.SignalConnected`; do not call `setMicrophoneEnabled(true)` from the child mount effect.

- [ ] **Step 2: Derive microphone readiness from the real track**

Require room connected, microphone publication present, publication unmuted, local track present, and `mediaStreamTrack.readyState === "live"`. Use this predicate for submit buttons and the visible microphone label.

- [ ] **Step 3: Add a development-only `<details>` panel**

Show local UI activity, authoritative server state/version/question, room connection, microphone publication/mute/stop state, agent presence, backend audio subscription/readiness, last VAD, and last STT. Refresh on LiveKit participant, track, reconnect, and attribute events.

- [ ] **Step 4: Run frontend checks**

Run: `corepack pnpm typecheck` and `corepack pnpm lint` in `LiveRAG-Fronted/agent-starter-react`.

Expected: both commands exit 0.

### Task 5: Add persisted transition logging

**Files:**
- Modify: `liverag/interview/orchestrator.py`
- Modify: `liverag/interview/controller.py`
- Test: `tests/interview/test_orchestrator.py`

- [ ] Log every successful persisted transition with interview/session/attempt/question IDs, from/event/to/version, and log persistence/version conflicts with the same attempted transition context.

### Task 6: Regression verification

**Files:**
- Verify only; no new production files.

**Interfaces:**
- Consumes: all changes above.
- Produces: evidence that Interview changes do not alter the normal VoiceAssistant.

- [ ] **Step 1: Run targeted backend tests and lint**

Run: `.venv\\Scripts\\python.exe -m pytest tests/interview/test_interview_worker.py tests/agent/test_providers.py -q`

Run: `.venv\\Scripts\\python.exe -m ruff check liverag/interview_main.py liverag/agent/interview_assistant.py tests/interview/test_interview_worker.py`

Expected: all pass.

- [ ] **Step 2: Run the broader Interview test suite**

Run: `.venv\\Scripts\\python.exe -m pytest tests/interview -q`

Expected: all pass.

- [ ] **Step 3: Review the final diff**

Confirm there are no provider threshold changes, no edits to `liverag/main.py`, and no question/evaluator/follow-up/report changes.
