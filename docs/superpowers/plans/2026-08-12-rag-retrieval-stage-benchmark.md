# RAG Retrieval Stage Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Identify whether the real RAG retrieval latency is spent in query rewrite, search, or rerank; then make and validate only the smallest quality-preserving optimization for the dominant existing stage.

**Architecture:** The production `RagEngine.query_context()` already executes query rewrite then invokes LightRAG search. It will expose those existing timings under stable names. The voice path passes `rewrite_followup=false` and `enable_rerank=false`, so rerank is not an executed stage and will not receive a fabricated metric. The existing latency benchmark reads the persisted production RAG metrics per RAG run and renders P50/P95.

**Tech Stack:** Python 3.10+, FastAPI, LightRAG, existing `RagEngine`, `RagClient`, latency benchmark, pytest.

## Global Constraints

- Do not change the frozen RAG benchmark dataset or evaluation protocol.
- Do not add a query rewrite or rerank operation when it is disabled in the production voice request.
- Preserve existing `top_k`, `chunk_top_k`, `enable_rerank`, retrieval mode, context size, Recall@5, Faithfulness, Output Relevance, and Abstention Accuracy.
- Record only existing production execution boundaries; do not mock benchmark results.
- Implement an optimization only after the baseline identifies a single dominant measured stage.

---

### Task 1: Expose existing RAG execution stages with stable metric names

**Files:**
- Modify: `liverag/rag/engine.py`
- Modify: `tests/rag/test_engine.py`

**Interfaces:**
- Produces `metrics["query_rewrite_ms"]` around the existing `rewrite_query()` call.
- Produces `metrics["search_ms"]` around the existing `rag.aquery_llm()` call.
- Does not produce a rerank metric when `enable_rerank` is false.

- [ ] **Step 1: Update the engine metrics test**

```python
for key in ("query_rewrite_ms", "search_ms", "extraction_ms", "evidence_gate_ms"):
    assert key in metrics
assert "rerank_ms" not in metrics
assert metrics["request_total_ms"] >= metrics["search_ms"]
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/rag/test_engine.py -q`
Expected: FAIL because the stable stage keys do not exist.

- [ ] **Step 3: Rename only the existing timing result keys**

```python
query_rewrite_started = time.perf_counter()
effective_query, rewritten = rewrite_query(query, conversation)
query_rewrite_ms = elapsed(query_rewrite_started)

search_started = time.perf_counter()
result = await rag.aquery_llm(effective_query, param=param)
search_ms = elapsed(search_started)
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/rag/test_engine.py -q`
Expected: PASS.

### Task 2: Surface real RAG metrics in the latency benchmark

**Files:**
- Modify: `evaluation/latency_benchmark.py`
- Modify: `evaluation/run_latency_benchmark.py`
- Modify: `tests/evaluation/test_latency_benchmark.py`

**Interfaces:**
- `LatencyRun` adds nullable `query_rewrite_ms` and `search_ms` fields.
- RAG runs read the persisted `rag_context.jsonl` `metrics` from the actual request; Normal leaves the fields absent.
- Markdown renders RAG stage P50/P95 and states that rerank is not executed for the configured voice path.

- [ ] **Step 1: Write failing report and runner tests**

```python
assert "Query rewrite P50:" in markdown
assert "Search P95:" in markdown
assert "Rerank: not executed" in markdown
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py -q`
Expected: FAIL because stage fields and report lines do not exist.

- [ ] **Step 3: Add raw fields and render only executed stages**

```python
record = _verify_rag_request(store, session_id)
query_rewrite_ms = float(record["metrics"]["query_rewrite_ms"])
search_ms = float(record["metrics"]["search_ms"])
```

- [ ] **Step 4: Run targeted tests and lint**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_latency_benchmark.py tests/rag/test_engine.py -q`
Expected: PASS.

Run: `.\\.venv\\Scripts\\python.exe -m ruff check liverag/rag/engine.py evaluation/latency_benchmark.py evaluation/run_latency_benchmark.py tests/evaluation/test_latency_benchmark.py tests/rag/test_engine.py`
Expected: exit code 0.

### Task 3: Baseline, optimize the dominant stage, and validate quality

**Files:**
- Create at runtime: two `evaluation/reports/*-latency-benchmark.{json,md}` bundles.
- Verify: `evaluation/baselines/rag-benchmark-v2-retrieval-baseline.md`
- Verify: `tests/evaluation/test_rag_alignment_v2.py`, `tests/evaluation/test_rag_metrics.py`

- [ ] **Step 1: Run the 20×2 baseline benchmark and read stage P50/P95**

Run: `.\\.venv\\Scripts\\python.exe -m evaluation.run_latency_benchmark`
Expected: 20 Normal and 20 RAG records with stage values in every RAG record.

- [ ] **Step 2: Select the largest existing stage**

Use only the P50/P95 from Step 1. Do not optimize rewrite or rerank if either stage is not executed.

- [ ] **Step 3: Implement the smallest optimization for that stage and add a regression test**

The optimization must preserve all query options and returned evidence order/content. It must not change benchmark data, retrieval thresholds, RAG mode, top-k, chunk-top-k, rerank setting, or grounding policy.

- [ ] **Step 4: Run retrieval-quality regression tests**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_rag_alignment_v2.py tests/evaluation/test_rag_metrics.py tests/rag/test_engine.py -q`
Expected: PASS.

- [ ] **Step 5: Run the same 20×2 benchmark again and compare**

Compare retrieval P50/P95, RAG TTFT P50/P95, and RAG TTFA P50/P95 using the two report bundles.
