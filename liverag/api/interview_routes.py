"""Interview Coach 的 FastAPI 路由与应用服务依赖，相当于Controller层
约定只能调用service"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from liverag.interview.application.evaluator import AnswerEvaluationProviderError
from liverag.interview.application.orchestrator import AnswerReceivedCommand
from liverag.interview.jobs.queue import RedisQueue
from liverag.interview.jobs.repository import JobRepository
from liverag.interview.persistence.repository import (
    ConcurrentUpdateError,
    DuplicateEventError,
    RecordNotFoundError,
)
from liverag.interview.records import JobStatus
from liverag.interview.schemas import InterviewConfig, InterviewPlan, PreparationStage
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
_job_repository: JobRepository | None = None
_redis_queue: RedisQueue | None = None


def configure_interview_service(service: InterviewService) -> None:
    """由 FastAPI 组合根在启动时配置正式 InterviewService。"""

    global _interview_service
    _interview_service = service


def configure_job_dependencies(
    job_repo: JobRepository,
    redis_queue: RedisQueue,
) -> None:
    """由 FastAPI 组合根在启动时注入 JobRepository 和 RedisQueue。"""
    global _job_repository, _redis_queue
    _job_repository = job_repo
    _redis_queue = redis_queue


def get_interview_service() -> InterviewService:
    """返回当前应用配置的 InterviewService，测试可通过依赖覆盖替换。"""

    if _interview_service is None:
        raise RuntimeError("InterviewService 尚未配置")
    return _interview_service


def get_job_repository() -> JobRepository:
    """返回当前应用配置的 JobRepository。"""
    if _job_repository is None:
        raise RuntimeError("JobRepository 尚未配置")
    return _job_repository


def get_redis_queue() -> RedisQueue:
    """返回当前应用配置的 RedisQueue。"""
    if _redis_queue is None:
        raise RuntimeError("RedisQueue 尚未配置")
    return _redis_queue


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


# =========================== Background Job API ==============================
JobRepoDependency = Annotated[JobRepository, Depends(get_job_repository)]
RedisQueueDependency = Annotated[RedisQueue, Depends(get_redis_queue)]

class CreateDemoJobRequest(BaseModel):
    """测试用 Demo Job 请求。"""
    delay_seconds: float = Field(default=3.0, ge=0.5, le=60.0)


@router.post("/jobs/demo")
async def create_demo_job(
    request: CreateDemoJobRequest,
    job_repo: JobRepoDependency,
    redis_queue: RedisQueueDependency,
):
    """创建一个 Demo 后台任务，验证异步链路。"""

    return await _execute_async(
        lambda: _create_and_enqueue_demo(job_repo, redis_queue, request.delay_seconds)
    )


async def _create_and_enqueue_demo(
    job_repo: JobRepository,
    redis_queue: RedisQueue,
    delay_seconds: float,   #延迟时间
) -> dict[str, Any]:
    """创建后台任务+后台任务异步入队"""

    import uuid
    resource_id = uuid.uuid4().hex[:12]

    #创建后台任务
    job = job_repo.create_job(
        job_type="demo",    #测试
        idempotency_key=f"demo_{resource_id}_{uuid.uuid4().hex[:8]}",   #幂等键
        business_resource_id=resource_id,   #业务资源id
        payload={"delay_seconds": delay_seconds},   
    )
    #后台任务入队
    await redis_queue.enqueue(job_type="demo", job_id=job.id)
    
    return {"job_id": job.id, "status": job.status.value}


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str, job_repo: JobRepoDependency):
    """查询后台任务状态和结果。"""

    return _execute(lambda: _read_job_status(job_repo, job_id))


def _read_job_status(job_repo: JobRepository, job_id: str) -> dict[str, Any]:
    """读取后台任务状态和结果"""

    #获取job
    job = job_repo.get_job(job_id)

    import json
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status.value,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "result": json.loads(job.result_json) if job.result_json else None,
        "error": job.error_message,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }


# ====================== Preparation API ==============================

class PrepareInterviewResponse(BaseModel):
    """异步准备面试的响应。"""
    job_id: str = Field(min_length=1)
    status: str = Field(min_length=1)


class PreparationStatusResponse(BaseModel):
    """面试准备进度查询的响应。"""
    job_id: str | None = None
    status: str = "PENDING"
    stage: str | None = None
    completed_steps: list[str] = Field(default_factory=list)
    degraded: bool = False
    degradation_reasons: list[str] = Field(default_factory=list)
    started_at: str | None = None
    updated_at: str | None = None
    error: str | None = None


@router.post("/{interview_id}/prepare")
async def prepare_interview_async(
    interview_id: str,
    service: ServiceDependency,
    job_repo: JobRepoDependency,
    redis_queue: RedisQueueDependency,
    async_mode: bool = Query(default=False, alias="async"),
):
    """触发面试异步准备。

    当 async_mode=true 时：
    - 创建 interview_preparation Job 并入队
    - 立即返回 job_id 供前端轮询
    - Worker 在后台按 stage 执行准备流程

    当 async_mode=false（默认）时：
    - 保持现有同步行为（复用 create_prepared_interview 逻辑）
    """

    #同步执行，报错
    if not async_mode:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="async=false 暂未实现——请使用 POST /api/interviews/prepared 同步创建已准备的面试",
        )

    return await _execute_async(
        lambda: _create_preparation_job(service, job_repo, redis_queue, interview_id)
    )


async def _create_preparation_job(
    service: InterviewService,
    job_repo: JobRepository,
    redis_queue: RedisQueue,
    interview_id: str,
) -> dict[str, Any]:
    """创建 interview_preparation Job 并入队。"""

    #验证 interview 存在
    interview = service.get_interview(interview_id)

    #解析 interview config 获取准备参数
    config = InterviewConfig.model_validate_json(interview.config_json)

    #构建幂等键（interview_id 本身已唯一，配置变更由业务层判断）
    idempotency_key = f"interview_preparation:{interview_id}"

    #幂等检查：已有 COMPLETED Job → 直接返回
    existing = job_repo.find_by_idempotency(
        job_type="interview_preparation",
        idempotency_key=idempotency_key,
    )
    if existing is not None:
        if existing.status is JobStatus.COMPLETED:
            return {"job_id": existing.id, "status": existing.status.value}
        if existing.status in (JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING):
            return {"job_id": existing.id, "status": existing.status.value}

    #构建 payload
    payload = {
        "interview_id": interview_id,
        "config_json": interview.config_json,
        "target_kb_id": config.target_kb_id,
        "target_company": config.target_company,
        "target_role": config.target_role,
        "candidate_kb_id": config.candidate_kb_id,
        "current_stage": PreparationStage.PENDING.value,
        "completed_steps": [],
        "degraded": False,
        "degradation_reasons": [],
        "stage_results": {},
    }

    #创建 Job
    job = job_repo.create_job(
        job_type="interview_preparation",
        idempotency_key=idempotency_key,
        business_resource_id=interview_id,
        payload=payload,
        max_attempts=3,
    )

    #入队 Redis
    await redis_queue.enqueue(job_type="interview_preparation", job_id=job.id)

    #更新 Interview.state: CREATED → PREPARING（如果有对应方法）
    # 注：3.2-A 骨架阶段暂不修改 Interview.state，后续 3.2-D 中实现

    return {"job_id": job.id, "status": job.status.value}


@router.get("/{interview_id}/preparation")
def get_preparation_status(
    interview_id: str,
    job_repo: JobRepoDependency,
):
    """查询面试准备的当前进度。
    返回 Preparation Workflow 的阶段、已完成步骤、降级信息等。
    前端通过此端点轮询准备进度。
    """
    return _execute(lambda: _read_preparation_status(job_repo, interview_id))


def _read_preparation_status(
    job_repo: JobRepository,
    interview_id: str,
) -> PreparationStatusResponse:
    """从 interview_preparation Job 中读取准备状态。"""

    # 查找该 interview 最近的 preparation Job
    job = job_repo.get_job_by_resource(
        job_type="interview_preparation",
        business_resource_id=interview_id,
    )

    # 未找到任何 preparation Job → 未开始
    if job is None:
        return PreparationStatusResponse(status="NOT_STARTED")

    # 从 payload_json 读取 stage 追踪信息
    payload = json.loads(job.payload_json) if job.payload_json else {}

    return PreparationStatusResponse(
        job_id=job.id,
        status=job.status.value,
        stage=payload.get("current_stage"),
        completed_steps=payload.get("completed_steps", []),
        degraded=payload.get("degraded", False),
        degradation_reasons=payload.get("degradation_reasons", []),
        started_at=job.started_at,
        updated_at=job.updated_at,
        error=job.error_message,
    )


__all__ = [
    "CreateDemoJobRequest",
    "CreateInterviewRequest",
    "CreatePreparedInterviewRequest",
    "PreparationStatusResponse",
    "PrepareInterviewResponse",
    "ReceiveAnswerRequest",
    "SavePlanRequest",
    "TransitionRequest",
    "configure_interview_service",
    "configure_job_dependencies",
    "get_interview_service",
    "get_job_repository",
    "get_redis_queue",
    "router",
]
