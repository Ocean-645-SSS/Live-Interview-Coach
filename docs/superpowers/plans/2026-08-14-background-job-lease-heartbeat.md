# Background Job Lease Heartbeat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure a Worker crash cannot leave a `RUNNING` background job permanently unavailable, while preventing an expired Worker from writing a result after another Worker has reclaimed the job.

**Architecture:** Keep PostgreSQL as the authoritative job state. Add a per-attempt `lease_token` and `lease_expires_at`; `mark_running` claims both, a lightweight coroutine renews the lease while the handler runs, and the existing recovery scan reclaims only expired leases. Completion and failure updates must include the token so a previous owner cannot overwrite a reclaimed attempt.

**Tech Stack:** Python 3.10+, asyncio, SQLAlchemy 2, Alembic, PostgreSQL/SQLite tests, pytest.

## Global Constraints

- Preserve the existing job types, Redis queue contract, retry count semantics, and `JobStatus` enum.
- Do not add a scheduler, a new queue, or a Redis-based source of truth.
- Use the existing `tests/interview/test_background_jobs.py` fixture pattern and `Base.metadata.create_all()` test schema.
- Lease duration: `task_timeout + 30` seconds; heartbeat interval: `max(1.0, lease_duration / 3)` seconds.
- The migration must be reversible and must not rewrite existing job rows; pre-existing `RUNNING` rows with a null lease are recovered by the existing `updated_at` fallback exactly once.

---

## File structure

- Modify `liverag/interview/persistence/models.py` — persist the active execution ownership and expiry fields; index recovery queries.
- Modify `liverag/interview/records.py` — expose the two fields from the immutable job record.
- Modify `liverag/interview/persistence/repository.py` — extend the repository Protocol for ownership-aware transitions.
- Modify `liverag/interview/jobs/repository.py` — claim, renew, release, and recover leases using conditional SQL updates.
- Modify `liverag/interview/jobs/worker.py` — run a heartbeat beside each handler and pass the claim token to terminal transitions.
- Create `alembic/versions/<revision>_add_background_job_leases.py` — add the two nullable columns and recovery index to deployed databases.
- Modify `tests/interview/test_background_jobs.py` — cover lease assignment, renewal, expiry recovery, and stale-owner write rejection.

### Task 1: Persist and expose a job execution lease

**Files:**
- Modify: `liverag/interview/persistence/models.py:588-621`
- Modify: `liverag/interview/records.py:232-262`
- Modify: `alembic/versions/<revision>_add_background_job_leases.py`
- Test: `tests/interview/test_background_jobs.py`

**Interfaces:**
- Produces: `BackgroundJobRecord.lease_token: str | None` and `BackgroundJobRecord.lease_expires_at: str | None`.
- Produces: ORM fields `BackgroundJobModel.lease_token` and `BackgroundJobModel.lease_expires_at`.

- [ ] **Step 1: Write the failing record assertion**

Add to `TestJobStatusTransitions.test_full_lifecycle` immediately after `mark_running`:

```python
assert job.lease_token is not None
assert job.lease_expires_at is not None
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `uv run pytest tests/interview/test_background_jobs.py::TestJobStatusTransitions::test_full_lifecycle -v`

Expected: FAIL because `BackgroundJobRecord` has no `lease_token` attribute.

- [ ] **Step 3: Add nullable model and record fields**

In `BackgroundJobModel`, add the following fields beside `started_at`:

```python
lease_token: Mapped[str | None] = mapped_column(String(64))
lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

Add `Index("idx_background_jobs_running_lease", "status", "lease_expires_at")` to `__table_args__`. Add the corresponding optional fields to `BackgroundJobRecord`, and populate them in `_job_record()` using `_to_iso()`.

- [ ] **Step 4: Create the Alembic migration**

Create a revision whose `down_revision` is the current Alembic head. Its upgrade must run:

```python
op.add_column("interview_background_jobs", sa.Column("lease_token", sa.String(length=64), nullable=True))
op.add_column("interview_background_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
op.create_index(
    "idx_background_jobs_running_lease",
    "interview_background_jobs",
    ["status", "lease_expires_at"],
    unique=False,
)
```

