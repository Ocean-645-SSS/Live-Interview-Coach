"""OpenAI-compatible 回答评价 Provider 的结构化输出测试。"""

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import AsyncOpenAI

from liverag.config.settings import VoiceSettings
from liverag.interview.evaluator import (
    AnswerEvaluationProviderError,
    OpenAIAnswerEvaluationProvider,
    OpenAIAnswerEvaluationSettings,
)
from liverag.interview.records import AnswerState, InterviewAnswerRecord
from liverag.interview.schemas import (
    InterviewDifficulty,
    InterviewQuestion,
    QuestionRubric,
    QuestionSource,
    QuestionType,
    RubricPoint,
)


class _FakeCompletions:
    def __init__(self, contents: list[str]):
        self.contents = contents
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.contents.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def _provider(contents: list[str]):
    completions = _FakeCompletions(contents)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    provider = OpenAIAnswerEvaluationProvider(
        OpenAIAnswerEvaluationSettings(
            model="test-model",
            base_url="https://example.test/v1",
            api_key="secret",
        ),
        client=cast(AsyncOpenAI, client),
    )
    return provider, completions


def _answer() -> InterviewAnswerRecord:
    return InterviewAnswerRecord(
        id="answer-1",
        session_id="session-1",
        question_id="question-1",
        attempt_id="attempt-1",
        answer_number=1,
        transcript="先召回候选文档，再将相关上下文交给模型生成答案。",
        state=AnswerState.RECEIVED,
        source_event_id="event-1",
        started_at="2026-08-03T00:00:00+00:00",
        ended_at="2026-08-03T00:01:00+00:00",
        created_at="2026-08-03T00:01:00+00:00",
        updated_at="2026-08-03T00:01:00+00:00",
    )


def _question() -> InterviewQuestion:
    return InterviewQuestion(
        id="question-1",
        order=1,
        type=QuestionType.TECHNICAL_KNOWLEDGE,
        source=QuestionSource.QUESTION_BANK,
        difficulty=InterviewDifficulty.INTERMEDIATE,
        category="RAG",
        topics=["检索增强生成"],
        question_text="RAG 的基本流程是什么？",
        objective="检查候选人是否理解检索和生成链路",
        rubric=QuestionRubric(
            expected_points=[
                RubricPoint(id="retrieval", content="说明检索过程", required=True)
            ]
        ),
        reference_answer="先检索相关资料，再结合资料生成答案。",
    )


def _evaluation_json(*, answer_id: str = "answer-1", weighted_score: float = 75.0):
    return json.dumps(
        {
            "answer_id": answer_id,
            "question_id": "question-1",
            "scores": {
                "technical_accuracy": 3,
                "completeness": 3,
                "clarity_and_structure": 3,
                "job_relevance": 3,
            },
            "weighted_score": weighted_score,
            "covered_points": ["说明了检索和生成"],
            "missing_points": [],
            "errors": [],
            "summary": "回答正确覆盖了基本流程。",
            "next_action": "NEXT_QUESTION",
            "follow_up_target": None,
            "follow_up_question": None,
        },
        ensure_ascii=False,
    )


async def test_provider_returns_validated_evaluation():
    provider, completions = _provider([f"```json\n{_evaluation_json()}\n```"])

    result = await provider.evaluate(answer=_answer(), question=_question())

    assert result.answer_id == "answer-1"
    assert result.weighted_score == 75.0
    assert completions.calls[0]["temperature"] == 0.0
    assert completions.calls[0]["response_format"] == {"type": "json_object"}


async def test_provider_recalculates_weighted_score_without_retry():
    provider, completions = _provider(
        [
            _evaluation_json(weighted_score=99.0),
            _evaluation_json(),
        ]
    )

    result = await provider.evaluate(answer=_answer(), question=_question())

    assert result.weighted_score == 75.0
    assert len(completions.calls) == 1


async def test_provider_rejects_identity_drift_after_retry():
    provider, _ = _provider(
        [
            _evaluation_json(answer_id="wrong-answer"),
            _evaluation_json(answer_id="wrong-answer"),
        ]
    )

    with pytest.raises(AnswerEvaluationProviderError, match="answer_id"):
        await provider.evaluate(answer=_answer(), question=_question())


def test_settings_reuse_voice_llm_configuration():
    voice = VoiceSettings(
        llm_model="qwen-plus",
        llm_base_url="https://dashscope.example/v1",
        llm_api_key="voice-secret",
    )

    settings = OpenAIAnswerEvaluationSettings.from_voice_settings(voice)

    assert settings.model == "qwen-plus"
    assert settings.base_url == "https://dashscope.example/v1"
    assert settings.api_key == "voice-secret"


def test_system_prompt_is_loaded_from_python_constant():
    prompt = OpenAIAnswerEvaluationProvider._system_prompt()

    assert "严格、一致、可审计" in prompt
    assert '"covered_points": ["评分点ID：实际覆盖内容"]' in prompt
