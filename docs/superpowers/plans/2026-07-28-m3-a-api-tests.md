# M3-A API Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add isolated tests for the M3-A management API health, gateway normalization, RAG proxying, Session management, and next-session knowledge-base selection.

**Architecture:** Tests import the FastAPI application through a fixture after redirecting all runtime data to pytest temporary directories. External RAG calls are replaced with asynchronous fakes, while Session persistence uses the real `ContextStore` wherever possible. Missing M3-A Session contracts are expressed as tests so implementation progress is measurable.

**Tech Stack:** pytest, pytest-asyncio, FastAPI TestClient, unittest.mock, Pydantic

## Global Constraints

- Do not access a real RAG Core service.
- Do not write to the user's real LiveRAG data directory.
- Do not delete directories recursively.
- Keep each test file focused on one API responsibility.

---

### Task 1: Isolated API test fixture and health

**Files:**
- Create: `tests/api/conftest.py`
- Create: `tests/api/test_health.py`

**Interfaces:**
- Consumes: `liverag.api.server:app`
- Produces: `api_server` and `api_client` pytest fixtures

- [ ] Add a fixture that imports the server only after test environment isolation.
- [ ] Add a test asserting `GET /health` returns HTTP 200 and `{"status": "ok"}`.
- [ ] Run `pytest tests/api/test_health.py -q`.

### Task 2: Gateway response normalization

**Files:**
- Create: `tests/api/test_rag_gateway.py`

**Interfaces:**
- Consumes: `GatewayResponse`, `RagGateway._map_data`
- Produces: coverage for successful normalization and unchanged error envelopes

- [ ] Test successful `data` mapping without mutation of the original envelope.
- [ ] Test that error envelopes bypass the mapper.
- [ ] Test stable document-list defaults.
- [ ] Run `pytest tests/api/test_rag_gateway.py -q`.

### Task 3: RAG proxy behavior

**Files:**
- Create: `tests/api/test_rag_proxy.py`

**Interfaces:**
- Consumes: `/rag/ready`, `/rag/knowledge-bases`, `/rag/knowledge-bases/{kb_id}/query/context`
- Produces: coverage for status/body forwarding and unavailable RAG responses

- [ ] Replace Gateway methods with async fakes.
- [ ] Assert proxy routes preserve HTTP status codes and envelope bodies.
- [ ] Assert query payload and downstream path are correct.
- [ ] Run `pytest tests/api/test_rag_proxy.py -q`.

### Task 4: Session management contracts

**Files:**
- Create: `tests/api/test_sessions.py`

**Interfaces:**
- Consumes: Session routes and real `ContextStore`
- Produces: coverage for turns, deletion protection, list, detail, and export contracts

- [ ] Create active and ended Sessions in the temporary runtime directory.
- [ ] Test active Session deletion returns 409.
- [ ] Test ended Session deletion removes its known files.
- [ ] Test Session turns.
- [ ] Add contract tests for list, detail, and export endpoints.
- [ ] Run `pytest tests/api/test_sessions.py -q`.

### Task 5: Next-session knowledge-base selection

**Files:**
- Create: `tests/api/test_knowledge_base_selection.py`

**Interfaces:**
- Consumes: `PUT /session/knowledge-base`, `MetadataStore.get_session_config`
- Produces: coverage that selection is stored for a new Session without changing an active Session

- [ ] Replace knowledge-base detail and readiness calls with async fakes.
- [ ] Select knowledge base B through the API.
- [ ] Assert the configured value becomes B.
- [ ] Assert an existing active Session remains locked to A.
- [ ] Run `pytest tests/api/test_knowledge_base_selection.py -q`.

### Task 6: Full verification

**Files:**
- Test: `tests/api/`

**Interfaces:**
- Consumes: all tests above
- Produces: an explicit list of passing tests and remaining missing-contract failures

- [ ] Run `pytest tests/api -q`.
- [ ] Run `ruff check tests/api`.
- [ ] Record any failures caused solely by unimplemented M3-A routes.
