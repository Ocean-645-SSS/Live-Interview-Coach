# M2-E HistoryCompactor Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the automated test coverage for M2-E session compaction, traceable history persistence, knowledge-base isolation, and shutdown integration.

**Architecture:** Add focused unit tests around `HistoryCompactor` with an isolated real `ContextStore` and a fake OpenAI client. Extend storage tests for cursor persistence, `source_session_id`, clearing, validation, and KB isolation; retain the existing Agent shutdown test as the lifecycle boundary.

**Tech Stack:** Python, pytest, pytest-asyncio, monkeypatch, JSONL filesystem fixtures.

## Global Constraints

- Never call a real Context Model or use real user data.
- Preserve raw session messages in every compaction path.
- Every appended history record must contain `source_session_id`.
- History records and prompt consumption must remain isolated by `kb_id`.
- Do not delete files or directories in test setup or implementation.

---

### Task 1: History storage contract

**Files:**
- Modify: `tests/context/test_store.py`
- Test: `tests/context/test_store.py`

**Interfaces:**
- Consumes: `ContextStore.append_history(kb_id, content, source_session_id)`, `read_recent_history(kb_id, limit)`, and `clear_history(kb_id)`.
- Produces: regression coverage for traceability, cursor behavior, isolation, validation, and explicit clearing.

- [x] **Step 1: Add storage tests**

Add tests that append records to two knowledge bases and assert trimmed content, monotonically increasing cursor values, the exact `source_session_id`, JSONL isolation, empty-content rejection, and cursor restart after `clear_history`.

- [x] **Step 2: Run the focused storage tests**

Run: `python -m pytest tests/context/test_store.py -v`

Expected: all storage tests pass; any contract mismatch fails with the relevant field or cursor assertion.

### Task 2: HistoryCompactor behavior

**Files:**
- Create: `tests/context/test_history.py`
- Test: `tests/context/test_history.py`

**Interfaces:**
- Consumes: `HistoryCompactor.compact_after_call(session_id, kb_id, kb_name)`.
- Produces: deterministic coverage of success, `NO_HISTORY`, empty sessions, missing API keys, model failures, truncation, prompt context, fenced output cleanup, source traceability, and KB isolation.

- [x] **Step 1: Build isolated fixtures and fake model client**

Use a real `ContextStore` rooted under `tmp_path`; replace `liverag.context.history.AsyncOpenAI` with a fake client that records completion arguments and returns configured text or raises a configured exception.

- [x] **Step 2: Add successful-compaction tests**

Assert that the model receives SOUL, overview, recent same-KB history, KB identity, and raw messages; assert the resulting JSONL record contains the cleaned model output and current session ID.

- [x] **Step 3: Add no-write and failure-path tests**

Cover empty session, missing key, blank/`NO_HISTORY` output, and provider exception. Assert that no history is appended and original `messages.jsonl` remains unchanged.

- [x] **Step 4: Add formatting boundary tests**

Cover message truncation, recent-history limits, code-fence cleanup, and exclusion of another KB’s history from the model prompt.

- [x] **Step 5: Run the focused compactor tests**

Run: `python -m pytest tests/context/test_history.py -v`

Expected: all compactor tests pass without network access.

### Task 3: M2-E regression

**Files:**
- Test: `tests/context/test_history.py`
- Test: `tests/context/test_store.py`
- Test: `tests/context/test_renderer.py`
- Test: `tests/agent/test_main.py`

**Interfaces:**
- Consumes: the storage, compactor, renderer, and Agent shutdown boundaries.
- Produces: an end-to-end automated regression signal for the complete M2-E stage.

- [x] **Step 1: Run the M2-E test set**

Run: `python -m pytest tests/context/test_history.py tests/context/test_store.py tests/context/test_renderer.py tests/agent/test_main.py -v`

Expected: all tests pass.

- [x] **Step 2: Run the complete test suite**

Run: `python -m pytest`

Expected: the repository test suite passes, or unrelated pre-existing failures are reported separately with exact evidence.

- [x] **Step 3: Review changed files**

Run: `git diff --check` and `git status --short`

Expected: no whitespace errors; only the user’s existing implementation changes plus the M2-E plan and test files are present.
