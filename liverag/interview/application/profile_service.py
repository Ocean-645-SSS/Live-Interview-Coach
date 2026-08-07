"""把个人资料库和目标岗位库整理成可用于选题的轻量画像。

正确调用链：ProfileService → CandidateFacts → Profile
不再依赖题库标签做字符串匹配，skills 直接从结构化事实中提取。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from liverag.interview.schemas import (
    CandidateFacts,
    CandidateProfile,
    InterviewDifficulty,
    JobProfile,
    WorkExperienceFact,
)


@dataclass(frozen=True, slots=True)
class KnowledgeContext:
    """一次知识库检索返回的正文和来源。"""

    context: str
    evidence_refs: tuple[str, ...] = ()


class KnowledgeContextSource(Protocol):
    """屏蔽 Profile Service 对具体 RAG HTTP 实现的依赖。"""

    #interview_profile_source.py中实现
    async def retrieve(self, *, kb_id: str, query: str) -> KnowledgeContext: ...


class InterviewProfileService:
    """读取个人简历+岗位JD，从结构化事实中生成画像。"""

    def __init__(self, source: KnowledgeContextSource):
        self._source = source

    async def build_candidate_profile(
        self,
        kb_id: str,
        *,
        candidate_facts: CandidateFacts | None = None,
    ) -> CandidateProfile:
        """读取简历资料库，从 CandidateFacts 提取技术栈和项目线索。

        当 candidate_facts 不为 None 时：
          - skills 直接取自 facts.skills
          - experience_level 从 work_experience 时间跨度推理
        否则 skills 为空（向后兼容）。
        """

        result = await self._source.retrieve(
            kb_id=kb_id,
            query=(
                "整理候选人的技术栈、工作或实习经历、项目经历、承担的职责、"
                "系统设计和可以在面试中深入追问的技术细节。"
            ),
        )

        experience_level = ""
        skills: list[str] = []
        if candidate_facts is not None:
            skills = list(candidate_facts.skills)
            experience_level = _infer_experience_level(candidate_facts.work_experience)

        return CandidateProfile(
            kb_id=kb_id,
            summary=_limit_text(result.context),
            skills=skills,
            projects=_project_lines(result.context),
            experience_level=experience_level,
            evidence_refs=list(result.evidence_refs),
        )

    async def build_job_profile(
        self,
        *,
        kb_id: str,
        company: str | None,
        role: str,
    ) -> JobProfile:
        """读取选中的公司岗位库，生成岗位画像。"""

        result = await self._source.retrieve(
            kb_id=kb_id,
            query=(
                f"整理 {company or '目标公司'} 的 {role} 岗位职责、必须技能、"
                "加分技能、业务场景和面试考察重点。"
            ),
        )
        return JobProfile(
            kb_id=kb_id,
            company=company,
            role=role,
            summary=_limit_text(result.context),
            required_skills=[],
            evidence_refs=list(result.evidence_refs),
        )


def _limit_text(text: str, maximum: int = 6000) -> str:
    """限制6000字以内"""

    return text.strip()[:maximum]


def _project_lines(text: str) -> list[str]:
    """保留少量包含项目职责的原文片段，供计划审计和后续追问使用。
    规则：只保留包含"项目/负责/设计/实现/架构"任意关键词的行；每行最多500个字；最多8行"""

    markers = ("项目", "负责", "设计", "实现", "架构")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [line[:500] for line in lines if any(marker in line for marker in markers)][:8]


def _infer_experience_level(
    work_experience: list[WorkExperienceFact],
) -> str:
    """从工作经历事实中推断经验等级。

    规则：
    - 计算所有经历的有效起止年份跨度总和
    - 0-1 年 → BEGINNER，1-3 年 → JUNIOR，3-5 年 → INTERMEDIATE
    - 5-8 年 → SENIOR，8+ 年 → EXPERT
    - 无法计算时返回空字符串
    """

    today = date.today()
    total_years: float = 0.0

    for exp in work_experience:
        start = exp.start_at
        end = exp.end_at if exp.end_at is not None else today

        if start is None:
            continue

        years = (end - start).days / 365.25
        if years < 0:
            continue
        total_years += years

    if total_years <= 0:
        return ""
    if total_years <= 1:
        return InterviewDifficulty.BEGINNER.value
    if total_years <= 3:
        return InterviewDifficulty.JUNIOR.value
    if total_years <= 5:
        return InterviewDifficulty.INTERMEDIATE.value
    if total_years <= 8:
        return InterviewDifficulty.SENIOR.value
    return InterviewDifficulty.EXPERT.value


__all__ = [
    "InterviewProfileService",
    "KnowledgeContext",
    "KnowledgeContextSource",
]
