# Annotated Benchmark Conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven development task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the human-reviewed calibration annotation drafts into runnable Answer, Behavior-ablation, and ProjectSkillRelation mapping benchmarks, while leaving every frozen draft non-runnable and unsealed.

**Architecture:** Keep `evaluation/annotation_templates/` as the human-review source of truth. Add evaluation-only conversion code that reads a reviewed template and its permitted source question bank, emits the existing Pydantic runner schema under `evaluation/benchmark/`, and fails when a required reviewed field is absent. Calibration artifacts are executable; frozen artifacts stay as reviewed drafts and are neither converted to runnable datasets nor passed to any runner in this phase.

**Tech Stack:** Python 3.12, Pydantic models in `evaluation.schemas`, `evaluation.behavior_schemas`, and `evaluation.project_skill_mapping_schemas`; pytest; Ruff.

## Global Constraints

- Do not modify production feature code, Evaluator, Planner, or any prompt.
- Do not modify the frozen templates, add a lock, or execute a frozen dataset.
- Preserve the reviewed annotation text, human scores, expected issue labels, selection seeds, and exact mapping relations.
- Calibration runs are permitted only after each generated dataset validates locally; model calls require a separate explicit execution step.
- `evaluation/` is ignored by Git in this workspace; never use broad `git add` for it.

---

### Task 1: Convert the Answer calibration template

**Files:**
- Create: `evaluation/build_answer_annotation_benchmark.py`
- Create: `evaluation/benchmark/answer-calibration-v2.json`
- Create: `tests/evaluation/test_build_answer_annotation_benchmark.py`
- Modify: `evaluation/annotation_templates/answer-calibration-v2.draft.json` only to record reviewed provenance if missing.

**Interfaces:**
- Consumes: template `samples[*].human_annotation`, `question_type`, source question-bank identifiers, and `liverag/interview/question_bank/question_bank.v2.reviewed.json`.
- Produces: `AnswerBenchmarkDataset` with `purpose="calibration"`, new dataset version, `human_score=human_overall_score`, `human_dimension_scores`, and `expected_issues`.

- [ ] **Step 1: Write failing conversion tests**

```python
def test_build_answer_calibration_preserves_reviewed_gold(tmp_path: Path) -> None:
    dataset = build_answer_calibration(template_path, question_bank_path)
    sample = dataset.samples[0]
    assert sample.human_score == 48.75
    assert sample.expected_issues[0].type.value == "incomplete_answer"
    assert dataset.purpose.value == "calibration"

def test_build_answer_calibration_rejects_unreviewed_annotation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="human_overall_score"):
        build_answer_calibration(unreviewed_template_path, question_bank_path)
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_build_answer_annotation_benchmark.py -q`

Expected: failure because the builder does not exist.

- [ ] **Step 3: Implement the smallest builder**

```python
def build_answer_calibration(template_path: Path, question_bank_path: Path) -> AnswerBenchmarkDataset:
    """Build only a reviewed calibration dataset; never build frozen data here."""
```

Resolve each question-bank row by `source_question_id`; use its reference answer and rubric. For generated rows, require their existing `reference_expectations` and construct the minimal reference answer from those expectations. Convert each issue dict through `ExpectedIssue.model_validate`. Refuse absent scores, dimensions, or issue lists.

- [ ] **Step 4: Write the JSON artifact and validate it through the runner schema**

Run: `.\\.venv\\Scripts\\python.exe -m evaluation.build_answer_annotation_benchmark --template evaluation/annotation_templates/answer-calibration-v2.draft.json --output evaluation/benchmark/answer-calibration-v2.json`

Expected: an `AnswerBenchmarkDataset` JSON with 10 calibration samples and no frozen purpose.

