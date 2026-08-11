# RAG Retrieval Failure Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn every `Recall@5=0` answerable retrieval result into an auditable human-review artifact.

**Architecture:** The analyzer reads a saved RAG evaluation report and the independent benchmark corpus; it does not query or mutate the live RAG system. Each failure combines the original query, expected logical documents and full corpus evidence, ranked actual chunks, their returned scores, and a review checklist that separates label, chunking, wording, and multi-document causes.

**Tech Stack:** Python 3.10+, Pydantic, JSON, pytest.

## Global Constraints

- Include only answerable samples whose Recall@5 equals 0.
- Never infer or fabricate a similarity score when the RAG service did not return one.
- Use the corpus text as Gold evidence; do not reconstruct it from expected facts.
- Generate JSON and Markdown artifacts without altering the original evaluation report.

---

### Task 1: Failure analysis model and renderer

**Files:** Create `evaluation/rag_failure_analysis.py`; test `tests/evaluation/test_rag_failure_analysis.py`.

**Interfaces:** `build_failure_analysis(report, corpus)` returns `RagFailureAnalysis`; `render_failure_analysis_markdown(analysis)` renders a reviewer-friendly report.

- [ ] Write a fixture with one failure and one hit; assert only the failure is included, full Gold evidence appears, and null score renders as `N/A (not returned)`.
- [ ] Run `pytest tests/evaluation/test_rag_failure_analysis.py -v`; expect an import failure.
- [ ] Implement typed conversion and Markdown rendering with top-five rank, chunk/document IDs, score, and content.
- [ ] Re-run the focused test; expect PASS.

### Task 2: Reproducible CLI and documentation

**Files:** Create `evaluation/run_rag_failure_analysis.py`; modify `pyproject.toml`, `evaluation/README.md`; test `tests/evaluation/test_rag_failure_analysis_cli.py`.

**Interfaces:** `agent-eval-rag-failures --report PATH [--corpus PATH] [--output-dir PATH]` writes paired JSON/Markdown artifacts.

- [ ] Test CLI parsing and deterministic output names.
- [ ] Run the focused test; expect failure.
- [ ] Implement the read-only report/corpus runner and document the review workflow.
- [ ] Run all evaluation tests and Ruff; expect PASS.

## Self-review

- The output contains every requested field: query, expected chunks/evidence, actual Top5, returned scores, and original text.
- The analyzer preserves an explicit distinction between missing scores and low scores.
- No live RAG request, deletion, or index change is made.
