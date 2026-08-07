# Resume Parse Dependency Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the `resume_parse` dependency contract so configuration is checked once while assembling the worker and the task handler receives guaranteed, typed dependencies.

**Architecture:** Treat worker construction as the configuration boundary: it creates a usable RAG profile source and OpenAI client or rejects a missing API key before consuming jobs. Keep `resume_parse_task` focused on job payload processing, retrieval, LLM parsing, and Pydantic validation, with dependencies expressed in its function signature instead of dynamically fetched from `**deps`.

**Tech Stack:** Python 3.11+, asyncio, OpenAI AsyncOpenAI client, Pydantic, pytest

## Global Constraints

- Do not batch-delete files or directories.
- Validate only at system boundaries; trust internal dependency injection.
- Preserve unrelated working-tree changes.

---

### Task 1: Make the resume parser dependency contract explicit

**Files:**
- Modify: `liverag/interview/jobs/tasks.py`
- Test: `tests/interview/test_background_jobs.py`

**Interfaces:**
- Consumes: `KnowledgeContextSource.retrieve(*, kb_id: str, query: str)` and an OpenAI-compatible async client.
- Produces: `resume_parse_task(job, repo, *, profile_source, llm_client, llm_model, **deps) -> dict[str, Any]`.

- [ ] **Step 1: Update the dependency-focused tests**

Replace tests for impossible missing internal dependencies with an assertion that the handler exposes the required keyword-only dependency names.

- [ ] **Step 2: Run the focused tests and verify the old handler contract fails**

Run: `pytest tests/interview/test_background_jobs.py -k resume_parse -q`

Expected: the new signature-contract assertion fails against the old `**deps` lookup implementation.

- [ ] **Step 3: Implement the explicit handler signature**

Import dependency types under `TYPE_CHECKING`, add required keyword-only parameters, remove `deps.get(...)` checks, and continue forwarding unused future dependencies through `**deps`.

- [ ] **Step 4: Run the focused tests**

Run: `pytest tests/interview/test_background_jobs.py -k resume_parse -q`

Expected: all resume-parse tests pass.

### Task 2: Validate LLM configuration at worker assembly

**Files:**
- Modify: `liverag/interview/jobs/worker_main.py`

**Interfaces:**
- Consumes: `AppSettings.voice.llm_api_key`, `llm_base_url`, and `llm_model`.
- Produces: a `BackgroundWorker` whose dependency mapping always contains a usable `profile_source`, `llm_client`, and `llm_model`.

- [ ] **Step 1: Move the missing-key check to the configuration boundary**

Raise a clear configuration error from `_build_worker` when `VOICE_LLM_API_KEY`/`DASHSCOPE_API_KEY` is absent, then construct `AsyncOpenAI` unconditionally after that check.

- [ ] **Step 2: Run static and regression checks**

Run: `pytest tests/interview/test_background_jobs.py -q`

Expected: all background-job tests pass.

Run: `python -m compileall -q liverag/interview/jobs/tasks.py liverag/interview/jobs/worker_main.py`

Expected: exit code 0.

### Task 3: Review the final patch

**Files:**
- Review: `liverag/interview/jobs/tasks.py`
- Review: `liverag/interview/jobs/worker_main.py`
- Review: `tests/interview/test_background_jobs.py`

**Interfaces:**
- Consumes: the completed changes from Tasks 1 and 2.
- Produces: a scoped diff with no unrelated edits.

- [ ] **Step 1: Inspect the final diff and test status**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `git diff -- liverag/interview/jobs/tasks.py liverag/interview/jobs/worker_main.py tests/interview/test_background_jobs.py`

Expected: only dependency-contract, worker-wiring, and corresponding test changes.
