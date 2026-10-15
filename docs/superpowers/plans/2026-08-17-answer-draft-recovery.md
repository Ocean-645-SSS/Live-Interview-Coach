# Recoverable Answer Drafts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve confirmed STT final segments across Interview Agent crashes and rebuild the active answer buffer after reconnect, while retaining the existing atomic formal Answer submission.

**Architecture:** Add a durable draft aggregate below `InterviewSession`: `answer_drafts` represents one active answer window and `answer_draft_segments` stores its ordered final STT segments. The Agent writes only final segments to the draft. On submit, the existing Session/Event/Answer transaction also marks the draft submitted; on reconnect in `LISTENING`, the Agent reloads the open draft and reopens its answer window without issuing a duplicate state-machine transition.

**Tech Stack:** Python 3.10, SQLAlchemy 2, Alembic, SQLite/PostgreSQL-compatible schema, Pydantic-free immutable records, pytest.

## Global Constraints

- Do not persist STT interim text; persist only final segments.
- Do not change the semantics of `InterviewAnswerModel`: it remains the immutable, evaluation-ready answer.
- Preserve the atomic transaction for Session state update, `ANSWER_RECEIVED` event, formal Answer creation, and draft completion.
- A draft belongs to a Session/question/answer number, not to one LiveKit Attempt; a reconnect may use a new Attempt.
- Use a unique `(draft_id, segment_index)` constraint for idempotent final-segment retries.
- Do not add client-side storage, Redis buffering, a cleanup worker, or audio-file storage in this change.
- Do not modify the pre-existing uncommitted Evaluator files: `liverag/interview/application/evaluator.py`, `liverag/interview/prompts/evaluation_prompts.py`, `tests/interview/test_evaluator.py`, or `tests/interview/test_services.py`.

---

### Task 1: Add the durable draft data model and Alembic migration

**Files:**
- Modify: `liverag/interview/records.py`
- Modify: `liverag/interview/persistence/models.py`
- Create: `alembic/versions/d9a8b7c6e5f4_add_answer_drafts.py`
- Create: `tests/interview/test_answer_drafts.py`

**Interfaces:**
- Produces `AnswerDraftState` with `OPEN` and `SUBMITTED`.
- Produces immutable `AnswerDraftRecord` and `AnswerDraftSegmentRecord`.
- Produces ORM models `InterviewAnswerDraftModel` and `InterviewAnswerDraftSegmentModel`.

- [ ] **Step 1: Write failing ORM persistence tests**

```python
def test_open_draft_has_one_ordered_final_segment(database_repository):
    draft = database_repository.get_or_create_open_answer_draft(
        session_id="session-1",
        question_id="question-1",
        answer_number=1,
        opened_at=utc_now_iso(),
        deadline_at=utc_now_iso(),
    )
    database_repository.append_answer_draft_segment(
        draft_id=draft.id, segment_index=0, transcript="第一段"
    )

    assert database_repository.list_answer_draft_segments(draft.id)[0].transcript == "第一段"
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `python -m pytest tests/interview/test_answer_drafts.py::test_open_draft_has_one_ordered_final_segment -v`

Expected: FAIL because the repository API and models do not exist.

- [ ] **Step 3: Define records and ORM relationships**

Add the following state and records in `records.py`:

```python
class AnswerDraftState(str, Enum):
    OPEN = "OPEN"
    SUBMITTED = "SUBMITTED"

@dataclass(frozen=True, slots=True)
class AnswerDraftRecord:
    id: str
    session_id: str
    question_id: str
    answer_number: int
    state: AnswerDraftState
    opened_at: str
    deadline_at: str
    submitted_at: str | None
    created_at: str
    updated_at: str

@dataclass(frozen=True, slots=True)
class AnswerDraftSegmentRecord:
    id: str
    draft_id: str
    segment_index: int
    transcript: str
    created_at: str
```

Add ORM tables with these constraints:

```python
class InterviewAnswerDraftModel(Base):
    __tablename__ = "answer_drafts"
    __table_args__ = (
        UniqueConstraint("session_id", "question_id", "answer_number",
                         name="uq_answer_drafts_session_question_number"),
        CheckConstraint("answer_number >= 1", name="ck_answer_drafts_answer_number"),
    )

class InterviewAnswerDraftSegmentModel(Base):
    __tablename__ = "answer_draft_segments"
    __table_args__ = (
        UniqueConstraint("draft_id", "segment_index",
                         name="uq_answer_draft_segments_draft_index"),
        CheckConstraint("segment_index >= 0", name="ck_answer_draft_segments_index"),
        CheckConstraint("length(trim(transcript)) > 0",
                        name="ck_answer_draft_segments_transcript"),
    )
