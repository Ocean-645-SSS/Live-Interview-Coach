# Evaluation-Driven Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build leakage-safe calibration and frozen-regression evaluation for answer scoring, question-selection ablation, and ProjectSkillRelation mapping before any subsequent optimization.

**Architecture:** The current answer and behavior datasets/reports are observed historical baselines and become calibration-only inputs. Create independently authored, versioned calibration and sealed frozen-test datasets with SHA-256 locks. Runners reject frozen-test execution unless explicitly invoked as the final regression. New diagnostics aggregate answer results by `QuestionType`, classify unresolved diagnostics from human calibration labels, compare the four pipeline variants using identical samples and selection seeds, and score ProjectSkillRelation mapping independently of planner behavior.

**Tech Stack:** Python 3.10, Pydantic 2, pytest, existing OpenAI-compatible evaluators, JSON datasets, Markdown/JSON reports.

## Global Constraints

- Pause all user-facing production features; changes in this phase are evaluation contracts, datasets, runners, reports, and deterministic evaluation hooks only.
- The existing `interview_answer_dataset.json` and `interview_behavior_benchmark_v2.json` have already been observed and may only be used for calibration/baseline analysis, never as the post-optimization frozen test set.
- Calibration and frozen-test rows must have disjoint IDs, source documents, candidate answers, and human annotations.
- Do not read, run, or inspect frozen-test results while tuning prompts, planner weights, taxonomy mappings, or mapper behavior.
- Every optimization iteration must record the calibration dataset SHA-256, command, model/judge settings, code revision, and dirty-worktree state.
- The only permitted frozen-test command is the final regression command after a calibration acceptance decision has been recorded.
- Preserve raw timestamped report bundles; never overwrite a prior report or lock file.
- Use fixed `selection_seed` values for all four relevance ablation variants; a score difference must not be caused by different question-bank sampling.
- Mapping gold labels must be human-authored from source evidence. Unsupported relation emission is an error, not a candidate for prompt tuning through test-set inspection.

---

## Locked Dataset Layout

- `evaluation/benchmark/answer-calibration-v2.json`: 100 newly curated rows, 5 `QuestionType` values × 5 answer-quality labels × 4 rows; allowed for all diagnosis and tuning.
- `evaluation/benchmark/answer-frozen-test-v1.json`: 50 independently curated rows, 5 `QuestionType` values × 5 answer-quality labels × 2 rows; sealed before optimization starts.
- `evaluation/benchmark/behavior-calibration-v3.json`: 40 newly curated planning rows, ten per ablation-relevant scenario family (taxonomy-matched, taxonomy-unmatched, direct-project-evidence, related-project-evidence).
- `evaluation/benchmark/behavior-frozen-test-v1.json`: 40 independently curated rows with the same family counts and no shared source material with calibration.
- `evaluation/benchmark/project-skill-mapping-calibration-v1.json`: 60 human-labeled candidate/JD/context triples (30 positive, 20 no-relation, 10 unsupported-claim traps).
- `evaluation/benchmark/project-skill-mapping-frozen-test-v1.json`: 30 independently curated triples (15 positive, 10 no-relation, 5 unsupported-claim traps).
- `evaluation/benchmark/locks/*.lock.json`: SHA-256, sample count, dataset version, purpose (`calibration` or `frozen_test`), and creation revision for every dataset above.

### Task 1: Establish sealed datasets and execution gates

**Files:**
- Create: `evaluation/benchmark/answer-calibration-v2.json`
- Create: `evaluation/benchmark/answer-frozen-test-v1.json`
- Create: `evaluation/benchmark/behavior-calibration-v3.json`
- Create: `evaluation/benchmark/behavior-frozen-test-v1.json`
- Create: `evaluation/benchmark/locks/answer-calibration-v2.lock.json`
- Create: `evaluation/benchmark/locks/answer-frozen-test-v1.lock.json`
- Create: `evaluation/benchmark/locks/behavior-calibration-v3.lock.json`
- Create: `evaluation/benchmark/locks/behavior-frozen-test-v1.lock.json`
- Modify: `evaluation/schemas.py`
- Modify: `evaluation/behavior_schemas.py`
- Modify: `evaluation/run_evaluation.py`
- Modify: `evaluation/run_behavior_evaluation.py`
- Create: `tests/evaluation/test_dataset_locks.py`

