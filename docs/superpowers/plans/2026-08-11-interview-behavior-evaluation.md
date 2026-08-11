# Interview Agent Behavior Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline, versioned benchmark that measures production InterviewPlanner personalization and AnswerEvaluationProvider follow-up behavior, plus an evaluation-only difficulty recommendation policy.

**Architecture:** The top-level `evaluation` package owns all behavior schemas, benchmark data, adapters, judge, runner, reporting, and tests. It invokes the existing `InterviewPlanner` and `AnswerEvaluationProvider` directly, but never imports application controllers, persistence, LiveKit, or the interview state machine. Difficulty adaptation is explicitly an evaluation-only deterministic policy that consumes a production answer evaluation; it is not wired into the runtime agent.

**Tech Stack:** Python 3.10+, Pydantic, asyncio, pytest, existing OpenAI-compatible provider and planner.

## Global Constraints

- Do not modify any file below `liverag/`.
- Keep all behavior-evaluation implementation under the independent top-level `evaluation/` package.
- Do not start API, database, worker, LiveKit, ASR, or the interview state machine.
- Reuse `InterviewPlanner`, `QuestionBank`, `CandidateProfile`, `JobProfile`, `InterviewQuestion`, and `AnswerEvaluationProvider` as read-only production dependencies.
- Report difficulty results as `offline_policy`, never as a runtime-agent capability.
- Validate benchmark JSON and external LLM output; trust typed internal objects.

---

### Task 1: Behavior benchmark contracts and offline difficulty policy

**Files:**
- Create: `evaluation/behavior_schemas.py`
- Create: `evaluation/difficulty_policy.py`
- Test: `tests/evaluation/test_behavior_schemas.py`
- Test: `tests/evaluation/test_difficulty_policy.py`

**Interfaces:**
- Consumes: benchmark JSON compatible with `QuestionBankDocument`, candidate/job inputs, a prior question ID, and candidate answer history.
- Produces: `InterviewBehaviorBenchmarkDataset`, `BehaviorSampleResult`, `InterviewBehaviorReport`, `DifficultyAdaptationDecision`, and a deterministic `OfflineDifficultyAdaptationPolicy.decide()`.

- [ ] **Step 1: Write failing schema and policy tests**

```python
def test_high_quality_answer_recommends_one_level_higher_difficulty():
    decision = OfflineDifficultyAdaptationPolicy().decide(
        evaluation=_evaluation(90), current_difficulty=InterviewDifficulty.INTERMEDIATE
    )
    assert decision.action is DifficultyAction.INCREASE
    assert decision.target_difficulty is InterviewDifficulty.SENIOR
```

- [ ] **Step 2: Run focused tests**

Run: `pytest tests/evaluation/test_behavior_schemas.py tests/evaluation/test_difficulty_policy.py -v`

Expected: FAIL with missing-module errors.

- [ ] **Step 3: Implement strict behavior-only Pydantic schemas and the policy**

```python
class OfflineDifficultyAdaptationPolicy:
    def decide(self, *, evaluation: AnswerEvaluation, current_difficulty: InterviewDifficulty) -> DifficultyAdaptationDecision:
        if evaluation.weighted_score >= 80:
            return DifficultyAdaptationDecision(action=DifficultyAction.INCREASE, target_difficulty=_step(current_difficulty, 1))
        if evaluation.weighted_score < 45:
            return DifficultyAdaptationDecision(action=DifficultyAction.DECREASE, target_difficulty=_step(current_difficulty, -1))
        return DifficultyAdaptationDecision(action=DifficultyAction.MAINTAIN, target_difficulty=current_difficulty)
```

- [ ] **Step 4: Re-run focused tests**

Run: `pytest tests/evaluation/test_behavior_schemas.py tests/evaluation/test_difficulty_policy.py -v`

Expected: PASS.

### Task 2: Production capability adapter and independent judge

**Files:**
- Create: `evaluation/evaluators/behavior_evaluator.py`
- Create: `evaluation/judges/behavior_judge.py`
- Test: `tests/evaluation/test_behavior_evaluator.py`
- Test: `tests/evaluation/test_behavior_judge.py`

**Interfaces:**
- Consumes: `InterviewBehaviorBenchmarkDataset`, an existing `InterviewPlanner`, an existing `AnswerEvaluationProvider`, and `BehaviorJudge`.
- Produces: one raw production plan, production answer evaluation, follow-up decision, difficulty recommendation, and independent judge scores per sample.

