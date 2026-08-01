# Logical Voice Turns And RAG Hit UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent short pauses from splitting one spoken question into multiple RAG turns, and show retrieval UI only when a finalized logical turn has usable evidence.

**Architecture:** Use a local deterministic LiveKit-compatible semantic turn detector to recognize unfinished Chinese sentence structures and keep collecting STT segments, with less aggressive endpoint delays as a safety window. Keep RAG prefetch at `on_user_turn_completed`, so retrieval starts only after the logical turn is committed. Derive visible RAG cards directly from server turns by filtering for `status === "hit"` and non-empty evidence, avoiding duplicate client state.

**Tech Stack:** Python 3.10, LiveKit Agents 1.4, LiveKit multilingual turn detector, pytest, React 19, Next.js 15, TypeScript.

## Global Constraints

- Do not delete files or directories in bulk.
- Do not show miss, failed, or not-queried RAG cards in the user-facing conversation.
- Do not trigger RAG from interim or STT-final fragments; retain `on_user_turn_completed` as the retrieval boundary.
- Preserve current session/message/RAG API contracts.

---

### Task 1: Semantic logical-turn detection

**Files:**
- Modify: `liverag/agent/providers.py`
- Create: `tests/agent/test_providers.py`

**Interfaces:**
- Consumes: `livekit.plugins.turn_detector.multilingual.MultilingualModel`
- Produces: `AgentSession(turn_detection=SemanticTurnDetector(), min_endpointing_delay=0.8, max_endpointing_delay=2.5)`

- [ ] **Step 1: Write a failing provider-construction test**

Patch provider constructors and assert that `build_agent_session()` supplies the local semantic detector, keeps `preemptive_generation=False`, and uses endpoint delays `0.8` and `2.5`. Cover incomplete and complete Chinese utterances in `tests/agent/test_turn_detector.py`.

- [ ] **Step 2: Run the focused test and confirm failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_providers.py -q --basetemp .pytest-runs\logical-turns`

Expected: FAIL because `turn_detection` is initially the string `"stt"` and delays are `0.1`/`0.5`.

- [ ] **Step 3: Configure semantic end-of-utterance detection**

Create `SemanticTurnDetector`, replace `turn_detection="stt"` with `turn_detection=SemanticTurnDetector()`, change `min_endpointing_delay` to `0.8`, `max_endpointing_delay` to `2.5`, and increase the STT `end_window_size` from `900` to `1200` milliseconds. The local detector avoids a runtime dependency on downloadable model weights.

- [ ] **Step 4: Run provider and assistant tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_providers.py tests/agent/test_assistant.py -q --basetemp .pytest-runs\logical-turns`

Expected: PASS. RAG remains attached to `on_user_turn_completed`, so incomplete fragments cannot create independent RAG requests.

### Task 2: Hit-only retrieval evidence UI

**Files:**
- Modify: `E:/CS/project/LiveRAG-Fronted/agent-starter-react/components/voice/rag-turn-panel.tsx`

**Interfaces:**
- Consumes: `SessionTurn[]`
- Produces: cards only for turns where `turn.rag.status === "hit"`, `turn.rag.has_context === true`, and at least one evidence chunk exists

- [ ] **Step 1: Replace status-driven rendering with hit-derived rendering**

Use one memo-free `filter` pass because the list is capped at 100 turns. Return `null` when no usable hits exist. Remove miss/failure copy and render only the `已命中` badge, source names, latency, and up to three evidence chunks.

- [ ] **Step 2: Verify frontend quality gates**

Run: `corepack pnpm lint; corepack pnpm typecheck; corepack pnpm build`

Expected: all commands exit `0`; miss/failed turns remain available through APIs but do not render in the conversation.

### Task 3: Integrated regression and deployment

**Files:**
- Verify: `liverag/agent/providers.py`
- Verify: `liverag/agent/assistant.py`
- Verify: frontend voice components

**Interfaces:**
- Consumes: backend and frontend Docker build contexts
- Produces: running API, Agent, RAG, LiveKit, and frontend services

- [ ] **Step 1: Run focused backend regression**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent -q --basetemp .pytest-runs\logical-turns-final`

Expected: all agent tests pass.

- [ ] **Step 2: Build and restart services**

Run: `docker compose up -d --build`

Expected: all services start and `liverag-rag` becomes healthy.

- [ ] **Step 3: Perform read-only deployment checks**

Check `docker compose ps`, API `/health`, and verify the Agent container includes `MultilingualModel`, `min_endpointing_delay=0.8`, and `max_endpointing_delay=2.5`.

- [ ] **Step 4: Manual acceptance scenario**

Say “我想问一下这个项目”，pause for roughly one second, then continue “涉及了哪些技术”. Expected: one user turn, one RAG query/card only if hit, and one answer based on the combined question.
