# Behavior Evaluation Production Follow-up Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the offline behavior benchmark execute the production follow-up provider context and policy path, then align its expectations with the three-round interview policy.

**Architecture:** The runner keeps using the production `OpenAIAnswerEvaluationProvider` and `FollowUpPolicy`, but constructs the same immutable `AnswerEvaluationContext` normally supplied by `AnswerEvaluator`. The judge receives the policy-approved question, not the provider's unapproved suggestion. Benchmark expectations gain a nested follow-up contract while legacy v2 fields remain readable.

**Tech Stack:** Python 3.10+, Pydantic, asyncio, existing Interview Planner, Answer Evaluator Provider, FollowUpPolicy, pytest.

## Global Constraints

- Do not change the live Interview Agent implementation or its state machine.
- Do not add or remove benchmark conversations; retain the existing 50 samples.
- Reuse `AnswerEvaluationContext`, `OpenAIAnswerEvaluationProvider`, and `FollowUpPolicy` rather than a behavior-evaluation-only follow-up generator.
- A high-quality root answer expects the first production layer: `experience_validation`, with a maximum of three follow-up rounds.
- Existing behavior-dataset consumers that only have legacy `expected_behavior` fields must remain valid.

---

### Task 1: Add a normalized expected follow-up contract

**Files:**
- Modify: `evaluation/behavior_schemas.py`
- Modify: `evaluation/benchmark/interview_behavior_benchmark_v2.json`
- Test: `tests/evaluation/test_behavior_evaluator.py`

**Interfaces:**
- Consumes: legacy `should_follow_up` and `expected_follow_up_type` fields and optional `expected_follow_up` JSON.
- Produces: `ExpectedFollowUp(required, preferred_type, max_round, expected_path)` through `ExpectedBehavior.follow_up_expectation`.

- [x] **Step 1: Write a failing schema test**

```python
assert sample.expected_behavior.follow_up_expectation.required is True
assert sample.expected_behavior.follow_up_expectation.expected_path == [
    FollowUpCategory.EXPERIENCE_VALIDATION,
    FollowUpCategory.ARCHITECTURE_DESIGN,
    FollowUpCategory.OPTIMIZATION,
]
```

- [x] **Step 2: Implement the contract and compatibility property**

```python
class ExpectedFollowUp(EvaluationModel):
    required: bool
    preferred_type: FollowUpCategory | None
    max_round: int = Field(ge=0, le=3)
    expected_path: list[FollowUpCategory]

class ExpectedBehavior(EvaluationModel):
    expected_follow_up: ExpectedFollowUp | None = None

    @property
    def follow_up_expectation(self) -> ExpectedFollowUp: ...
```

Add `optimization` to `FollowUpCategory`. Update high-quality samples to expect the three-layer path and retain legacy fields consistently.

- [x] **Step 3: Run the focused tests**

Run: `python -m pytest tests/evaluation/test_behavior_evaluator.py -q`

Expected: PASS.

### Task 2: Execute the production follow-up decision path

**Files:**
- Modify: `evaluation/evaluators/behavior_evaluator.py`
- Modify: `evaluation/judges/behavior_judge.py`
- Test: `tests/evaluation/test_behavior_evaluator.py`

**Interfaces:**
- Consumes: current turn, same-question prior turns, `ExpectedFollowUp.max_round`, production `AnswerEvaluationProvider`, and `FollowUpPolicy`.
- Produces: policy-approved `follow_up.question_text` and `BehaviorSampleResult` matches against `follow_up_expectation`.

- [x] **Step 1: Write failing runtime-path tests**

```python
assert provider.contexts[0].follow_up_round == 0
assert provider.contexts[0].max_follow_ups == 3
assert judge.generated_follow_up == result.follow_up_question
```

- [x] **Step 2: Build the production-shaped context**

```python
context = AnswerEvaluationContext(
    prior_answers=_prior_answers_for_current_question(sample),
    follow_up_round=len(prior_answers),
    max_follow_ups=sample.expected_behavior.follow_up_expectation.max_round,
)
evaluation = await answer_provider.evaluate(answer=current_answer, question=question, context=context)
decision = FollowUpPolicy().decide(...)
```

Pass `decision.question_text` to the judge. Use `follow_up_expectation.required` and `preferred_type` for metrics.

- [x] **Step 3: Run focused tests**

Run: `python -m pytest tests/evaluation/test_behavior_evaluator.py tests/evaluation/test_behavior_judge.py -q`

Expected: PASS.

### Task 3: Verify report compatibility

**Files:**
- Test: `tests/evaluation/test_behavior_evaluator.py`
- Test: `tests/evaluation/test_behavior_reporting.py`

**Interfaces:**
- Consumes: `BehaviorSampleResult` and its existing report serializer.
- Produces: unchanged report keys plus metrics computed from the nested expectation.

- [x] **Step 1: Assert high-quality v2 sample expects a first-layer follow-up**

```python
assert report.results[0].should_follow_up_match is True
assert report.results[0].follow_up_category_match is True
```

- [x] **Step 2: Run verification**

Run: `python -m pytest tests/evaluation tests/interview/test_evaluator.py tests/interview/test_services.py -q`

Expected: PASS.

Run: `python -m ruff check evaluation tests/evaluation`

Expected: PASS for files changed by this task.

## Self-Review

- Production Provider context, production policy, actual policy output, nested schema, and high-quality expectations each have a task.
- No benchmark sample count changes are included.
- `ExpectedFollowUp`, `follow_up_expectation`, and the runtime context fields use the same names in all tasks.
