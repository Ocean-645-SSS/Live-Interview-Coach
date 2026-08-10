"""长期技能画像的应用服务总入口"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from enum import Enum

from pydantic import Field

from liverag.interview.persistence.repository import InterviewRepository
from liverag.interview.question_bank.catalog import QuestionBank
from liverag.interview.records import AnswerEvaluationRecord
from liverag.interview.schemas import (
    InterviewDifficulty,
    InterviewPlan,
    SkillProgress,
    SkillProgressEvidence,
    StrictModel,
)
from liverag.interview.skill_progress.policy import calculate_skill_progress
from liverag.interview.skill_progress.taxonomy import SkillTaxonomy


class TrainingRecommendationReason(str, Enum):
    """推荐原因"""
    WEAK_RETEST = "WEAK_RETEST"     #用户的薄弱项
    EVIDENCE_GAP = "EVIDENCE_GAP"   #证据不足，需要再问几次补充


class TrainingQuestionRecommendation(StrictModel):
    """训练后推荐的问题"""
    question_id: str = Field(min_length=1)
    question_text: str = Field(min_length=1)
    difficulty: InterviewDifficulty
    skill_key: str = Field(min_length=1)
    skill_display_name: str = Field(min_length=1)
    reason: TrainingRecommendationReason


class SkillProgressDashboard(StrictModel):
    """展示给前端的skillprogress仪表盘数据"""
    candidate_profile_id: str = Field(min_length=1)
    taxonomy_version: int = Field(ge=1)
    skills: list[SkillProgress]
    recommendations: list[TrainingQuestionRecommendation]


class SkillProgressService:
    def __init__(self, repository: InterviewRepository, taxonomy: SkillTaxonomy):
        self._repository = repository
        self.taxonomy = taxonomy

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _to_evidence(self, record: AnswerEvaluationRecord) -> SkillProgressEvidence:
        """把一条 AnswerEvaluationRecord 转成 SkillProgressEvidence

        它会去拿该场冻结的 InterviewPlan 找到对应 question_id，
        再根据 category + subcategory 通过 taxonomy 找到 skill_key，
        最后把评价分数、missing_points、errors、rubric_version、时间等组装成 Evidence"""

        #根据评价中的interview_id获取interview
        interview = self._repository.get_interview(record.interview_id)

        #获取InterviewPlan
        if interview.plan_json is None:
            raise ValueError(f"面试尚无冻结计划：{record.interview_id}")
        plan = InterviewPlan.model_validate_json(interview.plan_json)
        #从 plan.questions 中找到第一道 id 等于 record.question_id 的题目，并赋值给 question
        question = next(item for item in plan.questions if item.id == record.question_id)

        #给question建立索引
        skill = self.taxonomy.resolve(question.category, question.subcategory)
        #根据cp_id+skill_id+record_id -> 哈希字段
        digest = hashlib.sha256(
            f"{interview.candidate_profile_id}\0{skill.key}\0{record.id}".encode()
        ).hexdigest()[:32]

        created_at = self._parse_datetime(record.created_at)

        return SkillProgressEvidence(
            id=f"skill_evidence_{digest}",
            candidate_profile_id=interview.candidate_profile_id,
            skill_key=skill.key,
            evaluation_id=record.id,
            session_id=record.session_id,
            interview_id=record.interview_id,
            question_id=record.question_id,
            taxonomy_version=self.taxonomy.version,
            rubric_version=record.rubric_version,
            score=record.evaluation.weighted_score,
            weak_points=[
                *record.evaluation.missing_points,
                *record.evaluation.errors,
            ],
            evaluated_at=created_at,
            created_at=created_at,
        )

    def apply_evaluation(self, answer_id: str) -> SkillProgress:
        """根据最新的answer，更新evidence->skillprogress

        answer_id → 查询 AnswerEvaluationRecord → 找到所属 Interview / Session / Question
        → 根据题目的 category + subcategory 映射 skill_key → 转换为 SkillProgressEvidence
        → 写入证据表 → 重新计算对应技能的 SkillProgress"""

        #根据answer_id获取record
        record=self._repository.get_evaluation_record(answer_id)
        #根据record获取skillprogress evidence
        evidence = self._to_evidence(record=record)
        #根据新的evidence，更新长期画像
        return self._repository.apply_skill_evidence(evidence)

    def build_candidate_snapshot(
        self, candidate_profile_id: str
    ) -> tuple[list[SkillProgress], list[SkillProgressEvidence]]:
        """根据一个候选人的全部历史评价，在内存里重新构造完整长期画像"""

        #把所有evaluation record转化为SkillProgressEvidence
        evidence = [
            self._to_evidence(record)
            for record in self._repository.list_evaluation_records_for_candidate(
                candidate_profile_id
            )
        ]

        grouped: dict[str, list[SkillProgressEvidence]] = defaultdict(list)
        #按照skill_key分组
        for item in evidence:
            grouped[item.skill_key].append(item)
        #把某一个技能的所有历史 Evidence 聚合成 SkillProgress
        progress = [
            calculate_skill_progress(items, taxonomy_version=self.taxonomy.version)
            for skill_key, items in sorted(grouped.items())
        ]
        return progress, evidence

    def rebuild_candidate(self, candidate_profile_id: str) -> list[SkillProgress]:
        """再build_candidate_snapshot的基础之上写回数据库，
        是画像异常的重建修复入口"""

        progress, evidence = self.build_candidate_snapshot(candidate_profile_id)
        #写回数据库，替换原来的skillprogress
        return self._repository.replace_skill_progress(
            candidate_profile_id=candidate_profile_id,
            progress=progress,
            evidence=evidence,
        )

    def list_progress(self, candidate_profile_id: str) -> list[SkillProgress]:
        """列出所有skillprogress"""

        return self._repository.list_skill_progress(candidate_profile_id)

    def recommend_questions(
        self,
        candidate_profile_id: str,
        question_bank: QuestionBank,
        *,
        limit: int = 5,
    ) -> list[TrainingQuestionRecommendation]:
        """生成推荐的问题列表"""

        #先将所有skillprogress排序，优先处理“得分低”或“证据不足”的skill
        progress = sorted(
            self.list_progress(candidate_profile_id),
            key=lambda item: (item.current_score, item.confidence, item.skill_key),
        )
        #收集已经做过的所有题目id，避免多次重复推荐同一题
        attempted: set[str] = {
            evidence_item.question_id
            for progress_item in progress
            for evidence_item in self._repository.list_skill_evidence(
                candidate_profile_id=candidate_profile_id,
                skill_key=progress_item.skill_key,
            )
        }

        recommendations: list[TrainingQuestionRecommendation] = []
        #遍历所有skillprogress
        for progress_item in progress:
            #1.分数不低、证据也够的skill，暂时不推荐训练题
            if progress_item.current_score >= 60 and progress_item.confidence >= 0.45:
                continue

            #2.从题库里找出当前skill下，用户还没回答过的第一道题目
            question = next(
                (
                    candidate
                    for candidate in question_bank.questions
                    if candidate.id not in attempted
                    and self.taxonomy.resolve(
                        candidate.category, candidate.subcategory
                    ).key
                    == progress_item.skill_key
                ),
                None,
            )
            if question is None:
                continue

            #3.在taxonomy中找到当前skill的完整定义
            skill = next(
                taxonomy_skill
                for taxonomy_skill in self.taxonomy.skills
                if taxonomy_skill.key == progress_item.skill_key
            )

            #4.增加推荐题目
            recommendations.append(
                TrainingQuestionRecommendation(
                    question_id=question.id,
                    question_text=question.question_text,
                    difficulty=question.difficulty,
                    skill_key=progress_item.skill_key,
                    skill_display_name=skill.display_name,
                    reason=(
                        #如果可靠性<0.45 -> 推荐原因是证据不足
                        #否则，原因是薄弱题目
                        TrainingRecommendationReason.EVIDENCE_GAP
                        if progress_item.confidence < 0.45
                        else TrainingRecommendationReason.WEAK_RETEST
                    ),
                )
            )
            #5.给当前question增加一次使用次数
            attempted.add(question.id)
            #6.到了限制的题目数量，结束
            if len(recommendations) == limit:
                break

        return recommendations

    def get_dashboard_for_kb(
        self, candidate_kb_id: str, question_bank: QuestionBank, *, limit: int = 5
    ) -> SkillProgressDashboard:
        """长期画像页面的一站式查询接口"""

        #获取candidate
        candidate = self._repository.ensure_candidate_profile(kb_id=candidate_kb_id)
        #获取该candidate的所有skillprogress，并从题库生成推荐的问题
        return SkillProgressDashboard(
            candidate_profile_id=candidate.id,
            taxonomy_version=self.taxonomy.version,
            skills=self.list_progress(candidate.id),
            recommendations=self.recommend_questions(candidate.id, question_bank, limit=limit),
        )
