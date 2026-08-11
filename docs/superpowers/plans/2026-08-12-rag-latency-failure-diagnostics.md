# RAG Latency Failure Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve a latency report when individual RAG turns fail, with one structured diagnostic per failed sample and all successful Normal/RAG timing samples retained.

**Architecture:** Keep the production `VoiceAssistant → ContextManager → RagClient → /query/context` call unchanged. The benchmark tags each operation with one of five allowed stages, wraps final exhausted retry failures into a diagnostic record, and writes successful runs and failed samples together to JSON and Markdown.

**Tech Stack:** Python 3.10, asyncio, LiveKit Agents, pytest, Ruff.

## Global Constraints

- Do not modify `liverag/` production logic, RAG options, KB/session setup, or benchmark questions.
- Do not mock TTFT, TTFA, E2E, RAG retrieval, LLM, or TTS.
- Failure stages are exactly `request setup`, `RAG retrieval`, `LLM`, `TTS`, or `benchmark runner`.

---

### Task 1: Persist per-sample latency failure diagnostics

**Files:**
- Modify: `evaluation/latency_benchmark.py`
- Modify: `evaluation/run_latency_benchmark.py`
- Test: `tests/evaluation/test_latency_benchmark.py`

**Interfaces:**
- Produces `LatencyFailure(sample_id, scenario, failure_stage, exception, http_status, elapsed_ms, timed_out)`.
- `LatencyBenchmarkReport` writes `runs` and `failures`; Markdown renders a `RAG Failures` table.

- [ ] **Step 1: Write failing render tests**

```python
failure = LatencyFailure("rag-001", "rag", "RAG retrieval", "RagClientError", 503, 15000.0, True)
assert "rag-001" in render_markdown(LatencyBenchmarkReport(failures=[failure]))
assert "RAG retrieval" in markdown
```

- [ ] **Step 2: Run the focused test**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py -q`

Expected: FAIL because failures are not represented.

- [ ] **Step 3: Add stage-aware benchmark wrappers**

```python
try:
    await assistant.on_user_turn_completed(chat_ctx, user_message)
    rag_record = _verify_rag_request(store, session_id)
except BaseException as exc:
    raise LatencyTurnError("RAG retrieval", elapsed_ms, exc, rag_record) from exc
```

Create a `LatencyFailure` after retries are exhausted. Use the persisted `rag_context.jsonl` error object to extract `status_code`; use exception type/message/cause to set `timed_out`.

- [ ] **Step 4: Continue after a failed sample**

```python
try:
    run, providers = await _run_turn_with_retries(...)
except LatencyTurnError as exc:
    failures.append(exc.to_failure(sample_id))
    continue
```

- [ ] **Step 5: Verify**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py -q; .\\.venv\\Scripts\\python.exe -m ruff check evaluation/latency_benchmark.py evaluation/run_latency_benchmark.py tests/evaluation/test_latency_benchmark.py`

Expected: PASS.

### Task 2: Run real combined benchmark and inspect evidence

**Files:**
- Output: `evaluation/reports/*-latency-benchmark.json`
- Output: `evaluation/reports/*-latency-benchmark.md`

- [ ] **Step 1: Run the unchanged two-scenario command**

Run: `.\\.venv\\Scripts\\python.exe -m evaluation.run_latency_benchmark`

Expected: report contains twenty completed samples per scenario or an auditable `RAG Failures` section with every failed sample.

- [ ] **Step 2: Verify actual RAG evidence**

For each RAG session, inspect `rag_context.jsonl` for `kb_id`, `session_id`, `profile=voice`, options, retrieval metrics, result error, and context/chunk count.