- [ ] **Step 5: Run focused tests and Ruff**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_build_answer_annotation_benchmark.py -q`

Run: `.\\.venv\\Scripts\\python.exe -m ruff check evaluation/build_answer_annotation_benchmark.py tests/evaluation/test_build_answer_annotation_benchmark.py`

Expected: all pass.

### Task 2: Convert the Behavior ablation calibration template

**Files:**
- Create: `evaluation/build_behavior_ablation_benchmark.py`
- Create: `evaluation/benchmark/behavior-ablation-calibration-v3.json`
- Create: `tests/evaluation/test_build_behavior_ablation_benchmark.py`

**Interfaces:**
- Consumes: reviewed behavior template `shared_input`, `variant_input_placeholders`, `human_gold`, and a question bank compatible with every conversation turn.
- Produces: `InterviewBehaviorBenchmarkDataset` with `purpose="calibration"`, explicit `BehaviorAblationInputs`, and one `ExpectedBehavior` per case.

- [ ] **Step 1: Write failing conversion tests**

```python
def test_build_behavior_ablation_keeps_same_seed_for_every_case() -> None:
    dataset = build_behavior_ablation_calibration(template_path, question_bank_path)
    assert dataset.samples[0].ablation_inputs is not None
    assert dataset.samples[0].expected_behavior.should_follow_up is True

def test_build_behavior_ablation_rejects_missing_adapter() -> None:
    with pytest.raises(ValueError, match="job_profile_from_job_facts"):
        build_behavior_ablation_calibration(incomplete_template_path, question_bank_path)
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_build_behavior_ablation_benchmark.py -q`

Expected: failure because the builder does not exist.

- [ ] **Step 3: Implement the smallest builder**

```python
def build_behavior_ablation_calibration(
    template_path: Path, question_bank_path: Path
) -> InterviewBehaviorBenchmarkDataset:
    """Build reviewed calibration fixtures with explicit adapter inputs only."""
```

Use each template `selection_seed` as the target sample ID only when they match; otherwise fail. Convert the four human-gold fields to `ExpectedBehavior`; do not infer or invoke JobFactsExtractor or ProjectSkillMapper. Resolve or add explicit evaluation-only question-bank entries so each conversation turn meets `InterviewBehaviorBenchmarkDataset` validation.

- [ ] **Step 4: Write the JSON artifact and validate it through the runner schema**

Run: `.\\.venv\\Scripts\\python.exe -m evaluation.build_behavior_ablation_benchmark --template evaluation/annotation_templates/behavior-calibration-v3.draft.json --output evaluation/benchmark/behavior-ablation-calibration-v3.json`

Expected: 4 calibration cases, each with fixed selection seed and non-null `ablation_inputs`.

- [ ] **Step 5: Run focused tests and Ruff**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_build_behavior_ablation_benchmark.py -q`

Run: `.\\.venv\\Scripts\\python.exe -m ruff check evaluation/build_behavior_ablation_benchmark.py tests/evaluation/test_build_behavior_ablation_benchmark.py`

Expected: all pass.

### Task 3: Convert the ProjectSkillRelation mapping calibration template

**Files:**
- Create: `evaluation/build_project_skill_mapping_benchmark.py`
- Create: `evaluation/benchmark/project-skill-mapping-calibration-v1.json`
- Create: `tests/evaluation/test_build_project_skill_mapping_benchmark.py`

**Interfaces:**
- Consumes: the 20 `split="calibration"` mapping cases and their reviewed `expected_relations`.
- Produces: `ProjectSkillMappingBenchmarkDataset` with `purpose="calibration"`, `CandidateFacts`, `JobProfile`, `KnowledgeContext`, and exact `ProjectSkillRelation` tuples.

- [ ] **Step 1: Write failing conversion tests**

```python
def test_build_mapping_calibration_excludes_reserved_frozen_cases() -> None:
    dataset = build_project_skill_mapping_calibration(template_path)
    assert len(dataset.samples) == 20
    assert all(not sample.id.startswith("map-frozen-") for sample in dataset.samples)

def test_build_mapping_calibration_preserves_no_relation_gold() -> None:
    dataset = build_project_skill_mapping_calibration(template_path)
    assert dataset.samples[-1].expected_relations == []
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_build_project_skill_mapping_benchmark.py -q`

