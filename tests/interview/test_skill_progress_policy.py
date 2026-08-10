"""长期技能画像纯策略测试。"""

from datetime import datetime, timedelta, timezone

from liverag.interview.schemas import SkillProgressEvidence
from liverag.interview.skill_progress.policy import (
    calculate_skill_progress,
    normalize_weak_point,
)


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def item(
    evaluation_id: str,
    *,
    score: float,
    day: int,
    session: str,
    weak_points: list[str] | None = None,
) -> SkillProgressEvidence:
    evaluated_at = BASE_TIME + timedelta(days=day)
    return SkillProgressEvidence(
        id=f"evidence_{evaluation_id}",
        candidate_profile_id="candidate_1",
        skill_key="skill_0123456789abcdef",
        evaluation_id=evaluation_id,
        session_id=session,
        interview_id=f"interview_{session}",
        question_id=f"question_{evaluation_id}",
        taxonomy_version=1,
        rubric_version=1,
        score=score,
        weak_points=weak_points or [],
        evaluated_at=evaluated_at,
        created_at=evaluated_at,
    )


def test_policy_calculates_average_decay_latest_and_confidence():
    evidence = [
        item("evaluation_old", score=40, day=0, session="s1"),
        item("evaluation_new", score=80, day=90, session="s2"),
    ]

    progress = calculate_skill_progress(evidence, taxonomy_version=1)

    assert progress.average_score == 60.0
    assert progress.current_score == 66.67
    assert progress.latest_score == 80.0
    assert progress.attempts == 2
    assert progress.source_evaluation_ids == ["evaluation_old", "evaluation_new"]
    assert progress.updated_at == BASE_TIME + timedelta(days=90)


def test_policy_aggregates_normalized_weak_points_with_stable_order():
    evidence = [
        item("evaluation_1", score=50, day=0, session="s1", weak_points=[" ＲＡＧ。", "事务"]),
        item("evaluation_2", score=60, day=1, session="s2", weak_points=["rag", " 事务；"]),
    ]

    progress = calculate_skill_progress(evidence, taxonomy_version=1)

    assert [(point.text, point.count) for point in progress.weak_points] == [
        ("RAG", 2),
        ("事务", 2),
    ]
    assert progress.weak_points[0].source_evaluation_ids == [
        "evaluation_1",
        "evaluation_2",
    ]
    assert normalize_weak_point(" ＲＡＧ。") == ("rag", "RAG")


def test_policy_is_independent_of_input_order():
    evidence = [
        item("evaluation_1", score=40, day=0, session="s1"),
        item("evaluation_2", score=80, day=90, session="s2"),
    ]

    first = calculate_skill_progress(evidence, taxonomy_version=1)
    second = calculate_skill_progress(list(reversed(evidence)), taxonomy_version=1)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
