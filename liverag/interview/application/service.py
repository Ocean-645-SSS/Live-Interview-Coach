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

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from liverag.interview.intelligence.service import IntelligenceService

from liverag.interview.application.evaluator import AnswerEvaluator
from liverag.interview.application.orchestrator import (
    AnswerReceivedCommand,
    AnswerReceivedResult,
    InterviewOrchestrator,
    InterviewTransitionResult,
)
from liverag.interview.application.planner import InterviewPlanner
from liverag.interview.application.profile_service import InterviewProfileService
from liverag.interview.application.report import InterviewReportBuilder
from liverag.interview.application.skill_progress_reconciliation import (
    reconcile_skill_progress,
)
from liverag.interview.follow_up import FollowUpDecision, FollowUpPolicy
from liverag.interview.persistence.repository import InterviewRepository, RecordNotFoundError
from liverag.interview.question_bank.catalog import QuestionBank
from liverag.interview.records import (
    InterviewAnswerRecord,
    InterviewAttemptRecord,
    InterviewEventRecord,
    InterviewRecord,
    InterviewReportRecord,
    InterviewSessionRecord,
    ReportState,
    generate_id,
)
from liverag.interview.schemas import (
    AnswerEvaluation,
    InterviewConfig,
    InterviewPlan,
    InterviewState,
    SkillProgress,
    StrictModel,
)
from liverag.interview.skill_progress.service import (
    SkillProgressDashboard,
    SkillProgressService,
)
from liverag.interview.state_machine import InterviewEventType

logger = logging.getLogger("liverag.interview.application.service")


@dataclass(frozen=True, slots=True)
class EvaluationDecisionResult:
    """回答评价、决策及自动状态推进后的权威结果。"""

    evaluation: AnswerEvaluation
    decision: FollowUpDecision
    session: InterviewSessionRecord
    transitions: tuple[InterviewTransitionResult, ...]


@dataclass(frozen=True, slots=True)
class PreparedInterviewResult:
    """创建页面一次提交后得到的 Interview、计划和 Session。"""

    interview: InterviewRecord
    plan: InterviewPlan
    session: InterviewSessionRecord


@dataclass(frozen=True, slots=True)
class InterviewReportHistoryItem:
    """目标岗位资料页展示的一次历史面试报告。"""

    interview_id: str
    interview_title: str
    session_id: str
    session_state: str
    report_state: str
    target_kb_id: str
    target_company: str | None
    target_role: str | None
    completed_at: str | None
    updated_at: str


class SkillTrendPoint(StrictModel):
    """一条可追溯到持久化 AnswerEvaluation 的技能趋势数据。"""

    evaluation_id: str
    session_id: str
    interview_id: str
    question_id: str
    score: float
    rubric_version: int
    evaluated_at: datetime


class SkillProgressDetail(SkillProgress):
    """单项技能画像及其完整评价趋势。"""

    trend: list[SkillTrendPoint]


