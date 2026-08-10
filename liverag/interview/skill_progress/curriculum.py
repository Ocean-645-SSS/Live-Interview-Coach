"""把长期技能画像转换为确定性的训练选题目标，
实现薄弱项、证据不足、已掌握分类及训练配比"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from liverag.interview.schemas import InterviewQuestion, JobProfile, SkillProgress
from liverag.interview.skill_progress.taxonomy import SkillTaxonomy


@dataclass(frozen=True, slots=True)
class TrainingSelectionRequest:
    """题目选择输入"""
    weak_skill_keys: tuple[str, ...]    #薄弱项keys
    evidence_skill_keys: tuple[str, ...]    #证据不足项keys
    mastery_skill_keys: tuple[str, ...]     #掌握项keys
    job_labels: tuple[str, ...] #岗位相关标签
    weak_target: int    #薄弱项期望数
    evidence_target: int    #证据不足项期望数
    mastery_target: int   #熟练项期望数
    minimum_job_core: int   #至少出几道岗位相关题
    job_constraint_enabled: bool    #是否启用岗位硬约束，没有JobProfile就为false
    weak_lower_difficulty_skill_keys: tuple[str, ...] = ()  #分数低于50的薄弱项技能ids


@dataclass(frozen=True, slots=True)
class TrainingSelectionResult:
    """题目选择输出"""
    questions: tuple[InterviewQuestion, ...]    #最终选中的题库列表
    selection_intents: dict[str, str]   #选择原因
    job_relevant_by_question: dict[str, bool]   #每个题目是否与岗位相关
    intent_targets: dict[str, int]  #每个选择原因的目标数量
    intent_selected: dict[str, int] #每个选择原因实际选中的数量
    job_core_required: int  #原始岗位硬约束
    job_core_available: int #候选池里可选的岗位相关题数量
    job_core_selected: int  #最终选择的岗位相关题数量
    degradation_reasons: tuple[str, ...]


class TrainingCurriculum:
    """依据固定阈值把画像划分为弱项、证据不足和已掌握技能。"""

    def __init__(self, taxonomy: SkillTaxonomy):
        self.taxonomy = taxonomy

    def build(
        self,
        *,
        question_count: int,
        progress: Sequence[SkillProgress],
        job_labels: Sequence[str] = (),
        job_profile: JobProfile | None = None,
        job_constraint_enabled: bool | None = None, #是否启用岗位硬约束
    ) -> TrainingSelectionRequest:
        """依据固定阈值把画像划分为弱项、证据不足和已掌握技能，生成题目选择输入"""

        #薄弱项：cs<60 + 稳定性>=0.65
        weak = tuple(
            item.skill_key
            for item in sorted(progress, key=lambda item: (item.current_score, item.skill_key))
            if item.current_score < 60 and item.confidence >= 0.65
        )
        #证据不足项：稳定性<0.45
        evidence = tuple(
            item.skill_key
            for item in sorted(progress, key=lambda item: (item.confidence, item.skill_key))
            if item.confidence < 0.45
        )
        #掌握项：cs>=80+稳定性>=0.65
        mastery = tuple(
            item.skill_key
            for item in sorted(progress, key=lambda item: (-item.current_score, item.skill_key))
            if item.current_score >= 80 and item.confidence >= 0.65
        )

        #岗位画像需要的skills标签
        profile_labels = (
            (job_profile.role, *job_profile.required_skills)
            if job_profile is not None
            else tuple(job_labels)
        )

        #构造“岗位相关标签”，并决定是否启用岗位硬约束
        normalized_labels_by_key: dict[str, str] = {}
        #遍历skills标签
        for label in profile_labels:
            clean_label = label.strip()
            if clean_label:
                normalized_labels_by_key.setdefault(clean_label.casefold(), clean_label)
        normalized_labels = tuple(normalized_labels_by_key.values())
        enabled = job_profile is not None if job_constraint_enabled is None else job_constraint_enabled

        return TrainingSelectionRequest(
            weak_skill_keys=weak,
            evidence_skill_keys=evidence,
            mastery_skill_keys=mastery,
            job_labels=normalized_labels,
            weak_target=min(len(weak), max(1, math.floor(question_count * 0.30))),
            evidence_target=min(len(evidence), 1),
            mastery_target=min(len(mastery), 1) if question_count >= 5 else 0,
            minimum_job_core=math.ceil(question_count * 0.50) if enabled else 0, #至少50%
            job_constraint_enabled=enabled,
            weak_lower_difficulty_skill_keys=tuple( #薄弱项对应的题目ids
                item.skill_key
                for item in progress
                if item.current_score < 50 and item.confidence >= 0.65
            ),
        )


__all__ = [
    "TrainingCurriculum",
    "TrainingSelectionRequest",
    "TrainingSelectionResult",
]