- [ ] **Step 1: Write async fake-provider tests**

```python
report = await InterviewBehaviorRunner(fake_planner, fake_provider, fake_judge).run(dataset)
assert report.summary.question_relevance.average_score == 5
assert report.results[0].difficulty_adaptation.mode == "offline_policy"
```

- [ ] **Step 2: Run focused tests**

Run: `pytest tests/evaluation/test_behavior_evaluator.py tests/evaluation/test_behavior_judge.py -v`

Expected: FAIL with missing-module errors.

- [ ] **Step 3: Implement direct production adapters and JSON-only OpenAI judge**

```python
plan = await planner.build(title=f"Behavior benchmark: {sample.id}", config=config, candidate_profile=candidate, job_profile=job)
evaluation = await answer_provider.evaluate(answer=answer_record, question=history_question)
judgment = await judge.judge(sample=sample, planned_question=plan.questions[0], evaluation=evaluation, difficulty=decision)
```

The judge returns strict relevance, follow-up, and difficulty scores and must receive the expected behavior plus raw generated behavior.

- [ ] **Step 4: Re-run focused tests**

Run: `pytest tests/evaluation/test_behavior_evaluator.py tests/evaluation/test_behavior_judge.py -v`

Expected: PASS.

### Task 3: Versioned behavior benchmark, runner, and reports

**Files:**
- Create: `evaluation/benchmark/interview_behavior_benchmark.json`
- Create: `evaluation/behavior_reporting.py`
- Create: `evaluation/run_behavior_evaluation.py`
- Test: `tests/evaluation/test_behavior_dataset.py`
- Test: `tests/evaluation/test_behavior_reporting.py`
- Test: `tests/evaluation/test_behavior_cli.py`

**Interfaces:**
- Consumes: a versioned JSON dataset and existing voice-model settings.
- Produces: timestamped JSON and Markdown report artifacts from `python -m evaluation.run_behavior_evaluation`.

- [ ] **Step 1: Write dataset, reporting, and parser tests**

```python
assert len(dataset.samples) == 12
assert {sample.job_profile.position for sample in dataset.samples} == {"AI Agent Engineer", "Backend Engineer", "LLM Application Engineer"}
assert "Difficulty Adaptation (offline policy)" in paths["markdown"].read_text(encoding="utf-8")
```

- [ ] **Step 2: Run focused tests**

Run: `pytest tests/evaluation/test_behavior_dataset.py tests/evaluation/test_behavior_reporting.py tests/evaluation/test_behavior_cli.py -v`

Expected: FAIL with missing file/module errors.

- [ ] **Step 3: Add a 12-sample balanced dataset and output writers**

The initial dataset contains 12 balanced samples: three candidate backgrounds, three target jobs, and all four answer qualities. It contains a shared validated question bank with references/rubrics, one historical answer per sample, expected follow-up behavior, and expected offline difficulty action.

- [ ] **Step 4: Implement runner CLI**

```powershell
python -m evaluation.run_behavior_evaluation --dataset evaluation/benchmark/interview_behavior_benchmark.json --concurrency 2
```

The CLI accepts dataset/output/model/provider/judge overrides. Omitting `--planner-model` keeps production planner question selection and disables LLM personalization explicitly.

- [ ] **Step 5: Re-run focused tests**

Run: `pytest tests/evaluation/test_behavior_dataset.py tests/evaluation/test_behavior_reporting.py tests/evaluation/test_behavior_cli.py -v`

Expected: PASS.

### Task 4: Documentation and verification

**Files:**
- Modify: `evaluation/README.md`

**Interfaces:**
- Consumes: finalized CLI, dataset, and report format.
- Produces: reproducible commands and accurate interpretation guidance.

- [ ] **Step 1: Document modes and metric meaning**

Document that Question Relevance invokes `InterviewPlanner`, Follow-up Quality invokes `AnswerEvaluationProvider`, and Difficulty Adaptation is an evaluation-only policy, not a runtime flow feature.

- [ ] **Step 2: Verify format and behavior**

Run: `python -m ruff check evaluation tests/evaluation`

Expected: PASS.

Run: `pytest tests/evaluation -v`

Expected: PASS.

Run: `python -m evaluation.run_behavior_evaluation --help`

Expected: successful help output without API configuration.

- [ ] **Step 3: Commit**

```bash
git add evaluation tests/evaluation docs/superpowers/plans/2026-08-11-interview-behavior-evaluation.md
git commit -m "feat: add interview behavior evaluation pipeline"
```
