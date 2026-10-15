# Report Failure Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Show terminal report-generation failures to users and let them create a fresh, bounded retry job without losing the completed interview.

**Architecture:** The session stays COMPLETING because the interview itself is over. ReportState is the report outcome: FAILED remains visible with its error until a fresh retry is submitted; a successful retry persists the report then uses existing REPORT_COMPLETED finalization.

**Tech Stack:** FastAPI, SQLAlchemy, pytest, Next.js, React, TypeScript.

## Global Constraints

- Do not transition session to FAILED because report generation failed.
- Retry is allowed only for a FAILED report whose session is COMPLETING.
- Manual retry creates a new report_generation job with max_attempts=3; it never revives an exhausted job.
- Do not change automatic retry behavior.
- Stop client polling after report state FAILED or COMPLETED.
- Do not batch-delete files or directories.

---

### Task 1: Add fresh-job report retry endpoint

**Files:**
- Modify: liverag/interview/jobs/report_generation.py
- Modify: liverag/api/interview_routes.py
- Modify: tests/interview/test_report_generation_finalization.py

**Interfaces:**
- Produces retry_report_generation(interview_repo, job_repo, redis_queue, session_id) returning job_id and PENDING status.
- Produces POST /sessions/{session_id}/report/retry.

- [ ] **Step 1: Write failing backend tests**

Create a COMPLETING session, mark its report FAILED, and create an exhausted FAILED original job with idempotency key report:{session_id}. Assert retry creates and queues a distinct PENDING job whose idempotency key is report-retry:{session_id}:{report.updated_at}, and whose max_attempts is 3. Assert retrying a PENDING report returns HTTP 409.

- [ ] **Step 2: Verify failure**

Run: .\.venv\Scripts\python.exe -m pytest tests/interview/test_report_generation_finalization.py -k retry -q

Expected: FAIL because retry_report_generation and the endpoint do not exist.

- [ ] **Step 3: Implement backend behavior**

~~~python
async def retry_report_generation(
    *, interview_repo, job_repo, redis_queue, session_id: str
) -> dict[str, Any]:
    session = interview_repo.get_session(session_id)
    if session.state is not InterviewState.COMPLETING:
        raise HTTPException(status_code=409, detail="面试不在报告生成阶段")
    report = interview_repo.get_report_by_session(session_id)
    if report is None or report.state is not ReportState.FAILED:
        raise HTTPException(status_code=409, detail="报告当前不可重试")
    job = job_repo.create_job(
        job_type="report_generation",
        idempotency_key=f"report-retry:{session_id}:{report.updated_at}",
        business_resource_id=session_id,
        payload={"session_id": session_id},
        max_attempts=3,
    )
    await redis_queue.enqueue(job_type="report_generation", job_id=job.id)
    return {"job_id": job.id, "status": job.status.value}
~~~

Expose it through the route using _execute_async. Do not call enqueue_report_generation because its original idempotency key belongs to the exhausted job.

- [ ] **Step 4: Verify pass**

Run: .\.venv\Scripts\python.exe -m pytest tests/interview/test_report_generation_finalization.py -k retry -q

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add liverag/interview/jobs/report_generation.py liverag/api/interview_routes.py tests/interview/test_report_generation_finalization.py
git commit -m "feat: add manual report generation retry"
~~~

### Task 2: Render report failure and retry control

**Files:**
- Modify: LiveRAG-Fronted/agent-starter-react/components/interview/interview-report.tsx

**Interfaces:**
- Consumes InterviewReportRecord.state and error_message.
- Posts to the retry endpoint through apiRequest.

- [ ] **Step 1: Add retry state and handler**

Use useCallback for the existing load function and add isRetrying. The handler posts to "/api/interviews/sessions/" + encodeURIComponent(sessionId) + "/report/retry", then reloads state. It displays API failure text or “重新生成报告失败”.

- [ ] **Step 2: Stop terminal-state polling**

Only create the 2.5-second interval when the report is missing, PENDING, or GENERATING. Do one immediate load when the page mounts and after a retry.

- [ ] **Step 3: Render the two distinct states**

When state is FAILED, render title “报告生成失败”, report.error_message, and a button “重新生成报告” disabled while isRetrying. When state is PENDING or GENERATING, retain “报告尚未生成完成，页面会自动刷新。” Never show the retry button for a missing or completed report.

- [ ] **Step 4: Verify frontend checks**

Run from LiveRAG-Fronted/agent-starter-react: corepack pnpm typecheck

Run from LiveRAG-Fronted/agent-starter-react: corepack pnpm lint

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add LiveRAG-Fronted/agent-starter-react/components/interview/interview-report.tsx
git commit -m "feat: show report generation failures and retry"
~~~

### Task 3: Prove retry finalizes the session

**Files:**
- Modify: tests/interview/test_report_generation_finalization.py

**Interfaces:**
- Consumes the job returned by retry_report_generation.
- Produces proof that a successful retry changes report to COMPLETED and session to COMPLETED.

- [ ] **Step 1: Write the end-to-end retry test**

Start with a failed report and exhausted original job. Call retry_report_generation, load its new job, pass it to report_generation_task, then assert report state COMPLETED and session state COMPLETED.

- [ ] **Step 2: Verify backend regression**

Run: .\.venv\Scripts\python.exe -m pytest tests/interview/test_report_generation_finalization.py tests/interview/test_background_jobs.py -q

Expected: PASS.

- [ ] **Step 3: Verify static checks**

Run: .\.venv\Scripts\python.exe -m ruff check liverag/interview/jobs/report_generation.py liverag/api/interview_routes.py tests/interview/test_report_generation_finalization.py

Expected: PASS.

- [ ] **Step 4: Commit**

~~~bash
git add tests/interview/test_report_generation_finalization.py
git commit -m "test: cover report retry completion"
~~~

## Self-Review

- **Spec coverage:** Task 1 adds a fresh retry job and endpoint, Task 2 exposes terminal failure and retry, Task 3 proves successful retry completes the existing session.
- **Placeholder scan:** All validation states, user copy, idempotency key, files, and commands are explicit.
- **Type consistency:** The retry job preserves existing report_generation payload and completion workflow; session ID is used throughout.

\n