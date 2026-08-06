from liverag.interview.application.profile_service import InterviewProfileService, KnowledgeContext
from liverag.interview.question_bank.catalog import QuestionBank, QuestionBankDocument
from liverag.interview.schemas import (
    InterviewDifficulty,
    InterviewQuestion,
    QuestionRubric,
    QuestionSource,
    QuestionType,
    RubricPoint,
)


def _bank() -> QuestionBank:
    return QuestionBank(
        QuestionBankDocument(
            version=1,
            questions=[
                InterviewQuestion(
                    id="q-python",
                    order=1,
                    type=QuestionType.TECHNICAL_KNOWLEDGE,
                    source=QuestionSource.QUESTION_BANK,
                    difficulty=InterviewDifficulty.INTERMEDIATE,
                    category="Python",
                    topics=["异步编程"],
                    question_text="解释事件循环。",
                    objective="检查异步基础",
                    rubric=QuestionRubric(
                        expected_points=[RubricPoint(id="event-loop", content="事件循环")]
                    ),
                    reference_answer="事件循环负责调度协程。",
                    source_reference="test.md#python",
                )
            ],
        )
    )


class _Source:
    async def retrieve(self, *, kb_id: str, query: str) -> KnowledgeContext:
        assert query
        if kb_id == "default":
            return KnowledgeContext(
                context="项目：使用 Python 实现异步编程服务，负责系统设计。",
                evidence_refs=("resume.pdf",),
            )
        return KnowledgeContext(
            context="岗位要求熟悉 Python 和异步编程。",
            evidence_refs=("jd.txt",),
        )


async def test_profile_service_builds_candidate_and_job_profiles():
    service = InterviewProfileService(_Source(), _bank())

    candidate = await service.build_candidate_profile("default")
    job = await service.build_job_profile(
        kb_id="job-byte-backend",
        company="字节跳动",
        role="后端开发",
    )

    assert candidate.skills == ["Python", "异步编程"]
    assert candidate.projects == ["项目：使用 Python 实现异步编程服务，负责系统设计。"]
    assert candidate.evidence_refs == ["resume.pdf"]
    assert job.required_skills == ["Python", "异步编程"]
    assert job.company == "字节跳动"
    assert job.evidence_refs == ["jd.txt"]


def test_profile_service_does_not_match_english_labels_inside_other_words():
    bank = QuestionBank(
        QuestionBankDocument(
            version=1,
            questions=[
                _bank().get_question("q-python"),
                InterviewQuestion(
                    id="q-short-labels",
                    order=2,
                    type=QuestionType.TECHNICAL_KNOWLEDGE,
                    source=QuestionSource.QUESTION_BANK,
                    difficulty=InterviewDifficulty.INTERMEDIATE,
                    category="Agent",
                    topics=["PPO", "SSE"],
                    question_text="解释强化学习与流式传输。",
                    objective="检查技术基础",
                    rubric=QuestionRubric(
                        expected_points=[RubricPoint(id="point", content="关键点")]
                    ),
                    reference_answer="参考答案",
                    source_reference="test.md#labels",
                ),
            ],
        )
    )
    service = InterviewProfileService(_Source(), bank)

    assert service._match_labels("support and assessment") == []