**Interfaces:**
- Produces `DatasetPurpose.CALIBRATION | FROZEN_TEST` and lock-aware dataset metadata.
- Produces CLI option `--purpose calibration|frozen_test` and `--final-regression`.
- Rejects a frozen-test run unless both `--purpose frozen_test` and `--final-regression` are present.

- [ ] **Step 1: Add failing frozen-test gate tests**

```python
def test_frozen_test_requires_explicit_final_regression(tmp_path: Path) -> None:
    dataset = _dataset(purpose="frozen_test")
    path = tmp_path / "answer-frozen-test-v1.json"
    path.write_text(dataset.model_dump_json(), encoding="utf-8")

    with pytest.raises(ValueError, match="--final-regression"):
        validate_dataset_execution(path, purpose="frozen_test", final_regression=False)
```

- [ ] **Step 2: Run the gate test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evaluation/test_dataset_locks.py -q`

Expected: FAIL because dataset purpose and execution validation do not exist.

- [ ] **Step 3: Add metadata and validation**

Add `purpose: DatasetPurpose` to answer and behavior dataset schemas. Add `validate_dataset_execution(dataset_path: Path, *, purpose: DatasetPurpose, final_regression: bool) -> None`, which loads the corresponding lock, checks the file SHA-256 and declared purpose, and raises `ValueError("frozen test requires --final-regression")` for an unapproved frozen run. Add matching parser arguments and call the validator before any provider is built.

- [ ] **Step 4: Curate and lock the datasets before optimization**

For each answer row, add a required `question_type: QuestionType`, human score, four human dimensions, and issue labels. For each behavior row, use structured taxonomy `JobSkill` evidence and include source-material identifiers. Author frozen rows from documents and answers not used in calibration. Generate each lock with this exact shape:

```json
{
  "dataset": "answer-frozen-test-v1.json",
  "purpose": "frozen_test",
  "samples": 50,
  "sha256": "<computed SHA-256>",
  "created_revision": "<git revision before tuning>"
}
```

- [ ] **Step 5: Verify isolation**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evaluation/test_dataset_locks.py tests/evaluation/test_dataset.py tests/evaluation/test_behavior_schemas.py -q`

Expected: PASS; tests assert no overlap in IDs or `source_material_id` between calibration and frozen datasets.

### Task 2: Add answer diagnostics by question type and unresolved failure class

**Files:**
- Modify: `evaluation/schemas.py`
- Modify: `evaluation/evaluators/answer_evaluator.py`
- Modify: `evaluation/reporting.py`
- Create: `evaluation/answer_diagnostics.py`
- Create: `evaluation/benchmark/answer-calibration-v2-unresolved-labels.json`
- Create: `tests/evaluation/test_answer_diagnostics.py`
- Modify: `tests/evaluation/test_answer_evaluator.py`

**Interfaces:**
- Produces per-question-type `samples`, `pearson_correlation`, `mae`, and `signed_bias`, where `signed_bias = mean(agent_score - human_score)`.
- Produces `UnresolvedFailureClass` values: `MISSING_EXPECTED_ISSUE`, `UNSUPPORTED_EXTRA_DIAGNOSTIC`, `AMBIGUOUS_GOLD_LABEL`, `JUDGE_DISAGREEMENT`, and `EVALUATOR_FORMAT_OR_CONTRACT_FAILURE`.
- Consumes human-authored unresolved labels keyed by `(sample_id, prediction_id)` from calibration only.

- [ ] **Step 1: Write diagnostic tests**