```

`InterviewSessionModel` gets a `answer_drafts` relationship with delete-orphan cascade; `InterviewAnswerDraftModel` gets ordered `segments` and one Session relationship.

- [ ] **Step 4: Add a reversible migration**

Create an Alembic revision whose `down_revision` is `c2f6d4a9e710`. In `upgrade()`, create `answer_drafts`, then `answer_draft_segments`, including foreign keys to `interview_sessions` with `ondelete="CASCADE"`, the constraints above, and an index on `answer_drafts(session_id, question_id, state)`. In `downgrade()`, drop the index, segment table, then draft table.

- [ ] **Step 5: Run migration and model tests**

Run: `python -m pytest tests/interview/test_answer_drafts.py -v`

Expected: PASS after the repository methods are added in Task 2; until then, retain the failing test as the next task's input.

- [ ] **Step 6: Commit**

```bash
git add liverag/interview/records.py liverag/interview/persistence/models.py alembic/versions/d9a8b7c6e5f4_add_answer_drafts.py tests/interview/test_answer_drafts.py
git commit -m "feat: add durable answer draft schema"
```

### Task 2: Add repository operations and atomic draft submission

**Files:**
- Modify: `liverag/interview/persistence/repository.py`
- Modify: `liverag/interview/persistence/sqlalchemy_repository.py`
- Modify: `liverag/interview/application/orchestrator.py`
- Modify: `liverag/interview/application/controller.py`
- Modify: `tests/interview/test_answer_drafts.py`

**Interfaces:**
- Consumes `AnswerDraftRecord` and `AnswerDraftSegmentRecord` from Task 1.
- Produces `get_or_create_open_answer_draft`, `get_open_answer_draft`, `append_answer_draft_segment`, and `list_answer_draft_segments` on `InterviewRepository`.
- Extends `AnswerReceivedCommand` with required `draft_id: str`.

- [ ] **Step 1: Extend the failing tests for idempotency and atomic submission**

```python
def test_duplicate_segment_index_returns_the_existing_segment(database_repository):
    draft = _open_draft(database_repository)
    first = database_repository.append_answer_draft_segment(
        draft_id=draft.id, segment_index=0, transcript="确认文本"
    )
    duplicate = database_repository.append_answer_draft_segment(
        draft_id=draft.id, segment_index=0, transcript="确认文本"
    )
    assert duplicate == first