Its downgrade must drop the index, then each column individually.

- [ ] **Step 5: Run the focused test and migration check**

Run: `uv run pytest tests/interview/test_background_jobs.py::TestJobStatusTransitions::test_full_lifecycle -v`

Expected: PASS.

Run: `uv run alembic upgrade head`

Expected: exits 0 against the configured development database.

- [ ] **Step 6: Commit**

```bash
git add liverag/interview/persistence/models.py liverag/interview/records.py alembic/versions/<revision>_add_background_job_leases.py tests/interview/test_background_jobs.py
git commit -m "feat: persist background job execution leases"
```

### Task 2: Make repository ownership and recovery lease-aware

**Files:**
- Modify: `liverag/interview/persistence/repository.py:325-398`
- Modify: `liverag/interview/jobs/repository.py:171-285`
- Modify: `tests/interview/test_background_jobs.py:172-260`

**Interfaces:**
- Consumes: `BackgroundJobRecord.lease_token` and `BackgroundJobRecord.lease_expires_at` from Task 1.
- Produces: `mark_running(job_id: str, *, lease_seconds: int) -> BackgroundJobRecord`.
- Produces: `renew_lease(job_id: str, *, lease_token: str, lease_seconds: int) -> None`.
- Produces: `mark_completed(job_id: str, result: dict[str, Any], *, lease_token: str) -> BackgroundJobRecord` and `mark_failed(job_id: str, error: str, *, lease_token: str) -> BackgroundJobRecord`.

- [ ] **Step 1: Write failing repository tests**

Add these tests to `TestJobStatusTransitions`:

```python
def test_renew_lease_extends_current_owner_lease(self, job_repo: JobRepository):
    job = job_repo.create_job(job_type="demo", idempotency_key="lease_renew", business_resource_id="biz")
    running = job_repo.mark_running(job.id, lease_seconds=60)
    before = running.lease_expires_at

    job_repo.renew_lease(job.id, lease_token=running.lease_token, lease_seconds=120)

    assert job_repo.get_job(job.id).lease_expires_at > before

def test_expired_lease_is_recovered_and_old_owner_cannot_complete(self, job_repo: JobRepository):
    job = job_repo.create_job(job_type="demo", idempotency_key="expired_lease", business_resource_id="biz")
    first = job_repo.mark_running(job.id, lease_seconds=1)

    recovered = job_repo.recover_stale_running_jobs(
        stale_before=datetime.now(timezone.utc),
    )
    assert [item.id for item in recovered] == [job.id]

    job_repo.mark_queued(job.id)
    second = job_repo.mark_running(job.id, lease_seconds=60)
    with pytest.raises(RuntimeError, match="lease 已失效"):
        job_repo.mark_completed(job.id, {"ok": True}, lease_token=first.lease_token)
    assert job_repo.get_job(job.id).lease_token == second.lease_token
```

Use a `stale_before` later than the lease expiry rather than `datetime.now()` if the test clock needs to be deterministic.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `uv run pytest tests/interview/test_background_jobs.py::TestJobStatusTransitions -v`

Expected: FAIL because `lease_seconds` and `renew_lease` are not supported.

- [ ] **Step 3: Implement atomic lease transitions**

Update the protocol and concrete repository with the signatures listed above. In `mark_running`, generate `secrets.token_urlsafe(32)`, set `started_at`, increment `attempt`, and set `lease_expires_at = now + timedelta(seconds=lease_seconds)` in the same transaction.

Implement `renew_lease` with one SQLAlchemy `update(BackgroundJobModel)` statement whose `WHERE` requires all of:

```python
BackgroundJobModel.id == job_id
BackgroundJobModel.status == JobStatus.RUNNING
BackgroundJobModel.lease_token == lease_token
BackgroundJobModel.lease_expires_at > now
```

Set only `lease_expires_at` and `updated_at`. If `rowcount != 1`, raise `RuntimeError("Job lease 已失效：<job_id>")`.

