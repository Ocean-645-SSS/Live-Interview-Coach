"""提取简历事实 — Application Service。

从候选人文档中提取结构化事实（CandidateFacts），不做推理或评价。
按计划抽取自 resume_parse_task handler，使 handler 变为薄层调度。
"""

from __future__ import annotations

import logging
import re

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from liverag.interview.application.profile_service import KnowledgeContextSource
from liverag.interview.prompts.resume_parse_prompts import (
    RESUME_FACTS_EXTRACTION_SYSTEM_PROMPT,
)
from liverag.interview.schemas import CandidateFacts

logger = logging.getLogger("liverag.interview.application.resume_parser")


def _clean_json_response(content: str) -> str:
    """清理 LLM 输出的 Markdown 代码块标记和首尾空白。"""
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


class ResumeParser:
    """简历文档事实抽取器。
    封装了 RAG 检索 → LLM 事实抽取 → Pydantic 校验的完整流程。Worker handler 只需调用 parse()，不关心内部实现。
    """

    def __init__(
        self,
        *,
        profile_source: KnowledgeContextSource,
        llm_client: AsyncOpenAI,
        llm_model: str,
    ) -> None:
        self._profile_source = profile_source
        self._llm_client = llm_client
        self._llm_model = llm_model

    async def parse(
        self,
        *,
        kb_id: str = "default",
        document_ids: list[str] | None = None,
        job_id: str = "",
    ) -> CandidateFacts:
        """从知识库检索候选人文档并提取结构化事实"""

        # =============== RAG 检索 ===============
        logger.info(
            "开始检索知识库文档",
            extra={"job_id": job_id, "kb_id": kb_id, "document_ids": document_ids or []},
        )

        knowledge = await self._profile_source.retrieve(
            kb_id=kb_id,
            query=(
                "整理候选人的姓名、工作经历、项目经历、技术技能。"
                "提取所有可验证的客观事实。"
            ),
        )

        context = knowledge.context
        evidence_refs = list(knowledge.evidence_refs)
        if not context:
            raise ValueError(f"知识库没有可用于准备面试的内容：{kb_id}")

        logger.info(
            "知识库检索完成",
            extra={
                "job_id": job_id,
                "kb_id": kb_id,
                "context_chars": len(context),
                "evidence_count": len(evidence_refs),
            },
        )

        # =============== LLM 事实抽取 ===============
        user_prompt = (
            f"kb_id: {kb_id}\n\n"
            "<candidate_documents>\n"
            f"{context}\n"
            "</candidate_documents>"
        )

        return await self._llm_extract(
            kb_id=kb_id,
            user_prompt=user_prompt,
            evidence_refs=evidence_refs,
            job_id=job_id,
        )

    async def _llm_extract(
        self,
        *,
        kb_id: str,
        user_prompt: str,
        evidence_refs: list[str],
        job_id: str,
    ) -> CandidateFacts:
        """调用 LLM 进行事实抽取，最多重试 2 次。"""

        validation_error: Exception | None = None

        for attempt in range(2):
            messages: list[ChatCompletionMessageParam] = [
                {"role": "system", "content": RESUME_FACTS_EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            if validation_error is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "上一份 JSON 未通过校验，请修正后重新输出完整 JSON。"
                            f"\n校验错误：{validation_error}"
                        ),
                    }
                )

            llm_response = await self._llm_client.chat.completions.create(
                model=self._llm_model,
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"},
            )

            content = llm_response.choices[0].message.content or ""

            try:
                facts = CandidateFacts.model_validate_json(
                    _clean_json_response(content)
                )

                # 如果 LLM 未返回 kb_id 或返回错误，用输入值覆盖
                if not facts.kb_id or facts.kb_id != kb_id:
                    facts = facts.model_copy(update={"kb_id": kb_id})

                # 如果 LLM 未返回 raw_evidence_refs，使用 RAG 返回的来源
                if not facts.raw_evidence_refs:
                    facts = facts.model_copy(
                        update={"raw_evidence_refs": evidence_refs}
                    )

                logger.info(
                    "简历事实抽取完成",
                    extra={
                        "job_id": job_id,
                        "kb_id": kb_id,
                        "skills_count": len(facts.skills),
                        "projects_count": len(facts.projects),
                        "work_experience_count": len(facts.work_experience),
                    },
                )

                return facts

            except Exception as exc:
                validation_error = exc
                if attempt == 1:
                    raise RuntimeError(
                        f"LLM 解析简历事实失败（已重试）：{type(exc).__name__}: {exc}"
                    ) from exc
                logger.warning(
                    "LLM 输出校验失败，准备重试",
                    extra={"job_id": job_id, "attempt": attempt + 1, "error": str(exc)},
                )

        raise RuntimeError("LLM 没有返回可校验的 CandidateFacts")


__all__ = ["ResumeParser"]
