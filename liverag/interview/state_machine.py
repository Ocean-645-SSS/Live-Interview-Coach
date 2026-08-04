"""Live Interview Coach V1 的持久化面试状态机，处于领域规划层：
解决当前面试处于什么阶段，收到某个业务事件后，是否允许进入下一阶段。

调用方只能提交业务事件，不能直接指定目标状态。状态机根据当前 Session、
冻结的 InterviewPlan 和配置计算新快照，再复用 Repository 的事务、
事件幂等键和乐观锁完成持久化。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from liverag.interview.records import InterviewSessionRecord, utc_now_iso
from liverag.interview.schemas import InterviewPlan, InterviewState


class InterviewTransitionError(RuntimeError):
    """事件不允许从当前状态执行，或者状态快照不完整时抛出的异常。"""


class InterviewEventType(str, Enum):
    """V1 中允许驱动实时面试状态变化的业务事件。"""

    START = "START" #开始
    INTRODUCTION_FINISHED = "INTRODUCTION_FINISHED" #开场白结束
    QUESTION_ASKED = "QUESTION_ASKED"   #问题已被问出，进入听答阶段
    ANSWER_RECEIVED = "ANSWER_RECEIVED" #回答已被收到，进入评价阶段
    FOLLOW_UP_REQUIRED = "FOLLOW_UP_REQUIRED"   #当前问题需要追问，进入追问阶段
    FOLLOW_UP_ASKED = "FOLLOW_UP_ASKED" #追问已被问出，进入听答阶段
    NEXT_QUESTION = "NEXT_QUESTION" #当前问题评价完成，进入下一题准备阶段
    QUESTION_ADVANCED = "QUESTION_ADVANCED" #当前问题已被推进到下一题，进入问答阶段
    FINISH = "FINISH"   #当前问题评价完成，进入面试结束阶段
    REPORT_COMPLETED = "REPORT_COMPLETED"  #面试报告已生成，进入面试完成阶段
    PAUSE = "PAUSE" #中断
    RESUME = "RESUME"   #恢复
    ABORT = "ABORT"  #终止
    FAIL = "FAIL"   #失败


@dataclass(frozen=True, slots=True)
class SessionTransition:
    """状态机计算出的新 Session 快照，不执行数据库写入。"""

    state: InterviewState
    resume_state: InterviewState | None    #暂停前的状态，PAUSED 时保存，恢复后清空
    current_question_index: int     #当前题目在计划中的索引
    current_question_id: str | None   #当前题目在计划中的标识，未开始或已结束时为 None
    follow_up_count: int    #追问次数
    started_at: str | None
    ended_at: str | None


"""状态机定义：
READY
  └─ START → INTRODUCTION
       └─ INTRODUCTION_FINISHED → ASKING
            └─ QUESTION_ASKED → LISTENING
                 └─ ANSWER_RECEIVED → EVALUATING
                      ├─ FOLLOW_UP_REQUIRED → FOLLOW_UP
                      ├─ NEXT_QUESTION → NEXT_QUESTION
                      └─ FINISH → COMPLETING
                           └─ REPORT_COMPLETED → COMPLETED"""

_NORMAL_TRANSITIONS: dict[
    tuple[InterviewState, InterviewEventType],
    InterviewState,
] = {
    (InterviewState.READY, InterviewEventType.START): InterviewState.INTRODUCTION,
    (
        InterviewState.INTRODUCTION,
        InterviewEventType.INTRODUCTION_FINISHED,
    ): InterviewState.ASKING,
    (InterviewState.ASKING, InterviewEventType.QUESTION_ASKED): InterviewState.LISTENING,
    (
        InterviewState.LISTENING,
        InterviewEventType.ANSWER_RECEIVED,
    ): InterviewState.EVALUATING,
    (
        InterviewState.EVALUATING,
        InterviewEventType.FOLLOW_UP_REQUIRED,
    ): InterviewState.FOLLOW_UP,
    (
        InterviewState.FOLLOW_UP,
        InterviewEventType.FOLLOW_UP_ASKED,
    ): InterviewState.LISTENING,
    (
        InterviewState.EVALUATING,
        InterviewEventType.NEXT_QUESTION,
    ): InterviewState.NEXT_QUESTION,
    (
        InterviewState.NEXT_QUESTION,
        InterviewEventType.QUESTION_ADVANCED,
    ): InterviewState.ASKING,
    (InterviewState.EVALUATING, InterviewEventType.FINISH): InterviewState.COMPLETING,
    (
        InterviewState.COMPLETING,
        InterviewEventType.REPORT_COMPLETED,
    ): InterviewState.COMPLETED,
}

# 可暂停状态机定义：
_PAUSABLE_STATES = frozenset(
    {
        InterviewState.INTRODUCTION,
        InterviewState.ASKING,
        InterviewState.LISTENING,
        InterviewState.EVALUATING,
        InterviewState.FOLLOW_UP,
        InterviewState.NEXT_QUESTION,
        InterviewState.COMPLETING,
    }
)

# 终态定义
_TERMINAL_STATES = frozenset(
    {
        InterviewState.COMPLETED,
        InterviewState.ABORTED,
        InterviewState.FAILED,
    }
)


class InterviewStateMachine:
    """校验面试事件并纯计算状态变化，不操作数据库。"""

    def calculate_transition(
        self,
        *,
        session: InterviewSessionRecord,
        plan: InterviewPlan,
        event_type: InterviewEventType,
    ) -> SessionTransition:
        """根据权威 Session、冻结计划和业务事件计算下一份快照:
        判断状态转换是否合法，计算更新后的session字段"""

        if session.state in _TERMINAL_STATES:
            raise InterviewTransitionError(
                f"终态 {session.state.value} 不能继续处理事件 {event_type.value}"
            )

        #处理特殊事件
        #暂停
        if event_type is InterviewEventType.PAUSE:
            return self._pause(session)
        #恢复
        if event_type is InterviewEventType.RESUME:
            return self._resume(session)
        #用户主动终止
        if event_type is InterviewEventType.ABORT:
            return self._finish_with_state(session, InterviewState.ABORTED)
        #系统异常失败
        if event_type is InterviewEventType.FAIL:
            return self._finish_with_state(session, InterviewState.FAILED)

        #处理正常事件
        #根据当前状态和事件类型查找目标状态
        target_state = _NORMAL_TRANSITIONS.get((session.state, event_type))
        #当前状态不合法
        if target_state is None:
            raise InterviewTransitionError(
                f"状态 {session.state.value} 不允许事件 {event_type.value}"
            )

        #复制session数据
        now = utc_now_iso()
        question_index = session.current_question_index
        question_id = session.current_question_id
        follow_up_count = session.follow_up_count
        started_at = session.started_at
        ended_at = session.ended_at

        #根据事件更新业务字段
        #START 事件设置开始时间
        if event_type is InterviewEventType.START:
            started_at = started_at or now
        #INTRODUCTION_FINISHED -> ASKING :
        # 事件设置第一道题，题目索引设为0，题目ID设为第一题ID，追问次数清零
        elif event_type is InterviewEventType.INTRODUCTION_FINISHED:
            question_index = 0
            question_id = plan.questions[0].id
            follow_up_count = 0
        #FOLLOW_UP_REQUIRED 事件增加追问次数，超过配置上限则报错
        elif event_type is InterviewEventType.FOLLOW_UP_REQUIRED:
            maximum = plan.config.max_follow_ups_per_question
            #达到追问上线
            if follow_up_count >= maximum:
                raise InterviewTransitionError(
                    f"当前问题追问次数已达到上限：{maximum}次"
                )
            follow_up_count += 1
        #QUESTION_ADVANCED 事件推进到下一题：
        # 题目索引+1，新题的追问次数清零，题目ID设为新题ID，若已经是最后一道题则报错
        elif event_type is InterviewEventType.QUESTION_ADVANCED:
            next_index = question_index + 1
            if next_index >= len(plan.questions):
                raise InterviewTransitionError("已经是最后一道题，不能继续前进")
            question_index = next_index
            question_id = plan.questions[next_index].id
            follow_up_count = 0
        #REPORT_COMPLETED 事件设置结束时间
        elif event_type is InterviewEventType.REPORT_COMPLETED:
            question_id = None
            ended_at = now

        #返回最新快照
        return SessionTransition(
            state=target_state,
            resume_state=None,
            current_question_index=question_index,
            current_question_id=question_id,
            follow_up_count=follow_up_count,
            started_at=started_at,
            ended_at=ended_at,
        )

    @staticmethod
    def _pause(session: InterviewSessionRecord) -> SessionTransition:
        """把可暂停状态保存为 resume_state，并进入 PAUSED。"""

        if session.state not in _PAUSABLE_STATES:
            raise InterviewTransitionError(f"状态 {session.state.value} 不能暂停")
        return SessionTransition(
            state=InterviewState.PAUSED,
            resume_state=session.state,
            current_question_index=session.current_question_index,
            current_question_id=session.current_question_id,
            follow_up_count=session.follow_up_count,
            started_at=session.started_at,
            ended_at=session.ended_at,
        )

    @staticmethod
    def _resume(session: InterviewSessionRecord) -> SessionTransition:
        """从 PAUSED 返回暂停前的状态，并清除 resume_state。"""

        if session.state is not InterviewState.PAUSED or session.resume_state is None:
            raise InterviewTransitionError("只有保存了恢复状态的 PAUSED Session 才能恢复")
        return SessionTransition(
            state=session.resume_state,
            resume_state=None,
            current_question_index=session.current_question_index,
            current_question_id=session.current_question_id,
            follow_up_count=session.follow_up_count,
            started_at=session.started_at,
            ended_at=session.ended_at,
        )

    @staticmethod
    def _finish_with_state(
        session: InterviewSessionRecord,
        state: InterviewState,
    ) -> SessionTransition:
        """终止或失败时保留题目位置，记录结束时间并清除恢复状态。"""

        return SessionTransition(
            state=state,
            resume_state=None,
            current_question_index=session.current_question_index,
            current_question_id=session.current_question_id,
            follow_up_count=session.follow_up_count,
            started_at=session.started_at,
            ended_at=utc_now_iso(),
        )


__all__ = [
    "InterviewEventType",
    "InterviewStateMachine",
    "InterviewTransitionError",
    "SessionTransition",
]