Apply the same owner predicate to `mark_completed` and `mark_failed`, and clear both lease fields in their successful terminal update. Change `recover_stale_running_jobs` to select `RUNNING` jobs whose `lease_expires_at < stale_before`, plus legacy rows with `lease_expires_at IS NULL AND updated_at < stale_before`. On recovery, clear both lease fields before returning the job to `PENDING` or setting it `FAILED`.

- [ ] **Step 4: Run repository tests**

Run: `uv run pytest tests/interview/test_background_jobs.py::TestJobStatusTransitions -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add liverag/interview/persistence/repository.py liverag/interview/jobs/repository.py tests/interview/test_background_jobs.py
git commit -m "feat: recover expired background job leases"
```

### Task 3: Renew the lease while each handler is running

**Files:**
- Modify: `liverag/interview/jobs/worker.py:38-275`
- Modify: `tests/interview/test_background_jobs.py`

**Interfaces:**
- Consumes: the Task 2 `mark_running`, `renew_lease`, `mark_completed`, and `mark_failed` signatures.
- Produces: one heartbeat coroutine per executing job, stopped before terminal state persistence.

- [ ] **Step 1: Write the failing Worker heartbeat test**

Register a test-only handler that blocks on an `asyncio.Event`, start `_execute_job`, wait longer than one heartbeat interval, and assert its `lease_expires_at` is later than the expiry observed immediately after `mark_running`. Then release the event and assert the job is `COMPLETED` with `lease_token is None` and `lease_expires_at is None`.

Instantiate `BackgroundWorker` in this test with `task_timeout=3.0`; this makes the plan's lease duration 33 seconds, so set a test-only configurable heartbeat interval of `0.05` seconds through the Worker constructor to avoid waiting 11 seconds.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `uv run pytest tests/interview/test_background_jobs.py -k heartbeat -v`

Expected: FAIL because the Worker does not renew a lease.

- [ ] **Step 3: Add the minimal heartbeat lifecycle**

Add optional constructor parameter `lease_heartbeat_interval: float | None = None`. In `_execute_job`:

1. Call `mark_running(job_id, lease_seconds=int(self._task_timeout) + 30)` and retain `running.lease_token`.
2. Start an internal coroutine which repeatedly waits for the interval, then calls `renew_lease(job_id, lease_token=lease_token, lease_seconds=lease_seconds)` until a local `asyncio.Event` is set.
3. Execute the handler with the existing `asyncio.wait_for` unchanged.
4. In `finally`, set the event, await the heartbeat task, and then make terminal updates using the retained token.

The default interval is `max(1.0, lease_seconds / 3)`. Do not catch a lease-renewal `RuntimeError`: cancel the handler and surface it so the current Worker never keeps executing after it has lost ownership.

- [ ] **Step 4: Run Worker and regression tests**

Run: `uv run pytest tests/interview/test_background_jobs.py -k "heartbeat or worker" -v`

Expected: PASS.

Run: `uv run pytest tests/interview/test_background_jobs.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add liverag/interview/jobs/worker.py tests/interview/test_background_jobs.py
git commit -m "feat: renew background job leases during execution"
```

## Self-review

- **Spec coverage:** Task 1 adds durable lease state and the production migration; Task 2 ensures only the owning Worker can update/recover jobs; Task 3 keeps healthy long-running jobs leased and confirms cleanup. The existing queue/retry behavior stays intact.
- **Intentional scope boundary:** This plan does not add a separate `heartbeat_at` column because a renewable `lease_expires_at` is the liveness signal needed for recovery. It also does not build an external scheduler: the existing Worker poll loop already calls recovery before backfill.
- **Placeholder scan:** The only `<revision>` token is resolved mechanically by Alembic when creating the migration; no implementation behavior is unspecified.
- **Type consistency:** The same `lease_token` is returned by `mark_running`, consumed by renewal and terminal transitions, and cleared by every terminal/recovery path.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-14-background-job-lease-heartbeat.md`. Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