class InterviewService:
    """提供面试管理、实时状态编排、评价决策和报告生成用例。"""

    def __init__(
        self,
        repository: InterviewRepository,
        evaluator: AnswerEvaluator | None = None,
        question_bank: QuestionBank | None = None,
        profile_service: InterviewProfileService | None = None,
        skill_progress_service: SkillProgressService | None = None,
        intelligence_service: IntelligenceService | None = None,
    ):
        self.repository = repository
        self.evaluator = evaluator
        self.orchestrator = InterviewOrchestrator(repository)
        self.follow_up_policy = FollowUpPolicy()
        self.report_builder = InterviewReportBuilder(repository)
        self.question_bank = question_bank
        self.profile_service = profile_service
        self.skill_progress_service = skill_progress_service
        self.intelligence_service = intelligence_service

    def create_interview(self, *, title: str, config: InterviewConfig) -> InterviewRecord:
        return self.repository.create_interview(title=title, config=config)

    async def create_prepared_interview(
        self,
        *,
        title: str,
        config: InterviewConfig,
    ) -> PreparedInterviewResult:
        """从题库选题，创建已经可以直接进入 Live 页面的面试和 Session。"""

        #拿到公共题库
        question_bank = self.question_bank or QuestionBank.from_file(
            Path(__file__).parents[1]
            / "question_bank"
            / "data"
            / "question_bank.v1.json"
        )

        #用户画像+岗位画像
        candidate_profile = None
        job_profile = None
        candidate_record = self.repository.ensure_candidate_profile(
            kb_id=config.candidate_kb_id
        )

        #开始生成画像
        if self.profile_service is not None:
            if config.target_kb_id and config.target_role:
                #个人简历和岗位 JD 互不依赖，同时检索可以缩短等待时间。
                candidate_profile, job_profile = await asyncio.gather(
                    self.profile_service.build_candidate_profile(
                        config.candidate_kb_id
                    ),
                    self.profile_service.build_job_profile(
                        kb_id=config.target_kb_id,
                        company=config.target_company,
                        role=config.target_role,
                    ),
                )
            else:
                candidate_profile = (
                    await self.profile_service.build_candidate_profile(
                        config.candidate_kb_id
                    )
                )

        #候选人画像不为空
        if candidate_profile is not None:
            #把本次生成的candidate profile快照保存到数据库
            candidate_record = self.repository.update_candidate_profile_snapshot(
                candidate_profile_id=candidate_record.id,
                profile=candidate_profile,
            )
        #列出候选人所有长期能力画像
        skill_progress = (
            self.skill_progress_service.list_progress(candidate_record.id)
            if self.skill_progress_service is not None
            else []
        )

        # 公司面经情报（可降级：没有情报不影响面试创建）
        company_intel = None
        if self.intelligence_service is not None and config.target_company:
            from liverag.interview.intelligence.provider import (
                InterviewIntelligenceQuery,
            )

            query = InterviewIntelligenceQuery(
                company=config.target_company,
                role=config.target_role or "",
            )
            try:
                enrichment = await self.intelligence_service.get_company_profile(query)
                company_intel = enrichment.profile
            except Exception:
                logger.exception(
                    "interview.intelligence.fetch_failed",
                    extra={
                        "company": config.target_company,
                        "role": config.target_role,
                    },
                )

        #生成面试顶层计划
        plan = await InterviewPlanner(question_bank).build(
            title=title,
            config=config,
            candidate_profile=candidate_profile,
            job_profile=job_profile,
            company_intel=company_intel,
            candidate_profile_id=candidate_record.id,
            skill_progress=skill_progress,
        )

        #创建interview
        created = self.create_interview(title=title, config=config)
        #冻结面试计划
        interview = self.save_interview_plan(
            interview_id=created.id,
            plan=plan,
            expected_version=created.version,
        )
        #创建当前面试session
        session = self.create_session(interview.id)

        # 返回 interview、plan 和 session
        return PreparedInterviewResult(
            interview=interview,
            plan=plan,
            session=session,
        )

    def get_interview(self, interview_id: str) -> InterviewRecord:
        return self.repository.get_interview(interview_id)

    def update_interview_state(
        self,
        *,
        interview_id: str,
        state: InterviewState,
        expected_version: int,
    ) -> InterviewRecord:
        """更新 Interview 顶层状态（如 CREATED → PREPARING）。"""
        return self.repository.update_interview_state(
            interview_id=interview_id,
            state=state,
            expected_version=expected_version,
        )

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

    def get_session(self, session_id: str) -> InterviewSessionRecord:
        """读取一场实时面试当前进行到哪里。"""

        return self.repository.get_session(session_id)

    def create_attempt(self, session_id: str) -> InterviewAttemptRecord:
        """为一次进入 LiveKit 房间创建连接记录和唯一房间名。"""

        self.repository.get_session(session_id)
        attempt_id = generate_id("attempt")
        room_name = f"interview-{session_id[-16:]}-{attempt_id[-16:]}"
        return self.repository.create_attempt(
            session_id=session_id,
            room_name=room_name,
            attempt_id=attempt_id,
        )

    def get_attempt(self, attempt_id: str) -> InterviewAttemptRecord:
        """读取一次 LiveKit 连接的当前状态。"""

        return self.repository.get_attempt(attempt_id)

    def list_events(self, session_id: str) -> list[InterviewEventRecord]:
        """按发生顺序返回 Session 的状态变化记录。"""

        return self.repository.list_events(session_id=session_id)

    def list_answers(self, session_id: str) -> list[InterviewAnswerRecord]:
        """返回 Session 已经收到的所有最终回答。"""

        return self.repository.list_answers(session_id=session_id)

    def get_report(self, session_id: str) -> InterviewReportRecord | None:
        """读取 Session 的报告；尚未生成报告时返回 None。"""

        self.repository.get_session(session_id)
        return self.repository.get_report_by_session(session_id)

    def list_report_history(self, target_kb_id: str) -> list[InterviewReportHistoryItem]:
        """列出某个公司岗位资料库关联的全部面试报告。"""

        clean_kb_id = target_kb_id.strip()
        if not clean_kb_id:
            raise ValueError("目标岗位知识库 ID 不能为空")

        history: list[InterviewReportHistoryItem] = []
        #遍历所有面试
        for interview in self.repository.list_interviews(limit=200):
            #获取面试配置
            config = self.repository.get_interview_config(interview.id)
            if config.target_kb_id != clean_kb_id:
                continue

            #遍历当前面试对应的所有session
            for session in self.repository.list_sessions(
                interview_id=interview.id,
                limit=100,
            ):
                #得到当前session对应的报告
                report = self.repository.get_report_by_session(session.id)
                if report is None:
                    continue

                #加入历史
                history.append(
                    InterviewReportHistoryItem(
                        interview_id=interview.id,
                        interview_title=interview.title,
                        session_id=session.id,
                        session_state=session.state.value,
                        report_state=report.state.value,
                        target_kb_id=clean_kb_id,
                        target_company=config.target_company,
                        target_role=config.target_role,
                        completed_at=report.completed_at,
                        updated_at=report.updated_at,
                    )
                )
        return sorted(history, key=lambda item: item.updated_at, reverse=True)

    def get_skill_progress_dashboard(
        self, candidate_kb_id: str
    ) -> SkillProgressDashboard:
        """读取资料库对应候选人的长期能力画像总览。"""

        if self.skill_progress_service is None or self.question_bank is None:
            raise RuntimeError("InterviewService 尚未配置长期能力画像查询")

        return self.skill_progress_service.get_dashboard_for_kb(
            candidate_kb_id, self.question_bank
        )

    def get_skill_progress_detail(
        self, *, candidate_kb_id: str, skill_key: str
    ) -> SkillProgressDetail:
        """读取单项技能画像及其 AnswerEvaluation 来源趋势。"""

        if self.skill_progress_service is None:
            raise RuntimeError("InterviewService 尚未配置长期能力画像查询")

        #获取CandidateProfileRecord
        candidate = self.repository.ensure_candidate_profile(kb_id=candidate_kb_id)
        #列出skill progress
        progress = next(
            (
                item
                for item in self.skill_progress_service.list_progress(candidate.id)
                if item.skill_key == skill_key
            ),
            None,
        )
        if progress is None:
            raise RecordNotFoundError(
                f"候选人 {candidate.id} 不存在技能画像：{skill_key}"
            )

        #列出skill evidences
        evidence = self.repository.list_skill_evidence(
            candidate_profile_id=candidate.id,
            skill_key=skill_key,
        )
        return SkillProgressDetail(
            **progress.model_dump(),
            trend=[
                SkillTrendPoint(
                    evaluation_id=item.evaluation_id,
                    session_id=item.session_id,
                    interview_id=item.interview_id,
                    question_id=item.question_id,
                    score=item.score,
                    rubric_version=item.rubric_version,
                    evaluated_at=item.evaluated_at,
                )
                for item in evidence
            ],
        )

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
            #幂等设计：已有评价，直接复用评价
            evaluation = self.repository.get_evaluation(answer_id)
        except RecordNotFoundError:
            #评价还未生成
            #没配置AnswerEvaluator
            if self.evaluator is None:
                raise RuntimeError("InterviewService 尚未配置 AnswerEvaluator") from None
            #生成评价
            evaluation = await self.evaluator.evaluate(answer_id)

        if self.skill_progress_service is not None:
            try:
                #把这次已经持久化的回答评价，增量应用到候选人的长期能力画像中
                self.skill_progress_service.apply_evaluation(answer_id)
            except Exception:
                logger.exception(
                    "interview.skill_progress.apply_failed",
                    extra={"answer_id": answer_id},
                )

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

    def recover_failed_evaluation(
        self,
        answer_id: str,
        *,
        reason: str,
    ) -> InterviewSessionRecord:
        """在评价不可用时释放停留在 EVALUATING 的会话。

        回答已经持久化，但不会伪造一份模型评价；恢复流程直接跳到下一题，
        最后一题则进入正常的收尾流程。确定性事件 ID 让重连或重试安全复用。
        """

        answer = self.repository.get_answer(answer_id)
        session = self.repository.get_session(answer.session_id)
        if session.state is not InterviewState.EVALUATING:
            return session

        plan = self.repository.get_interview_plan(session.interview_id)
        if plan is None:
            raise ValueError("回答对应的面试计划不存在")

        #不是最后一题
        if session.current_question_index + 1 < len(plan.questions):
            event_types = (
                InterviewEventType.NEXT_QUESTION,
                InterviewEventType.QUESTION_ADVANCED,
            )
        #是最后一题
        else:
            event_types = (InterviewEventType.FINISH,)

        for event_type in event_types:
            event_id = self._decision_event_id(answer.id, event_type)
            if self.repository.event_exists(event_id):
                continue
            self.transition(
                session_id=answer.session_id,
                event_id=event_id,
                event_type=event_type,
                payload={
                    "answer_id": answer.id,
                    "evaluation_recovery_reason": reason,
                },
            )

        return self.repository.get_session(answer.session_id)

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
        """生成面试报告。

        并发保护：
        - DB 层：start_report_generation() 条件更新（仅 PENDING/FAILED → GENERATING）
        - Redis 层：由调用方（API / Worker）在调用前获取 lock:report_generation:{session_id}
        - 本方法同时处理 DB 条件更新失败的情况（另一进程已抢先开始生成）
        """

        #获取当前session对应的报告
        report = self.repository.get_report_by_session(session_id)

        #没有报告，就创建报告任务记录,状态标记为PENDING
        if report is None:
            report = self.repository.create_report(session_id=session_id)
        #报告状态显示完成，直接返回
        elif report.state is ReportState.COMPLETED:
            reconcile_skill_progress(
                self.repository, self.skill_progress_service, session_id
            )
            return report

        #标记开始生成报告->GENERATING（防止并发生成）
        try:
            self.repository.start_report_generation(report.id)
        except ValueError:
            # 另一进程已抢先开始生成 → 等待其结果
            import time
            for _ in range(30):  # 最多等 30 秒
                time.sleep(1.0)
                refreshed = self.repository.get_report_by_session(session_id)
                if refreshed is not None and refreshed.state is ReportState.COMPLETED:
                    #报告完成后，从持久化评价重建候选人的长期技能画像
                    reconcile_skill_progress(
                        self.repository, self.skill_progress_service, session_id
                    )
                    return refreshed
            raise RuntimeError(
                f"等待报告生成超时：session {session_id} 的报告可能卡在 GENERATING 状态"
            ) from None

        try:
            #生成的报告内容
            content = self.report_builder.build(session_id)
            #更新报告状态为完成->COMPLETED
            completed = self.repository.complete_report(report_id=report.id, content=content)
        except Exception as exc:
            #更新报告状态为失败->FAILED
            self.repository.fail_report(
                report_id=report.id,
                error_message=str(exc) or type(exc).__name__,
            )
            raise

        #报告完成后，从持久化评价重建候选人的长期技能画像
        reconcile_skill_progress(
            self.repository, self.skill_progress_service, session_id
        )

        return completed


__all__ = [
    "EvaluationDecisionResult",
    "InterviewService",
    "PreparedInterviewResult",
    "SkillProgressDetail",
    "SkillTrendPoint",
]
