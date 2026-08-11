# ASR Transcript Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Have the existing answer evaluator emit auditable ASR normalizations in the same LLM response and persist the normalized answer without overwriting the ASR original.

**Architecture:** `AnswerEvaluation` carries a nullable compatibility field for the normalized text plus a list of explicit local replacements. New Evaluator responses must contain a non-null normalized text, and server-side validation replays every replacement against the raw answer before accepting the evaluation. `save_evaluation()` atomically stores the normalized text on `interview_answers` and the correction audit trail remains in the existing `answer_evaluations.evaluation_json` snapshot.

**Tech Stack:** Python 3.10, Pydantic v2, SQLAlchemy 2, Alembic, pytest, OpenAI-compatible JSON mode.

## Global Constraints

- `InterviewAnswerRecord.transcript` remains the immutable raw ASR audit record.
- A normalization may only perform high-confidence (at least 0.8) local substitutions for homophones, segmentation, case, or punctuation.
- Every textual change in `normalized_transcript` must be represented by one `TranscriptCorrection`; no correction means the normalized text equals the raw transcript.
- The implementation adds no LLM request: normalization and scoring share the existing Evaluator call.
- Older persisted evaluation JSON without normalization fields must remain readable.
- Do not add ASR protocol, reconnect, or hot-word behavior in this phase.

---

### Task 1: Define auditable normalization output and validate it against raw text

**Files:**
- Modify: `liverag/interview/schemas.py:434-479`
- Modify: `liverag/interview/application/evaluator.py:190-207`
- Modify: `tests/interview/test_evaluator.py`

**Interfaces:**
- Produces `TranscriptCorrection(original: NonEmptyText, replacement: NonEmptyText, confidence: float, reason: Literal[...])`.
- Extends `AnswerEvaluation` with `normalized_transcript: str | None = None` and `transcript_corrections: list[TranscriptCorrection]`.
- Adds `OpenAIAnswerEvaluationProvider._validate_transcript_normalization(evaluation, raw_transcript) -> None`.

- [ ] **Step 1: Write failing provider tests for an accepted local correction and rejected free rewrite**

```python
assert result.normalized_transcript == "我们用 Kafka 处理消息"
assert result.transcript_corrections == [
    TranscriptCorrection(
        original="卡夫卡",
        replacement="Kafka",
        confidence=0.97,
        reason="homophone",
    )
]

with pytest.raises(AnswerEvaluationProviderError, match="normalized_transcript"):
    await provider.evaluate(answer=raw_answer, question=_question())
```

- [ ] **Step 2: Run the evaluator tests and verify the new assertions fail**

Run: `python -m pytest tests/interview/test_evaluator.py -q`

Expected: FAIL because `AnswerEvaluation` has no normalization fields or raw-to-normalized validation.

- [ ] **Step 3: Add the schema and replay validation**

```python
class TranscriptCorrection(StrictModel):
    original: NonEmptyText
    replacement: NonEmptyText
    confidence: float = Field(ge=0.8, le=1.0)
    reason: Literal["homophone", "segmentation", "case_normalization", "punctuation"]


def _validate_transcript_normalization(
    evaluation: AnswerEvaluation,
    raw_transcript: str,
) -> None:
    normalized = evaluation.normalized_transcript
    if normalized is None:
        raise ValueError("normalized_transcript 不能为空")
    replayed = raw_transcript
    for correction in evaluation.transcript_corrections:
        if correction.original not in replayed:
            raise ValueError("transcript_corrections 未命中 raw transcript")
        replayed = replayed.replace(correction.original, correction.replacement, 1)
    if replayed != normalized:
        raise ValueError("normalized_transcript 必须由纠正记录逐项重放得到")
```

Call this validation from `_validate_evaluation()` after the answer and question identity checks. Keep absent normalization fields valid only for historical JSON reads; the provider path rejects them.

- [ ] **Step 4: Run evaluator tests**

Run: `python -m pytest tests/interview/test_evaluator.py -q`

Expected: PASS.

### Task 2: Persist the normalized transcript atomically with evaluation

**Files:**
- Create: `alembic/versions/b8a4c1d6e2f0_add_normalized_transcript.py`
- Modify: `liverag/interview/persistence/models.py:300-350`
- Modify: `liverag/interview/records.py:174-194`
- Modify: `liverag/interview/persistence/sqlalchemy_repository.py:267-281,1231-1264`
- Modify: `tests/interview/test_models.py`
- Modify: `tests/interview/test_sqlalchemy_repository.py`

**Interfaces:**
- Adds nullable `interview_answers.normalized_transcript`.
- Extends `InterviewAnswerRecord` with `normalized_transcript: str | None = None`.
- `SQLAlchemyInterviewRepository.save_evaluation()` writes `evaluation.normalized_transcript` to the answer row in its existing transaction.

