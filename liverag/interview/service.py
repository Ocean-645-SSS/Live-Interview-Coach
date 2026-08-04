"""FastAPI 与实时 Agent 共用的 Interview 应用服务层，
负责：
创建Interview+Session；提交普通事件；提交最终回答；根据评价计算后续动作；生成持久化报告

组装后的流程：
FastAPI / LiveKit Agent
          ↓
      service.py
          │
          ├── evaluator.py
          │      └── 评价回答
          ├── follow_up.py
          │      └── 决定追问、下一题或结束
          ├── orchestrator.py
          │      └── 执行状态转换（2种）
          └── report.py
                 └── 生成最终报告
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from liverag.interview.evaluator import AnswerEvaluator
from liverag.interview.follow_up import FollowUpDecision, FollowUpPolicy
from liverag.interview.orchestrator import (
    AnswerReceivedCommand,
    AnswerReceivedResult,
    InterviewOrchestrator,
    InterviewTransitionResult,
)
from liverag.interview.records import (
    InterviewRecord,
    InterviewReportRecord,
    InterviewSessionRecord,
    ReportState,
)
from liverag.interview.report import InterviewReportBuilder
from liverag.interview.repository import InterviewRepository, RecordNotFoundError
from liverag.interview.schemas import AnswerEvaluation, InterviewConfig, InterviewPlan
from liverag.interview.state_machine import InterviewEventType


@dataclass(frozen=True, slots=True)
class EvaluationDecisionResult:
    """回答评价、决策及自动状态推进后的权威结果。"""

    evaluation: AnswerEvaluation
    decision: FollowUpDecision
    session: InterviewSessionRecord
    transitions: tuple[InterviewTransitionResult, ...]


class InterviewService:
    """提供面试管理、实时状态编排、评价决策和报告生成用例。"""

    def __init__(
        self,
        repository: InterviewRepository,
        evaluator: AnswerEvaluator | None = None,
    ):
        self.repository = repository
        self.evaluator = evaluator
        self.orchestrator = InterviewOrchestrator(repository)
        self.follow_up_policy = FollowUpPolicy()
        self.report_builder = InterviewReportBuilder(repository)

    def create_interview(self, *, title: str, config: InterviewConfig) -> InterviewRecord:
        return self.repository.create_interview(title=title, config=config)

    def get_interview(self, interview_id: str) -> InterviewRecord:
        return self.repository.get_interview(interview_id)

    def save_interview_plan(
        self,
        *,
        interview_id: str,
        plan: InterviewPlan,
        expected_version: int,
    ) -> InterviewRecord:
        return self.repository.save_interview_plan(
            interview_id=interview_id,
            plan=plan,
            expected_version=expected_version,
        )

    def create_session(self, interview_id: str) -> InterviewSessionRecord:
        return self.repository.create_session(interview_id=interview_id)

    def transition(
        self,
        *,
        session_id: str,
        event_id: str,
        event_type: InterviewEventType,
        payload: dict[str, Any] | None = None,
    ) -> InterviewTransitionResult:
        return self.orchestrator.transition(
            session_id=session_id,
            event_id=event_id,
            event_type=event_type,
            payload=payload,
        )

    def receive_answer(self, command: AnswerReceivedCommand) -> AnswerReceivedResult:
        return self.orchestrator.receive_answer(command)

    def save_evaluation(
        self,
        *,
        evaluation_id: str,
        evaluation: AnswerEvaluation,
        rubric_version: int = 1,
    ) -> AnswerEvaluation:
        return self.repository.save_evaluation(
            evaluation_id=evaluation_id,
            evaluation=evaluation,
            rubric_version=rubric_version,
        )

    async def evaluate_answer(self, answer_id: str) -> EvaluationDecisionResult:
        """生成或恢复评价，并按照决策自动推进 Session 状态。"""

        try:
            #获取评价
            evaluation = self.repository.get_evaluation(answer_id)
        except RecordNotFoundError:
            #评价还未生成
            if self.evaluator is None:
                raise RuntimeError("InterviewService 尚未配置 AnswerEvaluator") from None
            #生成评价
            evaluation = await self.evaluator.evaluate(answer_id)

        #获取下一步行动
        decision = self.decide_after_evaluation(evaluation)
        #把评价转化为面试状态变化
        transitions = self._apply_evaluation_decision(evaluation, decision)
        #获取原本答案
        answer = self.repository.get_answer(answer_id)

        return EvaluationDecisionResult(
            evaluation=evaluation,
            decision=decision,
            session=self.repository.get_session(answer.session_id),
            transitions=tuple(transitions),
        )

    def _apply_evaluation_decision(
        self,
        evaluation: AnswerEvaluation,
        decision: FollowUpDecision,
    ) -> list[InterviewTransitionResult]:
        """把 follow_up/next_question/completed 决策转换成幂等状态机事件。"""

        #获取答案
        answer = self.repository.get_answer(evaluation.answer_id)
        #decision决定的驱动事件类型
        event_types = [decision.event_type]
        #是下一个问题，不追问/结束
        if decision.event_type is InterviewEventType.NEXT_QUESTION:
            #添加新状态：下一个问题，进入提问状态
            event_types.append(InterviewEventType.QUESTION_ADVANCED)

        transitions: list[InterviewTransitionResult] = []
        for event_type in event_types:
            #生成事件id
            event_id = self._decision_event_id(answer.id, event_type)
            #幂等：事件存在，跳过
            if self.repository.event_exists(event_id):
                continue
            #处理普通事件
            transitions.append(
                self.transition(
                    session_id=answer.session_id,
                    event_id=event_id,
                    event_type=event_type,
                    payload={
                        "answer_id": answer.id,
                        "evaluation_action": evaluation.next_action.value,
                        "follow_up_target": decision.target,
                        "follow_up_question": decision.question_text,
                    },
                )
            )
        return transitions

    @staticmethod
    def _decision_event_id(answer_id: str, event_type: InterviewEventType) -> str:
        """生成长度稳定的确定性事件 ID，支持评价后状态推进重试。"""

        digest = hashlib.sha256(
            f"{answer_id}:{event_type.value}".encode()
        ).hexdigest()[:32]
        return f"evaluation_{digest}"

    def decide_after_evaluation(self, evaluation: AnswerEvaluation) -> FollowUpDecision:
        """根据当前evaluation(answer+session+plan)决定下一步行动"""

        answer = self.repository.get_answer(evaluation.answer_id)
        session = self.repository.get_session(answer.session_id)
        plan = self.repository.get_interview_plan(session.interview_id)
        if plan is None:
            raise ValueError("回答对应的面试计划不存在")
        question = next(
            (item for item in plan.questions if item.id == answer.question_id),
            None,
        )
        if question is None:
            raise ValueError(f"面试计划中不存在题目：{answer.question_id}")

        return self.follow_up_policy.decide(
            evaluation=evaluation,
            question=question,
            session=session,
            plan=plan,
        )

    def generate_report(self, session_id: str) -> InterviewReportRecord:
        """生成面试报告"""

        #获取当前session对应的报告
        report = self.repository.get_report_by_session(session_id)

        #没有报告，就创建报告任务记录,状态标记为PENDING
        if report is None:
            report = self.repository.create_report(session_id=session_id)
        #报告状态显示完成，直接返回
        elif report.state is ReportState.COMPLETED:
            return report

        #标记开始生成报告->GENERATING
        self.repository.start_report_generation(report.id)

        try:
            #生成的报告内容
            content = self.report_builder.build(session_id)
            #更新报告状态为完成->COMPLETED
            return self.repository.complete_report(report_id=report.id, content=content)
        except Exception as exc:
            #更新报告状态为失败->FAILED
            self.repository.fail_report(
                report_id=report.id,
                error_message=str(exc) or type(exc).__name__,
            )
            raise


__all__ = ["EvaluationDecisionResult", "InterviewService"]
