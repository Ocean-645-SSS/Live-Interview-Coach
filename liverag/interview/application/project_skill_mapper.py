"""Build verified job-skill to candidate-project relations for one interview plan."""

from __future__ import annotations

import json

from openai import AsyncOpenAI
from pydantic import Field

from liverag.interview.application.profile_service import KnowledgeContext
from liverag.interview.schemas import (
    CandidateFacts,
    JobProfile,
    ProjectSkillRelation,
    StrictModel,
)


class ProjectSkillMappingOutput(StrictModel):
    """Strict envelope returned by the mapping LLM."""

    relations: list[ProjectSkillRelation] = Field(default_factory=list)


class ProjectSkillMapper:
    """Map already-retrieved candidate evidence to selected JD skills once.

    Retrieval deliberately stays outside this service.  The preparation workflow
    performs its single Candidate KB ``mix`` query and passes the raw context in,
    so this service only makes the one structured mapping LLM call.
    """

    def __init__(self, *, llm_client: AsyncOpenAI, llm_model: str) -> None:
        self._llm_client = llm_client
        self._llm_model = llm_model

    async def build(
        self,
        *,
        candidate_facts: CandidateFacts,
        job_profile: JobProfile,
        retrieval_context: KnowledgeContext,
    ) -> list[ProjectSkillRelation]:
        job_skills = [
            skill.model_dump(mode="json")
            for skill in (
                *job_profile.required_skills,
                *job_profile.preferred_skills,
            )
        ]
        if (
            not job_skills
            or not candidate_facts.projects
            or not retrieval_context.context.strip()
        ):
            return []

        response = await self._llm_client.chat.completions.create(
            model=self._llm_model,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你只建立有明确证据支持的岗位技能与候选人项目关系。"
                        "不得根据技术常识猜测；不得创建 skill 或项目。"
                        "一个技能可以没有关系，也可以对应多个项目。"
                        "candidate_evidence 和 responsibility 必须是候选人材料中的"
                        "原文证据或其直接片段，jd_evidence 必须原样使用输入岗位技能的 evidence。"
                        "source_refs 只能使用输入的 allowed_source_refs。"
                        "只输出严格 JSON：{\"relations\":[{\"skill_id\":...,\"project_name\":...,"
                        "\"responsibility\":...,\"jd_evidence\":...,\"candidate_evidence\":...,"
                        "\"source_refs\":[...],\"relation_type\":\"DIRECT\"|\"RELATED\"}]}。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "job_skills": job_skills,
                            "candidate_projects": [
                                project.model_dump(mode="json")
                                for project in candidate_facts.projects
                            ],
                            "retrieval_context": retrieval_context.context,
                            "allowed_source_refs": _source_refs(
                                candidate_facts, retrieval_context
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        output = ProjectSkillMappingOutput.model_validate_json(
            response.choices[0].message.content or "{}"
        )
        self._validate(
            relations=output.relations,
            candidate_facts=candidate_facts,
            job_skills=job_skills,
            retrieval_context=retrieval_context,
        )
        return output.relations

    @staticmethod
    def _validate(
        *,
        relations: list[ProjectSkillRelation],
        candidate_facts: CandidateFacts,
        job_skills: list[dict[str, object]],
        retrieval_context: KnowledgeContext,
    ) -> None:
        evidence_by_skill = {
            str(skill["skill_id"]): str(skill["evidence"])
            for skill in job_skills
        }
        projects_by_name = {
            project.name: project for project in candidate_facts.projects
        }
        allowed_refs = set(_source_refs(candidate_facts, retrieval_context))

        for relation in relations:
            expected_evidence = evidence_by_skill.get(relation.skill_id)
            if expected_evidence is None:
                raise ValueError(f"项目技能映射包含未知岗位技能：{relation.skill_id}")
            if relation.jd_evidence != expected_evidence:
                raise ValueError(f"项目技能映射的 JD 证据不匹配：{relation.skill_id}")

            project = projects_by_name.get(relation.project_name)
            if project is None:
                raise ValueError(f"项目技能映射包含未知候选人项目：{relation.project_name}")

            project_evidence = "\n".join(
                (
                    retrieval_context.context,
                    project.name,
                    project.role,
                    project.description,
                    *project.technologies,
                )
            )
            if not _is_supported(relation.candidate_evidence, project_evidence):
                raise ValueError(
                    f"项目技能映射的候选人证据不受支持：{relation.project_name}"
                )
            if not _is_supported(relation.responsibility, project_evidence):
                raise ValueError(
                    f"项目技能映射的职责证据不受支持：{relation.project_name}"
                )
            if not set(relation.source_refs).issubset(allowed_refs):
                raise ValueError(
                    f"项目技能映射包含未知来源引用：{relation.project_name}"
                )


def _source_refs(
    candidate_facts: CandidateFacts, retrieval_context: KnowledgeContext
) -> list[str]:
    return list(
        dict.fromkeys(
            (*candidate_facts.raw_evidence_refs, *retrieval_context.evidence_refs)
        )
    )


def _is_supported(claim: str, evidence: str) -> bool:
    """Require a direct normalized substring, not an LLM-inferred paraphrase."""

    normalized_claim = " ".join(claim.casefold().split())
    normalized_evidence = " ".join(evidence.casefold().split())
    return normalized_claim in normalized_evidence


__all__ = ["ProjectSkillMapper", "ProjectSkillMappingOutput"]
