"""从 JD 知识库提取可追溯的 JobFacts。"""

from __future__ import annotations

import json
import logging
import re

from openai import AsyncOpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam

from liverag.interview.application.profile_service import KnowledgeContextSource
from liverag.interview.prompts.job_facts_prompts import (
    JOB_FACTS_EXTRACTION_SYSTEM_PROMPT,
)
from liverag.interview.schemas import (
    FactsExtractionMode,
    JobFacts,
    JobFrameworkToolFact,
    JobSkillFact,
)
from liverag.interview.skill_progress.taxonomy import SkillTaxonomy

logger = logging.getLogger("liverag.interview.application.job_facts_extractor")


def _clean_json_response(content: str) -> str:
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _normalize_evidence(text: str) -> str:
    return " ".join(text.casefold().split())


class JobFactsExtractor:
    """JD RAG 检索、结构化抽取和事实校验的单次边界服务。"""

    def __init__(
        self,
        *,
        profile_source: KnowledgeContextSource,
        llm_client: AsyncOpenAI,
        llm_model: str,
        taxonomy: SkillTaxonomy,
    ) -> None:
        self._profile_source = profile_source
        self._llm_client = llm_client
        self._llm_model = llm_model
        self._taxonomy = taxonomy

    async def parse(
        self,
        *,
        kb_id: str,
        role: str,
        company: str | None = None,
        job_id: str = "",
    ) -> JobFacts:
        knowledge = await self._profile_source.retrieve(
            kb_id=kb_id,
            query=(
                f"整理 {company or '目标公司'} 的 {role} 岗位职责、任职资格、加分项、"
                "工作经验和学历要求。仅保留原文中明确出现的信息。"
            ),
        )
        if not knowledge.context:
            raise ValueError(f"知识库没有可用于准备面试的岗位内容：{kb_id}")

        allowed_taxonomy = [
            {
                "skill_id": skill.key,
                "category": skill.category,
                "subcategory": skill.subcategory,
            }
            for skill in self._taxonomy.skills
        ]
        user_prompt = json.dumps(
            {
                "kb_id": kb_id,
                "target_role": role,
                "company": company,
                "job_context": knowledge.context,
                "allowed_taxonomy": allowed_taxonomy,
            },
            ensure_ascii=False,
        )
        try:
            return await self._llm_extract(
                kb_id=kb_id,
                user_prompt=user_prompt,
                context=knowledge.context,
                evidence_refs=list(knowledge.evidence_refs),
                job_id=job_id,
            )
        except (OpenAIError, RuntimeError):
            logger.exception(
                "岗位事实 LLM 抽取失败，使用规则降级结果",
                extra={"job_id": job_id, "kb_id": kb_id},
            )
            return self._fallback_facts(
                kb_id=kb_id,
                role=role,
                context=knowledge.context,
                evidence_refs=list(knowledge.evidence_refs),
            )

    async def _llm_extract(
        self,
        *,
        kb_id: str,
        user_prompt: str,
        context: str,
        evidence_refs: list[str],
        job_id: str,
    ) -> JobFacts:
        validation_error: Exception | None = None

        for attempt in range(2):
            messages: list[ChatCompletionMessageParam] = [
                {"role": "system", "content": JOB_FACTS_EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
            if validation_error is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "上一份 JSON 未通过校验。请根据校验错误修正后重新输出完整 JSON。"
                            f"\n校验错误：{validation_error}"
                        ),
                    }
                )

            response = await self._llm_client.chat.completions.create(
                model=self._llm_model,
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"},
            )

            try:
                content = response.choices[0].message.content or ""
                facts = JobFacts.model_validate_json(_clean_json_response(content))
                self._validate_facts(facts, context)
                facts = facts.model_copy(
                    update={
                        "kb_id": kb_id,
                        "raw_text": context,
                        "raw_evidence_refs": evidence_refs,
                        "extraction_mode": FactsExtractionMode.LLM,
                        "confidence": 0.9,
                    }
                )
                logger.info(
                    "岗位事实抽取完成",
                    extra={
                        "job_id": job_id,
                        "kb_id": kb_id,
                        "required_skills_count": len(facts.required_skills),
                        "preferred_skills_count": len(facts.preferred_skills),
                    },
                )
                return facts
            except (AttributeError, IndexError, TypeError, ValueError) as exc:
                validation_error = exc
                if attempt == 1:
                    raise RuntimeError(
                        "LLM 解析岗位事实失败（已重试）："
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                logger.warning(
                    "岗位事实 LLM 输出校验失败，准备重试",
                    extra={"job_id": job_id, "attempt": attempt + 1, "error": str(exc)},
                )

        raise RuntimeError("LLM 没有返回可校验的 JobFacts")

    def _validate_facts(self, facts: JobFacts, context: str) -> None:
        known_ids = {skill.key for skill in self._taxonomy.skills}
        source = _normalize_evidence(context)
        for item in (*facts.required_skills, *facts.preferred_skills):
            if item.skill_id not in known_ids:
                raise ValueError(f"skill_id 不在当前 taxonomy 中：{item.skill_id}")
            if _normalize_evidence(item.evidence) not in source:
                raise ValueError(f"技能 evidence 未在 JD 上下文中出现：{item.skill_id}")

    def _fallback_facts(
        self,
        *,
        kb_id: str,
        role: str,
        context: str,
        evidence_refs: list[str],
    ) -> JobFacts:
        skills = self._extract_explicit_skills(context)
        required_skills: list[JobSkillFact] = []
        preferred_skills: list[JobSkillFact] = []
        for skill_id, evidence in skills:
            target = preferred_skills if _is_preferred_evidence(evidence) else required_skills
            target.append(JobSkillFact(skill_id=skill_id, evidence=evidence))

        return JobFacts(
            kb_id=kb_id,
            job_title=role,
            raw_text=context,
            responsibilities=_matching_lines(
                context, ("负责", "参与", "实现", "设计", "协助")
            ),
            required_skills=required_skills,
            preferred_skills=preferred_skills,
            frameworks_tools=_extract_frameworks_tools(context),
            experience_requirements=_matching_lines(
                context, ("经验", "实习", "年", "应届")
            ),
            education_requirements=_matching_lines(
                context, ("学历", "本科", "硕士", "博士", "专业")
            ),
            raw_evidence_refs=evidence_refs,
            extraction_mode=FactsExtractionMode.FALLBACK,
            confidence=0.35,
        )

    def _extract_explicit_skills(self, context: str) -> list[tuple[str, str]]:
        normalized_context = _normalize_evidence(context)
        selected: dict[str, str] = {}
        generic_by_category = {
            _normalize_evidence(skill.category): skill
            for skill in self._taxonomy.skills
            if _normalize_evidence(skill.subcategory) in {"通用", "general"}
        }

        for skill in self._taxonomy.skills:
            term = _normalize_evidence(skill.subcategory)
            if len(term) < 3 or term in {"通用", "general"}:
                continue
            if term in normalized_context:
                selected.setdefault(skill.key, _evidence_line(context, skill.subcategory))

        for category, skill in generic_by_category.items():
            if len(category) >= 3 and category in normalized_context:
                selected.setdefault(skill.key, _evidence_line(context, skill.category))

        return list(selected.items())


def _evidence_line(context: str, term: str) -> str:
    normalized_term = term.casefold()
    for line in _split_lines(context):
        if normalized_term in line.casefold():
            return line
    return term


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in re.split(r"[\r\n。；;!?]+", text) if line.strip()]


def _matching_lines(text: str, markers: tuple[str, ...]) -> list[str]:
    return [
        line
        for line in _split_lines(text)
        if any(marker.casefold() in line.casefold() for marker in markers)
    ][:5]


def _is_preferred_evidence(evidence: str) -> bool:
    lowered = evidence.casefold()
    return any(marker in lowered for marker in ("优先", "加分", "preferred", "plus"))


def _extract_frameworks_tools(context: str) -> list[JobFrameworkToolFact]:
    facts: list[JobFrameworkToolFact] = []
    for name in (
        "Spring Boot",
        "FastAPI",
        "Gin",
        "Django",
        "Flask",
        "Redis",
        "Kafka",
        "RabbitMQ",
        "Docker",
        "Kubernetes",
        "MySQL",
        "PostgreSQL",
    ):
        if name.casefold() in context.casefold():
            facts.append(JobFrameworkToolFact(name=name, evidence=_evidence_line(context, name)))
    return facts


__all__ = ["JobFactsExtractor"]
