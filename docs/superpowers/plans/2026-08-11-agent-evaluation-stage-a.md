# Agent Evaluation Stage A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline, auditable 50-sample benchmark for the existing Interview Coach answer evaluator.

**Architecture:** A top-level `evaluation` package owns benchmark schemas, deterministic metrics, the adapter to the existing `AnswerEvaluationProvider`, and report rendering. It calls the provider directly and never uses persistence, interview state transitions, or production APIs.

**Tech Stack:** Python 3.10+, Pydantic, asyncio, pytest, existing OpenAI-compatible evaluator.

## Global Constraints

- Do not change the existing interview business flow.
- Keep evaluation code in an independent top-level module.
- The benchmark must contain 50 samples across five answer-quality categories.
- Report Pearson correlation, Spearman correlation, MAE, and issue precision/recall/F1/accuracy.
- Validate only dataset and external model boundaries; trust internal typed objects.

---

### Task 1: Benchmark domain models and deterministic metrics

**Files:**
- Create: `evaluation/__init__.py`
- Create: `evaluation/schemas.py`
- Create: `evaluation/metrics.py`
- Test: `tests/evaluation/test_metrics.py`

**Interfaces:**
- Consumes: Python numeric sequences and typed expected/predicted issues.
- Produces: `pearson_correlation`, `spearman_correlation`, `mean_absolute_error`, `issue_detection_metrics`, and Pydantic report models.

- [ ] Write tests covering perfect, inverse, tied-rank, MAE, and issue confusion-matrix cases.
- [ ] Run `pytest tests/evaluation/test_metrics.py -v`; expect import failures.
- [ ] Implement average-rank Spearman, Pearson, MAE, and micro issue metrics without adding dependencies.
- [ ] Run the focused tests; expect all tests to pass.

### Task 2: Existing evaluator adapter and report generation

**Files:**
- Create: `evaluation/evaluators/__init__.py`
- Create: `evaluation/evaluators/answer_evaluator.py`
- Create: `evaluation/reporting.py`
- Test: `tests/evaluation/test_answer_evaluator.py`
- Test: `tests/evaluation/test_reporting.py`

**Interfaces:**
- Consumes: `AnswerBenchmarkDataset` and the existing `AnswerEvaluationProvider` protocol.
- Produces: `AnswerEvaluationReport` plus JSON, YAML, and Markdown artifacts.

- [ ] Write an async fake-provider test proving sample-to-domain mapping and aggregate calculations.
- [ ] Write report tests proving all three artifact formats contain the required metrics.
- [ ] Run focused tests; expect missing-module failures.
- [ ] Implement direct provider invocation with in-memory `InterviewQuestion` and `InterviewAnswerRecord` objects.
- [ ] Implement auditable issue matching using expected issue `match_terms` against evaluator `missing_points` and `errors`.
- [ ] Implement JSON, YAML, and Markdown writers using the standard library.
- [ ] Run focused tests; expect all tests to pass.

### Task 3: Versioned 50-sample benchmark and CLI

**Files:**
- Create: `evaluation/benchmark/interview_answer_dataset.json`
- Create: `evaluation/run_evaluation.py`
- Modify: `pyproject.toml`
- Test: `tests/evaluation/test_dataset.py`
- Test: `tests/evaluation/test_cli.py`

**Interfaces:**
- Consumes: voice LLM environment settings and a dataset path.
- Produces: `agent-eval-answer` CLI and timestamped report files.

- [ ] Write dataset tests requiring exactly 50 unique samples and ten samples in each quality category.
- [ ] Write a CLI parser test that performs no external model call.
- [ ] Run focused tests; expect missing dataset/CLI failures.
- [ ] Add 10 technical questions with five labeled answer variants each: correct, partially correct, concept-only, technically incorrect, and vague.
- [ ] Implement CLI arguments for dataset, report directory, sample limit, concurrency, and model overrides.
- [ ] Register `agent-eval-answer = "evaluation.run_evaluation:main"` in `pyproject.toml` and include `evaluation` packages in setuptools discovery.
- [ ] Run all evaluation tests; expect all tests to pass.

### Task 4: Verification and usage documentation

**Files:**
- Create: `evaluation/README.md`

**Interfaces:**
- Consumes: completed CLI and report formats.
- Produces: reproducible local run instructions and an honest statement that metrics require a real model run.

- [ ] Document dataset policy, metric definitions, environment requirements, commands, and report interpretation.
- [ ] Run `python -m ruff check evaluation tests/evaluation` and fix reported issues.
- [ ] Run `pytest tests/evaluation tests/interview/test_evaluator.py -v`; expect all tests to pass.
- [ ] Run `python -m evaluation.run_evaluation --help`; expect successful CLI help without an API key.

