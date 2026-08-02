"""测试结构化题库的整库校验、查询和确定性选题。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from liverag.interview.question_bank.catalog import (
    QuestionBank,
    QuestionBankDocument,
    QuestionBankError,
)
from liverag.interview.schemas import (
    InterviewConfig,
    InterviewDifficulty,
    InterviewQuestion,
    QuestionRubric,
    QuestionSource,
    QuestionType,
    RubricPoint,
)


def _question(
    question_id: str,
    *,
    order: int,
    category: str,
    subcategory: str,
    topic: str,
    difficulty: InterviewDifficulty = InterviewDifficulty.INTERMEDIATE,
    question_type: QuestionType = QuestionType.TECHNICAL_KNOWLEDGE,
    parent_question_id: str | None = None,
) -> InterviewQuestion:
    """创建字段完整且可按参数调整的测试题目。"""

    return InterviewQuestion(
        id=question_id,
        order=order,
        type=question_type,
        source=QuestionSource.QUESTION_BANK,
        difficulty=difficulty,
        category=category,
        subcategory=subcategory,
        topics=[topic],
        question_text=f"测试问题 {question_id}？",
        objective="验证候选人是否掌握核心概念",
        rubric=QuestionRubric(
            expected_points=[
                RubricPoint(id="concept", content="说明核心概念", required=True)
            ]
        ),
        reference_answer="这是文字参考答案。",
        source_reference=f"questions.md#{question_id}",
        parent_question_id=parent_question_id,
    )


@pytest.fixture
def question_bank() -> QuestionBank:
    """提供包含两个主问题和一个预设追问的测试题库。"""

    questions = [
        _question(
            "rag-basic",
            order=1,
            category="RAG",
            subcategory="检索",
            topic="向量召回",
        ),
        _question(
            "agent-basic",
            order=2,
            category="Agent",
            subcategory="规划",
            topic="工具调用",
            difficulty=InterviewDifficulty.SENIOR,
        ),
        _question(
            "rag-follow-up",
            order=3,
            category="RAG",
            subcategory="检索",
            topic="相似度度量",
            question_type=QuestionType.FOLLOW_UP,
            parent_question_id="rag-basic",
        ),
    ]
    return QuestionBank(QuestionBankDocument(version=1, questions=questions))


def test_document_rejects_follow_up_without_existing_parent():
    """追问题引用不存在的父题时，整库校验必须失败。"""

    follow_up = _question(
        "orphan",
        order=1,
        category="RAG",
        subcategory="检索",
        topic="召回率",
        question_type=QuestionType.FOLLOW_UP,
        parent_question_id="missing",
    )

    with pytest.raises(ValidationError, match="引用的主问题不存在"):
        QuestionBankDocument(version=1, questions=[follow_up])


def test_filter_questions_combines_all_conditions(question_bank: QuestionBank):
    """分类、子分类、主题、难度和题型之间按照 AND 关系过滤。"""

    result = question_bank.filter_questions(
        categories=["rag"],
        subcategories=["检索"],
        topics=["向量召回"],
        difficulties=[InterviewDifficulty.INTERMEDIATE],
        question_types=[QuestionType.TECHNICAL_KNOWLEDGE],
    )

    assert [question.id for question in result] == ["rag-basic"]
    assert [question.id for question in question_bank.list_follow_ups("rag-basic")] == [
        "rag-follow-up"
    ]


def test_select_questions_is_deterministic_and_excludes_follow_ups(
    question_bank: QuestionBank,
):
    """相同配置始终选出相同主问题，预设追问题不占主问题名额。"""

    config = InterviewConfig(
        question_count=2,
        difficulty=InterviewDifficulty.INTERMEDIATE,
        topic_weights={"RAG": 3.0, "Agent": 1.0},
    )

    first = question_bank.select_questions(config)
    second = question_bank.select_questions(config)

    assert [question.id for question in first] == ["rag-basic", "agent-basic"]
    assert [question.id for question in second] == ["rag-basic", "agent-basic"]
    assert [question.order for question in first] == [1, 2]


def test_from_file_wraps_invalid_json_as_business_error(tmp_path: Path):
    """调用方读取损坏 JSON 时得到稳定的题库业务异常。"""

    path = tmp_path / "invalid.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(QuestionBankError, match="不是合法 JSON"):
        QuestionBank.from_file(path)
