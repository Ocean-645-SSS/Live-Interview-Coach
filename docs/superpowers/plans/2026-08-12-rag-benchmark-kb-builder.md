# RAG Benchmark Knowledge Base Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a dedicated, reproducible `rag-benchmark-v1` knowledge base before evaluation.

**Architecture:** A reviewed corpus artifact is separate from labels so evaluation cannot derive indexed text from `expected_facts` at runtime. A CLI creates the dedicated KB, uploads each source with a stable document ID, polls asynchronous indexing jobs, and verifies that every required ID is processed. The evaluator rejects empty or incompatible KBs before scoring.

**Tech Stack:** Python 3.10+, Pydantic, aiohttp, pytest, existing LiveRAG HTTP API.

## Global Constraints

- Never delete or recursively replace a knowledge base or local files.
- Use `rag-benchmark-v1`, not the user's `default` personal knowledge base.
- Preserve source provenance and stable document IDs.
- Do not construct corpus text from benchmark expected facts at runtime.
- Only validate file/HTTP responses at system boundaries.

---

### Task 1: Reviewed corpus contract

**Files:** Create `evaluation/benchmark/rag-corpus-v1.json`; modify `evaluation/rag_schemas.py`; test `tests/evaluation/test_rag_corpus.py`.

**Interfaces:** `RagCorpusDocument(document_id, source_type, source_path, content)` and `RagCorpus` provide independent indexed text.

- [ ] Assert unique IDs and exact agreement with answerable benchmark IDs.
- [ ] Add the corpus models and reviewed source excerpts.
- [ ] Run the focused test and expect PASS.

### Task 2: Idempotent knowledge-base builder

**Files:** Create `evaluation/build_rag_benchmark.py`; modify `pyproject.toml`; test `tests/evaluation/test_rag_builder.py`.

**Interfaces:** `build(args)` creates the KB, uploads missing stable-ID documents, polls `/jobs/{id}`, and returns verified counts.

- [ ] Test create, existing-KB, upload, polling, and failed-job paths with a fake HTTP service.
- [ ] Implement `agent-eval-rag-build` with `--rag-base-url`, `--rag-api-key`, `--kb-id`, and timeout controls.
- [ ] Run focused tests and expect PASS.

### Task 3: Evaluation compatibility preflight

**Files:** Modify `evaluation/run_rag_evaluation.py`, `evaluation/README.md`, and `tests/evaluation/test_rag_cli.py`.

**Interfaces:** Full benchmark defaults to `rag-benchmark-v1`; preflight rejects zero documents/chunks and missing expected IDs before any metric is written.

- [ ] Add failing preflight tests for empty and mismatched knowledge bases.
- [ ] Implement the document manifest check and actionable error message.
- [ ] Document build → evaluate commands and run all evaluation tests plus Ruff.

## Self-review

- The corpus is independent from labels at runtime and retains provenance.
- No deletion operation is present.
- Builder and evaluator use the same stable document IDs and KB default.
