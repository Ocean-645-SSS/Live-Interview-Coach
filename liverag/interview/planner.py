"""根据个人画像、目标岗位和公共题库生成冻结的面试计划。"""

from __future__ import annotations

from liverag.interview.question_bank.catalog import QuestionBank
from liverag.interview.records import generate_id
from liverag.interview.schemas import (
    CandidateProfile,
    InterviewConfig,
    InterviewPlan,
    JobProfile,
)


class InterviewPlanner:
    """用画像提高题库选题权重，公共题库本身不暴露给前端。"""

    def __init__(self, question_bank: QuestionBank):
        self._question_bank = question_bank

    def build(
        self,
        *,
        title: str,
        config: InterviewConfig,
        candidate_profile: CandidateProfile | None, #用户画像
        job_profile: JobProfile | None,  #岗位画像
    ) -> InterviewPlan:
        """生成可直接交给实时 Agent 执行的计划。"""

        plan_id = generate_id("plan")

        #主题权重
        weights = dict(config.topic_weights)

        #用户画像不为空
        if candidate_profile is not None:
            #遍历用户技能
            for skill in candidate_profile.skills:
                #提到技术次数越多，权重越大
                weights[skill] = weights.get(skill, 0.0) + 1.0

        #岗位画像不为空
        if job_profile is not None:
            #遍历岗位需求技能
            for skill in job_profile.required_skills:
                #提到技术次数越多，权重越大
                weights[skill] = weights.get(skill, 0.0) + 2.0

        #个性化调整：复制一份原始配置，并更新主题权重
        effective_config = config.model_copy(update={"topic_weights": weights})

        # 画像原文既用于主题排序，也用于排除简历和 JD 从未出现过的特定实现名。
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
                [job_profile.summary, job_profile.role, *job_profile.required_skills]
            )

        #确定问题题目
        questions = self._question_bank.select_questions(
            effective_config,
            relevance_text="\n".join(relevance_parts) or None,
            explicitly_requested_topics=config.topic_weights,
            required_relevance_topics=(
                job_profile.required_skills if job_profile is not None else ()
            ),
            selection_seed=plan_id,
        )

        target = ""

        #补充目标岗位描述
        if job_profile is not None:
            target = f"，目标岗位是{job_profile.company or ''}{job_profile.role}"

        return InterviewPlan(
            id=plan_id,
            title=title,
            introduction=(
                "欢迎参加本次模拟面试。问题会结合你的个人经历和目标岗位准备"
                f"{target}。我会逐题提问，并根据回答进行追问。"
            ),  #开场白
            config=effective_config,    #有效配置
            questions=questions,    #问题列表
            closing_message="本次模拟面试已经结束，报告正在生成。", #结束语
            plan_version=self._question_bank.version,   #版本号
            candidate_profile=candidate_profile,
            job_profile=job_profile,
        )


__all__ = ["InterviewPlanner"]