```python
def test_question_type_metrics_keep_over_and_under_scoring_signed() -> None:
    metrics = summarize_question_type_results([
        _result("a", QuestionType.TECHNICAL_KNOWLEDGE, human=80, agent=90),
        _result("b", QuestionType.TECHNICAL_KNOWLEDGE, human=80, agent=70),
        _result("c", QuestionType.PROJECT_DEEP_DIVE, human=60, agent=50),
    ])

    assert metrics[QuestionType.TECHNICAL_KNOWLEDGE].signed_bias == 0.0
    assert metrics[QuestionType.TECHNICAL_KNOWLEDGE].mae == 10.0
    assert metrics[QuestionType.PROJECT_DEEP_DIVE].signed_bias == -10.0


def test_unresolved_classifier_counts_human_labels() -> None:
    summary = summarize_unresolved_failures([
        UnresolvedFailureLabel(sample_id="a", prediction_id="missing:0", failure_class="MISSING_EXPECTED_ISSUE"),
        UnresolvedFailureLabel(sample_id="b", prediction_id="error:0", failure_class="UNSUPPORTED_EXTRA_DIAGNOSTIC"),
    ])

    assert summary.counts["MISSING_EXPECTED_ISSUE"] == 1
    assert summary.counts["UNSUPPORTED_EXTRA_DIAGNOSTIC"] == 1
```

- [ ] **Step 2: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evaluation/test_answer_diagnostics.py -q`

Expected: FAIL because the diagnostics schemas and aggregators do not exist.

- [ ] **Step 3: Implement pure aggregation and attach it to reports**

Keep per-sample `question_type` in `AnswerSampleResult`; use existing `pearson_correlation` and `mean_absolute_error` helpers per group; calculate signed bias with `sum(agent - human) / sample_count`. Load failure labels only when the run purpose is calibration and fail if a label references an absent unresolved prediction. Add `by_question_type` and `unresolved_failure_classes` to `AnswerEvaluationSummary`, then render both tables in Markdown and YAML.

- [ ] **Step 4: Run calibration diagnostics**

Run: `.\.venv\Scripts\python.exe -m evaluation.run_evaluation --dataset evaluation/benchmark/answer-calibration-v2.json --purpose calibration --concurrency 1 --model qwen-flash --judge-model qwen-plus`

Expected: a timestamped report with five question-type rows and a failure-class table. Use this report, not frozen-test output, to select the next evaluator changes.

- [ ] **Step 5: Verify all answer tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evaluation/test_answer_diagnostics.py tests/evaluation/test_answer_evaluator.py tests/evaluation/test_reporting.py tests/evaluation/test_cli.py -q`

Expected: PASS.

### Task 3: Implement four-way question-relevance ablation

**Files:**
- Modify: `evaluation/behavior_schemas.py`
- Modify: `evaluation/evaluators/behavior_evaluator.py`
- Create: `evaluation/behavior_ablation.py`
- Modify: `evaluation/behavior_reporting.py`
- Modify: `evaluation/run_behavior_evaluation.py`
- Create: `tests/evaluation/test_behavior_ablation.py`
- Modify: `tests/evaluation/test_behavior_evaluator.py`

**Interfaces:**
- Produces `RelevanceAblationVariant.BASELINE`, `JOB_FACTS`, `PROJECT_SKILL_RELATION`, and `FULL_PIPELINE`.
- Produces a report containing one independent question-relevance summary and per-sample selected question for every variant.
- Uses the same `sample.id` as `selection_seed` in every variant.

- [ ] **Step 1: Add ablation-contract tests**

```python
@pytest.mark.asyncio
async def test_ablation_variants_receive_only_their_allowed_inputs() -> None:
    observed = await RecordingPlanner().run_all(_sample())

    assert observed[RelevanceAblationVariant.BASELINE].job_profile.required_skills == []
    assert observed[RelevanceAblationVariant.JOB_FACTS].project_skill_relations == ()
    assert observed[RelevanceAblationVariant.PROJECT_SKILL_RELATION].project_skill_relations
    assert observed[RelevanceAblationVariant.FULL_PIPELINE].project_skill_relations
    assert {item.selection_seed for item in observed.values()} == {"sample-1"}
```

