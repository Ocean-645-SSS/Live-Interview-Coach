# RAG Benchmark Fact Alignment V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the candidate resume, benchmark corpus, and Gold labels to one factual Interview Coach project before rerunning the unchanged retrieval profile.

**Architecture:** The source of truth is the current README, Interview Coach architecture, and RAG documentation. The resume is reduced to a single project supported by those documents. A v2 benchmark/corpus uses a fresh knowledge-base ID and stable document IDs, avoiding writes to the prior v1 corpus and preserving comparable retrieval settings.

**Tech Stack:** Python 3.10+, python-docx, LibreOffice render QA, Pydantic, aiohttp, pytest.

## Global Constraints

- Keep exactly 50 benchmark samples with 10 no-answer samples.
- Do not change query mode, top_k, chunk_top_k, rerank, embedding, or retrieval profile.
- Do not delete or overwrite existing knowledge bases or documents; use `rag-benchmark-v2`.
- Every resume, corpus, Gold fact, and document ID must be supported by current project materials.
- Render and inspect the edited DOCX before delivery.

---

### Task 1: Align the resume to the project facts

**Files:** Modify `evaluation/materials/个人简历.docx`; create `scripts/update_benchmark_resume.py`; test with `tests/evaluation/test_rag_alignment_v2.py`.

**Interfaces:** The resume contains only `Interview Coach｜可追溯中文技术模拟面试系统` under project experience; the project statement matches the current documented stack and behavior.

- [ ] Assert extracted resume text contains the new project and does not contain `Multi-Agent Education`, `Neo4j`, or `BKT`.
- [ ] Replace the project-experience section using python-docx while preserving all non-project resume content.
- [ ] Render with the documents-skill renderer and inspect all generated pages.

### Task 2: Reannotate v2 corpus and Gold labels

**Files:** Create `evaluation/benchmark/rag-corpus-v2.json`, `evaluation/benchmark/rag-benchmark-v2.json`; modify `tests/evaluation/test_rag_corpus.py`; add alignment tests.

**Interfaces:** Answerable samples refer only to v2 corpus documents; old education-project document IDs do not appear. Each claim is traceable to resume, README, architecture, RAG docs, JD, question-bank material, or Nowcoder aggregate contract.

- [ ] Replace old two-project samples with single-project fact, confusing, and cross-document samples.
- [ ] Run schema/coverage/alignment tests and expect PASS.

### Task 3: Build v2 and rerun unchanged retrieval profile

**Files:** Modify benchmark builder/evaluator defaults to v2; modify `evaluation/README.md`.

**Interfaces:** `agent-eval-rag-build` creates `rag-benchmark-v2`; `agent-eval-rag --retrieval-only` defaults to v2 using the existing profile options unchanged.

- [ ] Build `rag-benchmark-v2`, wait for all documents to index, and verify stable IDs.
- [ ] Run all 50 retrieval samples using default profile settings.
- [ ] Generate Failure Analysis for any v2 Recall@5 misses and record actual Recall@1/3/5 and MRR.

## Self-review

- No current sample relies on the old Multi-Agent Education project.
- The retrieval configuration has not changed from the prior full v1 run.
- The final report is labeled v2 and should not be compared numerically to v1 without noting the corrected factual corpus.
