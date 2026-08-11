# Vague Answer Hard-Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make keyword-only, conclusion-only, and detail-free answers score no more than 25 under the existing weighted score formula.

**Architecture:** Prompt v5 names the three qualifying conditions and sets four dimension ceilings. Because the service recomputes weighted score from dimensions, ceilings 1/1/1/1 enforce the 25-point total maximum without changing production schemas or score calculation.

**Tech Stack:** Existing evaluator prompt, evaluator prompt-version constant, pytest.

## Global Constraints

- Do not change the `DimensionScores` schema or weighted-score formula.
- Apply the hard cap only to answers with no principle, mechanism, causal explanation, technical detail, or practice evidence.
- Preserve short but substantive answers as non-vague.

---

### Task 1: Add and verify the 25-point keyword-only cap

**Files:**
- Modify: `liverag/interview/prompts/evaluation_prompts.py`
- Modify: `liverag/interview/application/evaluator.py`
- Modify: `tests/interview/test_evaluator.py`

**Interfaces:**
- Consumes: existing rubric and candidate answer.
- Produces: `answer-evaluation-v5` with dimension ceilings technical_accuracy=1, completeness=1, clarity_and_structure=1, job_relevance=1 for qualifying vague answers.

- [ ] Add test assertions for each qualifying condition and the 25-point bound.
- [ ] Replace the keyword-only rule with the hard-cap rule.
- [ ] Bump `EVALUATION_PROMPT_VERSION` to `answer-evaluation-v5`.
- [ ] Run `ruff check liverag/interview/application/evaluator.py liverag/interview/prompts/evaluation_prompts.py tests/interview/test_evaluator.py`.
- [ ] Run `pytest tests/interview/test_evaluator.py -q`.
