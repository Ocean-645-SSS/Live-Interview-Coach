"""把实时语音层的面试事件接到 InterviewService，面试流程的控制层

负责：
1. 告诉 Service，开场白或题目已经播放完毕；
2. 把 STT 确认后的完整回答交给 Service 保存和评价；
3. 根据评价结果，告诉 LiveKit 接下来应该播放追问、下一题还是结束语。

流程：
start():开场白 -> introduction_spoken()：获取第一道题 -> prompt_spoken()：标记题目播放完毕，等待回答
-> receive_final_answer():保存答案、调用评价、决定下一步行动 -> complete():生成报告，标记面试完成
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from liverag.interview.orchestrator import AnswerReceivedCommand, AnswerReceivedResult
from liverag.interview.records import InterviewSessionRecord, generate_id
from liverag.interview.schemas import InterviewState
from liverag.interview.service import EvaluationDecisionResult, InterviewService
from liverag.interview.state_machine import InterviewEventType


class InterviewSpeechKind(str, Enum):
    """告诉语音层当前要播放哪一种内容。"""

    INTRODUCTION = "INTRODUCTION"  # 开场白
    QUESTION = "QUESTION"  # 问题
    FOLLOW_UP = "FOLLOW_UP"  # 追问
    CLOSING = "CLOSING"  # 结束语


@dataclass(frozen=True, slots=True)
class InterviewSpeech:
    """交给 LiveKit 播放的一段文字，以及这段文字属于什么内容。"""

    kind: InterviewSpeechKind
    text: str


@dataclass(frozen=True, slots=True)
class AnswerTurnResult:
    """一份回答处理完成后，返回评价结果和接下来要播放的文字。"""

    evaluation_result: EvaluationDecisionResult
    next_speech: InterviewSpeech


class InterviewAgentController:
    """连接实时语音和面试业务。

    LiveKit worker 收到语音事件后调用这个类；这个类再调用 InterviewService。
    这样 LiveKit 代码不用了解数据库、状态机和评价保存的具体细节。
    """

    def __init__(
        self,
        *,
        service: InterviewService,
        session_id: str,
        attempt_id: str,
    ) -> None:
        self._service = service
        self._session_id = session_id
        self._attempt_id = attempt_id

    def start(self) -> InterviewSpeech:
        """根据数据库状态返回连接后应该播放的第一段话。"""

        session = self._service.repository.get_session(self._session_id)

        # 新会话准备就绪
        if session.state is InterviewState.READY:
            # 更新状态：START
            self._transition(InterviewEventType.START)
            return InterviewSpeech(
                # 播放开场白
                InterviewSpeechKind.INTRODUCTION,
                self._get_plan().introduction,
            )
        # 状态更新为INTRODUCTION，但因断线等原因还未播放开场白
        if session.state is InterviewState.INTRODUCTION:
            return InterviewSpeech(
                InterviewSpeechKind.INTRODUCTION,
                self._get_plan().introduction,
            )
        # 状态是ASKING/LISTENING
        if session.state in {InterviewState.ASKING, InterviewState.LISTENING}:
            return InterviewSpeech(
                # 播放下一个问题
                InterviewSpeechKind.QUESTION,
                self._question_text(session),
            )
        # 状态是FOLLOW_UP
        if session.state is InterviewState.FOLLOW_UP:
            # 播放追问
            return InterviewSpeech(
                InterviewSpeechKind.FOLLOW_UP,
                self._latest_follow_up_question(),
            )
        # 状态是COMPLETING
        if session.state is InterviewState.COMPLETING:
            # 播放结束语
            return InterviewSpeech(
                InterviewSpeechKind.CLOSING,
                self._get_plan().closing_message,
            )
        raise ValueError(f"当前状态不能进入实时面试：{session.state.value}")

    def get_session(self) -> InterviewSessionRecord:
        """返回当前 Session，供 Worker 判断这是首次进入还是断线恢复。"""

        return self._service.repository.get_session(self._session_id)

    def introduction_spoken(self) -> InterviewSpeech:
        """开场白播放完毕后更新状态，并返回第一道题。"""

        # 更新状态：开场白结束
        result = self._transition(InterviewEventType.INTRODUCTION_FINISHED)

        return InterviewSpeech(
            InterviewSpeechKind.QUESTION,
            self._question_text(result.session),  # 第一个问题
        )

    def prompt_spoken(
        self,
        kind: InterviewSpeechKind,
        *,
        answer_started_at: str | None = None,
        answer_deadline_at: str | None = None,
    ) -> InterviewSessionRecord:
        """题目或追问播放完毕后，把 Session 切换到等待回答状态。"""

        # 当前语音层要播放问题
        if kind is InterviewSpeechKind.QUESTION:
            # 事件类型：问好了问题
            event_type = InterviewEventType.QUESTION_ASKED
        # 当前语音层要追问
        elif kind is InterviewSpeechKind.FOLLOW_UP:
            # 事件类型：追问问题
            event_type = InterviewEventType.FOLLOW_UP_ASKED
        # 其他情况
        else:
            raise ValueError("只有题目或追问播放完毕后，才需要等待用户回答")

        # 更新状态
        return self._service.transition(
            session_id=self._session_id,
            event_id=generate_id("event"),
            event_type=event_type,
            payload={
                "attempt_id": self._attempt_id,
                "answer_started_at": answer_started_at,
                "answer_deadline_at": answer_deadline_at,
                "answer_timeout_seconds": self._get_plan().config.answer_timeout_seconds,
            },
        ).session

    def answer_timeout_seconds(self) -> int:
        """Return the frozen plan's effective timeout for this session."""

        return self._get_plan().config.answer_timeout_seconds

    async def receive_final_answer(
        self,
        transcript: str,
        *,
        started_at: str | None = None,
        ended_at: str | None = None,
        answer_disposition: str = "ANSWERED",
    ) -> AnswerTurnResult:
        """Compatibility wrapper: persist first, then perform evaluation."""

        received = self.submit_answer(
            transcript,
            started_at=started_at,
            ended_at=ended_at,
            answer_disposition=answer_disposition,
        )
        return await self.evaluate_submitted_answer(received)

    def submit_answer(
        self,
        transcript: str,
        *,
        started_at: str | None = None,
        ended_at: str | None = None,
        answer_disposition: str = "ANSWERED",
        event_id: str | None = None,
    ) -> AnswerReceivedResult:
        """Atomically persist an answer and move LISTENING to EVALUATING."""

        clean_transcript = transcript.strip()
        if not clean_transcript:
            raise ValueError("最终回答不能为空")
        session = self._service.repository.get_session(self._session_id)
        if session.current_question_id is None:
            raise ValueError("当前 Session 没有正在回答的题目")

        now = datetime.now(timezone.utc).isoformat()
        previous_answers = self._service.repository.list_answers(
            session_id=self._session_id,
            question_id=session.current_question_id,
        )
        return self._service.receive_answer(
            AnswerReceivedCommand(
                session_id=self._session_id,
                attempt_id=self._attempt_id,
                event_id=event_id or generate_id("event"),
                transcript=clean_transcript,
                answer_number=len(previous_answers) + 1,
                started_at=started_at or now,
                ended_at=ended_at or now,
                payload={"answer_disposition": answer_disposition},
            )
        )

    async def evaluate_submitted_answer(
        self,
        received: AnswerReceivedResult,
    ) -> AnswerTurnResult:
        """Evaluate an answer that was already persisted by submit_answer."""

        evaluation_result = await self._service.evaluate_answer(received.answer.id)
        return AnswerTurnResult(
            evaluation_result=evaluation_result,
            next_speech=self._next_speech(evaluation_result),
        )

    def _next_speech(self, result: EvaluationDecisionResult) -> InterviewSpeech:
        """把评价后的 Session 状态转换成 LiveKit 下一句要说的话。"""

        # 需要追问
        if result.session.state is InterviewState.FOLLOW_UP:
            # 追问的问题
            question = result.decision.question_text
            if not question:
                raise ValueError("追问决策缺少要播放的追问内容")
            return InterviewSpeech(InterviewSpeechKind.FOLLOW_UP, question)

        # 需要问下一个问题
        if result.session.state is InterviewState.ASKING:
            # 返回下一个问题
            return InterviewSpeech(
                InterviewSpeechKind.QUESTION,
                self._question_text(result.session),
            )
        # 问题问完了，需要结束
        if result.session.state is InterviewState.COMPLETING:
            return InterviewSpeech(
                InterviewSpeechKind.CLOSING,
                self._get_plan().closing_message,  # 结束语
            )

        raise ValueError(f"评价后出现了无法播放下一句话的状态：{result.session.state.value}")

    def complete(self) -> InterviewSessionRecord:
        """生成最终报告，并把 Session 标记成已完成。"""

        session = self._service.repository.get_session(self._session_id)
        # 当前状态还未结束
        if session.state is not InterviewState.COMPLETING:
            raise ValueError(f"当前状态不能结束面试：{session.state.value}")

        # 生成最终面试报告
        self._service.generate_report(self._session_id)

        # 更新状态：面试报告生成完毕
        return self._transition(InterviewEventType.REPORT_COMPLETED).session

    def _get_plan(self):
        """读取当前 Session 使用的面试计划；没有计划时给出明确错误。"""

        session = self._service.repository.get_session(self._session_id)
        plan = self._service.repository.get_interview_plan(session.interview_id)
        if plan is None:
            raise ValueError("当前 Session 对应的面试计划不存在")
        return plan

    def _question_text(self, session: InterviewSessionRecord) -> str:
        """根据 Session 当前的题目 ID，从计划中找到要播放的题目文字。"""

        question = next(
            (item for item in self._get_plan().questions if item.id == session.current_question_id),
            None,
        )
        if question is None:
            raise ValueError(f"面试计划中找不到当前题目：{session.current_question_id}")
        return question.question_text

    def _latest_follow_up_question(self) -> str:
        """从最近一次追问事件中取回断线前已经生成的追问文字。"""

        events = self._service.repository.list_events(session_id=self._session_id)
        for event in reversed(events):
            if event.event_type != InterviewEventType.FOLLOW_UP_REQUIRED.value:
                continue
            payload = json.loads(event.payload_json)
            question = str(payload.get("follow_up_question") or "").strip()
            if question:
                return question
        raise ValueError("当前 Session 处于追问状态，但找不到追问文字")

    def _transition(self, event_type: InterviewEventType):
        """生成一个新的事件 ID，并让 Service 执行一次状态更新。"""

        return self._service.transition(
            session_id=self._session_id,
            event_id=generate_id("event"),
            event_type=event_type,
            payload={"attempt_id": self._attempt_id},
        )


__all__ = [
    "AnswerTurnResult",
    "InterviewAgentController",
    "InterviewSpeech",
    "InterviewSpeechKind",
]
