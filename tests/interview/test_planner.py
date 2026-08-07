import pytest

from liverag.interview.application.planner import InterviewPlanner
from liverag.interview.question_bank.catalog import QuestionBank, QuestionBankDocument
from liverag.interview.schemas import (
    CandidateProfile,
    InterviewConfig,
    InterviewDifficulty,
    InterviewQuestion,
    JobProfile,
    QuestionRubric,
    QuestionSource,
    QuestionType,
    RubricPoint,
)


def _question(question_id: str, category: str) -> InterviewQuestion:
    return InterviewQuestion(
        id=question_id,
        order=1,
        type=QuestionType.TECHNICAL_KNOWLEDGE,
        source=QuestionSource.QUESTION_BANK,
        difficulty=InterviewDifficulty.INTERMEDIATE,
        category=category,
        topics=[f"{category}基础"],
        question_text=f"解释 {category}。",
        objective=f"检查 {category}",
        rubric=QuestionRubric(
            expected_points=[RubricPoint(id="point", content="关键点")]
        ),
        reference_answer="参考答案",
        source_reference=f"test.md#{category}",
    )


@pytest.mark.asyncio
async def test_planner_prioritizes_job_and_candidate_topics():
    bank = QuestionBank(
        QuestionBankDocument(
            version=3,
            questions=[_question("q-mysql", "MySQL"), _question("q-python", "Python")],
        )
    )
    config = InterviewConfig(
        question_count=1,
        target_kb_id="job-python",
        target_company="示例公司",
        target_role="Python 后端",
    )

    plan = await InterviewPlanner(bank).build(
        title="岗位模拟面试",
        config=config,
        candidate_profile=CandidateProfile(skills=["Python"]),
        job_profile=JobProfile(
            kb_id="job-python",
            company="示例公司",
            role="Python 后端",
            required_skills=["Python"],
        ),
    )

    assert plan.questions[0].id == "q-python"
    assert plan.config.topic_weights["Python"] == 3.0
    assert plan.candidate_profile is not None
    assert plan.job_profile is not None
    assert plan.plan_version == 3


@pytest.mark.asyncio
async def test_planner_rejects_source_specific_term_missing_from_profiles():
    generic = _question("q-agent", "Agent")
    generic = generic.model_copy(update={"topics": ["Agent架构"]})
    source_specific = _question("q-todo", "Agent").model_copy(
        update={"question_text": "TodoItem 中的 ask-id 有什么作用？"}
    )
    bank = QuestionBank(
        QuestionBankDocument(version=1, questions=[source_specific, generic])
    )

    plan = await InterviewPlanner(bank).build(
        title="Agent 面试",
        config=InterviewConfig(question_count=1, target_kb_id="job", target_role="Agent"),
        candidate_profile=CandidateProfile(
            summary="使用 LangGraph 开发 Agent 系统。",
            skills=["Agent"],
        ),
        job_profile=JobProfile(
            kb_id="job",
            role="Agent 开发",
            summary="负责 Agent 架构、工具调用和任务拆解。",
            required_skills=["Agent", "Agent架构"],
        ),
    )

    assert plan.questions[0].id == "q-agent"
