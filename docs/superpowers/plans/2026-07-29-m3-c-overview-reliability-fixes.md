# M3-C Overview Reliability Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Knowledge Overview generation use a consistent interface, distinguish upstream RAG failures from empty knowledge bases, expose bounded overview query parameters, and preserve actionable background-task diagnostics.

**Architecture:** Keep raw overview collection in the RAG Core and prose generation in the management API. Treat a non-success RAG Core response as an exception so it cannot be persisted as a fresh generated overview, while the background boundary logs failures without disrupting job polling.

**Tech Stack:** Python 3.11+, FastAPI, OpenAI async client, pytest, pytest-asyncio

## Global Constraints

- Do not add a bulk-delete API or use recursive deletion commands.
- Overview generation failures must not break document indexing, job polling, or calls.
- Runtime defaults remain compatible with the management API's existing limits.

---

### Task 1: Generator Interface Consistency

**Files:**
- Modify: `liverag/context/overview.py`
- Create: `tests/context/test_overview.py`

**Interfaces:**
- Consumes: `RagClientSettings`, `ContextModelSettings`, and `ContextStore`
- Produces: `KnowledgeOverviewGenerator.generate(..., rag_settings: RagClientSettings, ...)`

- [ ] **Step 1: Write failing generator tests**

Add tests that call `generate()` with the `rag_settings` keyword and verify missing-key fallback content and metadata are written without invoking a model.

- [ ] **Step 2: Run the focused tests and verify the keyword mismatch fails**

Run: `.venv/Scripts/python.exe -m pytest tests/context/test_overview.py -q`

- [ ] **Step 3: Rename `raw_settings` to `rag_settings`**

Use the same keyword in the public signature and `_build_user_prompt()` call.

- [ ] **Step 4: Run the focused tests**

Run: `.venv/Scripts/python.exe -m pytest tests/context/test_overview.py -q`

---

### Task 2: Raw Overview Failure Semantics and Logging

**Files:**
- Modify: `liverag/api/server.py`
- Create: `tests/api/test_overview_management.py`

**Interfaces:**
- Consumes: `RagGateway.get()` and `GatewayResponse`
- Produces: `_raw_knowledge_overview(kb_id) -> dict[str, Any]`, raising `RuntimeError` for non-success or malformed responses

- [ ] **Step 1: Write failing API helper tests**

Cover successful structured data, RAG error envelopes, malformed successful data, and background-task exception logging.

- [ ] **Step 2: Run the focused tests and verify failures**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_overview_management.py -q`

- [ ] **Step 3: Implement strict raw-overview handling and structured exception logging**

Raise on non-success and malformed payloads. Log `kb_id`, `job_id`, and the exception at the background boundary while still returning normally.

- [ ] **Step 4: Run the focused tests**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_overview_management.py -q`

---

### Task 3: Bounded RAG Overview Endpoint

**Files:**
- Modify: `liverag/rag/server.py`
- Test: `tests/rag/test_overview_api.py`

**Interfaces:**
- Consumes: `RagEngine.knowledge_overview(entity_limit, relation_limit, document_limit, topic_limit)`
- Produces: `GET /v1/knowledge-bases/{kb_id}/overview` with defaults and bounds

- [ ] **Step 1: Write endpoint validation tests**

Verify omitted limits use `20/12/10/8`, valid overrides reach the engine, and negative or excessive values return HTTP 422.

- [ ] **Step 2: Run tests and confirm required/unbounded parameters fail**

Run: `.venv/Scripts/python.exe -m pytest tests/rag/test_overview_api.py -q`

- [ ] **Step 3: Add FastAPI `Query` defaults and bounds**

Use non-negative limits with conservative maximums while retaining current defaults.

- [ ] **Step 4: Run focused and regression tests**

Run: `.venv/Scripts/python.exe -m pytest tests/rag/test_overview_api.py tests/api/test_rag_proxy.py tests/rag/test_engine.py -q`

---

### Task 4: Verification

**Files:**
- Verify: `liverag/context/overview.py`
- Verify: `liverag/api/server.py`
- Verify: `liverag/rag/server.py`
- Verify: `tests/context/test_overview.py`
- Verify: `tests/api/test_overview_management.py`
- Verify: `tests/rag/test_overview_api.py`

**Interfaces:**
- Consumes: completed tasks
- Produces: passing focused tests and clean compilation for changed modules

- [ ] **Step 1: Run Ruff on changed files**

Run: `.venv/Scripts/ruff.exe check liverag/context/overview.py liverag/api/server.py liverag/rag/server.py tests/context/test_overview.py tests/api/test_overview_management.py tests/rag/test_overview_api.py`

- [ ] **Step 2: Compile changed modules**

Run: `.venv/Scripts/python.exe -m compileall -q liverag/context/overview.py liverag/api/server.py liverag/rag/server.py`

- [ ] **Step 3: Run the complete relevant test selection**

Run: `.venv/Scripts/python.exe -m pytest tests/context/test_overview.py tests/api/test_overview_management.py tests/rag/test_overview_api.py tests/api/test_rag_proxy.py tests/rag/test_engine.py -q`

---

### Task 5: M3-C Management Contract and Scheduling Coverage

**Files:**
- Modify: `tests/api/test_overview_management.py`
- Create: `tests/rag/test_knowledge_overview.py`

**Interfaces:**
- Consumes: `GET/PUT /rag/knowledge-bases/{kb_id}/context/overview`, `_mark_overview_stale_if_ok()`, `_job_has_new_processed_documents()`, and `_schedule_overview_generation_after_completed_job()`
- Produces: regression coverage for the remaining M3-C acceptance behavior

- [x] **Step 1: Test GET and PUT Overview contracts**

Use the isolated management API fixture and a fake `_knowledge_base_detail()` to verify GET creates/returns the stable default, PUT persists trimmed manual content with `stale=false`, blank PUT returns 422 without mutation, and missing KB returns 404 without creating Overview files.

- [x] **Step 2: Test stale marking**

Call `_mark_overview_stale_if_ok()` with successful and failed gateway envelopes and assert only successful document changes persist `stale=true` with the supplied reason.

- [x] **Step 3: Test scheduling conditions**

Exercise `processing`, `failed`, `processed`, and `partial_failed` jobs. Assert scheduling occurs only when at least one document is processed.

- [x] **Step 4: Test duplicate suppression**

Persist `source_job_id=job-one` with `stale=false`, invoke the scheduler for the same job, and assert no background task is added.

- [x] **Step 5: Run focused and regression tests**

Add RagEngine coverage for the no-processed-document response shape and entity/relation/topic/document aggregation, then run:

`.venv/Scripts/python.exe -m pytest tests/api/test_overview_management.py tests/context/test_overview.py tests/rag/test_overview_api.py tests/rag/test_knowledge_overview.py tests/api/test_rag_proxy.py -q`

Expected: all selected tests pass.
