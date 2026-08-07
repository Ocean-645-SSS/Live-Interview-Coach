"""job_type → 异步执行函数的注册表。

每个 handler 接收 BackgroundJobRecord + JobRepository
返回 dict[str, Any] 作为结果写入 job.result_json。

外部依赖由 BackgroundWorker 以显式关键字参数注入（job_repo、profile_source、llm_client 等）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from openai import AsyncOpenAI

from liverag.interview.jobs.repository import BackgroundJobRecord
from liverag.interview.application.profile_service import KnowledgeContextSource
from liverag.interview.question_bank.catalog import QuestionBank
from liverag.interview.jobs.repository import JobRepository
from liverag.interview.application.resume_parser import ResumeParser
from liverag.interview.application.profile_service import InterviewProfileService
from liverag.interview.application.planner import InterviewPlanner
from liverag.interview.schemas import CandidateFacts, InterviewConfig

logger = logging.getLogger("liverag.interview.jobs.tasks")

# job_type → async handler
_TASK_REGISTRY: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {}


def register(job_type: str):
    """装饰器：将函数注册为指定 job_type 的处理器。"""

    def decorator(
        handler: Callable[..., Awaitable[dict[str, Any]]],
    ) -> Callable[..., Awaitable[dict[str, Any]]]:
        _TASK_REGISTRY[job_type] = handler
        logger.info("任务处理器已注册", extra={"job_type": job_type})
        return handler

    return decorator


def get_handler(
    job_type: str,
) -> Callable[..., Awaitable[dict[str, Any]]] | None:
    """返回已注册的任务处理器，未注册时返回 None。"""
    return _TASK_REGISTRY.get(job_type)


def registered_types() -> list[str]:
    """返回所有已注册的 job_type。"""
    return sorted(_TASK_REGISTRY.keys())


# ====================== Demo 任务（验证链路用）=============================
@register("demo")
async def demo_task(
    job: BackgroundJobRecord,
    **kwargs: Any,
) -> dict[str, Any]:
    """演示任务：sleep 后返回成功消息。"""

    payload = json.loads(job.payload_json) if job.payload_json else {}
    delay = float(payload.get("delay_seconds", 3.0))
    await asyncio.sleep(delay)
    return {
        "message": "hello async",
        "job_id": job.id,
        "slept_seconds": delay,
    }


# ========================= 简历事实抽取任务 =============================
@register("resume_parse")
async def resume_parse_task(
    job: BackgroundJobRecord,
    *,
    profile_source: KnowledgeContextSource,
    llm_client: AsyncOpenAI,
    llm_model: str,
    **kwargs: Any,
) -> dict[str,Any]:
    """简历事实抽取任务：RAG 检索 → LLM 结构化事实抽取 → CandidateFacts。

    输入（payload）：
        - kb_id: 知识库 ID（默认 "default"）
        - document_ids: 可选，指定文档 ID 列表
    输出：
        CandidateFacts.model_dump()
    幂等键：resume_parse:{kb_id}:{documents_snapshot_hash}
    """

    payload = json.loads(job.payload_json) if job.payload_json else {}
    kb_id = payload.get("kb_id", "default")
    document_ids: list[str] = payload.get("document_ids", [])

    parser = ResumeParser(
        profile_source=profile_source,
        llm_client=llm_client,
        llm_model=llm_model,
    )

    facts = await parser.parse(
        kb_id=kb_id,
        document_ids=document_ids,
        job_id=job.id,
    )

    return facts.model_dump(mode="json")


# ========================= 画像生成任务 =============================

async def _generate_candidate_profile(
    *,
    job: BackgroundJobRecord,
    service: InterviewProfileService,
    job_repo: JobRepository,
    kb_id: str,
    candidate_facts_job_id: str | None,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """生成候选人画像"""

    candidate_facts: CandidateFacts | None = None

    if candidate_facts_job_id:
        #查询当前job
        facts_job = job_repo.get_job(candidate_facts_job_id)
        #job完成的结构化输出
        facts_data = json.loads(facts_job.result_json) if facts_job.result_json else None
        if not facts_data:
            raise ValueError(
                f"candidate_facts_job_id={candidate_facts_job_id} 的 result_json 为空，无法加载 CandidateFacts"
            )
        #候选人事实
        candidate_facts = CandidateFacts.model_validate(facts_data)
        logger.info(
            "已加载 CandidateFacts",
            extra={"job_id": job.id, "facts_job_id": candidate_facts_job_id},
        )

    #建立候选人画像
    profile = await service.build_candidate_profile(
        kb_id,
        candidate_facts=candidate_facts,
    )
    logger.info(
        "候选人画像生成完成",
        extra={
            "job_id": job.id,
            "kb_id": kb_id,
            "skills_count": len(profile.skills),
            "projects_count": len(profile.projects),
            "experience_level": profile.experience_level,
        },
    )
    return profile.model_dump()


async def _generate_job_profile(
    *,
    job: BackgroundJobRecord,
    service: "InterviewProfileService",
    kb_id: str,
    company: str | None,
    role: str | None,
) -> dict[str, Any]:
    """生成岗位画像。role 为必填项。"""

    if not role:
        raise ValueError("job_profile 类型必须提供 role 字段")

    #建立岗位画像
    profile = await service.build_job_profile(
        kb_id=kb_id, company=company, role=role
    )
    logger.info(
        "岗位画像生成完成",
        extra={
            "job_id": job.id,
            "kb_id": kb_id,
            "company": company,
            "role": role,
            "skills_count": len(profile.required_skills),
        },
    )
    return profile.model_dump()


@register("profile_generation")
async def profile_generation_task(
    job: BackgroundJobRecord,
    *,
    profile_source: KnowledgeContextSource,
    job_repo: JobRepository,
    question_bank: QuestionBank,
    **kwargs: Any,
) -> dict[str, Any]:
    """画像生成任务：根据 profile_type 分流到候选人画像或岗位画像。

    输入（payload）：
        - profile_type: "candidate_profile" | "job_profile"（必填）
        - kb_id: 知识库 ID
        - candidate_facts_job_id: 可选，resume_parse Job ID（仅 candidate_profile）
        - company: 可选，目标公司名（仅 job_profile）
        - role: 目标岗位名（仅 job_profile，必填）
    输出：
        CandidateProfile 或 JobProfile 的 model_dump()
    """

    payload = json.loads(job.payload_json) if job.payload_json else {}
    #区分candidate_profile / job_profile
    profile_type = payload.get("profile_type")
    kb_id = payload.get("kb_id", "default")
    #获取题库的一级标签和主题
    labels = [*question_bank.list_categories(), *question_bank.list_topics()]
    service = InterviewProfileService(profile_source, labels)

    if profile_type == "candidate_profile":
        return await _generate_candidate_profile(
            job=job,
            service=service,
            job_repo=job_repo,
            kb_id=kb_id,
            candidate_facts_job_id=payload.get("candidate_facts_job_id"),
            kwargs=kwargs,
        )

    if profile_type == "job_profile":
        return await _generate_job_profile(
            job=job,
            service=service,
            kb_id=kb_id,
            company=payload.get("company"),
            role=payload.get("role"),
        )

    raise ValueError(f"不支持的画像类型：{profile_type}")


# ====================== Preparation Workflow ============================

# (stage_name, step_name) — 前者给前端展示，后者给后端标识
_PREPARATION_STAGES: list[tuple[str, str]] = [
    ("RESUME_PARSING", "RESUME_PARSE"),
    ("CANDIDATE_PROFILE_GENERATION", "CANDIDATE_PROFILE"),
    ("JOB_PROFILE_GENERATION", "JOB_PROFILE"),
    ("COMPANY_INTELLIGENCE", "COMPANY_INTELLIGENCE"),
    ("PLAN_GENERATION", "PLAN_GENERATION"),
]


async def _update_stage_payload(
    job_repo: JobRepository,
    job_id: str,
    payload: dict[str, Any],
    stage_name: str,
) -> None:
    """更新 Job payload 中的当前 stage 并持久化。"""

    payload["current_stage"] = stage_name
    job_repo.update_payload(job_id, payload)


def _persist_stage_error(
    job_repo: JobRepository,
    job_id: str,
    payload: dict[str, Any],
    stage_name: str,
    step_name: str,
    error_message: str,
    error_type: str,
) -> None:
    """将 stage 失败信息持久化到 payload，供前端展示和故障排查。"""

    payload["last_error"] = {
        "stage": stage_name,
        "step": step_name,
        "error": error_message,
        "error_type": error_type,
    }
    job_repo.update_payload(job_id, payload)


@register("interview_preparation")
async def interview_preparation_task(
    job: BackgroundJobRecord,
    *,
    job_repo: JobRepository,
    **kwargs: Any,
) -> dict[str, Any]:
    """面试准备 Workflow：直接复用 ResumeParser、ProfileService、Planner 等 Service。

    completed_steps / current_stage / stage_results 用于幂等恢复和前端展示。
    只有 COMPANY_INTELLIGENCE 允许降级缺失。
    stage_results 只保存 ID 和摘要，不保存完整领域对象以控制 payload 大小。
    Worker 重启后无法从 stage_results 恢复领域对象，会重新执行必要的上游 stage。
    """

    # ── 解析 payload ──
    payload = json.loads(job.payload_json) if job.payload_json else {}
    interview_id = payload.get("interview_id", "")
    completed_steps: list[str] = payload.get("completed_steps", [])
    degraded: bool = payload.get("degraded", False)
    degradation_reasons: list[str] = payload.get("degradation_reasons", [])
    stage_results: dict[str, Any] = payload.get("stage_results", {})

    candidate_kb_id: str = payload.get("candidate_kb_id", "default")
    target_kb_id: str | None = payload.get("target_kb_id")
    target_role: str | None = payload.get("target_role")
    target_company: str | None = payload.get("target_company")

    # 解析 InterviewConfig（供 plan_generation 使用）
    config_json = payload.get("config_json", "{}")
    interview_config = InterviewConfig.model_validate_json(config_json)

    # ── 构建 Service（直接从 worker 注入的 deps 获取依赖）──
    profile_source = kwargs["profile_source"]
    llm_client = kwargs["llm_client"]
    llm_model = kwargs["llm_model"]
    question_bank = kwargs["question_bank"]

    resume_parser = ResumeParser(
        profile_source=profile_source,
        llm_client=llm_client,
        llm_model=llm_model,
    )
    labels = [*question_bank.list_categories(), *question_bank.list_topics()]
    profile_service = InterviewProfileService(profile_source, labels)
    planner = InterviewPlanner(question_bank)

    # ── stage_results 不再保存完整对象，Worker 重启后无法恢复领域对象 ──
    # 如果下游 stage 未完成，需要重新执行上游 stage 以生成领域对象
    if "PLAN_GENERATION" not in completed_steps:
        # PLAN_GENERATION 需要 CandidateProfile + JobProfile，两者必须重跑
        completed_steps = [
            s for s in completed_steps
            if s not in ("CANDIDATE_PROFILE", "JOB_PROFILE")
        ]

    candidate_facts = None
    candidate_profile = None
    job_profile = None

    logger.info(
        "Preparation Workflow 开始",
        extra={
            "job_id": job.id,
            "interview_id": interview_id,
            "completed_steps": completed_steps,
        },
    )

    # ── Stage 循环 ──
    for stage_name, step_name in _PREPARATION_STAGES:
        if step_name in completed_steps:
            logger.debug(
                "Stage 已完成，跳过",
                extra={"job_id": job.id, "stage": stage_name},
            )
            continue

        await _update_stage_payload(job_repo, job.id, payload, stage_name)

        logger.info(
            "执行 stage",
            extra={"job_id": job.id, "stage": stage_name},
        )

        try:
            # ── RESUME_PARSE ──
            if step_name == "RESUME_PARSE":
                candidate_facts = await resume_parser.parse(
                    kb_id=candidate_kb_id,
                    job_id=job.id,
                )
                completed_steps.append(step_name)
                stage_results[step_name.lower()] = {
                    "status": "completed",
                    "stage": stage_name,
                    "skills_count": len(candidate_facts.skills),
                }

            # ── CANDIDATE_PROFILE ──
            elif step_name == "CANDIDATE_PROFILE":
                profile = await profile_service.build_candidate_profile(
                    candidate_kb_id,
                    candidate_facts=candidate_facts,
                )
                candidate_profile = profile
                completed_steps.append(step_name)
                stage_results[step_name.lower()] = {
                    "status": "completed",
                    "stage": stage_name,
                    "skills_count": len(profile.skills),
                    "experience_level": profile.experience_level,
                }

            # ── JOB_PROFILE ──
            elif step_name == "JOB_PROFILE":
                if not target_role:
                    raise ValueError("JOB_PROFILE stage 必须提供 target_role")
                if not target_kb_id:
                    raise ValueError("JOB_PROFILE stage 必须提供 target_kb_id")
                profile = await profile_service.build_job_profile(
                    kb_id=target_kb_id,
                    company=target_company,
                    role=target_role,
                )
                job_profile = profile
                completed_steps.append(step_name)
                stage_results[step_name.lower()] = {
                    "status": "completed",
                    "stage": stage_name,
                    "skills_count": len(profile.required_skills),
                }

            # ── COMPANY_INTELLIGENCE（可降级）──
            elif step_name == "COMPANY_INTELLIGENCE":
                degraded = True
                degradation_reasons.append(f"{step_name}: 功能尚未实现，降级跳过")
                completed_steps.append(step_name)
                stage_results[step_name.lower()] = {
                    "status": "degraded",
                    "stage": stage_name,
                    "reason": "not_implemented",
                }
                logger.warning(
                    f"Stage {stage_name} 降级跳过（未实现）",
                    extra={"job_id": job.id},
                )

            # ── PLAN_GENERATION（CandidateProfile + JobProfile 强制）──
            elif step_name == "PLAN_GENERATION":
                if candidate_profile is None:
                    raise RuntimeError("PLAN_GENERATION 需要 CandidateProfile，但尚未生成")
                if job_profile is None:
                    raise RuntimeError("PLAN_GENERATION 需要 JobProfile，但尚未生成")

                plan = planner.build(
                    title=f"模拟面试 - {interview_id}",
                    config=interview_config,
                    candidate_profile=candidate_profile,
                    job_profile=job_profile,
                )
                completed_steps.append(step_name)
                stage_results[step_name.lower()] = {
                    "status": "completed",
                    "stage": stage_name,
                    "plan_id": plan.id,
                    "question_count": len(plan.questions),
                }
                logger.info(
                    "面试计划生成完成",
                    extra={
                        "job_id": job.id,
                        "plan_id": plan.id,
                        "question_count": len(plan.questions),
                    },
                )

            # ── 持久化进度 ──
            payload["completed_steps"] = completed_steps
            payload["stage_results"] = stage_results
            payload["degraded"] = degraded
            payload["degradation_reasons"] = degradation_reasons
            payload.pop("last_error", None)  # 清除上次失败的错误信息

            job_repo.update_payload(job.id, payload)

            logger.info(
                "Stage 完成",
                extra={"job_id": job.id, "stage": stage_name},
            )

        except asyncio.TimeoutError:
            error_msg = f"Stage {stage_name} 超时"
            logger.error(error_msg, extra={"job_id": job.id})
            _persist_stage_error(
                job_repo, job.id, payload,
                stage_name, step_name, error_msg, "TimeoutError",
            )
            raise RuntimeError(error_msg)
        except Exception as exc:
            logger.error(
                f"Stage {stage_name} 失败",
                extra={"job_id": job.id, "error": str(exc)},
            )
            _persist_stage_error(
                job_repo, job.id, payload,
                stage_name, step_name, str(exc), type(exc).__name__,
            )
            raise

    # ── 所有 stage 完成 ──
    await _update_stage_payload(job_repo, job.id, payload, "READY")

    logger.info(
        "Preparation Workflow 完成",
        extra={
            "job_id": job.id,
            "interview_id": interview_id,
            "completed_steps": completed_steps,
            "degraded": degraded,
        },
    )

    return {
        "status": "READY",
        "completed_steps": completed_steps,
        "degraded": degraded,
        "degradation_reasons": degradation_reasons,
        "stage_results": stage_results,
    }


__all__ = ["get_handler", "register", "registered_types"]
