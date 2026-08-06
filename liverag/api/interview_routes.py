"""Interview Coach 的 FastAPI 路由与应用服务依赖，相当于Controller层
约定只能调用service"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from liverag.interview.application.evaluator import AnswerEvaluationProviderError
from liverag.interview.application.orchestrator import AnswerReceivedCommand
from liverag.interview.persistence.repository import (
    ConcurrentUpdateError,
    DuplicateEventError,
    RecordNotFoundError,
)
from liverag.interview.schemas import InterviewConfig, InterviewPlan
from liverag.interview.application.service import InterviewService
from liverag.interview.state_machine import InterviewEventType, InterviewTransitionError


class CreateInterviewRequest(BaseModel):
    """创建Interview请求"""
    title: str = Field(min_length=1)
    config: InterviewConfig


class CreatePreparedInterviewRequest(BaseModel):
    """创建一场从题库自动选题、可以立即开始的面试。"""

    title: str = Field(min_length=1)
    config: InterviewConfig
    target_kb_id: str | None = None     #目标知识库
    target_company: str | None = None   #目标公司
    target_role: str | None = None  #目标岗位


class SavePlanRequest(BaseModel):
    """冻结面试计划请求"""
    plan: InterviewPlan
    expected_version: int = Field(ge=1)


class TransitionRequest(BaseModel):
    """状态变更请求"""
    event_id: str = Field(min_length=1)
    event_type: InterviewEventType
    payload: dict[str, Any] = Field(default_factory=dict)


class ReceiveAnswerRequest(BaseModel):
    """接收答案请求"""
    attempt_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    transcript: str = Field(min_length=1)
    answer_number: int = Field(ge=1)
    started_at: str = Field(min_length=1)
    ended_at: str = Field(min_length=1)
    answer_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


_interview_service: InterviewService | None = None


def configure_interview_service(service: InterviewService) -> None:
    """由 FastAPI 组合根在启动时配置正式 InterviewService。"""

    global _interview_service
    _interview_service = service


def get_interview_service() -> InterviewService:
    """返回当前应用配置的 InterviewService，测试可通过依赖覆盖替换。"""

    if _interview_service is None:
        raise RuntimeError("InterviewService 尚未配置")
    return _interview_service


def _execute(operation: Callable[[], Any]) -> Any:
    """将领域和持久化异常转换成稳定的 HTTP 错误。"""

    try:
        return operation()
    #业务资源不存在->404
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    #并发/重复请求->409
    except (ConcurrentUpdateError, DuplicateEventError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    #非法业务操作/参数->422
    except (InterviewTransitionError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


async def _execute_async(operation: Callable[[], Awaitable[Any]]) -> Any:
    """执行异步业务用例，并补充模型 Provider 的 HTTP 错误映射。"""

    try:
        return await operation()
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ConcurrentUpdateError, DuplicateEventError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    #LLM Provider调用失败->502
    except AnswerEvaluationProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except InterviewTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    #超时->503
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


#前缀都以/api/interviews开头，最终在/api/server.py中加入APP
router = APIRouter(prefix="/api/interviews", tags=["interviews"])
#前提：InterviewService已配置
ServiceDependency = Annotated[InterviewService, Depends(get_interview_service)]


@router.post("")
def create_interview(request: CreateInterviewRequest, service: ServiceDependency):
    return _execute(
        lambda: service.create_interview(title=request.title, config=request.config)
    )


@router.post("/prepared")
async def create_prepared_interview(
    request: CreatePreparedInterviewRequest,
    service: ServiceDependency,
):
    """从版本化题库选题，同时创建 Interview、Plan 和 Session。"""

    #合并完整面试配置，同时重新校验参数：岗位名称必填
    config = InterviewConfig.model_validate(
        {
            **request.config.model_dump(),
            "target_kb_id": request.target_kb_id,
            "target_company": request.target_company,
            "target_role": request.target_role,
        }
    )
    return await _execute_async(
        lambda: service.create_prepared_interview(
            title=request.title,
            config=config,
        )
    )


@router.get("/reports")
def list_report_history(target_kb_id: str, service: ServiceDependency):
    """按公司岗位资料库列出历史面试报告。"""

    return _execute(lambda: service.list_report_history(target_kb_id))


@router.get("/{interview_id}")
def get_interview(interview_id: str, service: ServiceDependency):
    return _execute(lambda: service.get_interview(interview_id))


@router.put("/{interview_id}/plan")
def save_plan(
    interview_id: str,
    request: SavePlanRequest,
    service: ServiceDependency,
):
    return _execute(
        lambda: service.save_interview_plan(
            interview_id=interview_id,
            plan=request.plan,
            expected_version=request.expected_version,
        )
    )


@router.post("/{interview_id}/sessions")
def create_session(interview_id: str, service: ServiceDependency):
    return _execute(lambda: service.create_session(interview_id))


@router.get("/sessions/{session_id}")
def get_session(session_id: str, service: ServiceDependency):
    """返回实时面试当前状态，供 Live 页面轮询。"""

    return _execute(lambda: service.get_session(session_id))


@router.post("/sessions/{session_id}/attempts")
def create_attempt(session_id: str, service: ServiceDependency):
    """为前端进入 LiveKit 创建一次新的连接记录。"""

    return _execute(lambda: service.create_attempt(session_id))


@router.get("/attempts/{attempt_id}")
def get_attempt(attempt_id: str, service: ServiceDependency):
    """返回一次 LiveKit 连接是否已连接、断开或失败。"""

    return _execute(lambda: service.get_attempt(attempt_id))


@router.get("/sessions/{session_id}/events")
def list_events(session_id: str, service: ServiceDependency):
    """返回面试状态变化记录。"""

    return _execute(lambda: service.list_events(session_id))


@router.get("/sessions/{session_id}/answers")
def list_answers(session_id: str, service: ServiceDependency):
    """返回已经提交的最终回答。"""

    return _execute(lambda: service.list_answers(session_id))


@router.post("/sessions/{session_id}/events")
def transition(
    session_id: str,
    request: TransitionRequest,
    service: ServiceDependency,
):
    return _execute(
        lambda: service.transition(
            session_id=session_id,
            event_id=request.event_id,
            event_type=request.event_type,
            payload=request.payload,
        )
    )


@router.post("/sessions/{session_id}/answers")
def receive_answer(
    session_id: str,
    request: ReceiveAnswerRequest,
    service: ServiceDependency,
):
    return _execute(
        lambda: service.receive_answer(
            AnswerReceivedCommand(
                session_id=session_id,
                attempt_id=request.attempt_id,
                event_id=request.event_id,
                transcript=request.transcript,
                answer_number=request.answer_number,
                started_at=request.started_at,
                ended_at=request.ended_at,
                answer_id=request.answer_id,
                payload=request.payload,
            )
        )
    )


@router.post("/answers/{answer_id}/evaluation")
async def evaluate_answer(
    answer_id: str,
    service: ServiceDependency,
):
    """异步生成答案"""

    return await _execute_async(lambda: service.evaluate_answer(answer_id))


@router.post("/sessions/{session_id}/report")
def generate_report(session_id: str, service: ServiceDependency):
    return _execute(lambda: service.generate_report(session_id))


@router.get("/sessions/{session_id}/report")
def get_report(session_id: str, service: ServiceDependency):
    """返回已经生成的报告；尚未生成时返回 null。"""

    return _execute(lambda: service.get_report(session_id))


__all__ = [
    "CreateInterviewRequest",
    "CreatePreparedInterviewRequest",
    "ReceiveAnswerRequest",
    "SavePlanRequest",
    "TransitionRequest",
    "configure_interview_service",
    "get_interview_service",
    "router",
]
