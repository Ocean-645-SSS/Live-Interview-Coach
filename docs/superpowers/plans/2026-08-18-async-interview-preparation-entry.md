# Async Interview Preparation Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Interview Coach creation page use the existing asynchronous `interview_preparation` workflow instead of the synchronous `/api/interviews/prepared` compatibility endpoint.

**Architecture:** The browser creates a bare Interview with its complete frozen configuration, then requests `POST /api/interviews/{interview_id}/prepare?async=true`. It polls `GET /api/interviews/{interview_id}/preparation` until the Job is `COMPLETED`, verifies the Interview is `READY`, creates its Session, and only then navigates to the LiveKit page.

**Tech Stack:** Next.js/React, TypeScript, FastAPI, Redis Queue, Interview Worker, pytest.

## Global Constraints

- Reuse the existing preparation API and Worker; do not add a second preparation Job type.
- Keep `POST /api/interviews/prepared` unchanged as a backward-compatible synchronous endpoint.
- Do not navigate to a LiveKit page before the preparation Job completes and a Session exists.
- Surface terminal Job failure to the user; do not retry from the browser automatically.

---

### Task 1: Switch the create-page orchestration to the asynchronous preparation Job

**Files:**
- Modify: `LiveRAG-Fronted/agent-starter-react/components/interview/interview-create.tsx`
- Modify: `LiveRAG-Fronted/agent-starter-react/types/interview.ts`

**Interfaces:**
- Consumes `POST /api/interviews`, `POST /api/interviews/{interview_id}/prepare?async=true`, `GET /api/interviews/{interview_id}/preparation`, `GET /api/interviews/{interview_id}`, and `POST /api/interviews/{interview_id}/sessions`.
- Produces a navigation URL only after `InterviewPreparationStatus.status === "COMPLETED"` and `InterviewRecord.state === "READY"`.

- [ ] **Step 1: Define preparation response types**

```ts
export interface InterviewPreparationStart {
  job_id: string;
  status: "PENDING" | "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
}

export interface InterviewPreparationStatus extends InterviewPreparationStart {
  stage: string | null;
  error: string | null;
}
```

- [ ] **Step 2: Replace the synchronous prepared request**

```ts
const interview = await apiRequest<InterviewRecord>("/api/interviews", {
  method: "POST",
  body: { title, config },
});
await apiRequest<InterviewPreparationStart>(
  `/api/interviews/${encodeURIComponent(interview.id)}/prepare?async=true`,
  { method: "POST" },
);
```

- [ ] **Step 3: Poll preparation and create the Session only after READY**

```ts
const preparation = await apiRequest<InterviewPreparationStatus>(
  `/api/interviews/${encodeURIComponent(interviewId)}/preparation`,
);
if (preparation.status === "FAILED") throw new Error(preparation.error ?? "面试准备失败");
if (preparation.status !== "COMPLETED") await sleep(1000);
```

After Job completion, request the Interview, require `state === "READY"`, then call `/sessions` and route using the returned Session ID.

- [ ] **Step 4: Verify**

Run: `corepack pnpm lint && corepack pnpm typecheck`

Expected: the React application type-checks and the create page has no call to `/api/interviews/prepared`.

### Task 2: Run regression tests for the already-existing Worker endpoint

**Files:**
- Test: `tests/interview/test_background_jobs.py`

**Interfaces:**
- Consumes the unchanged `interview_preparation` Worker handler.
- Produces confidence that the UI now invokes an already-tested asynchronous path.

- [ ] **Step 1: Run preparation workflow tests**

Run: `python -m pytest tests/interview/test_background_jobs.py -q`

Expected: existing interview-preparation job, retry, and recovery tests pass.

- [ ] **Step 2: Commit**

```bash
git add LiveRAG-Fronted/agent-starter-react/components/interview/interview-create.tsx LiveRAG-Fronted/agent-starter-react/types/interview.ts docs/superpowers/plans/2026-08-18-async-interview-preparation-entry.md
git commit -m "feat: prepare interviews asynchronously from create page"
```
