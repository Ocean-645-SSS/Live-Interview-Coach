# M3-B Configuration Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add regression coverage for runtime voice, context-model, RAG, and SOUL configuration management.

**Architecture:** Configuration-unit tests exercise JSON override persistence and secret masking directly. API tests use the existing isolated FastAPI fixture and verify partial updates, validation, secret preservation, and effective-state behavior without contacting RAG Core.

**Tech Stack:** Python, pytest, FastAPI TestClient, Pydantic

## Global Constraints

- Do not perform recursive or bulk deletion.
- Keep all API test data under pytest temporary runtime directories.
- Never assert or expose a real secret in an API response.

---

### Task 1: Configuration persistence coverage

**Files:**
- Modify: `tests/config/test_settings.py`

**Interfaces:**
- Consumes: runtime configuration read/write/load helpers from `liverag.config.settings`
- Produces: regression coverage for JSON overrides, masked public output, and RAG modes

- [x] **Step 1: Add tests for voice, context-model, and RAG runtime configuration helpers**

- [x] **Step 2: Run `pytest tests/config/test_settings.py -q` and confirm all cases pass**

### Task 2: Management API coverage

**Files:**
- Modify: `tests/api/conftest.py`
- Create: `tests/api/test_model_config.py`
- Create: `tests/api/test_context_model_config.py`
- Create: `tests/api/test_rag_config.py`
- Create: `tests/api/test_prompt_config.py`

**Interfaces:**
- Consumes: `GET/PUT /model/config`, `/model/context-config`, `/rag/config`, `/prompt/soul`, and `/model/effective-state/{session_id}`
- Produces: request/response contract and persistence regression coverage

- [x] **Step 1: Remove obsolete M3-B import stubs from the API fixture**

- [x] **Step 2: Add model partial-update, masking, validation, and effective-state tests**

- [x] **Step 3: Add context-model numeric validation and masked-key preservation tests**

- [x] **Step 4: Add RAG partial-update, mode validation, and masked-key preservation tests**

- [x] **Step 5: Add SOUL read/write tests**

- [x] **Step 6: Run `pytest tests/api -q` and confirm all cases pass**

### Task 3: Regression verification

**Files:**
- Modify only production code proven incorrect by the new tests.

**Interfaces:**
- Consumes: failures from Tasks 1 and 2
- Produces: passing M3-B and existing API/config test suites

- [x] **Step 1: Correct any configuration route wiring exposed by the tests**

- [x] **Step 2: Run `pytest tests/config tests/api -q`**

- [x] **Step 3: Run `python -m compileall -q liverag`; test modules were already imported by pytest**
