# Question-Type Rubrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Remove the behavioral question type and make the five remaining interview types use distinct, auditable evaluation-weight profiles, including runtime follow-up answers.

**Architecture:** Keep the four existing 0–4 score dimensions. A QuestionRubric profile is selected by question type. A runtime follow-up retains its parent question’s expected points, notes, question text, and reference answer, while replacing only the four dimension weights with the follow-up profile.

**Tech Stack:** Python 3.10+, Pydantic v2, pytest, versioned JSON question banks.

## Global Constraints

- Remove BEHAVIORAL; do not retain a compatibility alias.
- Keep the SCENARIO serialized enum value; its Chinese product-facing name is “真实场景”, avoiding an unnecessary migration of persisted plans.
- The five scored types are TECHNICAL_KNOWLEDGE, PROJECT_DEEP_DIVE, SYSTEM_DESIGN, SCENARIO, and FOLLOW_UP. INTRODUCTION remains flow-only.
- Profiles, in technical accuracy / completeness / clarity / job relevance order:
  - TECHNICAL_KNOWLEDGE: 0.45 / 0.35 / 0.15 / 0.05
  - PROJECT_DEEP_DIVE: 0.25 / 0.25 / 0.20 / 0.30
  - SYSTEM_DESIGN: 0.30 / 0.30 / 0.20 / 0.20
  - SCENARIO (真实场景): 0.25 / 0.25 / 0.20 / 0.30
  - FOLLOW_UP: 0.35 / 0.30 / 0.20 / 0.15
- FOLLOW_UP stays excluded from fixed main-question selection.

---

### Task 1: Add the five-type profile policy

**Files:**
- Modify: liverag/interview/schemas.py, lines 58–67 and 326–359
- Create: tests/interview/test_question_type_rubrics.py

**Interfaces:**
- Produces QuestionRubric.for_question_type(question_type: QuestionType) -> QuestionRubric.
- Produces QuestionRubric.with_dimension_weights_from(profile: QuestionRubric) -> QuestionRubric.

- [ ] **Step 1: Write the failing tests**

~~~python
@pytest.mark.parametrize(
    ("question_type", "weights"),
    [
        (QuestionType.TECHNICAL_KNOWLEDGE, (0.45, 0.35, 0.15, 0.05)),
        (QuestionType.PROJECT_DEEP_DIVE, (0.25, 0.25, 0.20, 0.30)),
        (QuestionType.SYSTEM_DESIGN, (0.30, 0.30, 0.20, 0.20)),
        (QuestionType.SCENARIO, (0.25, 0.25, 0.20, 0.30)),
        (QuestionType.FOLLOW_UP, (0.35, 0.30, 0.20, 0.15)),
    ],
)
def test_question_type_rubric_profiles(question_type, weights):
    rubric = QuestionRubric.for_question_type(question_type)
    assert (
        rubric.technical_accuracy_weight,
        rubric.completeness_weight,
        rubric.clarity_and_structure_weight,
        rubric.job_relevance_weight,
    ) == weights


def test_question_type_no_longer_contains_behavioral():
    assert "BEHAVIORAL" not in QuestionType.__members__
~~~

- [ ] **Step 2: Verify it fails**

Run: .\.venv\Scripts\python.exe -m pytest tests/interview/test_question_type_rubrics.py -q

Expected: FAIL because the factory does not exist and BEHAVIORAL remains in the enum.

- [ ] **Step 3: Implement the minimal schema policy**

Remove BEHAVIORAL from QuestionType. Retain SCENARIO but change its Chinese comment to “真实场景”. Add an exact type-to-weight mapping in QuestionRubric.for_question_type; it raises ValueError("INTRODUCTION 不参与回答评分") for INTRODUCTION. Create the factory model with a valid RubricPoint(id="profile", content="profile"). Implement with_dimension_weights_from with model_copy(update={...}) for only the four weights, preserving expected points and notes.

- [ ] **Step 4: Verify it passes**

Run: .\.venv\Scripts\python.exe -m pytest tests/interview/test_question_type_rubrics.py -q

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add liverag/interview/schemas.py tests/interview/test_question_type_rubrics.py
git commit -m "feat: define question type rubric profiles"
~~~

### Task 2: Apply profiles to both question-bank versions

**Files:**
- Modify: liverag/interview/question_bank/catalog.py, lines 142–149
- Modify: liverag/interview/question_bank/data/question_bank.v1.json
- Modify: liverag/interview/question_bank/data/question_bank.v2.reviewed.json
- Modify: tests/interview/test_question_type_rubrics.py

**Interfaces:**
- Consumes QuestionRubric.for_question_type().
- Produces banks where every non-introduction record’s four weights equal its type profile.

- [ ] **Step 1: Add the failing bank test**

~~~python
@pytest.mark.parametrize(
    "filename", ["question_bank.v1.json", "question_bank.v2.reviewed.json"]
)
def test_every_question_uses_its_type_weight_profile(filename):
    path = Path(__file__).parents[2] / "liverag/interview/question_bank/data" / filename
    bank = QuestionBank.from_file(path)
    for question in bank.filter_questions():
        if question.type is QuestionType.INTRODUCTION:
            continue
        profile = QuestionRubric.for_question_type(question.type)
        assert question.rubric.with_dimension_weights_from(profile) == question.rubric
