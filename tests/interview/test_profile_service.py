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
    """ProfileService 不再通过题库标签匹配 skills；skills 来自 CandidateFacts，
    required_skills 为空（JobProfile 不依赖标签匹配）。"""
    service = InterviewProfileService(_Source())

    candidate = await service.build_candidate_profile("default")
    job = await service.build_job_profile(
        kb_id="job-byte-backend",
        company="字节跳动",
        role="后端开发",
    )

    # 无 CandidateFacts 时 skills 为空
    assert candidate.skills == []
    assert candidate.projects == ["项目：使用 Python 实现异步编程服务，负责系统设计。"]
    assert candidate.evidence_refs == ["resume.pdf"]
    # required_skills 不再由标签匹配产生
    assert job.required_skills == []
    assert job.company == "字节跳动"
    assert job.evidence_refs == ["jd.txt"]