- [ ] **Step 2: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evaluation/test_behavior_ablation.py -q`

Expected: FAIL because variants and fixed seed are unavailable.

- [ ] **Step 3: Build the ablation adapter**

Define a dedicated evaluation-only `BehaviorAblationRunner`; do not branch the production planner. It invokes the production `InterviewPlanner` four times per sample with these exact inputs:

| Variant | Candidate profile | Job profile | Relations | Personalization |
| --- | --- | --- | --- | --- |
| BASELINE | profile text | raw job summary, no structured skills | none | disabled |
| JOB_FACTS | profile text | JobFacts-derived `JobProfile` | none | disabled |
| PROJECT_SKILL_RELATION | profile text | JobFacts-derived `JobProfile` | human-verified relations | enabled only when relations exist |
| FULL_PIPELINE | facts-derived profile | extracted `JobProfile` | mapper output from raw context | enabled only when relations exist |

Add an optional `selection_seed: str | None = None` to `InterviewPlanner.build`; use it only to derive the selection seed (`selection_seed or plan_id`) and leave all runtime callers unchanged.

- [ ] **Step 4: Report paired deltas**

For each variant report mean relevance and per-sample delta against baseline. The report must state the judge model, selection seed strategy, source fixture hash, and count of mapped relation-bearing questions. Do not aggregate a variant if any sample fails.

- [ ] **Step 5: Run calibration ablation**

Run: `.\.venv\Scripts\python.exe -m evaluation.run_behavior_evaluation --dataset evaluation/benchmark/behavior-calibration-v3.json --purpose calibration --ablation baseline,job_facts,project_skill_relation,full_pipeline --concurrency 1 --model qwen-flash --judge-model qwen-flash --planner-model qwen-flash`

Expected: a single calibration artifact containing four 40-sample relevance results. Optimize only against paired calibration deltas and retained follow-up safeguards.

### Task 4: Add an independent ProjectSkillRelation mapping benchmark

**Files:**
- Create: `evaluation/project_skill_mapping_schemas.py`
- Create: `evaluation/evaluators/project_skill_mapping_evaluator.py`
- Create: `evaluation/project_skill_mapping_reporting.py`
- Create: `evaluation/run_project_skill_mapping_evaluation.py`
- Create: `evaluation/benchmark/project-skill-mapping-calibration-v1.json`
- Create: `evaluation/benchmark/project-skill-mapping-frozen-test-v1.json`
- Create: `evaluation/benchmark/locks/project-skill-mapping-calibration-v1.lock.json`
- Create: `evaluation/benchmark/locks/project-skill-mapping-frozen-test-v1.lock.json`
- Create: `tests/evaluation/test_project_skill_mapping_schemas.py`
- Create: `tests/evaluation/test_project_skill_mapping_evaluator.py`
- Create: `tests/evaluation/test_project_skill_mapping_reporting.py`

**Interfaces:**
- Consumes `CandidateFacts`, `JobProfile`, `KnowledgeContext`, and human-authorized `ProjectSkillRelation` records.
- Produces exact relation precision/recall/F1, unsupported-claim count, unknown-skill/project count, and no-relation accuracy.

- [ ] **Step 1: Write mapping metric tests**

```python
def test_mapping_metrics_penalize_hallucinated_relation() -> None:
    report = summarize_mapping_results([
        _result(expected=[_relation("skill-rag", "RAG项目")], actual=[_relation("skill-rag", "RAG项目")]),
        _result(expected=[], actual=[_relation("skill-redis", "不存在项目")]),
    ])

    assert report.precision == 0.5
    assert report.recall == 1.0
    assert report.unsupported_relation_count == 1
    assert report.no_relation_accuracy == 0.0
```

- [ ] **Step 2: Run the test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evaluation/test_project_skill_mapping_evaluator.py -q`

Expected: FAIL because mapping benchmark contracts and metrics do not exist.

- [ ] **Step 3: Implement direct mapper evaluation**

