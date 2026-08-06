"""评价、追问、报告与应用服务的协作测试。"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from liverag.interview.application.controller import (
    InterviewAgentController,
    InterviewSpeechKind,
)
from liverag.interview.persistence.db import create_session_factory, create_sqlite_engine
from liverag.interview.application.evaluator import AnswerEvaluator
from liverag.interview.persistence.models import Base
from liverag.interview.application.orchestrator import AnswerReceivedCommand
from liverag.interview.records import InterviewAnswerRecord, utc_now_iso
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
from liverag.interview.application.service import InterviewService
from liverag.interview.persistence.sqlalchemy_repository import SQLAlchemyInterviewRepository
from liverag.interview.state_machine import InterviewEventType


def _question() -> InterviewQuestion:
    return InterviewQuestion(
        id="question-1",
        order=1,
        type=QuestionType.TECHNICAL_KNOWLEDGE,
        source=QuestionSource.QUESTION_BANK,
        difficulty=InterviewDifficulty.INTERMEDIATE,
        category="RAG",
        topics=["检索"],
        question_text="RAG 的流程是什么？",
        objective="检查对 RAG 的理解",
        rubric=QuestionRubric(expected_points=[RubricPoint(id="flow", content="说明检索与生成")]),
    )


class _EvaluationProvider:
    async def evaluate(
        self,
        *,
        answer: InterviewAnswerRecord,
        question: InterviewQuestion,
    ) -> AnswerEvaluation:
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
    service = InterviewService(
        repository,
        evaluator=AnswerEvaluator(repository, _EvaluationProvider()),
    )
    config = InterviewConfig(question_count=1)
    interview = service.create_interview(title="服务测试", config=config)
    repository.save_interview_plan(
        interview_id=interview.id,
        plan=InterviewPlan(
            id="plan-1",
            title="服务测试计划",
            introduction="欢迎。",
            config=config,
            questions=[_question()],
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


async def test_evaluation_retry_reuses_saved_result_and_transition(interview_service):
    service, session_id, attempt_id = interview_service
    answer = _receive_answer(service, session_id, attempt_id)

    first = await service.evaluate_answer(answer.id)
    second = await service.evaluate_answer(answer.id)

    assert second.evaluation == first.evaluation
    assert second.session.state is InterviewState.FOLLOW_UP
    assert second.transitions == ()
    assert len(service.repository.list_events(session_id=session_id)) == 5


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
