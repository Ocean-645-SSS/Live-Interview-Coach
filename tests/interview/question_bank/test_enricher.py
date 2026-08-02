"""测试题目草稿经过 Provider 补全后形成正式题目的规则。"""

import pytest

from liverag.interview.question_bank.converter import ExtractedQuestionDraft
from liverag.interview.question_bank.enricher import (
    QuestionBankEnricher,
    QuestionEnrichment,
    QuestionEnrichmentError,
)
from liverag.interview.schemas import (
    InterviewDifficulty,
    QuestionType,
    RubricPoint,
)


def _draft(*, parent_question_id: str | None = None) -> ExtractedQuestionDraft:
    """创建一条包含全部 Markdown 原始证据的测试草稿。"""

    return ExtractedQuestionDraft(
        id="draft-rag",
        question_text="为什么 RAG 需要重排？",
        reference_answer="重排对召回结果进行更精细的相关性评分。",
        category="RAG",
        subcategory="检索",
        source_reference="study_source.md:line-100#RAG / 检索",
        parent_question_id=parent_question_id,
        is_high_frequency=True,
        source_line=100,
    )


class FakeProvider:
    """返回固定补全结果，保证测试不访问外部模型。"""

    def __init__(self, topics: list[str] | None = None):
        self._topics = topics or ["重排模型", "相关性评分"]

    async def enrich(self, draft: ExtractedQuestionDraft) -> QuestionEnrichment:
        """根据测试配置返回合法或故意冲突的 topics。"""

        return QuestionEnrichment(
            difficulty=InterviewDifficulty.INTERMEDIATE,
            question_type=QuestionType.TECHNICAL_KNOWLEDGE,
            topics=self._topics,
            objective="判断候选人是否理解召回与重排的职责边界",
            expected_points=[
                RubricPoint(id="recall", content="说明初步召回", required=True),
                RubricPoint(id="rerank", content="说明精细重排", required=True),
            ],
            follow_up_hints=["比较 Cross-Encoder 与向量相似度"],
        )


async def test_enrich_draft_preserves_source_evidence():
    """LLM 只能补充字段，不能覆盖 Markdown 中的题干、答案和来源。"""

    question = await QuestionBankEnricher(FakeProvider()).enrich_draft(
        _draft(),
        order=3,
    )

    assert question.order == 3
    assert question.question_text == "为什么 RAG 需要重排？"
    assert question.reference_answer == "重排对召回结果进行更精细的相关性评分。"
    assert question.source_reference.startswith("study_source.md:line-100")
    assert question.is_high_frequency is True


async def test_enrich_draft_forces_follow_up_type_when_parent_exists():
    """带父题 ID 的草稿必须成为追问题，不能沿用 Provider 给出的主问题类型。"""

    question = await QuestionBankEnricher(FakeProvider()).enrich_draft(
        _draft(parent_question_id="main-question"),
        order=2,
    )

    assert question.type is QuestionType.FOLLOW_UP
    assert question.parent_question_id == "main-question"


async def test_enrich_draft_rejects_when_only_topic_repeats_classification():
    """移除分类重复项后没有任何细粒度 topic 时必须拒绝。"""

    with pytest.raises(QuestionEnrichmentError, match="没有可用 topics"):
        await QuestionBankEnricher(FakeProvider(["RAG"])).enrich_draft(
            _draft(),
            order=1,
        )


async def test_enrich_draft_removes_repeated_classification_topic():
    """分类名称混入多个 topics 时只移除重复项，保留有效细粒度主题。"""

    question = await QuestionBankEnricher(
        FakeProvider(["RAG", "重排模型"])
    ).enrich_draft(_draft(), order=1)

    assert question.topics == ["重排模型"]


def test_enrichment_keeps_only_first_four_required_points():
    """模型标记过多必答点时按输出顺序保留前四个，其余降为扩展项。"""

    enrichment = QuestionEnrichment(
        difficulty=InterviewDifficulty.INTERMEDIATE,
        question_type=QuestionType.TECHNICAL_KNOWLEDGE,
        topics=["重排模型"],
        objective="验证重排原理",
        expected_points=[
            RubricPoint(
                id=f"point-{index}",
                content=f"评分点 {index}",
                required=True,
            )
            for index in range(1, 7)
        ],
    )

    assert [point.required for point in enrichment.expected_points] == [
        True,
        True,
        True,
        True,
        False,
        False,
    ]
