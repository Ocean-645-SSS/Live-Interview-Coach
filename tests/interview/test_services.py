"""评价、追问、报告与应用服务的协作测试。"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from liverag.interview.application.controller import (
    InterviewAgentController,
    InterviewSpeechKind,
)
from liverag.interview.application.evaluator import (
    AnswerEvaluationContext,
    AnswerEvaluator,
)
from liverag.interview.application.orchestrator import AnswerReceivedCommand
from liverag.interview.application.service import InterviewService
from liverag.interview.persistence.db import create_session_factory, create_sqlite_engine
from liverag.interview.persistence.models import Base
from liverag.interview.persistence.sqlalchemy_repository import SQLAlchemyInterviewRepository
from liverag.interview.records import InterviewAnswerRecord, ReportState, utc_now_iso
from liverag.interview.schemas import (
    AnswerEvaluation,
    DimensionScores,
    FollowUpAction,
    InterviewConfig,
    InterviewDifficulty,
    InterviewPlan,
    InterviewQuestion,
    InterviewState,
    QuestionRubric,
    QuestionSource,
    QuestionType,
    RubricPoint,
)
from liverag.interview.skill_progress.service import SkillProgressService
from liverag.interview.skill_progress.taxonomy import SkillTaxonomy
from liverag.interview.state_machine import InterviewEventType


def _question(*, question_id: str = "question-1", order: int = 1) -> InterviewQuestion:
    return InterviewQuestion(
        id=question_id,
        order=order,
        type=QuestionType.TECHNICAL_KNOWLEDGE,
        source=QuestionSource.QUESTION_BANK,
        difficulty=InterviewDifficulty.INTERMEDIATE,
        category="RAG",
        subcategory="Embedding",
        topics=["检索"],
        question_text="RAG 的流程是什么？",
        objective="检查对 RAG 的理解",
        rubric=QuestionRubric(expected_points=[RubricPoint(id="flow", content="说明检索与生成")]),
    )


class _EvaluationProvider:
    def __init__(self):
        self.contexts: list[AnswerEvaluationContext | None] = []

    async def evaluate(
        self,
        *,
        answer: InterviewAnswerRecord,
        question: InterviewQuestion,
        context: AnswerEvaluationContext | None = None,
    ) -> AnswerEvaluation:
        self.contexts.append(context)
        scores = DimensionScores(
            technical_accuracy=3,
            completeness=3,
            clarity_and_structure=4,
            job_relevance=3,
        )
        return AnswerEvaluation(
            answer_id=answer.id,
            question_id=question.id,
            scores=scores,
            weighted_score=scores.calculate_weighted_score(question.rubric),
            covered_points=["检索"],
            missing_points=["重排"],
            normalized_transcript=answer.transcript,
            summary="基本正确，但缺少重排过程。",
            next_action=FollowUpAction.FOLLOW_UP,
            follow_up_target="重排",
            follow_up_question="为什么需要重排？",
        )


@pytest.fixture
def interview_service(tmp_path: Path) -> Iterator[tuple[InterviewService, str, str]]:
    engine = create_sqlite_engine(tmp_path / "services.db")
    Base.metadata.create_all(engine)
    repository = SQLAlchemyInterviewRepository(create_session_factory(engine))
    skill_progress_service = SkillProgressService(
        repository,
        SkillTaxonomy.from_file(
            Path("liverag/interview/skill_progress/data/skill_taxonomy.v1.json")
        ),
    )
    service = InterviewService(
        repository,
        evaluator=AnswerEvaluator(repository, _EvaluationProvider()),
        skill_progress_service=skill_progress_service,
    )
    config = InterviewConfig(question_count=2)
    interview = service.create_interview(title="服务测试", config=config)
    repository.save_interview_plan(
        interview_id=interview.id,
        plan=InterviewPlan(
            id="plan-1",
            title="服务测试计划",
            introduction="欢迎。",
            config=config,
            questions=[
                _question(),
                _question(question_id="question-2", order=2),
            ],
            closing_message="结束。",
        ),
        expected_version=interview.version,
    )
    session = service.create_session(interview.id)
    attempt = repository.create_attempt(session_id=session.id, room_name="room-1")
    try:
        yield service, session.id, attempt.id
    finally:
        engine.dispose()


def _receive_answer(
    service: InterviewService,
    session_id: str,
    attempt_id: str,
) -> InterviewAnswerRecord:
    for number, event_type in enumerate(
        (
            InterviewEventType.START,
            InterviewEventType.INTRODUCTION_FINISHED,
            InterviewEventType.QUESTION_ASKED,
        ),
        start=1,
    ):
        service.transition(
            session_id=session_id,
            event_id=f"event-{number}",
            event_type=event_type,
        )
    now = utc_now_iso()
    return service.receive_answer(
        AnswerReceivedCommand(
            session_id=session_id,
            attempt_id=attempt_id,
            event_id="event-answer",
            transcript="先检索，再生成。",
            answer_number=1,
            started_at=now,
            ended_at=now,
        )
    ).answer


def _receive_follow_up_answer(
    service: InterviewService,
    session_id: str,
    attempt_id: str,
    *,
    answer_number: int,
) -> InterviewAnswerRecord:
    service.transition(
        session_id=session_id,
        event_id=f"event-follow-up-asked-{answer_number}",
        event_type=InterviewEventType.FOLLOW_UP_ASKED,
    )
    now = utc_now_iso()
    return service.receive_answer(
        AnswerReceivedCommand(
            session_id=session_id,
            attempt_id=attempt_id,
            event_id=f"event-follow-up-answer-{answer_number}",
            transcript=f"follow-up answer {answer_number}",
            answer_number=answer_number,
            started_at=now,
            ended_at=now,
        )
    ).answer


async def test_evaluation_drives_follow_up_and_report(interview_service):
    service, session_id, attempt_id = interview_service
    answer = _receive_answer(service, session_id, attempt_id)

    result = await service.evaluate_answer(answer.id)
    report = service.generate_report(session_id)

    assert result.decision.event_type is InterviewEventType.FOLLOW_UP_REQUIRED
    assert result.decision.question_text == "为什么需要重排？"
    assert result.evaluation.answer_id == answer.id
    assert result.session.state is InterviewState.FOLLOW_UP
    assert [item.event.event_type for item in result.transitions] == [
        InterviewEventType.FOLLOW_UP_REQUIRED.value
    ]
    assert report.content_json is not None
    assert '"evaluation_count":1' in report.content_json
    assert service.repository.get_session(session_id).state is InterviewState.FOLLOW_UP


async def test_progressive_follow_ups_keep_same_question_context(interview_service):
    service, session_id, attempt_id = interview_service
    first_answer = _receive_answer(service, session_id, attempt_id)

    first_result = await service.evaluate_answer(first_answer.id)
    second_answer = _receive_follow_up_answer(
        service, session_id, attempt_id, answer_number=2
    )
    second_result = await service.evaluate_answer(second_answer.id)
    third_answer = _receive_follow_up_answer(
        service, session_id, attempt_id, answer_number=3
    )
    third_result = await service.evaluate_answer(third_answer.id)
    fourth_answer = _receive_follow_up_answer(
        service, session_id, attempt_id, answer_number=4
    )
    fourth_result = await service.evaluate_answer(fourth_answer.id)

    assert [
        first_result.decision.event_type,
        second_result.decision.event_type,
        third_result.decision.event_type,
    ] == [InterviewEventType.FOLLOW_UP_REQUIRED] * 3
    assert fourth_result.decision.event_type is InterviewEventType.NEXT_QUESTION
    assert service.get_session(session_id).current_question_id == "question-2"
    assert service.get_session(session_id).follow_up_count == 0

    assert service.evaluator is not None
    provider = service.evaluator._provider
    assert isinstance(provider, _EvaluationProvider)
    assert [context.follow_up_round for context in provider.contexts] == [0, 1, 2, 3]
    assert [len(context.prior_answers) for context in provider.contexts] == [0, 1, 2, 3]


async def test_evaluation_retry_reuses_saved_result_and_transition(interview_service):
    service, session_id, attempt_id = interview_service
    answer = _receive_answer(service, session_id, attempt_id)

    first = await service.evaluate_answer(answer.id)
    second = await service.evaluate_answer(answer.id)

    assert second.evaluation == first.evaluation
    assert second.session.state is InterviewState.FOLLOW_UP
    assert second.transitions == ()
    assert len(service.repository.list_events(session_id=session_id)) == 5
    interview_id = service.repository.get_session(session_id).interview_id
    interview = service.repository.get_interview(interview_id)
    progress = service.skill_progress_service.list_progress(
        interview.candidate_profile_id
    )
    assert progress[0].attempts == 1


def test_failed_evaluation_recovery_advances_once_to_next_question(interview_service):
    service, session_id, attempt_id = interview_service
    answer = _receive_answer(service, session_id, attempt_id)

    first = service.recover_failed_evaluation(answer.id, reason="timeout")
    retry = service.recover_failed_evaluation(answer.id, reason="timeout")

    assert first.state is InterviewState.ASKING
    assert first.current_question_id == "question-2"
    assert retry == first
    assert [item.event_type for item in service.list_events(session_id)][-2:] == [
        InterviewEventType.NEXT_QUESTION.value,
        InterviewEventType.QUESTION_ADVANCED.value,
    ]


def test_failed_evaluation_recovery_finishes_the_last_question(interview_service):
    service, session_id, attempt_id = interview_service
    first_answer = _receive_answer(service, session_id, attempt_id)
    service.recover_failed_evaluation(first_answer.id, reason="provider_failed")
    service.transition(
        session_id=session_id,
        event_id="event-question-2-asked",
        event_type=InterviewEventType.QUESTION_ASKED,
    )
    now = utc_now_iso()
    second_answer = service.receive_answer(
        AnswerReceivedCommand(
            session_id=session_id,
            attempt_id=attempt_id,
            event_id="event-answer-2",
            transcript="second answer",
            answer_number=1,
            started_at=now,
            ended_at=now,
        )
    ).answer

    recovered = service.recover_failed_evaluation(
        second_answer.id,
        reason="provider_failed",
    )

    assert recovered.state is InterviewState.COMPLETING
    assert service.list_events(session_id)[-1].event_type == InterviewEventType.FINISH.value


async def test_skill_progress_failure_does_not_block_evaluation_or_transition(
    interview_service,
):
    service, session_id, attempt_id = interview_service
    answer = _receive_answer(service, session_id, attempt_id)
    real_service = service.skill_progress_service
    failing_service = MagicMock(wraps=real_service)
    failing_service.apply_evaluation.side_effect = RuntimeError("progress unavailable")
    service.skill_progress_service = failing_service

    result = await service.evaluate_answer(answer.id)

    assert service.repository.get_evaluation(answer.id) == result.evaluation
    assert result.decision.event_type is InterviewEventType.FOLLOW_UP_REQUIRED
    assert result.transitions
    interview = service.repository.get_interview(
        service.repository.get_session(session_id).interview_id
    )
    assert real_service.rebuild_candidate(interview.candidate_profile_id)


async def test_report_remains_completed_when_skill_progress_rebuild_fails(
    interview_service,
):
    service, session_id, attempt_id = interview_service
    answer = _receive_answer(service, session_id, attempt_id)
    await service.evaluate_answer(answer.id)
    failing_service = MagicMock(wraps=service.skill_progress_service)
    failing_service.rebuild_candidate.side_effect = RuntimeError("rebuild unavailable")
    service.skill_progress_service = failing_service

    report = service.generate_report(session_id)

    assert report.state is ReportState.COMPLETED
    saved_report = service.repository.get_report_by_session(session_id)
    assert saved_report is not None
    assert saved_report.state is ReportState.COMPLETED


async def test_evaluator_rejects_provider_identity_drift(interview_service):
    service, session_id, attempt_id = interview_service
    answer = _receive_answer(service, session_id, attempt_id)

    class InvalidProvider(_EvaluationProvider):
        async def evaluate(self, **kwargs) -> AnswerEvaluation:
            evaluation = await super().evaluate(**kwargs)
            return evaluation.model_copy(update={"answer_id": "another-answer"})

    with pytest.raises(ValueError, match="标识与请求不一致"):
        await AnswerEvaluator(service.repository, InvalidProvider()).evaluate(answer.id)


async def test_evaluate_answer_requires_configured_evaluator(interview_service):
    service, session_id, attempt_id = interview_service
    answer = _receive_answer(service, session_id, attempt_id)
    service_without_evaluator = InterviewService(service.repository)

    with pytest.raises(RuntimeError, match="尚未配置 AnswerEvaluator"):
        await service_without_evaluator.evaluate_answer(answer.id)


async def test_interview_agent_controller_connects_voice_to_service(interview_service):
    service, session_id, attempt_id = interview_service
    controller = InterviewAgentController(
        service=service,
        session_id=session_id,
        attempt_id=attempt_id,
    )

    introduction = controller.start()
    question = controller.introduction_spoken()
    listening_session = controller.prompt_spoken(question.kind)
    answer_result = await controller.receive_final_answer("先检索，再生成。")

    assert introduction.kind is InterviewSpeechKind.INTRODUCTION
    assert question.kind is InterviewSpeechKind.QUESTION
    assert listening_session.state is InterviewState.LISTENING
    assert answer_result.next_speech.kind is InterviewSpeechKind.FOLLOW_UP
    assert answer_result.evaluation_result.session.state is InterviewState.FOLLOW_UP


async def test_interview_agent_controller_resumes_pending_evaluation(interview_service):
    service, session_id, attempt_id = interview_service
    _receive_answer(service, session_id, attempt_id)

    resumed = await InterviewAgentController(
        service=service,
        session_id=session_id,
        attempt_id=attempt_id,
    ).resume_pending_evaluation()

    assert resumed.next_speech.kind is InterviewSpeechKind.FOLLOW_UP
    assert service.get_session(session_id).state is InterviewState.FOLLOW_UP
    assert len(service.list_answers(session_id)) == 1


async def test_interview_agent_controller_rejects_empty_answer(interview_service):
    service, session_id, attempt_id = interview_service
    controller = InterviewAgentController(
        service=service,
        session_id=session_id,
        attempt_id=attempt_id,
    )

    controller.start()
    question = controller.introduction_spoken()
    controller.prompt_spoken(question.kind)

    with pytest.raises(ValueError, match="最终回答不能为空"):
        await controller.receive_final_answer("   ")


async def test_interview_agent_controller_restores_question_after_disconnect(
    interview_service,
):
    service, session_id, attempt_id = interview_service
    controller = InterviewAgentController(
        service=service,
        session_id=session_id,
        attempt_id=attempt_id,
    )
    controller.start()
    question = controller.introduction_spoken()
    controller.prompt_spoken(question.kind)

    restored = InterviewAgentController(
        service=service,
        session_id=session_id,
        attempt_id=attempt_id,
    ).start()

    assert restored.kind is InterviewSpeechKind.QUESTION
    assert restored.text == question.text
    assert service.get_session(session_id).state is InterviewState.LISTENING


async def test_interview_agent_controller_restores_follow_up_after_disconnect(
    interview_service,
):
    service, session_id, attempt_id = interview_service
    controller = InterviewAgentController(
        service=service,
        session_id=session_id,
        attempt_id=attempt_id,
    )
    controller.start()
    question = controller.introduction_spoken()
    controller.prompt_spoken(question.kind)
    result = await controller.receive_final_answer("先检索，再生成。")

    restored = InterviewAgentController(
        service=service,
        session_id=session_id,
        attempt_id=attempt_id,
    ).start()

    assert restored.kind is InterviewSpeechKind.FOLLOW_UP
    assert restored.text == result.next_speech.text
