# Latency Normal Reliability and LLM Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete twenty successful Normal benchmark samples despite a transient external TTS/LLM stream cancellation, and make slow LLM requests plus LiveKit retry states observable.

**Architecture:** Keep the production voice request unchanged. The benchmark retries a whole failed real turn up to a bounded count, rebuilding providers only after a failed attempt. Per turn, attach the existing LiveKit LLM error event to record recoverable retry status and log a warning if first token has not arrived after five seconds.

**Tech Stack:** Python 3.10, asyncio, LiveKit Agents, pytest, Ruff.

## Global Constraints

- Do not mock latency metrics or change Normal/RAG questions, models, or timing boundaries.
- Retry only failed benchmark turns; every accepted sample remains a complete real LLM + TTS turn.
- Do not change production RAG behavior.

---

### Task 1: Benchmark turn retry and LLM status logging

**Files:**
- Modify: `evaluation/run_latency_benchmark.py`
- Test: `tests/evaluation/test_latency_benchmark.py`

**Interfaces:**
- Produces `run()` with exactly `--runs` completed samples per selected scenario.
- Produces logs `latency_benchmark.turn.retry`, `latency_benchmark.llm.slow`, `latency_benchmark.llm.retry`, and `latency_benchmark.llm.first_token`.

- [ ] **Step 1: Write failing tests**

```python
async def test_run_retries_a_cancelled_normal_turn_until_it_completes():
    # First fake turn raises asyncio.CancelledError; second returns a complete LatencyRun.
    # Assert the successful run is recorded and a replacement provider is built.

async def test_normal_turn_logs_a_slow_llm_and_recoverable_provider_retry(caplog):
    # A fake LLM emits recoverable error then delays its first token past the threshold.
    # Assert both status messages exist.
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py -q`

Expected: FAIL because retry/status behavior does not exist.

- [ ] **Step 3: Implement minimum retry and status logging**

```python
for attempt in range(1, MAX_TURN_ATTEMPTS + 1):
    try:
        return await run_turn(...)
    except (asyncio.CancelledError, Exception) as exc:
        logger.warning("latency_benchmark.turn.retry", extra={...})
        await _close_failed_providers(providers)
        providers = build_agent_session(settings)
```

Attach a temporary LiveKit LLM `error` handler while consuming a turn. Emit structured retry status from its `recoverable` flag. Start a five-second task before LLM stream consumption; cancel it once first token arrives and log first-token duration.

- [ ] **Step 4: Run focused verification**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py -q; .\\.venv\\Scripts\\python.exe -m ruff check evaluation/run_latency_benchmark.py tests/evaluation/test_latency_benchmark.py`

Expected: PASS.

### Task 2: Verify related RAG timing behavior remains intact

**Files:**
- Test: `tests/rag/test_engine.py`

- [ ] **Step 1: Run regression tests**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py tests/rag/test_engine.py -q`

Expected: all pass.
