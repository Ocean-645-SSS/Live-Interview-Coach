"""根据个人画像、目标岗位和公共题库生成冻结的面试计划。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from liverag.interview.intelligence.provider import (
    CompanyInterviewProfile,
)
from liverag.interview.prompts.plan_prompts import PLAN_PERSONALIZATION_SYSTEM_PROMPT
from liverag.interview.question_bank.catalog import QuestionBank
from liverag.interview.records import generate_id
from liverag.interview.schemas import (
    CandidateProfile,
    FactsExtractionMode,
    InterviewConfig,
    InterviewPlan,
    InterviewQuestion,
    JobProfile,
    ProjectSkillRelation,
    QuestionType,
    SkillProgress,
    TrainingAdjustmentAudit,
)
from liverag.interview.skill_progress.curriculum import TrainingCurriculum
from liverag.interview.skill_progress.taxonomy import SkillNotMappedError, SkillTaxonomy


class InterviewPlanner:
    """用画像提高题库选题权重，可选 LLM 个性化改写题目。"""

    def __init__(
        self,
        question_bank: QuestionBank,
        *,
        llm_client: AsyncOpenAI | None = None,
        llm_model: str = "",
        taxonomy: SkillTaxonomy | None = None,
    ):
        self._question_bank = question_bank
        self._llm_client = llm_client
        self._llm_model = llm_model
        self._taxonomy = taxonomy or SkillTaxonomy.from_file(
            Path(__file__).resolve().parents[1]
            / "skill_progress"
            / "data"
            / "skill_taxonomy.v1.json"
        )

    async def build(
        self,
        *,
        title: str,
        config: InterviewConfig,
        candidate_profile: CandidateProfile | None,  # 用户画像
        job_profile: JobProfile | None,  # 岗位画像
        company_intel: CompanyInterviewProfile | None = None,   #牛客mcp提供的公司情报
        candidate_profile_id: str | None = None,    #用户整体画像id
        skill_progress: Sequence[SkillProgress] = (),   #用户长期画像
        project_skill_relations: Sequence[ProjectSkillRelation] = (),
        selection_seed: str | None = None,
    ) -> InterviewPlan:
        """生成可直接交给实时 Agent 执行的计划。

        company_intel 为 None 时 Planner 正常降级，仅使用 CandidateProfile + JobProfile。
        llm_client 为 None 时跳过个性化改写，直接使用题库原题。
        """

        plan_id = generate_id("plan")
        effective_selection_seed = selection_seed or plan_id

        # ── 主题权重 ──
        weights = dict(config.topic_weights)

        if candidate_profile is not None:
            candidate_skill_weight = (
                0.6
                if candidate_profile.facts_extraction_mode
                is FactsExtractionMode.FALLBACK
                else 1.0
            )
            for skill in candidate_profile.skills:
                weights[skill] = weights.get(skill, 0.0) + candidate_skill_weight

        job_skill_weights: dict[str, float] = {}
        if job_profile is not None:
            for skill in (*job_profile.required_skills, *job_profile.preferred_skills):
                job_skill_weights[skill.skill_id] = skill.weight

        if company_intel is not None:
            for topic in company_intel.frequent_topics:
                weights[topic] = weights.get(topic, 0.0) + 1.5

        effective_config = config.model_copy(update={"topic_weights": weights})

        # ── 相关性文本 ──
        relevance_parts: list[str] = []
        if candidate_profile is not None:
            relevance_parts.extend(
                [
                    candidate_profile.summary,
                    *candidate_profile.projects,
                    *candidate_profile.skills,
                ]
            )
        if job_profile is not None:
            relevance_parts.extend(
                [
                    job_profile.summary,
                    job_profile.role,
                    *(skill.evidence for skill in job_profile.required_skills),
                    *(skill.evidence for skill in job_profile.preferred_skills),
                ]
            )
        if company_intel is not None:
            relevance_parts.extend(company_intel.frequent_topics)

        # ── 选题（程序负责：section / 题量 / 难度）──
        training_adjustment = None
        #skill progress配置了
        if skill_progress:
            #依据固定阈值把画像划分为弱项、证据不足和已掌握技能，生成题目选择输入
            training = TrainingCurriculum(self._taxonomy).build(
                question_count=config.question_count,
                progress=skill_progress,
                job_profile=job_profile,
            )
            #根据硬约束和软约束选择出的题目
            selection = self._question_bank.select_training_questions(
                effective_config,
                training=training,
                taxonomy=self._taxonomy,
                relevance_text="\n".join(relevance_parts) or None,
                explicitly_requested_topics=config.topic_weights,
                selection_seed=effective_selection_seed,
                max_total_seconds=config.duration_minutes * 60,
                job_skill_weights=job_skill_weights,
            )
            #取出training推荐的题目
            questions = list(selection.questions)
            #写入审计记录
            training_adjustment = TrainingAdjustmentAudit(
                taxonomy_version=self._taxonomy.version,
                source_progress_updated_at=max(
                    item.updated_at for item in skill_progress
                ),
                weak_retest_skills=list(training.weak_skill_keys),
                evidence_skills=list(training.evidence_skill_keys),
                mastery_audit_skills=list(training.mastery_skill_keys),
                selection_reasons=selection.selection_intents,
                job_relevant_by_question=selection.job_relevant_by_question,
                intent_targets=selection.intent_targets,
                intent_selected=selection.intent_selected,
                job_core_required=selection.job_core_required,
                job_core_available=selection.job_core_available,
                job_core_selected=selection.job_core_selected,
                degraded=bool(selection.degradation_reasons),
                degradation_reasons=list(selection.degradation_reasons),
            )

        #不使用skill progress，直接从题库选题
        else:
            questions = self._question_bank.select_questions(
                effective_config,
                taxonomy=self._taxonomy,
                job_skill_weights=job_skill_weights,
                relevance_text="\n".join(relevance_parts) or None,
                explicitly_requested_topics=config.topic_weights,
                selection_seed=effective_selection_seed,
                max_total_seconds=config.duration_minutes * 60,
            )

        questions = self._annotate_job_skill_matches(questions, job_profile)
        questions = self._annotate_project_skill_relations(
            questions, project_skill_relations
        )

        # ── LLM 个性化改写 ──
        if self._llm_client is not None and any(
            question.project_skill_relations for question in questions
        ):
            try:
                questions = await self._personalize_questions(
                    questions=questions,
                )
            except Exception:
                import logging
                _logger = logging.getLogger("liverag.interview.application.planner")
                _logger.warning(
                    "LLM 个性化改写失败，降级使用原始题库题目",
                    exc_info=True,
                )

        target = ""
        if job_profile is not None:
            target = f"，目标岗位是{job_profile.company or ''}{job_profile.role}"

        return InterviewPlan(
            id=plan_id,
            title=title,
            introduction=(
                "欢迎参加本次模拟面试。问题会结合你的个人经历和目标岗位准备"
                f"{target}。我会逐题提问，并根据回答进行追问。"
            ),
            config=effective_config,
            questions=questions,
            closing_message="本次模拟面试已经结束，报告正在生成。",
            plan_version=self._question_bank.version,
            candidate_profile=candidate_profile,
            candidate_profile_id=candidate_profile_id,
            job_profile=job_profile,
            training_adjustment=training_adjustment,
        )

    # ── LLM 个性化改写 ──────────────────────────────────────────
    async def _personalize_questions(
        self,
        *,
        questions: list[InterviewQuestion],
    ) -> list[InterviewQuestion]:
        """只按已验证的项目—技能关系改写适合的项目型题目。

        LLM 只修改 question_text / objective / follow_up_hints，其余字段由代码
        原样复制。没有关系、或不是项目型的题目绝不交给 LLM 改写。
        """

        # 降级防御：没传入llm_api，只用profile个性化
        if self._llm_client is None:
            return questions

        eligible_types = {
            QuestionType.PROJECT_DEEP_DIVE,
            QuestionType.SYSTEM_DESIGN,
            QuestionType.SCENARIO,
        }
        eligible_questions = [
            question
            for question in questions
            if question.type in eligible_types and question.project_skill_relations
        ]
        if not eligible_questions:
            return questions

        # 输入：只给 LLM 需要的字段，减少 token 消耗。候选人画像不作为
        # 项目关系依据，只有冻结在题目上的关系可用于项目化改写。
        questions_input = [
            {
                "id": q.id,
                "order": q.order,
                "type": q.type.value,
                "difficulty": q.difficulty.value,
                "category": q.category,
                "subcategory": q.subcategory,
                "topics": q.topics,
                "question_text": q.question_text,
                "objective": q.objective,
                "rubric": q.rubric.model_dump(mode="json"),
                "reference_answer": q.reference_answer,
                "source_reference": q.source_reference,
                "parent_question_id": q.parent_question_id,
                "is_high_frequency": q.is_high_frequency,
                "estimated_seconds": q.estimated_seconds,
                "allow_follow_up": q.allow_follow_up,
                "follow_up_hints": q.follow_up_hints,
                "project_skill_relations": [
                    relation.model_dump(mode="json")
                    for relation in q.project_skill_relations
                ],
            }
            for q in eligible_questions
        ]

        # LLM 调用
        response = await self._llm_client.chat.completions.create(
            model=self._llm_model,
            messages=[
                {
                    "role": "system",
                    "content": PLAN_PERSONALIZATION_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "questions": questions_input,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        llm_output = json.loads(
            response.choices[0].message.content or "{}"
        )
        personalized_list = llm_output.get("questions", [])

        # ── 安全合并：程序优先，LLM 只改三个字段 ──
        lookup: dict[str, dict[str, Any]] = {
            item["id"]: item for item in personalized_list
        }

        merged: list[InterviewQuestion] = []
        eligible_question_ids = {question.id for question in eligible_questions}
        for q in questions:
            if q.id not in eligible_question_ids:
                merged.append(q)
                continue
            llm_item = lookup.get(q.id)
            if llm_item is None:
                # LLM 没有返回该题目 → 保留原题（防御）
                merged.append(q)
                continue

            merged.append(
                q.model_copy(
                    update={
                        "question_text": llm_item.get("question_text", q.question_text),
                        "objective": llm_item.get("objective", q.objective),
                        "follow_up_hints": llm_item.get(
                            "follow_up_hints", q.follow_up_hints
                        ),
                    }
                )
            )

        return merged

    @staticmethod
    def _annotate_project_skill_relations(
        questions: list[InterviewQuestion],
        relations: Sequence[ProjectSkillRelation],
    ) -> list[InterviewQuestion]:
        """只把与已命中 JD 技能对应的关系冻结到项目型题目。"""

        relations_by_skill: dict[str, list[ProjectSkillRelation]] = {}
        for relation in relations:
            relations_by_skill.setdefault(relation.skill_id, []).append(relation)

        eligible_types = {
            QuestionType.PROJECT_DEEP_DIVE,
            QuestionType.SYSTEM_DESIGN,
            QuestionType.SCENARIO,
        }
        annotated: list[InterviewQuestion] = []
        for question in questions:
            if question.type not in eligible_types:
                annotated.append(question)
                continue
            matched_relations = [
                relation
                for job_skill in question.job_skill_matches
                for relation in relations_by_skill.get(job_skill.skill_id, [])
            ]
            annotated.append(
                question.model_copy(
                    update={"project_skill_relations": matched_relations}
                )
            )
        return annotated

    def _annotate_job_skill_matches(
        self,
        questions: list[InterviewQuestion],
        job_profile: JobProfile | None,
    ) -> list[InterviewQuestion]:
        if job_profile is None:
            return questions

        skills_by_id = {
            skill.skill_id: skill
            for skill in (*job_profile.required_skills, *job_profile.preferred_skills)
        }
        if not skills_by_id:
            return questions

        annotated: list[InterviewQuestion] = []
        for question in questions:
            try:
                skill_id = self._taxonomy.resolve(
                    question.category, question.subcategory
                ).key
            except SkillNotMappedError:
                annotated.append(question)
                continue
            matched = skills_by_id.get(skill_id)
            if matched is None:
                annotated.append(question)
                continue
            annotated.append(
                question.model_copy(
                    update={
                        "job_skill_matches": [matched],
                        "job_skill_score": round(2.0 * matched.weight, 4),
                    }
                )
            )
        return annotated


def validate_plan_quality(plan: InterviewPlan) -> list[str]:
    """对生成的面试计划执行增强复核，返回问题列表（空列表 = 全部通过）。

    复核规则：
    - 必要 section 存在：TECHNICAL_KNOWLEDGE + PROJECT_DEEP_DIVE 至少各 1 题
    - 难度分布与 config.difficulty 匹配：至少 30% 题目匹配目标难度
    - 总题数和总时长已在 InterviewPlan Pydantic 校验中覆盖
    """

    issues: list[str] = []

    # ── Section 检查 ──
    tech_count = sum(
        1 for q in plan.questions if q.type == QuestionType.TECHNICAL_KNOWLEDGE
    )
    project_count = sum(
        1 for q in plan.questions if q.type == QuestionType.PROJECT_DEEP_DIVE
    )
    if tech_count < 1:
        issues.append("缺少 TECHNICAL_KNOWLEDGE 类型题目（至少需要 1 题）")
    if project_count < 1:
        issues.append("缺少 PROJECT_DEEP_DIVE 类型题目（至少需要 1 题）")

    # ── 难度分布检查 ──
    matching_difficulty = sum(
        1 for q in plan.questions if q.difficulty == plan.config.difficulty
    )
    threshold = max(1, int(plan.config.question_count * 0.3))
    if matching_difficulty < threshold:
        issues.append(
            f"难度分布不匹配：仅 {matching_difficulty}/{plan.config.question_count} 题"
            f"为 {plan.config.difficulty.value} 难度"
            f"（需要至少 {threshold} 题）"
        )

    return issues


__all__ = ["InterviewPlanner", "validate_plan_quality"]
