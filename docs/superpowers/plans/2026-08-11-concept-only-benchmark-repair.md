# Concept-Only Benchmark Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the semantic meaning of the ten `concept_only` benchmark samples so their human score of 52 remains valid.

**Architecture:** Replace each generic non-answer with a question-specific, technically correct conceptual explanation that explicitly lacks implementation, measurement, and incident-handling detail. Add a dataset test that rejects generic shared concept-only answers and requires relevant technical vocabulary.

**Tech Stack:** JSON benchmark data, Pydantic, pytest.

## Global Constraints

- Do not modify the production Interview Coach evaluator.
- Preserve exactly 50 samples and five answer-quality categories with ten samples each.
- Preserve the existing 0-100 human scores because the repaired answers match their original annotation intent.

---

### Task 1: Repair and verify concept-only sample semantics

**Files:**
- Modify: `evaluation/benchmark/interview_answer_dataset.json`
- Modify: `tests/evaluation/test_dataset.py`

**Interfaces:**
- Consumes: the versioned `AnswerBenchmarkDataset`.
- Produces: ten question-specific `concept_only` answers that state a correct concept but no implementation evidence.

- [ ] Add a failing test requiring ten unique concept-only candidate answers and topic-specific vocabulary.
- [ ] Replace each generic concept-only answer with its concise topic-specific conceptual explanation and an explicit lack of practice detail.
- [ ] Run `pytest tests/evaluation/test_dataset.py -q`; expect all tests to pass.
- [ ] Run `ruff check evaluation tests/evaluation` and `pytest tests/evaluation tests/interview/test_evaluator.py -q`.
