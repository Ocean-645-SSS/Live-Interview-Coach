# Interview Answer Window P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Interview prompts wait for a usable participant audio/STT pipeline and make an answer end only through explicit user submission or the authoritative 90-second backend deadline.

**Architecture:** Keep the existing Interview state machine and repository transaction boundary. Add an Interview-only, event-driven audio readiness barrier around the shared prompt delivery path, and add a per-question in-memory answer buffer whose atomic close operation is shared by manual submission and timeout. Expose the authoritative answer deadline and submit event through the existing Interview API/LiveKit RPC so the Live page only projects backend state.

**Tech Stack:** Python, asyncio, FastAPI/Pydantic, LiveKit Agents, React/Next.js, TypeScript, pytest; no new dependencies.

## Global Constraints

- Do not refactor the whole project or add a framework/dependency.
- Do not change the normal LiveRAG VoiceAssistant, evaluator, question bank, report, MCP, or database migrations.
- Do not use fixed sleeps, infinite retries, or AgentSession restarts to mask audio readiness.
- Do not bulk-delete files or directories.
- Preserve unrelated user changes in the dirty worktree.

---

### Task 1: Characterize the current event and persistence paths

**Files:**
- Inspect: `liverag/interview_main.py`
- Inspect: `liverag/agent/interview_assistant.py`
- Inspect: `liverag/interview/controller.py`
- Inspect: `liverag/interview/orchestrator.py`
- Inspect: `liverag/interview/state_machine.py`
- Inspect: `liverag/api/interview_routes.py`
- Inspect: `LiveRAG-Fronted/agent-starter-react/components/interview/interview-live.tsx`

**Interfaces:**
- Consumes: current LiveKit room/session callbacks and existing Answer transaction.
- Produces: an evidence-backed map of every path that can play a prompt or persist an Answer.

- [ ] Trace room connection, participant/track publication/subscription, `AgentSession.start`, STT callbacks, TTS playback completion, `LISTENING`, and answer persistence.
- [ ] Record dirty-worktree overlap before editing and preserve existing changes.

### Task 2: Specify readiness and answer-window behavior with tests

**Files:**
- Modify: `tests/interview/test_interview_worker.py`
- Modify: `tests/interview/test_orchestrator.py`
- Modify: `tests/api/test_interview_routes.py`
- Modify: relevant frontend tests if present

**Interfaces:**
- Produces: executable tests for `InterviewAudioReadiness`, `ActiveAnswerBuffer`, manual/timeout close idempotency, restored deadline, and the 90-second default.

- [ ] Add tests proving prompts cannot play before participant microphone subscription plus Session/STT readiness.
- [ ] Add tests proving interim, segment-final, VAD stop, and semantic turn completion do not transition from `LISTENING`.
- [ ] Add tests proving manual submission and timeout share one close operation and persist exactly one Answer, including an empty answer.
- [ ] Run focused tests and confirm failures identify the missing behavior.

### Task 3: Implement the Interview-only backend lifecycle

**Files:**
- Modify: `liverag/agent/interview_assistant.py`
- Modify: `liverag/interview_main.py`
- Modify: `liverag/interview/orchestrator.py`
- Modify: `liverag/interview/schemas.py`
- Modify: `liverag/interview/service.py` only if required by the existing read model
- Modify: `liverag/api/interview_routes.py` only if the existing RPC cannot expose the required command/read data

**Interfaces:**
- Produces: `deliver_prompt_and_open_answer_window`, per-turn `ActiveAnswerBuffer`, a shared atomic close method, and an authoritative `answer_started_at`/`answer_deadline_at` projection.
- Consumes: the existing `receive_answer` transaction and existing LiveKit data/RPC plumbing.

- [ ] Change the configuration default to 90 seconds and return the actual effective timeout/deadline.
- [ ] Route first question, later questions, and follow-ups through the same readiness/TTS/window helper.
- [ ] Append STT final segments without submitting; update interim independently and log every accepted/ignored transcript.
- [ ] Start the timeout only after TTS finishes and `LISTENING` is persisted.
- [ ] Close through one lock-protected/idempotent path for both `answer_submit_requested` and `answer_timeout`, canceling the losing timeout task.
- [ ] Run focused backend tests and make them pass.

### Task 4: Project the authoritative answer window in Interview Live

**Files:**
- Modify: `LiveRAG-Fronted/agent-starter-react/types/interview.ts`
- Modify: `LiveRAG-Fronted/agent-starter-react/components/interview/interview-live.tsx`
- Modify: existing Interview API client/hook files discovered during Task 1

**Interfaces:**
- Consumes: session state, current question, live transcript events, and backend `answer_deadline_at`/effective timeout.
- Produces: current question, remaining `01:30` countdown, live user captions, and an idempotent `结束回答` action.

- [ ] Send a structured submit command with stable client event ID, session/question/attempt IDs; never synthesize transcript text.
- [ ] Enable the button only in `LISTENING`, disable immediately after click/timeout, and show submission/evaluation status.
- [ ] Derive countdown from the backend deadline so refresh/reconnect resumes it and transcript events never reset it.
- [ ] Run frontend typecheck, lint, and build.

### Task 5: Regression verification

**Files:**
- Verify only.

**Interfaces:**
- Produces: test evidence and a final diff limited to Interview behavior.

- [ ] Run focused worker, orchestrator, route, schema, and state-machine tests.
- [ ] Run the broader Interview suite and normal provider/VoiceAssistant regression tests.
- [ ] Inspect the final diff for accidental threshold, evaluator, question-bank, report, migration, or normal VoiceAssistant changes.
- [ ] Report root causes, changed files, before/after timing diagrams, readiness design, idempotency design, and exact test commands/results.
