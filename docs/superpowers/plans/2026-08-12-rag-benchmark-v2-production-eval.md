# RAG Benchmark v2 Production Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze the fact-aligned RAG Benchmark v2, record a retrieval baseline, and evaluate the real production answer path before applying evidence-led production fixes.

**Architecture:** Retrieval evaluation continues to call `/query/data` so Recall and MRR observe the raw ranked candidates. Generation evaluation calls `/query/answer`, which is the production evidence-gated retrieval-and-generation path; an independent judge receives only the production context and output. A lock manifest hashes the benchmark and corpus so later runs cannot silently change the test set.

**Tech Stack:** Python 3.10, Pydantic, aiohttp, OpenAI-compatible judge, LiveRAG FastAPI service, pytest.

## Global Constraints

- Freeze `rag-benchmark-v2.json` and `rag-corpus-v2.json`; later retrieval/generation changes must reuse their exact hashes.
- Keep the `default` retrieval profile and `top_k=5` unchanged for the baseline and regressions.
- Use `/query/answer` for production generation evaluation, never an evaluator-owned generation prompt.
- Do not modify production retrieval/generation behavior until failure analysis identifies a low-score pattern.

---

### Task 1: Lock the v2 benchmark contract

**Files:**
- Create: `evaluation/benchmark/rag-benchmark-v2.lock.json`
- Create: `evaluation/benchmark_lock.py`
- Modify: `evaluation/run_rag_evaluation.py`
- Test: `tests/evaluation/test_rag_benchmark_lock.py`

**Interfaces:**
- Produces `verify_benchmark_lock(benchmark_path: Path, corpus_path: Path) -> None`.
- Consumes the two v2 JSON source files and checks their SHA-256 values before evaluation.

- [ ] **Step 1: Write a test for the checked-in v2 files and a modified temporary benchmark.**
- [ ] **Step 2: Implement SHA-256 lock verification and invoke it in the default v2 evaluator path.**
- [ ] **Step 3: Run `pytest tests/evaluation/test_rag_benchmark_lock.py -v`.**

### Task 2: Preserve the formal retrieval baseline

**Files:**
- Create: `evaluation/baselines/rag-benchmark-v2-retrieval-baseline.md`
- Modify: `evaluation/rag_reporting.py`
- Test: `tests/evaluation/test_rag_reporting.py`

**Interfaces:**
- Records `Recall@1`, `Recall@3`, `Recall@5`, and `MRR` with source-report and locked-data provenance.
- Report identifies whether generation uses the production answer path.

- [ ] **Step 1: Record the completed default-profile run from `2026-08-12T07-44-45.945577_00-00-rag-evaluation.json`.**
- [ ] **Step 2: Expose production-generation provenance in the report.**
- [ ] **Step 3: Run reporting tests.**

### Task 3: Evaluate the real production RAG answer path

**Files:**
- Modify: `evaluation/run_rag_evaluation.py`
- Modify: `evaluation/rag_schemas.py`
- Test: `tests/evaluation/test_rag_production_generation.py`

**Interfaces:**
- Produces `_retrieve_production_answer(session, args, query) -> tuple[str, str]`, returning production context and answer from `/query/answer`.
- `RagEvaluationReport.generation_path` records `production_query_answer` when generation is enabled.

- [ ] **Step 1: Write an aiohttp-mocked test that checks `/query/answer` is used and its answer/context are retained.**
- [ ] **Step 2: Replace evaluator-owned `_generate` and `/query/context` calls with `/query/answer`.**
- [ ] **Step 3: Obtain the live RAG model name from `/readyz` for truthful report provenance.**
- [ ] **Step 4: Run evaluator tests.**

### Task 4: Produce actionable low-score failure analysis

**Files:**
- Create: `evaluation/rag_generation_failure_analysis.py`
- Create: `evaluation/run_rag_generation_failure_analysis.py`
- Modify: `pyproject.toml`
- Test: `tests/evaluation/test_rag_generation_failure_analysis.py`

**Interfaces:**
- Produces a JSON/Markdown bundle for samples with faithfulness/relevance at most 3 or incorrect no-answer abstention.
- Each case contains query, Gold evidence, raw Top5, production context/output, judge findings, and a routing recommendation.

- [ ] **Step 1: Write test data with one grounded retrieval/generation failure and one correct sample.**
- [ ] **Step 2: Implement low-score selection and Markdown evidence rendering.**
- [ ] **Step 3: Run the new analysis tests.**

### Task 5: Run baseline, analyze, repair only if evidence warrants it, and regress

**Files:**
- Generated: `evaluation/reports/*rag-evaluation.{json,md}`
- Generated: `evaluation/reports/*generation-failure-analysis.{json,md}`

- [ ] **Step 1: Run the full 50-sample production evaluation against `rag-benchmark-v2`.**
- [ ] **Step 2: Generate the low-score analysis bundle.**
- [ ] **Step 3: If a consistent production defect is shown, modify only the implicated production grounding/prompt/evidence-gate code.**
- [ ] **Step 4: Re-run the identical frozen benchmark and report before/after metrics.**

## Self-Review

- Benchmark freeze is covered by Task 1.
- Formal Retrieval baseline is covered by Task 2.
- Faithfulness, relevance, and abstention against `/query/answer` are covered by Task 3 and Task 5.
- Failure-led production repair and same-data regression are covered by Task 4 and Task 5.
