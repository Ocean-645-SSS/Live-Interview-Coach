"""OpenAI-compatible 回答评价 Provider 的结构化输出测试。"""

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import AsyncOpenAI

from liverag.config.settings import VoiceSettings
from liverag.interview.application.evaluator import (
    AnswerEvaluationContext,
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
            "normalized_transcript": "先召回候选文档，再将相关上下文交给模型生成答案。",
            "transcript_corrections": [],
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


def test_provider_prompt_contains_progressive_follow_up_context():
    provider, _ = _provider([])
    previous_answer = replace(
        _answer(),
        id="answer-previous",
        answer_number=1,
        transcript="我们使用 Redis 缓存热点数据，并设置了失效策略。",
    )

    prompt = provider._build_prompt(
        _answer(),
        _question(),
        AnswerEvaluationContext(
            prior_answers=(previous_answer,),
            follow_up_round=1,
            max_follow_ups=3,
        ),
    )

    assert "follow_up_round: 1" in prompt
    assert "remaining_follow_ups: 2" in prompt
    assert "allow_follow_up: True" in prompt
    assert "prior_candidate_answers:" in prompt
    assert "Redis 缓存热点数据" in prompt


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


async def test_provider_retries_follow_up_missing_required_fields_with_invalid_json_context():
    invalid_payload = json.loads(_evaluation_json())
    invalid_payload.update(
        {
            "next_action": "FOLLOW_UP",
            "follow_up_target": None,
            "follow_up_question": None,
        }
    )
    repaired_payload = dict(invalid_payload)
    repaired_payload.update(
        {
            "follow_up_target": "retrieval design",
            "follow_up_question": "How do you validate retrieval quality?",
        }
    )
    provider, completions = _provider(
        [
            json.dumps(invalid_payload, ensure_ascii=False),
            json.dumps(repaired_payload, ensure_ascii=False),
        ]
    )

    result = await provider.evaluate(answer=_answer(), question=_question())

    assert result.follow_up_question == "How do you validate retrieval quality?"
    assert len(completions.calls) == 2
    repair_message = completions.calls[1]["messages"][-1]["content"]
    assert "follow_up_target 和 follow_up_question 都必须是非空字符串" in repair_message
    assert json.dumps(invalid_payload, ensure_ascii=False) in repair_message


async def test_provider_rejects_identity_drift_after_retry():
    provider, _ = _provider(
        [
            _evaluation_json(answer_id="wrong-answer"),
            _evaluation_json(answer_id="wrong-answer"),
        ]
    )

    with pytest.raises(AnswerEvaluationProviderError, match="answer_id"):
        await provider.evaluate(answer=_answer(), question=_question())


async def test_provider_accepts_auditable_transcript_correction():
    raw_answer = replace(_answer(), transcript="我们用卡夫卡处理消息。")
    payload = json.loads(_evaluation_json())
    payload["normalized_transcript"] = "我们用Kafka处理消息。"
    payload["transcript_corrections"] = [
        {
            "original": "卡夫卡",
            "replacement": "Kafka",
            "confidence": 0.97,
            "reason": "homophone",
        }
    ]
    provider, _ = _provider([json.dumps(payload, ensure_ascii=False)])

    result = await provider.evaluate(answer=raw_answer, question=_question())

    assert result.normalized_transcript == "我们用Kafka处理消息。"
    assert result.transcript_corrections[0].original == "卡夫卡"
    assert result.transcript_corrections[0].replacement == "Kafka"


async def test_provider_rejects_normalization_that_is_not_backed_by_corrections():
    raw_answer = replace(_answer(), transcript="我们用卡夫卡处理消息。")
    payload = json.loads(_evaluation_json())
    payload["normalized_transcript"] = "我们用 Kafka 处理消息。"
    payload["transcript_corrections"] = []
    response = json.dumps(payload, ensure_ascii=False)
    provider, _ = _provider([response, response])

    with pytest.raises(AnswerEvaluationProviderError, match="normalized_transcript"):
        await provider.evaluate(answer=raw_answer, question=_question())


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
    assert "ASR 文本规范化" in prompt
    assert "normalized_transcript" in prompt
    assert "transcript_corrections" in prompt
    assert "项目实践题与空泛回答约束" in prompt
    assert "practical-evidence" in prompt
    assert "关键词堆砌/空泛回答" in prompt
    assert "只给结论，没有解释其原理、机制、原因或步骤" in prompt
    assert "没有任何技术细节、可验证示例或实践内容" in prompt
    assert "weighted_score` 因而不得超过 **25 分**" in prompt
    assert '"covered_points": ["评分点ID：实际覆盖内容"]' in prompt
    assert "高质量回答的递进式追问" in prompt
    assert "验证经历与设计选择" in prompt
    assert "权衡与扩容优化" in prompt
