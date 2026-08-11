# Interview Behavior Benchmark V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the offline Interview Agent behavior benchmark to 50 conversations and make follow-up quality measurable by category and information gain.

**Architecture:** Preserve the existing offline production adapters (`InterviewPlanner`, `AnswerEvaluationProvider`, and `FollowUpPolicy`). Extend only the evaluation data contract, independent judge contract, deterministic aggregate metrics, report renderer, tests, and benchmark JSON. The runtime `liverag/` implementation remains untouched.

**Tech Stack:** Python 3.10+, Pydantic, asyncio, pytest, existing OpenAI-compatible behavior judge.

## Global Constraints

- Do not modify any file below `liverag/`.
- Do not change the live interview flow, state machine, persistence, or model prompts.
- Keep all changes in `evaluation/`, `tests/evaluation/`, and planning documentation.
- The dataset must have exactly 50 conversations: 10 `high_quality`, 10 `partially_correct`, 10 `vague`, 10 `incorrect`, and 10 `edge_case`.
- Follow-up categories are exactly `technical_depth`, `clarification`, `experience_validation`, and `architecture_design`.
- Information Gain is scored from 1 to 5 by the independent judge and measures whether a follow-up elicits a discriminating new fact, mechanism, trade-off, or implementation detail.

---

### Task 1: Extend behavior contracts and aggregate metrics

**Files:**
- Modify: `evaluation/behavior_schemas.py`
- Modify: `evaluation/evaluators/behavior_evaluator.py`
- Test: `tests/evaluation/test_behavior_schemas.py`
- Test: `tests/evaluation/test_behavior_evaluator.py`

**Interfaces:**
- Consumes: `expected_follow_up_type` in a behavior sample and judge-provided actual follow-up category and information-gain score.
- Produces: `FollowUpCategory`, `information_gain_score`, per-sample category matches, plus `category_accuracy` and `information_gain_average_score` in the follow-up summary.

- [ ] **Step 1: Write failing contract and aggregation tests**

```python
assert sample.expected_behavior.expected_follow_up_type is FollowUpCategory.TECHNICAL_DEPTH
assert report.summary.follow_up_quality.information_gain_average_score == 4.5
assert report.summary.follow_up_quality.category_accuracy == 1.0
```

- [ ] **Step 2: Run focused tests**

Run: `pytest tests/evaluation/test_behavior_schemas.py tests/evaluation/test_behavior_evaluator.py -v`

Expected: FAIL because V1 has no category or information-gain fields.

- [ ] **Step 3: Implement schemas and runner aggregation**

```python
class FollowUpCategory(str, Enum):
    TECHNICAL_DEPTH = "technical_depth"
    CLARIFICATION = "clarification"
    EXPERIENCE_VALIDATION = "experience_validation"
    ARCHITECTURE_DESIGN = "architecture_design"
```

`ExpectedBehavior` requires a category whenever `should_follow_up=True` and rejects one otherwise. The runner excludes no-follow-up cases from category accuracy and averages `information_gain_score` across the scenarios where the benchmark expects a follow-up.

- [ ] **Step 4: Re-run focused tests**

Run: `pytest tests/evaluation/test_behavior_schemas.py tests/evaluation/test_behavior_evaluator.py -v`

Expected: PASS.

### Task 2: Judge contract and report rendering

**Files:**
- Modify: `evaluation/judges/behavior_judge.py`
- Modify: `evaluation/behavior_reporting.py`
- Test: `tests/evaluation/test_behavior_reporting.py`

**Interfaces:**
- Consumes: expected follow-up type, generated follow-up question, and prior answer.
- Produces: structured category, information-gain score, and report rows/summary columns.

- [ ] **Step 1: Write failing report assertions**

```python
assert "Follow-up Category Accuracy" in markdown
assert "Information Gain Score" in markdown
```

- [ ] **Step 2: Run focused test**

Run: `pytest tests/evaluation/test_behavior_reporting.py -v`

Expected: FAIL because V1 does not render those metrics.

- [ ] **Step 3: Extend the JSON-only judge prompt and report**

The judge output includes `follow_up_category` and `information_gain_score`. The prompt defines each category and gives the Redis example: “Redis 缓存失效策略如何设计？” scores higher than “你觉得 Redis 怎么样？”. The Markdown report shows both aggregate metrics and each sample's actual category.

- [ ] **Step 4: Re-run focused test**

Run: `pytest tests/evaluation/test_behavior_reporting.py -v`

Expected: PASS.

### Task 3: Replace V1 benchmark with 50 balanced conversations

**Files:**
- Modify: `evaluation/benchmark/interview_behavior_benchmark.json`
- Modify: `tests/evaluation/test_behavior_schemas.py`

**Interfaces:**
- Consumes: valid shared question bank and 50 benchmark samples.
- Produces: balanced, auditable sample coverage for all five answer-quality classes and all follow-up categories.

- [ ] **Step 1: Write failing balance and category-coverage tests**

```python
assert len(dataset.samples) == 50
assert Counter(sample.answer_quality for sample in dataset.samples) == dict.fromkeys(BehaviorAnswerQuality, 10)
assert {sample.expected_behavior.expected_follow_up_type for sample in dataset.samples if sample.expected_behavior.should_follow_up} == set(FollowUpCategory)
```

- [ ] **Step 2: Run focused test**

Run: `pytest tests/evaluation/test_behavior_schemas.py -v`

Expected: FAIL because V1 has only 12 samples and four quality classes.

- [ ] **Step 3: Add 50 balanced conversations**

Keep the three existing candidate profiles and jobs; add edge cases for contradictory answers, explicit uncertainty, fabricated experience, ambiguous pronouns, partial architecture explanations, and safe no-follow-up answers. Each follow-up sample includes its required category.

- [ ] **Step 4: Re-run focused test**

Run: `pytest tests/evaluation/test_behavior_schemas.py -v`

Expected: PASS.

### Task 4: Documentation and full verification

**Files:**
- Modify: `evaluation/README.md`

**Interfaces:**
- Consumes: completed V2 CLI and report shape.
- Produces: documented metric definitions and 50-sample run instructions.

- [ ] **Step 1: Document V2 dataset distribution and metric semantics**

Document that category accuracy is calculated only where a follow-up is expected, and Information Gain evaluates the generated question's likely ability to produce novel diagnostic evidence.

- [ ] **Step 2: Run verification**

Run: `python -m ruff check evaluation tests/evaluation`

Expected: PASS.

Run: `pytest tests/evaluation -v`

Expected: PASS.

Run: `python -m evaluation.run_behavior_evaluation --help`

Expected: successful help without API credentials.
