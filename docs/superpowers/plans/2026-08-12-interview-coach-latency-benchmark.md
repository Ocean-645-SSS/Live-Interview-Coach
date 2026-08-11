# Interview Coach Latency Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repeatable 20-run Normal/RAG latency benchmark that measures the real production LLM and TTS path, saves every run, and renders P50/P95 Markdown.

**Architecture:** The benchmark creates the same production `AgentSession` providers, runs the `VoiceAssistant.on_user_turn_completed` lifecycle hook for RAG prefetch, then consumes the configured LiveKit LLM stream and forwards its textual chunks into the configured DashScope TTS stream. A monotonic clock records request start, first textual LLM delta, first emitted playable audio frame, and final TTS stream completion.

**Tech Stack:** Python 3.10+, LiveKit Agents, OpenAI-compatible LLM, DashScope realtime TTS, existing `ContextStore`, `RagClient`, pytest.

## Global Constraints

- Reuse the production `VoiceAssistant`, `ContextManager`, `RagClient`, `build_agent_session`, LLM, and TTS configuration.
- Do not mock any metric in the runnable benchmark.
- Do not alter production request behavior or latency settings.
- Normal runs use `rag_tool_mode="never"`; RAG runs execute `VoiceAssistant.on_user_turn_completed`, which calls the real RAG context endpoint.
- Persist the timestamp-derived raw values for every run and generate P50/P95 from those values.
- Execute 20 runs per scenario by default.

---

### Task 1: Add latency models and report renderer

**Files:**
- Create: `evaluation/latency_benchmark.py`
- Test: `tests/evaluation/test_latency_benchmark.py`

**Interfaces:**
- Produces: `LatencyRun`, `LatencyBenchmarkReport`, `percentile_ms(values, percentile)`, and `render_markdown(report)`.
- Consumes: per-run `request_start`, `first_token`, `first_audio`, and `response_end` timestamps recorded by Task 2.

- [ ] **Step 1: Write the failing statistics and Markdown tests**

```python
def test_render_markdown_reports_p50_and_p95_for_both_scenarios() -> None:
    report = LatencyBenchmarkReport(runs=[
        LatencyRun("normal", 1, 0.0, 0.1, 0.2, 0.3),
        LatencyRun("rag", 1, 0.0, 0.2, 0.4, 0.6),
    ])
    markdown = render_markdown(report)
    assert "## Normal" in markdown
    assert "TTFT P50: 100.0 ms" in markdown
    assert "## RAG" in markdown
    assert "E2E P95: 600.0 ms" in markdown
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py -q`
Expected: FAIL because `evaluation.latency_benchmark` does not exist.

- [ ] **Step 3: Implement immutable run data, nearest-rank percentile calculation, JSON serialization, and the exact required Markdown headings**

```python
def percentile_ms(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    index = ceil((percentile / 100) * len(ordered)) - 1
    return round(ordered[index], 1)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py -q`
Expected: PASS.

### Task 2: Add a CLI runner that drives the production request path

**Files:**
- Create: `evaluation/run_latency_benchmark.py`
- Modify: `pyproject.toml`
- Modify: `tests/evaluation/test_latency_benchmark.py`

**Interfaces:**
- Consumes: `AppSettings`, selected production KB metadata, `LatencyRun`, and `LatencyBenchmarkReport`.
- Produces: `agent-benchmark-latency` CLI command and `<timestamp>-latency-benchmark.json` / `.md` report files.

- [ ] **Step 1: Write failing tests for parser defaults and output bundle naming**

```python
def test_parser_defaults_to_twenty_runs_per_scenario() -> None:
    args = build_parser().parse_args([])
    assert args.runs == 20

def test_write_report_bundle_writes_json_and_markdown(tmp_path: Path) -> None:
    paths = write_report_bundle(LatencyBenchmarkReport(runs=[]), tmp_path, "sample")
    assert paths["json"].name == "sample-latency-benchmark.json"
    assert paths["markdown"].name == "sample-latency-benchmark.md"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py -q`
Expected: FAIL because the runner functions do not exist.

- [ ] **Step 3: Implement `run_turn` with four monotonic timestamps**

```python
request_start = time.perf_counter()
await assistant.on_user_turn_completed(chat_ctx, user_message)
async for chunk in llm.chat(chat_ctx=chat_ctx, tools=[]):
    if text and first_token is None:
        first_token = time.perf_counter()
    tts_stream.push_text(text)
tts_stream.end_input()
await audio_consumer
response_end = time.perf_counter()
```

The audio consumer must set `first_audio` on the first `SynthesizedAudio` event from the production `DashScopeRealtimeTTS` stream. The benchmark must fail the run if the LLM emits no text or TTS emits no playable frame.

- [ ] **Step 4: Implement scenario setup and output**

```python
for scenario, rag_tool_mode, question in (
    ("normal", "never", args.normal_question),
    ("rag", "auto", args.rag_question),
):
    for run_number in range(1, args.runs + 1):
        runs.append(await run_turn(...))
```

Create benchmark-only `ContextStore` sessions under the requested output directory; use the production metadata-selected KB and production `RagClient` endpoint/configuration. Record model, KB, scenario questions, and timestamps in raw JSON.

- [ ] **Step 5: Register and verify the CLI**

```toml
agent-benchmark-latency = "evaluation.run_latency_benchmark:main"
```

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py tests/agent/test_assistant.py -q`
Expected: PASS.

### Task 3: Validate static quality and runnable help

**Files:**
- Verify: `evaluation/latency_benchmark.py`
- Verify: `evaluation/run_latency_benchmark.py`
- Verify: `tests/evaluation/test_latency_benchmark.py`

- [ ] **Step 1: Run targeted tests**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py tests/agent/test_assistant.py -q`
Expected: PASS.

- [ ] **Step 2: Run lint**

Run: `.\\.venv\\Scripts\\python.exe -m ruff check evaluation/latency_benchmark.py evaluation/run_latency_benchmark.py tests/evaluation/test_latency_benchmark.py`
Expected: exit code 0.

- [ ] **Step 3: Verify CLI availability without making external calls**

Run: `.\\.venv\\Scripts\\python.exe -m evaluation.run_latency_benchmark --help`
Expected: exit code 0 and options including `--runs`, `--normal-question`, `--rag-question`, and `--output-dir`.
