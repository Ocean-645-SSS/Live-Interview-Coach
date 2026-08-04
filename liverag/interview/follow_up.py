"""把结构化评价转换为受状态机约束的下一步动作。"""

from __future__ import annotations

from dataclasses import dataclass

from liverag.interview.records import InterviewSessionRecord
from liverag.interview.schemas import (
    AnswerEvaluation,
    FollowUpAction,
    InterviewPlan,
    InterviewQuestion,
)
from liverag.interview.state_machine import InterviewEventType


@dataclass(frozen=True, slots=True)
class FollowUpDecision:
    event_type: InterviewEventType
    question_text: str | None = None
    target: str | None = None


class FollowUpPolicy:
    """根据评价、题目设置和流程上限产生确定性的后续事件：追问/下一题/结束。"""

    def decide(
        self,
        *,
        evaluation: AnswerEvaluation,
        question: InterviewQuestion,
        session: InterviewSessionRecord,
        plan: InterviewPlan,
    ) -> FollowUpDecision:
        """根据对于问题的评价决定下一步"""

        if evaluation.question_id != question.id:
            raise ValueError("评价与当前题目不匹配")

        #评价给出的下一个动作是追问/理解用户
        if evaluation.next_action in {FollowUpAction.FOLLOW_UP, FollowUpAction.CLARIFY}:
            #检查题目是否允许追问+追问次数是否在限定范围内
            can_follow_up = (
                question.allow_follow_up
                and session.follow_up_count < plan.config.max_follow_ups_per_question
            )
            #允许追问
            if can_follow_up:
                #下一步：追问
                return FollowUpDecision(
                    event_type=InterviewEventType.FOLLOW_UP_REQUIRED,
                    question_text=evaluation.follow_up_question,
                    target=evaluation.follow_up_target,
                )

        #检查下一个问题是否在计划问题数之内
        has_next_question = session.current_question_index + 1 < len(plan.questions)
        #下一步：下一个问题
        if evaluation.next_action is not FollowUpAction.END and has_next_question:
            return FollowUpDecision(event_type=InterviewEventType.NEXT_QUESTION)

        #下一步：结束
        return FollowUpDecision(event_type=InterviewEventType.FINISH)


__all__ = ["FollowUpDecision", "FollowUpPolicy"]
