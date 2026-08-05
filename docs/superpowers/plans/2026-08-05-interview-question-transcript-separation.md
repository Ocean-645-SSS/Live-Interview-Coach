# Interview Question and Transcript Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the current primary interview question visible while user STT interim/final text updates in a separate answer area, including follow-up and reconnect recovery.

**Architecture:** Reuse the existing Session cursor (`current_question_id`), frozen Interview `plan_json`, and persisted `FOLLOW_UP_REQUIRED` events as the structured sources of truth. Keep primary question, follow-up, interim user transcript, and final user transcript in independent React state inside the live room; filter LiveKit transcriptions to the local microphone track so agent TTS never becomes user-answer text.

**Tech Stack:** Next.js 15, React 19, TypeScript, LiveKit React components, existing REST API.

## Global Constraints

- Only solve question/transcript display separation and recovery.
- Do not refactor the Agent, state machine, frontend architecture, or LiveRAG.
- Do not add frameworks or dependencies.
- Do not change Answer persistence behavior.
- Do not delete files or directories in bulk.

---

### Task 1: Add typed structured read models

**Files:**
- Modify: `LiveRAG-Fronted/agent-starter-react/types/interview.ts`

**Interfaces:**
- Consumes: Existing GET `/api/interviews/{interview_id}` and GET `/api/interviews/sessions/{session_id}/events` payloads.
- Produces: `InterviewPlanSnapshot`, `InterviewPlanQuestion`, and `InterviewEventRecord` for the live-page recovery code.

- [ ] **Step 1: Add the minimal response fields and types**

```typescript
export interface InterviewPlanQuestion {
  id: string;
  question_text: string;
}

export interface InterviewPlanSnapshot {
  questions: InterviewPlanQuestion[];
}

export interface InterviewEventRecord {
  id: string;
  event_type: string;
  payload_json: string;
}
```

Extend `InterviewRecord` with the existing nullable `plan_json` field; do not add a new backend protocol.

- [ ] **Step 2: Run the TypeScript compiler**

Run: `corepack pnpm typecheck`

Expected: PASS with no TypeScript errors.

### Task 2: Recover question context and separate live state

**Files:**
- Modify: `LiveRAG-Fronted/agent-starter-react/components/interview/interview-live.tsx`

**Interfaces:**
- Consumes: `InterviewSessionRecord.current_question_id`, parsed `InterviewRecord.plan_json`, persisted `InterviewEventRecord[]`, and LiveKit transcription stream attributes `lk.transcribed_track_id` / `lk.transcription_final`.
- Produces: Independent `currentQuestionText`, `currentQuestionId`, `currentFollowUpText`, `interimUserTranscript`, and `finalUserTranscript` UI state.

- [ ] **Step 1: Load structured question context during the existing refresh**

Fetch the current Session and its events on every existing refresh. Cache the frozen plan by `interview_id`, parse `plan_json`, and pass the plan/events to `InterviewRoom`. Do not derive questions from TTS or chat messages.

- [ ] **Step 2: Update primary and follow-up state only from structured data**

When `session.current_question_id` changes, find that exact ID in the plan, update the primary question, clear the old follow-up, and reset the answer-round transcript. When `follow_up_count > 0`, restore the latest persisted `FOLLOW_UP_REQUIRED.payload_json.follow_up_question`; a new follow-up resets only the answer-round transcript, not the primary question.

- [ ] **Step 3: Split user interim and final STT**

Filter `useTranscriptions()` to streams whose `lk.transcribed_track_id` equals the local microphone publication SID (with local participant identity as the legacy fallback). Accumulate finalized segments into `finalUserTranscript` and keep only the active non-final segment in `interimUserTranscript`. STT effects must never call the primary/follow-up setters.

- [ ] **Step 4: Render two independent regions**

Render a `当前面试题` section containing the persistent primary question and optional follow-up, plus a separate `你的实时回答` section containing final/interim user text. Keep `aria-live` on the answer region only.

- [ ] **Step 5: Verify static quality gates**

Run: `corepack pnpm lint`

Expected: PASS with zero warnings.

Run: `corepack pnpm typecheck`

Expected: PASS with no TypeScript errors.

Run: `corepack pnpm build`

Expected: PASS and generate the existing Next.js routes without adding packages.

### Task 3: Review the acceptance paths

**Files:**
- Review: `LiveRAG-Fronted/agent-starter-react/components/interview/interview-live.tsx`

**Interfaces:**
- Consumes: The implementation from Tasks 1-2.
- Produces: Evidence that primary question updates are isolated from user transcript updates.

- [ ] **Step 1: Inspect the final diff**

Confirm every call to `setCurrentQuestionText` is in the plan/session-cursor effect, while every STT update only calls `setInterimUserTranscript` and `setFinalUserTranscript`.

- [ ] **Step 2: Check transition reset rules**

Confirm a new primary ID clears follow-up/interim/final state; a new follow-up preserves primary text and resets interim/final; repeated session polling and repeated interim/final transcription updates do not clear or replace the primary text.

- [ ] **Step 3: Check recovery rules**

Confirm a mounted/reconnected room restores the primary question from the Session cursor plus frozen plan, and restores the latest follow-up from persisted events whenever `follow_up_count > 0`.
