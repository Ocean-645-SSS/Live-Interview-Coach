# Refresh Answer and Behavior Evaluations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the behavior benchmark against the structured `JobSkill` contract, rerun the complete answer and behavior benchmarks, and update the frozen evaluation summary without changing the preserved RAG or latency results.

**Architecture:** Keep the benchmark dataset's legacy free-text requirements as independent evaluation inputs and convert them to `JobSkill` only at the production-model boundary. Generate new timestamped bundles, freeze new answer/behavior Markdown reports only after complete 50-sample runs, and update the consolidated summary with explicit scope limitations for unchanged RAG and latency reports.

**Tech Stack:** Python 3.10, Pydantic 2, pytest, OpenAI-compatible APIs, Markdown reports.

## Global Constraints

- Do not guess taxonomy mappings for labels that have no exact stable key.
- Do not alter production taxonomy or benchmark labels merely to make tests pass.
- Do not overwrite the existing 2026-08-11 answer/behavior or 2026-08-12 RAG/latency frozen reports.
- A best-version report requires a successful full 50-sample run; smoke or partial reports cannot be frozen.
- Preserve the old comparable model口径: answer evaluator `qwen-flash`, issue judge `qwen-plus`; behavior planner `question-bank-selection`, evaluator/judge `qwen-flash`.
- Do not expose API keys in commands, reports, logs, or conversation output.
- Do not modify unrelated user changes.

---

## File Structure

- Modify `evaluation/evaluators/behavior_evaluator.py`: adapt legacy benchmark requirements to the structured production `JobSkill` boundary.
- Modify `tests/evaluation/test_behavior_evaluator.py`: assert the runner passes structured job skills to the planner.
- Modify `tests/evaluation/test_behavior_planner_integration.py`: construct a valid `JobProfile` at the integration boundary.
- Create timestamped artifacts under `evaluation/reports/`: retain raw answer JSON/YAML/Markdown and behavior JSON/Markdown bundles.
- Create `evaluation/reports/2026-08-22-answer-best-version.md`: freeze the complete answer run.
- Create `evaluation/reports/2026-08-22-behavior-best-version.md`: freeze the complete behavior run.
- Modify `evaluation/reports/evaluation-summary.md`: switch only answer/behavior sources and metrics; preserve RAG/latency numbers and document scope.

### Task 1: Restore the structured behavior benchmark boundary

**Files:**
- Modify: `evaluation/evaluators/behavior_evaluator.py`
- Modify: `tests/evaluation/test_behavior_evaluator.py`
- Modify: `tests/evaluation/test_behavior_planner_integration.py`

**Interfaces:**
- Consumes: `BehaviorJobProfile.requirements: list[str]`.
- Produces: `JobProfile.required_skills: list[JobSkill]` with `skill_id`, `weight=1.0`, and original requirement as `evidence`.

- [ ] **Step 1: Add a failing assertion for the planner input**

Capture the `JobProfile` received by the fake planner and assert every `required_skills` item is a `JobSkill`, retains its original label in `skill_id` and `evidence`, and has weight `1.0`.

- [ ] **Step 2: Verify the focused tests fail**

Run: `\.\.venv\Scripts\python.exe -m pytest tests/evaluation/test_behavior_evaluator.py tests/evaluation/test_behavior_planner_integration.py -q`

Expected: the existing string-to-`JobProfile` construction fails Pydantic validation.

- [ ] **Step 3: Implement the boundary conversion**

Import `JobSkill` and replace direct string-list assignment with:

```python
required_skills=[
    JobSkill(skill_id=requirement, weight=1.0, evidence=requirement)
    for requirement in sample.job_profile.requirements
]
```

Use the same explicit construction in the production-planner integration test. Do not add a fallback mapper or change taxonomy data.

- [ ] **Step 4: Verify behavior and complete evaluation tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/evaluation/test_behavior_planner_integration.py tests/evaluation/test_behavior_evaluator.py tests/evaluation/test_behavior_schemas.py tests/evaluation/test_behavior_cli.py -q
.\.venv\Scripts\python.exe -m pytest tests/evaluation -q
```

Expected: PASS.

### Task 2: Run comparable answer and behavior benchmarks

**Files:**
- Create: `evaluation/reports/<timestamp>-answer-evaluation.json`
- Create: `evaluation/reports/<timestamp>-answer-evaluation.yaml`
- Create: `evaluation/reports/<timestamp>-answer-evaluation.md`
- Create: `evaluation/reports/<timestamp>-interview-behavior-evaluation.json`
- Create: `evaluation/reports/<timestamp>-interview-behavior-evaluation.md`

**Interfaces:**
- Consumes: configured OpenAI-compatible evaluator and judge credentials.
- Produces: complete 50-sample report bundles.

- [ ] **Step 1: Run small paid/network smoke tests**

Run with credentials supplied by existing configuration, never printed:

```powershell
.\.venv\Scripts\python.exe -m evaluation.run_evaluation --limit 2 --concurrency 1 --model qwen-flash --judge-model qwen-plus
.\.venv\Scripts\python.exe -m evaluation.run_behavior_evaluation --limit 1 --concurrency 1 --model qwen-flash --judge-model qwen-flash
```

Expected: both commands create timestamped bundles with the requested sample counts; answer metadata reports `answer-evaluation-v7`.

- [ ] **Step 2: Run complete comparable benchmarks**

```powershell
.\.venv\Scripts\python.exe -m evaluation.run_evaluation --concurrency 1 --model qwen-flash --judge-model qwen-plus
.\.venv\Scripts\python.exe -m evaluation.run_behavior_evaluation --concurrency 1 --model qwen-flash --judge-model qwen-flash
```

Expected: each report contains exactly 50 samples and no partial failures.

- [ ] **Step 3: Validate report metadata and preserve raw bundles**

Verify dataset name/version, sample count, model labels, planner label, generated timestamp, and answer prompt v7. Compute SHA-256 for datasets and raw JSON artifacts and record them in the frozen Markdown reports. Record the exact command and current Git revision plus dirty-worktree status.

### Task 3: Freeze reports and update the consolidated summary

**Files:**
- Create: `evaluation/reports/2026-08-22-answer-best-version.md`
- Create: `evaluation/reports/2026-08-22-behavior-best-version.md`
- Modify: `evaluation/reports/evaluation-summary.md`

**Interfaces:**
- Consumes: validated complete report bundles from Task 2.
- Produces: two new immutable best-version Markdown reports and an updated four-part summary.

- [ ] **Step 1: Freeze complete answer and behavior reports**

Copy the validated complete Markdown content into new dated files without overwriting old frozen reports. Add raw bundle paths, SHA-256 hashes, command, revision/dirty status, and the behavior limitation that legacy free-text requirements do not fully exercise taxonomy-key weighting or `ProjectSkillRelation` mapping.

- [ ] **Step 2: Update only affected summary sections**

Replace answer/behavior source links, metadata, metrics, and concluding numbers. Leave all RAG and latency metrics unchanged. Add:

```markdown
RAG 检索与生产延迟沿用 2026-08-12 冻结结果。本轮未重跑这两项；其数值仅代表原报告定义的被测链路，不覆盖本次新增链路。
```

State that RAG does not cover JobFacts/ProjectSkillMapper raw-context mapping quality, and latency does not cover asynchronous preparation, draft persistence/recovery, or report retry.

- [ ] **Step 3: Final verification**

Run the full `tests/evaluation` suite again, verify both best-version links resolve, and confirm every summary metric equals its referenced report. Do not promote smoke/partial artifacts.

\n