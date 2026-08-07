"""把个人资料库和目标岗位库整理成可用于选题的轻量画像。

"""

from __future__ import annotations

import re
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
    """读取个人简历+岗位JD，并提取题库能够识别的技术标签。"""

    def __init__(self, source: KnowledgeContextSource, labels: list[str]):
        self._source = source
        #去重后的一级分类标签和主题标签
        self._labels = _unique_labels(labels)

    async def build_candidate_profile(
        self,
        kb_id: str,
        *,
        candidate_facts: CandidateFacts | None = None,
    ) -> CandidateProfile:
        """读取简历资料库，找出技术栈和项目线索。

        当 candidate_facts 不为 None 时，利用事实数据推理 experience_level；
        否则保持规则匹配模式（向后兼容）。
        """

        result = await self._source.retrieve(
            kb_id=kb_id,
            query=(
                "整理候选人的技术栈、工作或实习经历、项目经历、承担的职责、"
                "系统设计和可以在面试中深入追问的技术细节。"
            ),
        )

        #推断用户经验
        experience_level = ""
        if candidate_facts is not None:
            experience_level = _infer_experience_level(candidate_facts.work_experience)

        return CandidateProfile(
            kb_id=kb_id,
            summary=_limit_text(result.context),
            skills=self._match_labels(result.context),
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
        """读取选中的公司岗位库，找出招聘要求对应的技术标签。"""

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
            required_skills=self._match_labels(result.context),
            evidence_refs=list(result.evidence_refs),
        )

    def _match_labels(self, text: str) -> list[str]:
        """从简历或JD文本里，提取出题库已经认识的技术标签"""

        normalized = text.casefold()  # 不区分大小写
        return [
            label
            for label in self._labels
            if _contains_label(normalized, label.casefold())
        ]


def _contains_label(normalized_text: str, normalized_label: str) -> bool:
    """匹配完整英文技术词，避免把 PPO 从 support、SSE 从 assessment 中误提取。"""

    if re.search(r"[a-z0-9]", normalized_label):
        pattern = rf"(?<![a-z0-9]){re.escape(normalized_label)}(?![a-z0-9])"
        return re.search(pattern, normalized_text) is not None
    return normalized_label in normalized_text


def _unique_labels(labels: list[str]) -> list[str]:
    """标签去重"""

    seen: set[str] = set()
    result: list[str] = []
    for label in labels:
        normalized = label.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(label)
    return result


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
