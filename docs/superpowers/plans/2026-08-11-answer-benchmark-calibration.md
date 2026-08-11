# Answer Benchmark Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make answer-issue metrics compare stable issue identities rather than accidental Chinese wording.

**Architecture:** Benchmark labels use an `id`, semantic aliases, and issue type. An alias judge is the default offline path; an optional independent OpenAI judge resolves aliases that are not enough and explicitly distinguishes acceptable extras, false positives, and unresolved diagnostics.

**Tech Stack:** Python 3.10+, Pydantic, asyncio, existing OpenAI-compatible client, pytest.

## Global Constraints

- Do not change Interview Coach runtime evaluation, persistence, or state-machine code.
- Preserve a fully offline default runner with no additional model calls.
- Do not count unresolved diagnostics as false positives.
- Keep every Judge verdict in the per-sample report.

---

### Task 1: Stable issue-label and judgment interfaces

**Files:**
- Modify: `evaluation/schemas.py`
- Create: `evaluation/judges/__init__.py`
- Create: `evaluation/judges/issue_judge.py`
- Test: `tests/evaluation/test_issue_judge.py`

**Interfaces:**
- Consumes: `ExpectedIssue(id, type, description, aliases)` and `AnswerEvaluation`.
- Produces: `IssuePrediction` and `IssueJudgment(matched_issue_ids, acceptable_extra_prediction_ids, false_positive_prediction_ids, unresolved_prediction_ids)`.

- [ ] Test alias matching, source-aware matching, acceptable extras, and unresolved predictions.
- [ ] Implement the typed domain models and alias judge.
- [ ] Implement the optional OpenAI semantic judge with validated JSON output and a separate model identifier.
- [ ] Run `pytest tests/evaluation/test_issue_judge.py -v`.

### Task 2: Runner, report, and CLI integration

**Files:**
- Modify: `evaluation/evaluators/answer_evaluator.py`
- Modify: `evaluation/metrics.py`
- Modify: `evaluation/reporting.py`
- Modify: `evaluation/run_evaluation.py`
- Test: `tests/evaluation/test_answer_evaluator.py`
- Test: `tests/evaluation/test_reporting.py`

**Interfaces:**
- Consumes: an `IssueJudge` and per-sample evaluator output.
- Produces: issue metrics where only Judge-classified false positives contribute to FP and unresolved counts remain visible.

- [ ] Test runner aggregation with a fake semantic judgment.
- [ ] Implement judge selection, judgment persistence, unresolved counts, YAML/Markdown visibility, and `--judge-model` CLI settings.
- [ ] Run all evaluation tests.

### Task 3: Recalibrate the versioned 50-sample dataset

**Files:**
- Modify: `evaluation/benchmark/interview_answer_dataset.json`
- Modify: `tests/evaluation/test_dataset.py`

**Interfaces:**
- Consumes: the new stable issue schema.
- Produces: 50 internally consistent samples, including truly partial answers that omit the labeled details.

- [ ] Test that every non-correct sample has unique stable issue ids and aliases.
- [ ] Correct every partial and concept-only candidate answer so it does not state a labeled missing point.
- [ ] Bump dataset version to `1.1.0`.
- [ ] Run `ruff check evaluation tests/evaluation` and `pytest tests/evaluation tests/interview/test_evaluator.py -q`.
