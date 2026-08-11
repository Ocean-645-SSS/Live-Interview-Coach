# Answer Benchmark Calibration v1.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the 50-sample answer benchmark with its intended technical-knowledge and project-practice evaluation contracts, then reduce lenient scores for vague answers.

**Architecture:** The dataset contains two explicit question contracts: project-practice questions require a second practical-evidence rubric point, while technical-knowledge questions do not penalize a correct conceptual answer merely for lacking experience. Human labels are recalibrated against each contract. The production evaluator prompt adds bounded handling for generic, non-evidenced answers.

**Tech Stack:** Existing Pydantic interview schemas, JSON benchmark data, production evaluation prompt, pytest.

## Global Constraints

- Do not add a fifth production score dimension or change the weighted-score formula.
- Do not alter persistence, state-machine, or runtime interview flow.
- Preserve 50 benchmark samples and ten samples per answer-quality category.
- Increment both dataset and evaluator prompt versions because the benchmark contract changes.

---

### Task 1: Encode question contracts and recalibrate labels

**Files:**
- Modify: `evaluation/schemas.py`
- Modify: `evaluation/evaluators/answer_evaluator.py`
- Modify: `evaluation/benchmark/interview_answer_dataset.json`
- Modify: `tests/evaluation/test_dataset.py`

**Interfaces:**
- Consumes: `AnswerBenchmarkSample.evaluation_contract` with `technical_knowledge` or `project_practice`.
- Produces: in-memory `InterviewQuestion` objects whose project-practice contract adds a required practical-evidence rubric point.

- [ ] Add tests verifying that project-practice samples produce a required practical evidence point and technical-knowledge samples do not.
- [ ] Mark five domains as project-practice and add question-specific practical evidence to their correct answers.
- [ ] Keep five domains as technical-knowledge and raise concept-only human scores to the calibrated 75–85 range.
- [ ] Recalibrate partial-answer human scores and dimension labels to their real omissions.
- [ ] Run `pytest tests/evaluation/test_dataset.py tests/evaluation/test_answer_evaluator.py -q`.

### Task 2: Tighten vague-answer scoring instructions

**Files:**
- Modify: `liverag/interview/prompts/evaluation_prompts.py`
- Modify: `liverag/interview/application/evaluator.py`
- Modify: `tests/interview/test_evaluator.py`

**Interfaces:**
- Consumes: rubric and candidate answer in the existing provider prompt.
- Produces: `answer-evaluation-v3`, which caps generic non-evidenced answers while preserving correct concise answers.

- [ ] Add a prompt assertion for the vague-answer cap and the project-practice contract.
- [ ] Add explicit score limits for generic statements that supply no relevant mechanism, example, or rubric point.
- [ ] Add explicit completeness and job-relevance limits when a required practical-evidence point is missing.
- [ ] Run `pytest tests/interview/test_evaluator.py -q`.

### Task 3: Verify the calibration contract

**Files:**
- Modify: `evaluation/README.md`

**Interfaces:**
- Consumes: completed v1.3 dataset and v3 evaluator prompt.
- Produces: clear instructions that report comparisons require matching dataset and prompt versions.

- [ ] Document the two evaluation contracts and the required report version fields.
- [ ] Run `ruff check evaluation tests/evaluation liverag/interview/application/evaluator.py`.
- [ ] Run `pytest tests/evaluation tests/interview/test_evaluator.py -q`.