def test_answer_transition_marks_its_open_draft_submitted(database_repository):
    draft = _open_draft_with_segment(database_repository, "确认文本")
    result = _record_answer_transition(database_repository, draft_id=draft.id)
    assert result.answer.transcript == "确认文本"
    assert database_repository.get_open_answer_draft("session-1", "question-1") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/interview/test_answer_drafts.py -v`

Expected: FAIL because no draft repository operations or `draft_id` transition exist.

- [ ] **Step 3: Implement the repository contract and SQLAlchemy methods**

Add these protocol methods:

```python
def get_or_create_open_answer_draft(... ) -> AnswerDraftRecord: ...
def get_open_answer_draft(self, *, session_id: str, question_id: str) -> AnswerDraftRecord | None: ...
def append_answer_draft_segment(... ) -> AnswerDraftSegmentRecord: ...
def list_answer_draft_segments(self, draft_id: str) -> list[AnswerDraftSegmentRecord]: ...
```

`get_or_create_open_answer_draft()` must return the existing `OPEN` draft for the exact Session/question/answer number, otherwise insert one. `append_answer_draft_segment()` must return the existing identical segment when the unique key already exists, and raise if the stored transcript differs. `list_answer_draft_segments()` must order by `segment_index` ascending.

Extend `record_answer_transition()` with `draft_id: str`. Inside its existing `session_scope` transaction, load the draft, require `state == OPEN`, require its Session/question/answer number to match the answer being created, create Event and Answer as today, then set the draft state to `SUBMITTED` and `submitted_at = now` before `flush()`.

- [ ] **Step 4: Thread `draft_id` through application entry points**

Add `draft_id: str` to `AnswerReceivedCommand`, pass it from `InterviewAgentController.submit_answer()`, and pass it from `InterviewOrchestrator.receive_answer()` into `record_answer_transition()`. Change the controller to accept the draft's fixed `answer_number`, rather than recomputing it from final answers after reconnect.

- [ ] **Step 5: Run repository and existing interview service tests**

Run: `python -m pytest tests/interview/test_answer_drafts.py tests/interview/test_services.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add liverag/interview/persistence/repository.py liverag/interview/persistence/sqlalchemy_repository.py liverag/interview/application/orchestrator.py liverag/interview/application/controller.py tests/interview/test_answer_drafts.py
git commit -m "feat: persist answer draft segments atomically"
```

### Task 3: Checkpoint final STT segments and restore the answer window on reconnect

**Files:**
- Modify: `liverag/agent/interview_assistant.py`
- Modify: `liverag/interview/application/controller.py`
- Modify: `tests/interview/test_answer_drafts.py`

**Interfaces:**
- Consumes repository draft operations from Task 2.
- `ActiveAnswerBuffer` gains `draft_id`, `answer_number`, and `next_segment_index`.
- Controller exposes draft opening/loading/checkpoint methods for the Agent; the Agent never accesses repository internals.

- [ ] **Step 1: Write failing controller-level tests for draft recovery**

```python
def test_reconnect_reuses_open_draft_and_final_segments(controller):
    draft = controller.open_answer_draft(opened_at=OPENED, deadline_at=DEADLINE)
    controller.checkpoint_final_segment(draft.id, 0, "第一段")

    restored = controller.load_open_answer_draft()

    assert restored.draft.id == draft.id
    assert [item.transcript for item in restored.segments] == ["第一段"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/interview/test_answer_drafts.py::test_reconnect_reuses_open_draft_and_final_segments -v`

Expected: FAIL because controller draft methods do not exist.

- [ ] **Step 3: Add narrow controller methods**

Add a controller return dataclass:

```python
@dataclass(frozen=True, slots=True)
class OpenAnswerDraft:
    draft: AnswerDraftRecord
    segments: tuple[AnswerDraftSegmentRecord, ...]
```

Implement:

```python
def open_or_resume_answer_draft(self, *, opened_at: str, deadline_at: str) -> OpenAnswerDraft: ...
def checkpoint_final_segment(self, *, draft_id: str, segment_index: int, transcript: str) -> AnswerDraftSegmentRecord: ...
```

The first method loads the current Session, requires `LISTENING` and a non-null `current_question_id`, derives `answer_number` from prior formal answers only when opening a new draft, then returns all saved segments. It must not emit a state-machine event.

- [ ] **Step 4: Update the Agent buffer lifecycle**

In `_mark_prompt_spoken_and_verify()`, call `controller.prompt_spoken()` first, then call `open_or_resume_answer_draft()` and construct `ActiveAnswerBuffer` from its ID, answer number, and saved final segments. In `record_transcript_segment()`, after a new `append_final()` result, call `checkpoint_final_segment()` with `next_segment_index` and increment it only after success. Do not checkpoint interim text.

In `on_enter()`, replace the current `LISTENING` replay-only branch with: replay the current question, call `open_or_resume_answer_draft()` with a fresh deadline, rebuild `ActiveAnswerBuffer`, mark it open, and schedule `_submit_answer_after_timeout()`. Do not call `prompt_spoken()` again because the Session is already `LISTENING`.

In `submit_active_answer()`, pass `buffer.draft_id` and `buffer.answer_number` into the controller submission path. Remove the old behavior that discards the recovered final segments on reconnect.

- [ ] **Step 5: Run the complete Interview Coach test suite and lint**

Run: `python -m pytest tests/interview -q`

Expected: PASS.

Run: `python -m ruff check liverag/interview liverag/agent/interview_assistant.py tests/interview/test_answer_drafts.py`

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add liverag/agent/interview_assistant.py liverag/interview/application/controller.py tests/interview/test_answer_drafts.py
git commit -m "feat: recover answer drafts after agent reconnect"
```

### Task 4: Verify migration behavior and document the implemented boundary

**Files:**
- Modify: `docs/INTERVIEW_COACH_ARCHITECTURE.md`
- Test: `tests/interview/test_answer_drafts.py`

**Interfaces:**
- Documents the implemented behavior without claiming client-side or database-outage zero-loss guarantees.

- [ ] **Step 1: Add an Alembic upgrade test or manual migration verification command**

Run: `python -m alembic upgrade head`

Expected: output ends at the new answer-draft revision and the database contains `answer_drafts` and `answer_draft_segments`.

- [ ] **Step 2: Document the exact recovery boundary**

Add a short subsection stating:

```markdown
Confirmed STT final segments are checkpointed in AnswerDraftSegment before formal answer submission.
Interim STT text remains in memory and can still be lost.
If the database is unavailable, the server cannot checkpoint final segments; client-side replay is not implemented.
```

- [ ] **Step 3: Run final verification**

Run: `python -m pytest tests/interview -q`

Expected: PASS.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add docs/INTERVIEW_COACH_ARCHITECTURE.md tests/interview/test_answer_drafts.py
git commit -m "docs: document answer draft recovery boundary"
```

## Self-Review

1. **Spec coverage:** Tasks 1 and 2 create the two durable tables and formal submission boundary; Task 3 writes final STT segments and restores them after reconnect; Task 4 verifies migrations and documents the intentionally remaining loss boundary.
2. **Placeholder scan:** No task uses TODO-style implementation placeholders; new APIs, constraints, state values, and verification commands are named explicitly.
3. **Type consistency:** `AnswerDraftRecord` and `AnswerDraftSegmentRecord` are produced in Task 1, persisted in Task 2, exposed through the controller in Task 3, and consumed by `ActiveAnswerBuffer` in Task 3.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-17-answer-draft-recovery.md`. The implementation is intended for inline execution in this side conversation; subagents are unavailable here.
