# Volcengine STT Frame Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrument and verify every hop from LiveKit `AudioFrame` ingestion through the Volcengine WebSocket, while fixing only deterministic stream lifecycle and duplicate-error defects.

**Architecture:** Add a narrow subclass around the installed Volcengine BigModel STT stream. Preserve the plugin protocol implementation and the existing Interview pipeline, but own stream construction, input accounting, send/receive task lifecycle, structured logs, and response deduplication in repository code that can be tested.

**Tech Stack:** Python 3.11+, asyncio, aiohttp, LiveKit Agents, pytest.

## Global Constraints

- Keep Interview state machine, database, LLM, VAD, and TTS behavior unchanged.
- Do not log PCM content or credentials.
- Every diagnostic event carries a unique `stream_id`.
- Retain and await send/receive tasks; record all terminal reasons and exceptions.
- Emit or record each Volcengine error response at most once per stream.

---

### Task 1: Auditable Volcengine stream

**Files:**
- Create: `liverag/agent/volcengine_stt.py`
- Modify: `liverag/agent/providers.py`

**Interfaces:**
- Consumes: LiveKit `rtc.AudioFrame`, plugin `BigModelSTTOptions`, and plugin protocol packet builders/parser.
- Produces: `AuditedBigModelSTT.stream()` and `AuditedSpeechStream` with structured lifecycle counters.

- [ ] **Step 1: Write stream tests using fake WebSocket and input frames.**
- [ ] **Step 2: Run focused tests and confirm the missing audited classes fail.**
- [ ] **Step 3: Implement frame receipt/enqueue logging, explicit task ownership, packet logging, response deduplication, and close summary.**
- [ ] **Step 4: Replace only the STT constructor in `build_agent_session`; remove the global response monkeypatch.**
- [ ] **Step 5: Run focused tests and static checks.**

### Task 2: Regression tests and call-chain verification

**Files:**
- Create: `tests/agent/test_volcengine_stt.py`
- Modify: `tests/agent/test_providers.py`

**Interfaces:**
- Consumes: audited stream with fake WebSocket responses.
- Produces: regression coverage proving audio packet transmission and single handling of duplicate error bytes.

- [ ] **Step 1: Assert a pushed frame becomes an initialization packet followed by a non-final audio packet.**
- [ ] **Step 2: Assert repeated identical server error packets are logged/processed once.**
- [ ] **Step 3: Assert provider construction selects the audited implementation without changing its options.**
- [ ] **Step 4: Run all agent/provider tests and report the exact LiveKit-to-Volcengine call chain.**
