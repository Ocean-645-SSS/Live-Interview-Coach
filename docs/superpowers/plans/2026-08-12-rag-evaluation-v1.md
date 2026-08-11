# Interview Coach RAG Evaluation V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a practical 50-sample benchmark and an executable Retrieval → Generation → End-to-End evaluation pipeline that demonstrates correct recall, grounded output, accurate citations, and abstention.

**Architecture:** Keep benchmark data, metric calculation, model judging, production adapters, and reporting separate. Retrieval uses the existing RAG context endpoint and preserves ranked chunk IDs; generation and end-to-end outputs are judged against both retrieved context and benchmark facts. Pure metric functions remain fully offline and unit tested, while the CLI is the system boundary for live RAG and OpenAI-compatible services.

**Tech Stack:** Python 3.10+, Pydantic, asyncio, existing `RagClient`, OpenAI-compatible SDK, pytest.

## Global Constraints

- Start with exactly 50 samples, including 10 unanswerable cases.
- Report Recall@1, Recall@3, Recall@5, MRR, Faithfulness, Output Relevance, Abstention Accuracy, and Grounded Question Rate.
- Reuse the real resume, three JDs, project docs, question-bank material, and available Nowcoder-derived interview intelligence.
- Do not claim live scores until a saved, versioned report has been produced.
- Never bulk-delete files or directories.
- Validate only benchmark/model/network boundaries; trust internal typed interfaces.

---

### Task 1: Versioned benchmark contract and dataset

**Files:**
- Create: `evaluation/rag_schemas.py`
- Create: `evaluation/benchmark/rag-benchmark-v1.json`
- Test: `tests/evaluation/test_rag_dataset.py`

**Interfaces:**
- Produces: `RagBenchmarkDataset`, `RagBenchmarkSample`, and exactly 50 auditable samples split across explicit fact, cross-chunk, confusing, and no-answer categories.

- [ ] Write tests asserting schema validity, unique IDs, category/source coverage, 10 unanswerable samples, and required facts/document IDs.
- [ ] Run `pytest tests/evaluation/test_rag_dataset.py -v`; expect the missing schema/dataset failure.
- [ ] Implement strict Pydantic models and populate the dataset from repository sources with stable logical document IDs.
- [ ] Re-run the test; expect PASS.

### Task 2: Retrieval evaluation

**Files:**
- Create: `evaluation/evaluators/rag_evaluator.py`
- Test: `tests/evaluation/test_rag_metrics.py`

**Interfaces:**
- Consumes: ranked `RetrievedChunk` values and each sample's `expected_doc_ids`.
- Produces: per-sample hit ranks and aggregate `recall_at_1`, `recall_at_3`, `recall_at_5`, and `mrr`.

- [ ] Write failing tests covering first-rank, later-rank, multiple expected documents, and misses.
- [ ] Run `pytest tests/evaluation/test_rag_metrics.py -v`; expect import failure.
- [ ] Implement macro Recall@K (fraction of expected documents present) and reciprocal rank of the first relevant result.
- [ ] Re-run the test; expect PASS.

### Task 3: Generation and grounding judge

**Files:**
- Create: `evaluation/judges/rag_judge.py`
- Test: `tests/evaluation/test_rag_judge.py`

**Interfaces:**
- Consumes: query, retrieved context, generated output, expected/forbidden facts.
- Produces: structured 1–5 faithfulness and relevance scores, unsupported claims, answerability/abstention verdict, and grounded-question verdict.

- [ ] Write tests for strict JSON parsing and prompt inclusion of evidence/fact constraints.
- [ ] Run the focused test; expect failure.
- [ ] Implement one OpenAI-compatible structured judge call per generated result using a fixed rubric.
- [ ] Re-run the test; expect PASS.

### Task 4: Live pipeline runner and no-answer evaluation

**Files:**
- Create: `evaluation/run_rag_evaluation.py`
- Modify: `pyproject.toml`
- Test: `tests/evaluation/test_rag_cli.py`

**Interfaces:**
- Produces: `agent-eval-rag` CLI supporting dataset/limit/concurrency/K/model/base URL/API key and a retrieval-only mode.

- [ ] Write CLI tests for defaults, 1..50 limits, retrieval-only behavior, and configuration wiring.
- [ ] Run the focused test; expect failure.
- [ ] Connect the existing production `RagClient`, direct grounded generation, judge, concurrency control, and the 10 no-answer samples.
- [ ] Re-run the test; expect PASS.

### Task 5: End-to-end question grounding and reports

**Files:**
- Create: `evaluation/rag_reporting.py`
- Test: `tests/evaluation/test_rag_reporting.py`
- Modify: `evaluation/README.md`

**Interfaces:**
- Produces: timestamped JSON and Markdown reports aligned with Answer/Behavior Evaluation, including all six headline metrics and per-sample evidence.

- [ ] Write a report snapshot test containing metric labels and unsupported-claim details.
- [ ] Run the focused test; expect failure.
- [ ] Implement report rendering and document live and smoke commands, provenance limitations, and score-publication rules.
- [ ] Run all RAG evaluation tests and Ruff; expect PASS.

## Self-review

- Spec coverage: all six requested tasks are covered; Citation Accuracy is represented by grounded evidence plus unsupported claims and remains visible per sample rather than adding another headline metric.
- Placeholder scan: no TBD/TODO/"implement later" steps.
- Type consistency: benchmark → retrieved chunks → judgments → report uses the schema names defined in Task 1.
