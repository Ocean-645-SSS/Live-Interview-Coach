# Keyword-Only Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent keyword-only answers from receiving unjustified basic scores and recalibrate pure technical concept-only labels.

**Architecture:** Prompt v4 makes keyword-only answers a named scoring case with direct dimension limits and a 30-point maximum. The v1.4 benchmark adjusts only `technical_knowledge` concept-only labels to the human dimensions 4/2/4/3; project-practice labels retain their practice-evidence penalty.

**Tech Stack:** Existing answer evaluation prompt, JSON benchmark data, pytest.

## Global Constraints

- Do not change production schemas, weighted-score calculation, persistence, or state-machine behavior.
- Keyword-only limits apply only when no explanation of a principle, mechanism, cause, or practical detail is present.
- Keep project-practice concept-only samples distinct from technical-knowledge concept-only samples.

---

### Task 1: Add the keyword-only score rule

**Files:**
- Modify: `liverag/interview/prompts/evaluation_prompts.py`
- Modify: `liverag/interview/application/evaluator.py`
- Modify: `tests/interview/test_evaluator.py`

**Interfaces:**
- Consumes: existing rubric and answer text.
- Produces: `answer-evaluation-v4` instructions with technical accuracy and completeness at most one, plus a 30-point maximum, for keyword-only answers.

- [ ] Add a prompt assertion for the keyword-only rule.
- [ ] Add the direct rule while preserving the concise-but-substantive exception.
- [ ] Bump the prompt version to v4.
- [ ] Run `pytest tests/interview/test_evaluator.py -q`.

### Task 2: Calibrate technical concept-only labels

**Files:**
- Modify: `evaluation/benchmark/interview_answer_dataset.json`
- Modify: `tests/evaluation/test_dataset.py`

**Interfaces:**
- Consumes: five technical-knowledge concept-only rows.
- Produces: human dimensions technical_accuracy=4, completeness=2, clarity_and_structure=4, job_relevance=3 and human_score=80.

- [ ] Add a test for the recalibrated technical concept-only labels.
- [ ] Update MySQL, Python, concurrency, transaction, and Docker concept-only rows.
- [ ] Bump the dataset version to v1.4.0.
- [ ] Run `ruff check` and the evaluation plus evaluator tests.
