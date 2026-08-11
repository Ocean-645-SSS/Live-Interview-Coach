# Async Report Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the normal LiveKit interview completion path from synchronous report generation to an idempotent background report-generation job.

**Architecture:** The LiveKit agent will enqueue a `report_generation` job only after its closing speech completes. The Worker will build and persist the report, reconcile long-term skill progress, and then emit `REPORT_COMPLETED`; therefore `COMPLETED` means the background report is durable.

**Tech Stack:** Python 3.10+, FastAPI, LiveKit Agents, SQLAlchemy, Redis, pytest.

## Global Constraints

- Do not alter unrelated uncommitted worktree changes.
- PostgreSQL remains the source of truth for job and interview state; Redis is used only for queuing and locks.
- Preserve the existing `report:{session_id}` idempotency key and report-generation locking behavior.

---

### Task 1: Share report-job enqueueing between API and LiveKit

**Files:**
- Create: `liverag/interview/jobs/report_generation.py`
- Modify: `liverag/api/interview_routes.py`
- Modify: `liverag/interview_main.py`
- Test: `tests/interview/test_report_generation_finalization.py`

**Interfaces:**
- Produces: `enqueue_report_generation(interview_repo, job_repo, redis_queue, session_id) -> dict[str, Any]`.
- Consumes: an existing Session, `JobRepository`, and `RedisQueue`.

- [x] **Step 1: Write the failing enqueue idempotency test**

```python
result = await enqueue_report_generation(
    interview_repo=repository,
    job_repo=job_repo,
    redis_queue=redis_queue,
    session_id=session.id,
)
assert result["status"] == "PENDING"
assert job_repo.find_by_idempotency(
    job_type="report_generation", idempotency_key=f"report:{session.id}"
) is not None
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/interview/test_report_generation_finalization.py -q`
Expected: FAIL because `enqueue_report_generation` does not exist.

- [x] **Step 3: Implement the shared enqueue use case**

```python
async def enqueue_report_generation(...):
    interview_repo.get_session(session_id)
    # find `report:{session_id}` before creating a PostgreSQL job and Redis queue item
```

- [x] **Step 4: Make the API and LiveKit composition roots use it**

```python
return await enqueue_report_generation(
    interview_repo=service.repository,
    job_repo=job_repo,
    redis_queue=redis_queue,
    session_id=session_id,
)
```

- [x] **Step 5: Run the focused test**

Run: `uv run pytest tests/interview/test_report_generation_finalization.py -q`
Expected: PASS.

### Task 2: Finalize Session only after the Worker finishes its report

**Files:**
- Modify: `liverag/interview/application/controller.py`
- Modify: `liverag/agent/interview_assistant.py`
- Modify: `liverag/interview/jobs/tasks.py`
- Test: `tests/interview/test_report_generation_finalization.py`

**Interfaces:**
- Consumes: the shared enqueue function from Task 1.
- Produces: a `COMPLETING` Session immediately after closing speech, and `COMPLETED` only after a successful Worker task.

- [x] **Step 1: Write failing success and repeated-job tests**

```python
result = await report_generation_task(job, interview_repo=repository)
assert result["state"] == "COMPLETED"
assert repository.get_session(session.id).state is InterviewState.COMPLETED
```

- [x] **Step 2: Run the focused tests to verify they fail**

Run: `uv run pytest tests/interview/test_report_generation_finalization.py -q`
Expected: FAIL because the task leaves the Session in `COMPLETING`.

- [x] **Step 3: Enqueue after closing speech and transition in the Worker**

```python
await self._enqueue_report_generation(self._session_id)
# Worker only after report persistence and progress reconciliation:
InterviewOrchestrator(interview_repo).transition(
    session_id=session_id,
    event_id=f"report_completed:{session_id}",
    event_type=InterviewEventType.REPORT_COMPLETED,
)
```

- [x] **Step 4: Make repeated successful jobs no-op for the Session transition**

```python
if interview_repo.get_session(session_id).state is not InterviewState.COMPLETED:
    # emit REPORT_COMPLETED exactly once
```

- [x] **Step 5: Run focused tests and relevant regression tests**

Run: `uv run pytest tests/interview/test_report_generation_finalization.py tests/interview/test_background_jobs.py tests/interview/test_interview_worker.py -q`
Expected: PASS.