Call `ProjectSkillMapper.build` directly; do not call the planner or question-relevance judge. Identify a relation by its exact `(skill_id, project_name, responsibility, jd_evidence, candidate_evidence, source_refs, relation_type)` fields. Count every emitted relation absent from the human-approved set as a false positive; separately count mapper exceptions raised by existing evidence validation as unsupported output. Require zero unsupported output before promotion.

- [ ] **Step 4: Run calibration mapping benchmark**

Run: `.\.venv\Scripts\python.exe -m evaluation.run_project_skill_mapping_evaluation --dataset evaluation/benchmark/project-skill-mapping-calibration-v1.json --purpose calibration --concurrency 1 --model qwen-flash`

Expected: a timestamped JSON/Markdown report with precision, recall, F1, no-relation accuracy, and unsupported-output count.

- [ ] **Step 5: Verify mapping tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evaluation/test_project_skill_mapping_schemas.py tests/evaluation/test_project_skill_mapping_evaluator.py tests/evaluation/test_project_skill_mapping_reporting.py -q`

Expected: PASS.

### Task 5: Optimize on calibration and perform exactly one frozen regression

**Files:**
- Create: `evaluation/reports/<timestamp>-optimization-ledger.md`
- Create: `evaluation/reports/<timestamp>-answer-frozen-regression.*`
- Create: `evaluation/reports/<timestamp>-behavior-frozen-regression.*`
- Create: `evaluation/reports/<timestamp>-mapping-frozen-regression.*`
- Modify: `evaluation/reports/evaluation-summary.md`

**Interfaces:**
- Consumes accepted calibration reports and sealed locks.
- Produces immutable final regression reports; does not create a new tuning target.

- [ ] **Step 1: Record each calibration decision**

For every proposed evaluator/planner/mapper change, write an optimization-ledger row with: hypothesis, exact code/prompt diff, calibration dataset hash, before/after metrics, unresolved-failure-class change, ablation delta, mapping metrics, and acceptance/rejection reason. Reject a change unless it improves its targeted calibration metric without worsening a guardrail.

- [ ] **Step 2: Define acceptance guardrails before opening frozen test**

Use calibration thresholds: no question-type MAE increase greater than `1.0`; no question-type signed-bias absolute increase greater than `2.0`; unresolved diagnostics do not increase; relevance does not fall below the accepted baseline; mapping unsupported-output count is `0`.

- [ ] **Step 3: Run final frozen regression once**

```powershell
.\.venv\Scripts\python.exe -m evaluation.run_evaluation --dataset evaluation/benchmark/answer-frozen-test-v1.json --purpose frozen_test --final-regression --concurrency 1 --model qwen-flash --judge-model qwen-plus
.\.venv\Scripts\python.exe -m evaluation.run_behavior_evaluation --dataset evaluation/benchmark/behavior-frozen-test-v1.json --purpose frozen_test --final-regression --ablation baseline,job_facts,project_skill_relation,full_pipeline --concurrency 1 --model qwen-flash --judge-model qwen-flash --planner-model qwen-flash
.\.venv\Scripts\python.exe -m evaluation.run_project_skill_mapping_evaluation --dataset evaluation/benchmark/project-skill-mapping-frozen-test-v1.json --purpose frozen_test --final-regression --concurrency 1 --model qwen-flash
```

Expected: each lock hash validates, every report is complete, and no subsequent prompt/weight/mapper adjustment is made from those outputs.

- [ ] **Step 4: Publish regression-only summary**

Update the summary with calibration-vs-frozen-test labels, all dataset hashes, commands, models, and known limitations. Do not replace a frozen report with a calibration report.

## Self-Review

- Answer diagnostics, unresolved failure classification, four-way relevance ablation, and independent mapping evaluation each have a dedicated task.
- Calibration/frozen isolation is enforced by disjoint data, lock hashes, CLI gates, and a final-regression flag.
- Existing observed 50-sample datasets are explicitly excluded from frozen regression.
- The plan changes no user-facing product capability; the optional planner seed exists only to make evaluation comparisons deterministic.
