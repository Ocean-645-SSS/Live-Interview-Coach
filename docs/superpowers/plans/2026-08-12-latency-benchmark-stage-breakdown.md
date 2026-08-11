# Latency Benchmark Stage Breakdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add P50/P95 stage breakdowns for real RAG retrieval, LLM first token, and first-token-to-first-audio time to the existing Normal/RAG latency benchmark.

**Architecture:** The benchmark retains its existing monotonic timestamps and production calls. It records the duration of `VoiceAssistant.on_user_turn_completed()` only for RAG retrieval, measures LLM dispatch to first emitted text chunk, and measures first emitted text chunk to the first playable TTS frame. Normal has no RAG call, so `rag_retrieval_ms` is explicitly zero.

**Tech Stack:** Python 3.10+, LiveKit Agents, production `VoiceAssistant`, `ContextManager`, `RagClient`, OpenAI-compatible LLM, DashScope realtime TTS, pytest.

## Global Constraints

- Do not change production agent, RAG, LLM, TTS, prompt, or provider behavior.
- Reuse the existing Normal/RAG 20-run benchmark runner and questions.
- Do not mock final metrics when running the benchmark.
- `rag_retrieval_ms` measures only the awaited real RAG pre-answer hook; it is `0.0` in Normal.
- `llm_first_token_ms` measures LLM request dispatch to the first non-empty production text delta.
- `tts_first_audio_ms` measures first non-empty production text delta to the first playable production audio frame; it is the observed TTFT-to-TTFA interval, not an assumption about internal provider work.
- Markdown reports only P50/P95 for the three added metrics; JSON retains raw values per run.

---

### Task 1: Extend the raw result model and Markdown report

**Files:**
- Modify: `evaluation/latency_benchmark.py`
- Modify: `tests/evaluation/test_latency_benchmark.py`

**Interfaces:**
- `LatencyRun` adds required `rag_retrieval_ms`, `llm_first_token_ms`, and `tts_first_audio_ms` floats.
- `render_markdown(report)` renders the three P50/P95 pairs under each scenario.

- [ ] **Step 1: Write failing report assertions**

```python
assert "RAG retrieval P50: 0.0 ms" in markdown
assert "LLM first token P50: 100.0 ms" in markdown
assert "TTS first audio P95: 200.0 ms" in markdown
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py -q`
Expected: FAIL because the report has no stage metrics.

- [ ] **Step 3: Add the three raw metrics and P50/P95 report lines**

```python
@dataclass(frozen=True)
class LatencyRun:
    rag_retrieval_ms: float
    llm_first_token_ms: float
    tts_first_audio_ms: float
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py -q`
Expected: PASS.

### Task 2: Capture stage boundaries in the existing production benchmark runner

**Files:**
- Modify: `evaluation/run_latency_benchmark.py`
- Modify: `tests/evaluation/test_latency_benchmark.py`

**Interfaces:**
- `run_turn()` returns a `LatencyRun` whose stage values use the production call boundaries.

- [ ] **Step 1: Write failing runner assertions**

```python
assert result.rag_retrieval_ms == 0.0
assert result.llm_first_token_ms >= 0.0
assert result.tts_first_audio_ms >= 0.0
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py -q`
Expected: FAIL because the stage attributes do not exist.

- [ ] **Step 3: Record exact real boundaries without changing call order**

```python
rag_started = time.perf_counter()
await assistant.on_user_turn_completed(chat_ctx, user_message)
rag_retrieval_ms = elapsed(rag_started) if scenario == "rag" else 0.0

llm_started = time.perf_counter()
stream = llm_provider.chat(chat_ctx=chat_ctx, tools=[])
# On first text: llm_first_token_ms = elapsed(llm_started)
# On first audio: tts_first_audio_ms = elapsed(first_token)
```

- [ ] **Step 4: Run targeted tests and lint**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py tests/agent/test_assistant.py -q`
Expected: PASS.

Run: `.\\.venv\\Scripts\\python.exe -m ruff check evaluation/latency_benchmark.py evaluation/run_latency_benchmark.py tests/evaluation/test_latency_benchmark.py`
Expected: exit code 0.

### Task 3: Execute and inspect the real 20-run benchmark

**Files:**
- Create at runtime: `evaluation/reports/<timestamp>-latency-benchmark.json`
- Create at runtime: `evaluation/reports/<timestamp>-latency-benchmark.md`

- [ ] **Step 1: Run the real benchmark with the existing defaults**

Run: `.\\.venv\\Scripts\\python.exe -m evaluation.run_latency_benchmark`
Expected: exactly 20 Normal and 20 RAG raw records, then JSON and Markdown output paths.

- [ ] **Step 2: Verify run counts and report stage lines**

Run: `.\\.venv\\Scripts\\python.exe -c "import json; from pathlib import Path; p=max(Path('evaluation/reports').glob('*-latency-benchmark.json'), key=lambda x: x.stat().st_mtime); r=json.loads(p.read_text(encoding='utf-8'))['runs']; print(len(r), sum(x['scenario']=='normal' for x in r), sum(x['scenario']=='rag' for x in r))"`
Expected: `40 20 20`.