~~~

- [ ] **Step 2: Verify it fails**

Run: .\.venv\Scripts\python.exe -m pytest tests/interview/test_question_type_rubrics.py::test_every_question_uses_its_type_weight_profile -q

Expected: FAIL because project, system-design, scenario, and follow-up records still use the default weights.

- [ ] **Step 3: Make targeted data and selection changes**

Replace only the four rubric weight fields of every question in both JSON files with the profile matching its type. Preserve IDs, question text, expected points, notes, reference answers, and sources. Remove QuestionType.BEHAVIORAL from _DEFAULT_SELECTABLE_TYPES. Retain SCENARIO, label it “真实场景”, and do not add FOLLOW_UP to selectable types.

- [ ] **Step 4: Verify it passes**

Run: .\.venv\Scripts\python.exe -m pytest tests/interview/test_question_type_rubrics.py -q

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add liverag/interview/question_bank/catalog.py liverag/interview/question_bank/data/question_bank.v1.json liverag/interview/question_bank/data/question_bank.v2.reviewed.json tests/interview/test_question_type_rubrics.py
git commit -m "feat: apply rubric weights by question type"
~~~

### Task 3: Apply the follow-up profile at runtime

**Files:**
- Modify: liverag/interview/application/evaluator.py, lines 120–182 and 250–269
- Modify: tests/interview/test_evaluator.py

**Interfaces:**
- Consumes AnswerEvaluationContext.asked_follow_up_question.
- Produces follow-up weighted scores using FOLLOW_UP weights with parent question scoring points.

- [ ] **Step 1: Write the failing test**

Use a non-null asked_follow_up_question and scores 4, 0, 4, 0; assert weighted_score equals 55.0, calculated as (4*0.35 + 0*0.30 + 4*0.20 + 0*0.15) / 4 * 100. Also assert the prompt still contains the parent expected-point ID.

- [ ] **Step 2: Verify it fails**

Run: .\.venv\Scripts\python.exe -m pytest tests/interview/test_evaluator.py -k follow_up_profile -q

Expected: FAIL because the evaluator always uses question.rubric.

- [ ] **Step 3: Add one effective-rubric method**

~~~python
def _evaluation_rubric(
    self, question: InterviewQuestion, context: AnswerEvaluationContext
) -> QuestionRubric:
    if context.asked_follow_up_question is None:
        return question.rubric
    return question.rubric.with_dimension_weights_from(
        QuestionRubric.for_question_type(QuestionType.FOLLOW_UP)
    )
~~~

Use the effective rubric in _build_prompt, the server-side weighted score calculation, and _validate_evaluation.

- [ ] **Step 4: Verify it passes**

Run: .\.venv\Scripts\python.exe -m pytest tests/interview/test_evaluator.py -q

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add liverag/interview/application/evaluator.py tests/interview/test_evaluator.py
git commit -m "feat: score follow-up answers with follow-up profile"
~~~

### Task 4: Align the prompt and run regressions

**Files:**
- Modify: liverag/interview/prompts/evaluation_prompts.py, scoring-priority section
- Test: tests/interview/test_services.py
- Test: tests/interview/test_state_machine.py

- [ ] **Step 1: Add one prompt sentence**

~~~text
输入 rubric 的四项权重已经按当前题型设定；当前回答若是对追问的回答，权重为追问类型权重，但评分点仍来自主问题 rubric。
~~~

Do not alter the four score dimensions or their definitions.

- [ ] **Step 2: Run regression tests**

Run: .\.venv\Scripts\python.exe -m pytest tests/interview -q

Expected: PASS.

- [ ] **Step 3: Lint changed Python files**

Run: .\.venv\Scripts\python.exe -m ruff check liverag/interview/schemas.py liverag/interview/question_bank/catalog.py liverag/interview/application/evaluator.py tests/interview/test_question_type_rubrics.py tests/interview/test_evaluator.py

Expected: PASS.

- [ ] **Step 4: Confirm runtime type distribution**

~~~powershell
$document = Get-Content -Raw liverag/interview/question_bank/data/question_bank.v1.json | ConvertFrom-Json
$document.questions | Group-Object type | Sort-Object Name | Select-Object Name, Count
~~~

Expected: no BEHAVIORAL; existing FOLLOW_UP records retained; SCENARIO remains the serialized real-world-scenario value.

- [ ] **Step 5: Commit**

~~~bash
git add liverag/interview/prompts/evaluation_prompts.py
git commit -m "docs: describe type-specific answer scoring"
~~~

## Self-Review

- **Spec coverage:** Tasks 1–2 establish exactly five scored types and update both banks; Task 3 handles the separate runtime follow-up path; Task 4 verifies prompt and regression consistency.
- **Placeholder scan:** Weight values, files, interfaces, commands, and expected outcomes are explicit.
- **Type consistency:** The profile factory and weight replacement method introduced in Task 1 are consumed unchanged by Tasks 2 and 3; SCENARIO remains stable in code and stored data.

\n