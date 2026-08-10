import hashlib
import math
from datetime import datetime, timezone

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
    SkillProgress,
)
from liverag.interview.skill_progress.curriculum import (
    TrainingCurriculum,
    TrainingSelectionRequest,
)
from liverag.interview.skill_progress.taxonomy import (
    SkillDefinition,
    SkillTaxonomy,
    SkillTaxonomyDocument,
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


def _skill_key(label: str) -> str:
    return f"skill_{hashlib.sha256(label.encode()).hexdigest()[:16]}"


def _training_bank(size: int = 30) -> tuple[QuestionBank, SkillTaxonomy]:
    questions = []
    skills = []
    for category, prefix in (("Python", "job"), ("Other", "non-job")):
        for index in range(size):
            subcategory = f"{prefix}-{index}"
            questions.append(
                _question(f"q-{prefix}-{index}", category).model_copy(
                    update={
                        "subcategory": subcategory,
                        "topics": [f"主题-{prefix}-{index}"],
                        "question_text": f"请解释题目 {prefix}-{index}。",
                        "objective": f"检查题目 {prefix}-{index}",
                        "source_reference": f"test.md#{prefix}-{index}",
                    }
                )
            )
            skills.append(
                SkillDefinition(
                    key=_skill_key(f"{category}/{subcategory}"),
                    parent_key=f"domain_{hashlib.sha256(category.encode()).hexdigest()[:12]}",
                    category=category,
                    subcategory=subcategory,
                )
            )
    return (
        QuestionBank(QuestionBankDocument(version=1, questions=questions)),
        SkillTaxonomy(SkillTaxonomyDocument(version=1, skills=skills)),
    )


def _progress(skill_key: str, *, score: float, confidence: float) -> SkillProgress:
    now = datetime(2026, 8, 10, tzinfo=timezone.utc)
    return SkillProgress(
        candidate_profile_id="candidate-1",
        skill_key=skill_key,
        taxonomy_version=1,
        attempts=1,
        average_score=score,
        current_score=score,
        latest_score=score,
        confidence=confidence,
        weak_points=[],
        source_evaluation_ids=[f"evaluation-{skill_key}"],
        first_evaluated_at=now,
        last_evaluated_at=now,
        updated_at=now,
    )


def test_curriculum_builds_soft_targets_and_hard_job_minimum():
    _bank, taxonomy = _training_bank(3)
    request = TrainingCurriculum(taxonomy).build(
        question_count=5,
        progress=[
            _progress("skill-weak", score=45, confidence=0.8),
            _progress("skill-unknown", score=70, confidence=0.2),
            _progress("skill-mastered", score=90, confidence=0.8),
        ],
        job_labels=["Python"],
        job_constraint_enabled=True,
    )

    assert request.weak_target == 1
    assert request.evidence_target == 1
    assert request.mastery_target == 1
    assert request.minimum_job_core == 3


@pytest.mark.parametrize("question_count", range(1, 31))
def test_job_core_minimum_holds_for_every_supported_question_count(question_count):
    bank, taxonomy = _training_bank()
    minimum = math.ceil(question_count * 0.50)
    result = bank.select_training_questions(
        InterviewConfig(question_count=question_count),
        training=TrainingSelectionRequest(
            weak_skill_keys=(),
            evidence_skill_keys=(),
            mastery_skill_keys=(),
            job_labels=("Python",),
            weak_target=0,
            evidence_target=0,
            mastery_target=0,
            minimum_job_core=minimum,
            job_constraint_enabled=True,
        ),
        taxonomy=taxonomy,
        relevance_text=None,
        explicitly_requested_topics=(),
        selection_seed=f"plan-{question_count}",
    )

    assert result.job_core_selected >= minimum
    assert result.degradation_reasons == ()


def test_non_job_soft_target_cannot_consume_reserved_job_capacity():
    bank, taxonomy = _training_bank(4)
    non_job_keys = tuple(
        _skill_key(f"Other/non-job-{index}") for index in range(3)
    )
    result = bank.select_training_questions(
        InterviewConfig(question_count=5),
        training=TrainingSelectionRequest(
            weak_skill_keys=(non_job_keys[0],),
            evidence_skill_keys=(non_job_keys[1],),
            mastery_skill_keys=(non_job_keys[2],),
            job_labels=("Python",),
            weak_target=1,
            evidence_target=1,
            mastery_target=1,
            minimum_job_core=3,
            job_constraint_enabled=True,
        ),
        taxonomy=taxonomy,
        relevance_text=None,
        explicitly_requested_topics=(),
        selection_seed="plan-soft-targets",
    )

    assert result.job_core_selected >= 3
    assert sum(not value for value in result.job_relevant_by_question.values()) <= 2
    assert sum(result.intent_selected.values()) == 2


def test_missing_job_labels_is_explicitly_degraded():
    bank, taxonomy = _training_bank(3)
    result = bank.select_training_questions(
        InterviewConfig(question_count=5),
        training=TrainingSelectionRequest(
            weak_skill_keys=(),
            evidence_skill_keys=(),
            mastery_skill_keys=(),
            job_labels=(),
            weak_target=0,
            evidence_target=0,
            mastery_target=0,
            minimum_job_core=3,
            job_constraint_enabled=True,
        ),
        taxonomy=taxonomy,
        relevance_text=None,
        explicitly_requested_topics=(),
        selection_seed="plan-no-labels",
    )

    assert result.job_core_available == 0
    assert "JOB_CORE_LABELS_UNAVAILABLE" in result.degradation_reasons


@pytest.mark.asyncio
async def test_planner_freezes_skill_history_selection_audit():
    bank, taxonomy = _training_bank(5)
    weak_key = _skill_key("Python/job-0")

    plan = await InterviewPlanner(bank, taxonomy=taxonomy).build(
        title="长期画像训练",
        config=InterviewConfig(question_count=5),
        candidate_profile=CandidateProfile(skills=["Python"]),
        job_profile=JobProfile(
            kb_id="job-python",
            role="Python 后端",
            required_skills=["Python"],
        ),
        candidate_profile_id="candidate-1",
        skill_progress=[_progress(weak_key, score=45, confidence=0.8)],
    )

    audit = plan.training_adjustment
    assert plan.candidate_profile_id == "candidate-1"
    assert audit is not None
    assert audit.source_progress_updated_at is not None
    assert audit.selection_reasons["q-job-0"] == "WEAK_RETEST"
    assert audit.job_relevant_by_question["q-job-0"] is True
    assert audit.job_core_selected >= 3