- [ ] **Step 1: Write failing persistence tests**

```python
repository.save_evaluation(evaluation_id="evaluation-1", evaluation=evaluation)
stored_answer = repository.get_answer(answer.id)
assert stored_answer.transcript == "我们用卡夫卡处理消息"
assert stored_answer.normalized_transcript == "我们用 Kafka 处理消息"
assert database.get_columns("interview_answers")[-1]["name"] == "normalized_transcript"
```

- [ ] **Step 2: Run the focused persistence tests and verify failure**

Run: `python -m pytest tests/interview/test_models.py tests/interview/test_sqlalchemy_repository.py -q`

Expected: FAIL because neither the model nor record exposes `normalized_transcript`.

- [ ] **Step 3: Add the schema migration and atomic repository update**

```python
# alembic revision b8a4c1d6e2f0, down_revision="4a1d9c7e2b6f"
def upgrade() -> None:
    op.add_column("interview_answers", sa.Column("normalized_transcript", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("interview_answers", "normalized_transcript")


# inside save_evaluation(), before adding AnswerEvaluationModel
answer.normalized_transcript = evaluation.normalized_transcript
answer.state = AnswerState.EVALUATED
```

Update `_answer_record()` to include the nullable column. The correction list needs no extra column because `_model_to_json(evaluation)` already stores it in `answer_evaluations.evaluation_json`.

- [ ] **Step 4: Run persistence tests**

Run: `python -m pytest tests/interview/test_models.py tests/interview/test_sqlalchemy_repository.py -q`

Expected: PASS.

### Task 3: Require constrained normalization in the existing evaluator prompt

**Files:**
- Modify: `liverag/interview/prompts/evaluation_prompts.py:14-55,150-193`
- Modify: `liverag/interview/application/evaluator.py:20`
- Modify: `tests/interview/test_evaluator.py`

**Interfaces:**
- Increments `EVALUATION_PROMPT_VERSION` from `answer-evaluation-v1` to `answer-evaluation-v2`.
- Requires `normalized_transcript` and `transcript_corrections` in every provider response.

- [ ] **Step 1: Add a failing prompt-contract test**

```python
prompt = OpenAIAnswerEvaluationProvider._system_prompt()
assert "ASR 文本规范化" in prompt
assert "normalized_transcript" in prompt
assert "transcript_corrections" in prompt
```

- [ ] **Step 2: Run the prompt-contract test and verify failure**

Run: `python -m pytest tests/interview/test_evaluator.py::test_system_prompt_is_loaded_from_python_constant -q`

Expected: FAIL because the prompt does not yet declare normalization output.

- [ ] **Step 3: Add exact normalization constraints and JSON fields**

```text
先从 candidate_answer 生成 normalized_transcript，再依据它评分。
只允许同音词、缩写拆分/合并、大小写或标点的局部替换；不得增加、删除或改写观点。
只在置信度至少 0.8 且题目、rubric、上下文支持时写入 transcript_corrections。
每个 correction 必须含 original、replacement、confidence、reason，且 original 必须来自 candidate_answer。
无法确定时保持原文，并写入 asr_uncertainties。
```

Add these fields to the JSON example:

```json
"normalized_transcript": "我们用 Kafka 处理消息",
"transcript_corrections": [
  {
    "original": "卡夫卡",
    "replacement": "Kafka",
    "confidence": 0.97,
    "reason": "homophone"
  }
]
```

- [ ] **Step 4: Run evaluator tests**

Run: `python -m pytest tests/interview/test_evaluator.py -q`

Expected: PASS.

### Task 4: Run regression verification

**Files:**
- Modify only files listed in Tasks 1–3 when verification identifies a B+C defect.

- [ ] **Step 1: Run focused B+C tests and static checks**

Run: `python -m pytest tests/interview/test_evaluator.py tests/interview/test_models.py tests/interview/test_sqlalchemy_repository.py tests/interview/test_interview_worker.py -q`

Expected: PASS.

Run: `python -m ruff check liverag/interview/schemas.py liverag/interview/application/evaluator.py liverag/interview/prompts/evaluation_prompts.py liverag/interview/records.py liverag/interview/persistence/models.py liverag/interview/persistence/sqlalchemy_repository.py tests/interview/test_evaluator.py tests/interview/test_models.py tests/interview/test_sqlalchemy_repository.py`

Expected: PASS.

- [ ] **Step 2: Run migration-chain and diff checks**

Run: `python -m alembic heads; git diff --check`

Expected: One Alembic head and no whitespace errors.

## Self-Review

- The evaluator contract, raw-text replay validation, prompt, database column, migration, and tests each have an assigned task.
- The plan never rewrites raw ASR transcript and does not add a second model request.
- Nullable schema defaults preserve reads of historical evaluation JSON, while the live provider path requires normalized output.
