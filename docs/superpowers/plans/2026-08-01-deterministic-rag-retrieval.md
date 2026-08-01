# Deterministic RAG Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee that an enabled voice session queries its locked knowledge base before the LLM answers, instead of relying on optional model tool selection.

**Architecture:** Use LiveKit's `Agent.on_user_turn_completed` lifecycle hook as the deterministic retrieval boundary. Query through the existing `ContextManager`/`RagClient` path, append a per-turn developer message containing either evidence or a strict miss/failure instruction, and retain the existing function tool for compatibility.

**Tech Stack:** Python 3.10+, LiveKit Agents, pytest, existing LightRAG HTTP client.

## Global Constraints

- Preserve the session's immutable `kb_id` binding and existing evidence audit log.
- Respect `rag_tool_mode="never"` without issuing a query.
- Do not invent knowledge-base facts when retrieval misses or fails.
- Do not delete files or directories.

---

### Task 1: Add deterministic pre-answer retrieval

**Files:**
- Modify: `liverag/agent/assistant.py`
- Test: `tests/agent/test_assistant.py`

**Interfaces:**
- Consumes: `ContextManager.query_knowledge_base(query, source, tool_name, turn_index)` and LiveKit `Agent.on_user_turn_completed(turn_ctx, new_message)`.
- Produces: `VoiceAssistant.on_user_turn_completed(...)` and a developer message added to the current `ChatContext`.

- [ ] **Step 1: Write failing lifecycle tests**

Add tests proving that `auto` mode queries once and injects hit evidence, miss/failure states inject anti-hallucination instructions, and `never` mode neither queries nor mutates the chat context.

- [ ] **Step 2: Run tests to verify the lifecycle behavior is absent**

Run: `.venv\Scripts\python.exe -m pytest tests/agent/test_assistant.py -q`

Expected: the new `on_user_turn_completed` assertions fail because no deterministic query exists.

- [ ] **Step 3: Implement the lifecycle hook**

Extract the final user text, ensure its turn is recorded, call the existing context manager with `source="pre_answer"`, and append a developer instruction with evidence, miss, or failure semantics. Keep exceptions contained so RAG downtime does not crash the voice session.

- [ ] **Step 4: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests/agent/test_assistant.py tests/context/test_manager.py tests/agent/tool/test_rag_client.py -q`

Expected: all focused tests pass.

### Task 2: Verify the end-to-end contract

**Files:**
- Test: `tests/agent/test_assistant.py`
- Test: `tests/rag/test_engine.py`

**Interfaces:**
- Consumes: the deterministic pre-answer hook and existing RAG Core structured evidence response.
- Produces: regression evidence that every enabled completed user turn reaches the RAG query boundary before generation.

- [ ] **Step 1: Run the broader backend suite**

Run: `.venv\Scripts\python.exe -m pytest tests/agent tests/context tests/rag tests/api -q`

Expected: all tests pass.

- [ ] **Step 2: Rebuild and inspect the running services**

Run: `docker compose up -d --build liverag-rag liverag-api liverag-agent`

Expected: all three services start, `liverag-rag` becomes healthy, and a new knowledge question produces a `/query/context` request plus a non-empty session `rag-context` audit record.

### Task 3: Restore the latest session transcript after navigation or refresh

**Files:**
- Modify: `E:/CS/project/LiveRAG-Fronted/agent-starter-react/components/voice/voice-experience.tsx`
- Test: frontend lint, typecheck, and production build

**Interfaces:**
- Consumes: browser `sessionStorage`, backend `/sessions/{session_id}/messages`, and `/sessions/{session_id}/turns`.
- Produces: a stable latest-session pointer and an idle-state transcript/RAG view restored from the backend.

- [ ] **Step 1: Persist the active room name**

When connection details are created, save `roomName` under a versioned session-storage key. Read it after client mount so a route remount or full refresh retains the backend session identifier.

- [ ] **Step 2: Reuse a single session-record loader**

Extract the existing message/turn polling logic into a hook used by both connected and idle states. Connected calls continue polling; idle restoration performs a final load and displays the saved record without claiming the audio connection is active.

- [ ] **Step 3: Validate the frontend**

Run: `corepack pnpm lint`, `corepack pnpm typecheck`, and `corepack pnpm build` in the frontend project.

Expected: all commands exit successfully; refreshing `/` or returning from `/knowledge` restores the latest transcript from the backend.
