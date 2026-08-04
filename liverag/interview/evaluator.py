"""回答评价的 Provider 边界与持久化应用服务。
负责把Answer+InterviewQuestion -> 评价Provider -> AnswerEvaluation"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import ValidationError

from liverag.config.settings import VoiceSettings
from liverag.interview.evaluation_prompts import ANSWER_EVALUATION_SYSTEM_PROMPT
from liverag.interview.records import InterviewAnswerRecord, generate_id
from liverag.interview.repository import InterviewRepository
from liverag.interview.schemas import AnswerEvaluation, InterviewQuestion

EVALUATION_PROMPT_VERSION = "answer-evaluation-v1"


class AnswerEvaluationProviderError(RuntimeError):
    """模型调用失败或模型输出无法形成可信的结构化评价。"""


@dataclass(frozen=True, slots=True)
class OpenAIAnswerEvaluationSettings:
    """OpenAI-compatible LLM 回答评价调用配置。"""

    model: str
    base_url: str
    api_key: str
    timeout_seconds: float = 30.0   #超时
    max_transcript_chars: int = 12000   #用户回答最大字数
    max_reference_answer_chars: int = 12000   #参考答案最大字数

    @classmethod
    def from_voice_settings(
        cls,
        voice: VoiceSettings,
    ) -> OpenAIAnswerEvaluationSettings:
        return cls(
            model=voice.llm_model,
            base_url=voice.llm_base_url,
            api_key=voice.llm_api_key,
        )


class AnswerEvaluationProvider(Protocol):
    """根据题目 rubric 和最终回答生成结构化评价。"""

    async def evaluate(
        self,
        *,
        answer: InterviewAnswerRecord,
        question: InterviewQuestion,
    ) -> AnswerEvaluation: ...


class OpenAIAnswerEvaluationProvider:
    """调用 OpenAI-compatible 模型生成基于 rubric 的结构化评价。"""

    def __init__(
        self,
        settings: OpenAIAnswerEvaluationSettings,
        *,
        client: AsyncOpenAI | None = None,
    ):
        if not settings.api_key.strip():
            raise ValueError("回答评价需要配置 LLM API Key")
        self._settings = settings
        self._client = client or AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
        )

    async def evaluate(
        self,
        *,
        answer: InterviewAnswerRecord,
        question: InterviewQuestion,
    ) -> AnswerEvaluation:
        """通过已知的answer+question，调用LLM返回评价结果"""

        #构造prompt
        prompt = self._build_prompt(answer, question)

        try:
            validation_error: ValidationError | ValueError | None = None

            #最多尝试调用 LLM 2次
            for attempt in range(2):
                messages: list[ChatCompletionMessageParam] = [
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ]
                #第一次结果不合格
                if validation_error is not None:
                    #让LLM修复错误重新来
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "上一份 JSON 未通过校验，请修正后重新输出完整 JSON。"
                                f"\n校验错误：{validation_error}"
                            ),
                        }
                    )
                #调用LLM
                response = await self._client.chat.completions.create(
                    model=self._settings.model,
                    messages=messages,
                    temperature=0.0,
                    response_format={"type": "json_object"},
                )
                #生成答案
                content = response.choices[0].message.content or ""

                try:
                    #把LLM但回的content转换为AnswerEvaluation
                    evaluation = AnswerEvaluation.model_validate_json(
                        self._clean_json_response(content)
                    )

                    #校验evaluation是否合格
                    self._validate_evaluation(evaluation, answer, question)

                    return evaluation
                #捕捉失败
                except (ValidationError, ValueError) as exc:
                    validation_error = exc
                    if attempt == 1:
                        raise
            raise AnswerEvaluationProviderError("模型没有返回可校验的评价")

        #尝试2次都失败，抛出异常
        except Exception as exc:
            raise AnswerEvaluationProviderError(
                f"回答评价失败：{answer.id}：{type(exc).__name__}: {exc}"
            ) from exc

    def _build_prompt(
        self,
        answer: InterviewAnswerRecord,
        question: InterviewQuestion,
    ) -> str:
        """构造模型提示词"""

        #根据config截取用户回答和参考答案
        transcript = answer.transcript[: self._settings.max_transcript_chars]
        reference_answer = (question.reference_answer or "")[
            : self._settings.max_reference_answer_chars
        ]

        return (
            f"prompt_version: {EVALUATION_PROMPT_VERSION}\n"
            f"answer_id: {answer.id}\n"
            f"question_id: {question.id}\n"
            f"question: {question.question_text}\n"
            f"objective: {question.objective}\n"
            f"rubric: {question.rubric.model_dump_json()}\n"
            "<reference_answer>\n"
            f"{reference_answer}\n"
            "</reference_answer>\n"
            "<candidate_answer>\n"
            f"{transcript}\n"
            "</candidate_answer>"
        )

    @staticmethod
    def _system_prompt() -> str:
        """返回随代码发布的回答评价系统提示词。"""

        if not ANSWER_EVALUATION_SYSTEM_PROMPT.strip():
            raise AnswerEvaluationProviderError("回答评价系统 Prompt 不能为空")
        return ANSWER_EVALUATION_SYSTEM_PROMPT

    @staticmethod
    def _validate_evaluation(
        evaluation: AnswerEvaluation,
        answer: InterviewAnswerRecord,
        question: InterviewQuestion,
    ) -> None:
        """校验评价是否合法"""

        if evaluation.answer_id != answer.id:
            raise ValueError("评价结果的 answer_id 与输入不一致")
        if evaluation.question_id != question.id:
            raise ValueError("评价结果的 question_id 与输入不一致")
        expected_score = evaluation.scores.calculate_weighted_score(question.rubric)
        if abs(evaluation.weighted_score - expected_score) > 1e-9:
            raise ValueError(
                "weighted_score 与 rubric 加权结果不一致："
                f"期望 {expected_score}，实际 {evaluation.weighted_score}"
            )

    @staticmethod
    def _clean_json_response(content: str) -> str:
        """清理 LLM 输出"""

        cleaned = content.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        return cleaned.strip()


class AnswerEvaluator:
    """完整校验 Provider 输出归属并保存评价。"""

    def __init__(
        self,
        repository: InterviewRepository,
        provider: AnswerEvaluationProvider,
    ):
        self._repository = repository
        self._provider = provider

    async def evaluate(self, answer_id: str) -> AnswerEvaluation:
        """对answer做出评价"""

        #获得答案
        answer = self._repository.get_answer(answer_id)
        #获得答案对应的session
        session = self._repository.get_session(answer.session_id)
        #获得session对应的plan
        plan = self._repository.get_interview_plan(session.interview_id)
        if plan is None:
            raise ValueError("回答对应的面试计划不存在")

        #查找当前answer对应的问题是在当前plan中的哪一个题目
        question = next(
            (item for item in plan.questions if item.id == answer.question_id),
            None,
        )
        if question is None:
            raise ValueError(f"面试计划中不存在题目：{answer.question_id}")

        #做出评价
        evaluation = await self._provider.evaluate(answer=answer, question=question)
        if evaluation.answer_id != answer.id or evaluation.question_id != question.id:
            raise ValueError("评价结果的回答或题目标识与请求不一致")

        #保存结构化评价，记录rubric版本
        return self._repository.save_evaluation(
            evaluation_id=generate_id("evaluation"),
            evaluation=evaluation,
            rubric_version=plan.plan_version,
        )


__all__ = [
    "EVALUATION_PROMPT_VERSION",
    "AnswerEvaluationProvider",
    "AnswerEvaluationProviderError",
    "AnswerEvaluator",
    "OpenAIAnswerEvaluationProvider",
    "OpenAIAnswerEvaluationSettings",
]