Expected: failure because the builder does not exist.

- [ ] **Step 3: Implement the smallest builder**

```python
def build_project_skill_mapping_calibration(
    template_path: Path,
) -> ProjectSkillMappingBenchmarkDataset:
    """Build only calibration cases from reviewed mapping annotations."""
```

Map candidate project evidence to one `ProjectFact`, turn the reviewed JD skill key/evidence into a `JobProfile`, and make `KnowledgeContext` contain only the template retrieval context and source refs. Preserve `[]` as no-relation gold; do not create a relation for a negative case.

- [ ] **Step 4: Write the JSON artifact and validate it through the runner schema**

Run: `.\\.venv\\Scripts\\python.exe -m evaluation.build_project_skill_mapping_benchmark --template evaluation/annotation_templates/project-skill-mapping-cases.draft.json --output evaluation/benchmark/project-skill-mapping-calibration-v1.json`

Expected: exactly 20 calibration samples and at least 6 no-relation cases.

- [ ] **Step 5: Run focused tests and Ruff**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_build_project_skill_mapping_benchmark.py -q`

Run: `.\\.venv\\Scripts\\python.exe -m ruff check evaluation/build_project_skill_mapping_benchmark.py tests/evaluation/test_build_project_skill_mapping_benchmark.py`

Expected: all pass.

### Task 4: Integration validation and controlled calibration-run handoff

**Files:**
- Modify: `evaluation/annotation_templates/ANNOTATION_GUIDE.md`
- Test: `tests/evaluation/test_dataset_locks.py`

**Interfaces:**
- Consumes: the three generated calibration artifacts.
- Produces: a verified handoff containing exact calibration-only commands; no report and no frozen artifact.

- [ ] **Step 1: Add a guide section listing the three runnable calibration paths and explicitly listing the untouched frozen drafts**

```markdown
## 已转换的 Calibration 数据

这些 JSON 可以用于 calibration；所有 `*-frozen-test-*.draft.json` 和 `map-frozen-*` 仍不可运行，也未被转换。
```

- [ ] **Step 2: Validate all three generated artifacts with their Pydantic dataset schemas and assert no frozen IDs/purpose appear**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests/evaluation/test_build_answer_annotation_benchmark.py tests/evaluation/test_build_behavior_ablation_benchmark.py tests/evaluation/test_build_project_skill_mapping_benchmark.py tests/evaluation/test_dataset_locks.py -q`

Expected: all pass.

- [ ] **Step 3: Run scoped Ruff and JSON parse checks**

Run: `.\\.venv\\Scripts\\python.exe -m ruff check evaluation/build_answer_annotation_benchmark.py evaluation/build_behavior_ablation_benchmark.py evaluation/build_project_skill_mapping_benchmark.py tests/evaluation/test_build_answer_annotation_benchmark.py tests/evaluation/test_build_behavior_ablation_benchmark.py tests/evaluation/test_build_project_skill_mapping_benchmark.py`

Expected: all pass.

- [ ] **Step 4: Stop before any model call and report the calibration-only commands for a separately authorized run**

```powershell
.\.venv\Scripts\python.exe -m evaluation.run_evaluation --dataset evaluation/benchmark/answer-calibration-v2.json --purpose calibration
.\.venv\Scripts\python.exe -m evaluation.run_behavior_evaluation --dataset evaluation/benchmark/behavior-ablation-calibration-v3.json --purpose calibration --ablation
.\.venv\Scripts\python.exe -m evaluation.run_project_skill_mapping_evaluation --dataset evaluation/benchmark/project-skill-mapping-calibration-v1.json --purpose calibration
```

## Self-Review

- Scope coverage: Tasks 1–3 produce the three runnable calibration artifacts. Task 4 proves they validate and prevents an accidental frozen run.
- Frozen safety: no task writes a frozen runner dataset, lock, report, or model output.
- Type consistency: output types are the exact three existing runner dataset models; adapters are constructed before the existing ablation runner runs.
- No placeholders: every task names exact files, schema contracts, tests, and commands.
