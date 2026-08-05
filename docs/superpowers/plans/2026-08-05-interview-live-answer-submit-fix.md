# Interview Live Answer Submit Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the first Interview Coach question accept and display speech immediately, and make MANUAL, UNKNOWN, and TIMEOUT close the active answer through one authoritative, idempotent backend entry point.

**Architecture:** Keep the change inside the Interview LiveKit agent/controller and Interview React view. Create the answer buffer before publishing the persisted `LISTENING` window, gate transcript acceptance on an explicit open flag and matching question, and split answer persistence from asynchronous evaluation so RPC acknowledgement reflects real backend acceptance. The frontend derives button availability only from authoritative window state and keeps all transcriptions belonging to the current question.

**Tech Stack:** Python 3.11, asyncio, LiveKit Agents, pytest, React 19, Next.js 15, TypeScript.

## Global Constraints

- Do not modify the question bank, evaluator implementation, reports, PostgreSQL, Redis, MCP, or the normal LiveRAG VoiceAssistant.
- Do not tune VAD/STT parameters, add fixed sleeps, infinite retries, or restart `AgentSession`.
- An answer ends only via `MANUAL`, `UNKNOWN`, or `TIMEOUT`.
- Do not bulk-delete files or directories; preserve unrelated dirty-worktree changes.

---

### Task 1: Lock down the first-question answer-window lifecycle

**Files:**
- Modify: `tests/interview/test_interview_worker.py`
- Modify: `liverag/agent/interview_assistant.py`

**Interfaces:**
- Produces: `deliver_question_and_open_answer_window()` and an `ActiveAnswerBuffer` with an explicit window identifier/open state.
- Consumes: `InterviewAgentController.prompt_spoken()` and attempt-scoped audio readiness.

- [ ] Add a failing test proving the buffer exists before `prompt_spoken()` publishes `LISTENING` and a transcript arriving at that boundary is retained.
- [ ] Add a failing test proving first, later, and follow-up prompts use the same delivery method.
- [ ] Create the buffer before the state transition, mark it open only after persisted `LISTENING` is verified, and start its deadline task last.
- [ ] Add the required compact lifecycle and ignored-transcript context logs.
- [ ] Run `python -m pytest tests/interview/test_interview_worker.py -q` and expect all tests to pass.

### Task 2: Unify and acknowledge all answer submissions

**Files:**
- Modify: `tests/interview/test_interview_worker.py`
- Modify: `liverag/agent/interview_assistant.py`
- Modify: `liverag/interview/controller.py`
- Modify: `liverag/interview_main.py`

**Interfaces:**
- Produces: `submit_active_answer(session_id, question_id, reason, event_id)` and a result containing success/error/old/new state.
- Consumes: the existing atomic `InterviewService.receive_answer()` repository transition.

- [ ] Add failing tests for MANUAL transcript persistence, UNKNOWN without STT/session mutation, TIMEOUT, duplicate event IDs, and competing manual/timeout submissions.
- [ ] Split controller answer persistence from evaluation so the state reaches `EVALUATING` before the RPC response.
- [ ] Route MANUAL, UNKNOWN, and TIMEOUT through `submit_active_answer`; validate the active window tuple, close once, cancel the timeout loser, and persist once.
- [ ] Make the RPC await the authoritative submission result and return rejected errors instead of accepting a detached task blindly.
- [ ] Run `python -m pytest tests/interview/test_interview_worker.py tests/interview/test_orchestrator.py -q` and expect all tests to pass.

### Task 3: Fix frontend transcript ownership and button feedback

**Files:**
- Modify: `LiveRAG-Fronted/agent-starter-react/components/interview/interview-live.tsx`

**Interfaces:**
- Consumes: persisted `LISTENING`, current question ID, answer deadline event, and RPC submit result.
- Produces: visible first-question captions, immediate `正在提交`, a precise disabled reason, and readable submission errors.

- [ ] Stop resetting the transcription start index after first-question speech has already arrived; reset only when an already-established question changes.
- [ ] Enable submit only when server state is `LISTENING`, the answer window is open, a question ID exists, and no submission is active.
- [ ] Display the exact disabled reason and keep backend/RPC failures visible.
- [ ] Send one structured reason (`MANUAL` or `UNKNOWN`) plus session/question/attempt/event IDs.
- [ ] Run `corepack pnpm typecheck`, `corepack pnpm lint`, and `corepack pnpm build` from the frontend directory and expect success.

### Task 4: Regression verification and handoff

**Files:**
- Verify only.

**Interfaces:**
- Produces: root-cause evidence, changed-file list, test results, and manual acceptance steps.

- [ ] Run the focused Interview worker/controller/orchestrator/API tests.
- [ ] Run normal agent/VoiceAssistant regression tests without changing their code.
- [ ] Inspect the final diff and confirm excluded subsystems were untouched by this fix.
- [ ] Report the eight requested findings and manual acceptance sequence.
