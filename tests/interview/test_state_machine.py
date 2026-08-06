"""测试持久化面试状态机的主流程、暂停恢复和事件保护。"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from liverag.interview.persistence.db import create_session_factory, create_sqlite_engine
from liverag.interview.persistence.models import Base
from liverag.interview.records import InterviewEventRecord, InterviewSessionRecord
from liverag.interview.persistence.repository import DuplicateEventError, InterviewRepository
from liverag.interview.schemas import (
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
from liverag.interview.persistence.sqlalchemy_repository import SQLAlchemyInterviewRepository
from liverag.interview.state_machine import (
    InterviewEventType,
    InterviewStateMachine,
    InterviewTransitionError,
)


class _StateMachineTestDriver:
    """仅为规则测试提供持久化后的连续 Session 输入。"""

    def __init__(self, repository: InterviewRepository):
        self.machine = InterviewStateMachine()
        self.repository = repository


@dataclass(frozen=True, slots=True)
class _TransitionResult:
    event: InterviewEventRecord
    session: InterviewSessionRecord


def _question(question_id: str, order: int) -> InterviewQuestion:
    """创建状态机冻结计划中使用的一道合法主问题。"""

    return InterviewQuestion(
        id=question_id,
        order=order,
        type=QuestionType.TECHNICAL_KNOWLEDGE,
        source=QuestionSource.QUESTION_BANK,
        difficulty=InterviewDifficulty.INTERMEDIATE,
        category="RAG",
        subcategory="检索",
        topics=[f"知识点-{order}"],
        question_text=f"第 {order} 道测试题是什么？",
        objective="验证候选人是否理解核心原理",
        rubric=QuestionRubric(
            expected_points=[
                RubricPoint(id="concept", content="说明核心概念", required=True)
            ]
        ),
        reference_answer="参考答案",
        source_reference=f"questions.md#{question_id}",
    )


@pytest.fixture
def state_machine(
    tmp_path: Path,
) -> Iterator[tuple[_StateMachineTestDriver, InterviewRepository, str]]:
    """创建注入 SQLAlchemy Repository 的真实 SQLite 状态机。"""

    engine = create_sqlite_engine(tmp_path / "interview.db")
    Base.metadata.create_all(engine)
    repository = SQLAlchemyInterviewRepository(create_session_factory(engine))
    config = InterviewConfig(question_count=2, max_follow_ups_per_question=1)
    interview = repository.create_interview(
        interview_id="interview-test",
        title="状态机测试",
        config=config,
    )
    plan = InterviewPlan(
        id="plan-test",
        title="状态机测试计划",
        introduction="欢迎参加模拟面试。",
        config=config,
        questions=[_question("question-1", 1), _question("question-2", 2)],
        closing_message="本次模拟面试结束。",
    )
    repository.save_interview_plan(
        interview_id=interview.id,
        plan=plan,
        expected_version=interview.version,
    )
    session = repository.create_session(
        interview_id=interview.id,
        session_id="session-test",
    )
    try:
        yield _StateMachineTestDriver(repository), repository, session.id
    finally:
        engine.dispose()


def _apply(
    driver: _StateMachineTestDriver,
    session_id: str,
    event_number: int,
    event_type: InterviewEventType,
):
    """使用稳定且唯一的测试事件 ID 执行一次迁移。"""

    repository = driver.repository
    event_id = f"event-{event_number}"
    if repository.event_exists(event_id):
        raise DuplicateEventError(f"事件已经处理：{event_id}")
    session = repository.get_session(session_id)
    plan = repository.get_interview_plan(session.interview_id)
    assert plan is not None
    snapshot = driver.machine.calculate_transition(
        session=session,
        plan=plan,
        event_type=event_type,
    )
    event = repository.record_transition(
        event_id=event_id,
        session_id=session.id,
        event_type=event_type.value,
        payload={},
        expected_version=session.version,
        state_before=session.state,
        state_after=snapshot.state,
        resume_state=snapshot.resume_state,
        current_question_index=snapshot.current_question_index,
        current_question_id=snapshot.current_question_id,
        follow_up_count=snapshot.follow_up_count,
        started_at=snapshot.started_at,
        ended_at=snapshot.ended_at,
    )
    return _TransitionResult(
        event=event,
        session=repository.get_session(session.id),
    )


def test_complete_interview_flow_persists_every_transition(state_machine):
    """两道题、一次追问和报告生成可以完整到达 COMPLETED。"""

    machine, repository, session_id = state_machine
    events = [
        InterviewEventType.START,
        InterviewEventType.INTRODUCTION_FINISHED,
        InterviewEventType.QUESTION_ASKED,
        InterviewEventType.ANSWER_RECEIVED,
        InterviewEventType.FOLLOW_UP_REQUIRED,
        InterviewEventType.FOLLOW_UP_ASKED,
        InterviewEventType.ANSWER_RECEIVED,
        InterviewEventType.NEXT_QUESTION,
        InterviewEventType.QUESTION_ADVANCED,
        InterviewEventType.QUESTION_ASKED,
        InterviewEventType.ANSWER_RECEIVED,
        InterviewEventType.FINISH,
        InterviewEventType.REPORT_COMPLETED,
    ]

    result = None
    for event_number, event_type in enumerate(events, start=1):
        result = _apply(machine, session_id, event_number, event_type)

    assert result is not None
    assert result.session.state is InterviewState.COMPLETED
    assert result.session.current_question_index == 1
    assert result.session.current_question_id is None
    assert result.session.ended_at is not None
    assert len(repository.list_events(session_id=session_id)) == len(events)


def test_pause_and_resume_restore_exact_previous_state(state_machine):
    """暂停不会丢失题目位置，恢复后回到暂停前的 LISTENING。"""

    machine, _, session_id = state_machine
    _apply(machine, session_id, 1, InterviewEventType.START)
    _apply(machine, session_id, 2, InterviewEventType.INTRODUCTION_FINISHED)
    listening = _apply(machine, session_id, 3, InterviewEventType.QUESTION_ASKED)
    paused = _apply(machine, session_id, 4, InterviewEventType.PAUSE)
    resumed = _apply(machine, session_id, 5, InterviewEventType.RESUME)

    assert listening.session.state is InterviewState.LISTENING
    assert paused.session.state is InterviewState.PAUSED
    assert paused.session.resume_state is InterviewState.LISTENING
    assert resumed.session.state is InterviewState.LISTENING
    assert resumed.session.resume_state is None
    assert resumed.session.current_question_id == "question-1"


def test_illegal_event_does_not_create_database_event(state_machine):
    """READY 不能直接接收回答，非法事件不会写入事件表。"""

    machine, repository, session_id = state_machine

    with pytest.raises(InterviewTransitionError, match="不允许事件"):
        _apply(machine, session_id, 1, InterviewEventType.ANSWER_RECEIVED)

    assert repository.list_events(session_id=session_id) == []


def test_duplicate_event_id_is_rejected_before_second_state_change(state_machine):
    """相同事件被重复投递时不会因为当前状态变化而掩盖幂等错误。"""

    machine, repository, session_id = state_machine
    _apply(machine, session_id, 1, InterviewEventType.START)

    with pytest.raises(DuplicateEventError, match="已经处理"):
        _apply(machine, session_id, 1, InterviewEventType.START)

    assert repository.get_session(session_id).state is InterviewState.INTRODUCTION
    assert len(repository.list_events(session_id=session_id)) == 1


def test_follow_up_limit_is_enforced(state_machine):
    """达到配置追问上限后，评价阶段不能再次进入 FOLLOW_UP。"""

    machine, _, session_id = state_machine
    _apply(machine, session_id, 1, InterviewEventType.START)
    _apply(machine, session_id, 2, InterviewEventType.INTRODUCTION_FINISHED)
    _apply(machine, session_id, 3, InterviewEventType.QUESTION_ASKED)
    _apply(machine, session_id, 4, InterviewEventType.ANSWER_RECEIVED)
    _apply(machine, session_id, 5, InterviewEventType.FOLLOW_UP_REQUIRED)
    _apply(machine, session_id, 6, InterviewEventType.FOLLOW_UP_ASKED)
    _apply(machine, session_id, 7, InterviewEventType.ANSWER_RECEIVED)

    with pytest.raises(InterviewTransitionError, match="追问次数已达到上限"):
        _apply(machine, session_id, 8, InterviewEventType.FOLLOW_UP_REQUIRED)


def test_calculate_transition_is_pure_and_does_not_write_repository(state_machine):
    """纯状态机只返回新快照，不改变数据库中的 Session 或 Event。"""

    _, repository, session_id = state_machine
    session = repository.get_session(session_id)
    plan = repository.get_interview_plan(session.interview_id)
    assert plan is not None

    snapshot = InterviewStateMachine().calculate_transition(
        session=session,
        plan=plan,
        event_type=InterviewEventType.START,
    )

    assert snapshot.state is InterviewState.INTRODUCTION
    assert repository.get_session(session_id) == session
    assert repository.list_events(session_id=session_id) == []
