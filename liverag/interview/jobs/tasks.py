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

from liverag.interview.application.planner import InterviewPlanner, validate_plan_quality
from liverag.interview.application.profile_service import (
    InterviewProfileService,
    KnowledgeContextSource,
)
from liverag.interview.application.report import InterviewReportBuilder
from liverag.interview.application.resume_parser import ResumeParser
from liverag.interview.application.skill_progress_reconciliation import (
    reconcile_skill_progress,
)
from liverag.interview.intelligence.provider import InterviewIntelligenceQuery
from liverag.interview.intelligence.service import IntelligenceService
from liverag.interview.jobs.queue import RedisQueue
from liverag.interview.jobs.repository import BackgroundJobRecord, JobRepository
from liverag.interview.persistence.repository import ConcurrentUpdateError, InterviewRepository
from liverag.interview.question_bank.catalog import QuestionBank
from liverag.interview.records import ReportState
from liverag.interview.schemas import CandidateFacts, InterviewConfig
from liverag.interview.skill_progress.service import SkillProgressService

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
    service: InterviewProfileService,
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

    service = InterviewProfileService(profile_source)

    if profile_type == "candidate_profile":
        return await _generate_candidate_profile(
            job=job,
            service=service,
            job_repo=job_repo,
            kb_id=kb_id,
            candidate_facts_job_id=payload.get("candidate_facts_job_id"),
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
    profile_source: KnowledgeContextSource,
    llm_client: AsyncOpenAI,
    llm_model: str,
    question_bank: QuestionBank,
    interview_repo: InterviewRepository | None = None,
    intelligence_service: IntelligenceService | None = None,
    skill_progress_service: SkillProgressService | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """面试准备 Workflow：直接复用 ResumeParser、ProfileService、Planner 等 Service。

    创建面试前准备任务
            ↓
    依次执行：
    简历解析 → 候选人画像 → 岗位画像 → 公司面经增强 → 面试计划
            ↓
    保存每一步结果和状态

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

    resume_parser = ResumeParser(
        profile_source=profile_source,
        llm_client=llm_client,
        llm_model=llm_model,
    )
    profile_service = InterviewProfileService(profile_source)
    planner = InterviewPlanner(question_bank, llm_client=llm_client, llm_model=llm_model)

    # 当前 stage_results 不保存领域对象，仅保存摘要，因此 Worker 重启后需要重新执行生成领域对象的 stage。
    # 如果下游 stage 未完成，需要重新执行上游 stage 以生成领域对象
    if "PLAN_GENERATION" not in completed_steps:
        # PLAN_GENERATION → CANDIDATE_PROFILE → RESUME_PARSE（需要 CandidateFacts）
        # PLAN_GENERATION → JOB_PROFILE
        # 三个都必须重跑，否则依赖链断裂（CandidateFacts 不存在时 Profile 为空壳）
        completed_steps = [
            s for s in completed_steps
            if s not in ("RESUME_PARSE", "CANDIDATE_PROFILE", "JOB_PROFILE")
        ]

    candidate_facts = None
    candidate_profile = None
    job_profile = None
    company_intel = None
    intelligence_enrichment_result = None

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
                if interview_repo is not None:
                    #获得candidateprofile记录
                    candidate_record = interview_repo.ensure_candidate_profile(
                        kb_id=candidate_kb_id
                    )
                    #更新candidate profile快照
                    interview_repo.update_candidate_profile_snapshot(
                        candidate_profile_id=candidate_record.id,
                        profile=profile,
                    )
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
                # 未提供公司名，主动跳过（不算降级）
                if not target_company:
                    completed_steps.append(step_name)
                    stage_results[step_name.lower()] = {
                        "status": "skipped",
                        "stage": stage_name,
                        "reason": "company_not_provided",
                    }
                    company_intel = None
                    logger.info(
                        "未提供目标公司，跳过 Company Intelligence",
                        extra={"job_id": job.id},
                    )
                else:
                    if intelligence_service is None:
                        # IntelligenceService 未注入 → 降级
                        degraded = True
                        degradation_reasons.append(
                            f"{step_name}: IntelligenceService 未注入 (NO_SERVICE)"
                        )
                        completed_steps.append(step_name)
                        stage_results[step_name.lower()] = {
                            "status": "degraded",
                            "stage": stage_name,
                            "reason": "no_intelligence_service",
                        }
                        company_intel = None
                        logger.warning(
                            "IntelligenceService 未注入，降级跳过 Company Intelligence",
                            extra={"job_id": job.id},
                        )

                    else:
                        # 构建查询并调用 IntelligenceService 生成 CompanyProfile
                        query = InterviewIntelligenceQuery(
                            company=target_company,
                            role=target_role or "",
                        )
                        enrichment = await intelligence_service.get_company_profile(query)
                        intelligence_enrichment_result = enrichment
                        company_intel = enrichment.profile

                        # 记录 stage 结果元数据
                        stage_result: dict[str, Any] = {
                            "status": enrichment.status.value,
                            "stage": stage_name,
                            "provider": enrichment.provider,
                            "snapshot_hash": enrichment.snapshot_hash,
                        }
                        #如果enrich降级
                        if enrichment.degraded:
                            degraded = True
                            for reason in enrichment.degradation_reasons:
                                degradation_reasons.append(
                                    f"{step_name}: {reason}"
                                )
                            stage_result["degraded"] = True
                        if enrichment.cache_age_seconds is not None:
                            stage_result["cache_age_seconds"] = enrichment.cache_age_seconds

                        completed_steps.append(step_name)
                        stage_results[step_name.lower()] = stage_result

                        logger.info(
                            "Company Intelligence 完成",
                            extra={
                                "job_id": job.id,
                                "status": enrichment.status.value,
                                "company": target_company,
                                "degraded": enrichment.degraded,
                            },
                        )

            # ── PLAN_GENERATION（CandidateProfile + JobProfile 强制）──
            elif step_name == "PLAN_GENERATION":
                if candidate_profile is None:
                    raise RuntimeError("PLAN_GENERATION 需要 CandidateProfile，但尚未生成")
                if job_profile is None:
                    raise RuntimeError("PLAN_GENERATION 需要 JobProfile，但尚未生成")

                # 生成LLM改写过的个性化面试计划
                candidate_profile_id = None
                skill_progress = []
                if interview_repo is not None:
                    #获得candidate profile record
                    candidate_record = interview_repo.ensure_candidate_profile(
                        kb_id=candidate_kb_id
                    )
                    candidate_profile_id = candidate_record.id
                    if skill_progress_service is not None:
                        #列出候选人所有的 skill progress
                        skill_progress = skill_progress_service.list_progress(
                            candidate_profile_id
                        )
                plan = await planner.build(
                    title=f"模拟面试 - {interview_id}",
                    config=interview_config,
                    candidate_profile=candidate_profile,
                    job_profile=job_profile,
                    company_intel=company_intel,
                    candidate_profile_id=candidate_profile_id,
                    skill_progress=skill_progress,
                )

                # 设置公司情报审计状态
                if intelligence_enrichment_result is not None:
                    plan = plan.model_copy(
                        update={
                            "intelligence_status": intelligence_enrichment_result.status.value
                        }
                    )

                # ──  增强复核 ──
                quality_issues = validate_plan_quality(plan)
                if quality_issues:
                    logger.warning(
                        "面试计划复核发现问题",
                        extra={
                            "job_id": job.id,
                            "interview_id": interview_id,
                            "issues": quality_issues,
                        },
                    )
                    # 复核问题作为降级记录，不阻塞流程
                    for issue in quality_issues:
                        degradation_reasons.append(f"PLAN_QUALITY: {issue}")
                    degraded = True

                # ── 持久化 InterviewPlan + 状态 READY ──
                if interview_repo is not None:
                    try:
                        interview = interview_repo.get_interview(interview_id)
                        interview_repo.save_interview_plan(
                            interview_id=interview_id,
                            plan=plan,
                            expected_version=interview.version,
                        )
                        logger.info(
                            "InterviewPlan 已持久化，Interview.state → READY",
                            extra={
                                "job_id": job.id,
                                "interview_id": interview_id,
                                "plan_id": plan.id,
                            },
                        )
                    except ConcurrentUpdateError:
                        # 幂等：plan 已由之前的部分执行持久化
                        logger.info(
                            "InterviewPlan 已存在（版本冲突），按幂等跳过",
                            extra={"job_id": job.id, "interview_id": interview_id},
                        )
                else:
                    logger.warning(
                        "interview_repo 未注入，跳过 InterviewPlan 持久化",
                        extra={"job_id": job.id, "interview_id": interview_id},
                    )

                completed_steps.append(step_name)

                #把planner生成的训练审计信息写入后台job的阶段结果
                #1.取出训练审计结果
                training_adjustment = plan.training_adjustment
                #2.取出降级原因
                training_degradation_reasons = (
                    training_adjustment.degradation_reasons
                    if training_adjustment is not None
                    else []
                )
                #3.发生了降级，设置degrade=true，并且写入原因
                if training_degradation_reasons:
                    degraded = True
                    degradation_reasons.extend(
                        reason
                        for reason in training_degradation_reasons
                        if reason not in degradation_reasons
                    )
                #4.把当前阶段摘要写入job payload
                stage_results[step_name.lower()] = {
                    "status": "completed",
                    "stage": stage_name,
                    "plan_id": plan.id,
                    "question_count": len(plan.questions),
                    "quality_issues": quality_issues,
                    "skill_count": len(skill_progress),
                    "selection_intent_count": (
                        len(training_adjustment.selection_reasons)
                        if training_adjustment is not None
                        else 0
                    ),
                    "job_core_required": (
                        training_adjustment.job_core_required
                        if training_adjustment is not None
                        else 0
                    ),
                    "job_core_available": (
                        training_adjustment.job_core_available
                        if training_adjustment is not None
                        else 0
                    ),
                    "job_core_selected": (
                        training_adjustment.job_core_selected
                        if training_adjustment is not None
                        else 0
                    ),
                    "degradation_reasons": training_degradation_reasons,
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

        except asyncio.TimeoutError as exc:
            error_msg = f"Stage {stage_name} 超时"
            logger.error(error_msg, extra={"job_id": job.id})
            _persist_stage_error(
                job_repo, job.id, payload,
                stage_name, step_name, error_msg, "TimeoutError",
            )
            raise RuntimeError(error_msg) from exc
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


# ====================== Report Generation Workflow ============================

@register("report_generation")
async def report_generation_task(
    job: BackgroundJobRecord,
    *,
    interview_repo: InterviewRepository,
    redis_queue: RedisQueue | None = None,
    skill_progress_service: SkillProgressService | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """报告生成任务：加载 Session 的所有评价 → 聚合生成报告 → 持久化。

    输入（payload）：
        - session_id（必填）
    输出：
        {report_id, session_id, state: "COMPLETED"}
    幂等键：report:{session_id}

    流程：
    先看报告有没有生成过 → 没生成就抢 Redis 锁 → 创建/标记报告生成中 →
    聚合评价生成报告 → 落库 COMPLETED → 顺手重建长期 SkillProgress → 最后释放锁

    report_generation_task
            ↓
    解析 session_id
            ↓
    查询是否已有 COMPLETED Report
            ├── 有
            │    ↓
            │  reconcile_skill_progress()
            │    ↓
            │  从持久化 AnswerEvaluation
            │  rebuild Candidate SkillProgress
            │    ↓
            │  返回已有 COMPLETED Report
            │
            ↓ 没有
    尝试获取 Redis 锁
            ├── 未获取
            │     ↓
            │  轮询等待已有 Report
            │     ├── 等到 COMPLETED
            │     │      ↓
            │     │  reconcile_skill_progress()
            │     │      ↓
            │     │  返回已有 Report
            │     │
            │     └── 60s 未完成
            │            ↓
            │         再尝试获取锁
            │            ├── 失败 → 抛错，交给任务重试
            │            └── 成功 → 继续
            │
            ↓
    创建 / 复用 Report
            ↓
    start_report_generation()
            ↓
    加载 Session + InterviewPlan + Answers + AnswerEvaluations
            ↓
    InterviewReportBuilder.build()
            ↓
    生成 Report Content
            ↓
    complete_report()
            ↓
    Report = COMPLETED
            ↓
    reconcile_skill_progress()
            ↓
    根据持久化 AnswerEvaluation
    重建 Candidate SkillProgress
            ↓
    返回 {report_id , session_id , state="COMPLETED"}
            ↓
    finally：释放 Redis 锁

    并发保护（lock:interview_report:{session_id}）：
    - Redis 锁确保同一 session 的报告只生成一次
    - 获取锁失败 → 等待已有生成完成 → 返回 COMPLETED 结果
    - 锁 TTL 300s，Worker 崩溃后自动释放
    """

    payload = json.loads(job.payload_json) if job.payload_json else {}
    session_id = payload.get("session_id", "")
    if not session_id.strip():
        raise ValueError("report_generation 必须提供 session_id")

    #报告生成器
    builder = InterviewReportBuilder(interview_repo)

    # ── 幂等检查：已有 COMPLETED 报告 → 构造长期画像 → 返回 ──
    existing = interview_repo.get_report_by_session(session_id)
    if existing is not None and existing.state is ReportState.COMPLETED:
        logger.info(
            "报告已生成，跳过",
            extra={"job_id": job.id, "session_id": session_id, "report_id": existing.id},
        )
        #已有报告，从持久化评价重建候选人的长期技能画像
        reconcile_skill_progress(
            interview_repo, skill_progress_service, session_id
        )
        return {
            "report_id": existing.id,
            "session_id": session_id,
            "state": "COMPLETED",
        }

    # ── Redis 锁：防止多个 Worker/API 同时生成同一份报告 ──
    lock_ttl = 300  # 5 分钟，足够报告生成完成
    lock_token: str | None = None
    if redis_queue is not None:
        lock_token = await redis_queue.acquire_lock(
            job_type="report_generation",
            resource_id=session_id,
            ttl=lock_ttl,
        )
        if lock_token is None:
            # 另一进程正在生成 → 轮询等待其结果
            logger.info(
                "报告生成锁已被占用，等待另一进程完成",
                extra={"job_id": job.id, "session_id": session_id},
            )
            for _ in range(60):  # 最多等 60 秒
                await asyncio.sleep(1.0)
                existing = interview_repo.get_report_by_session(session_id)
                if existing is not None and existing.state is ReportState.COMPLETED:
                    logger.info(
                        "另一进程已完成报告生成",
                        extra={"job_id": job.id, "session_id": session_id, "report_id": existing.id},
                    )
                    #已有报告，从持久化评价重建候选人的长期技能画像
                    reconcile_skill_progress(
                        interview_repo, skill_progress_service, session_id
                    )
                    return {
                        "report_id": existing.id,
                        "session_id": session_id,
                        "state": "COMPLETED",
                    }
            # 超时仍未完成 → 判定上一进程已崩溃，
            # 等 60 秒后再尝试一次，如果还是拿不到，就认为当前仍然有人持锁生成，抛错让任务后续重试。
            logger.warning(
                "等待报告生成超时（60s），强制接管",
                extra={"job_id": job.id, "session_id": session_id},
            )
            # 旧锁已过 TTL 的 60/300，重新尝试获取
            lock_token = await redis_queue.acquire_lock(
                job_type="report_generation",
                resource_id=session_id,
                ttl=lock_ttl,
            )
            if lock_token is None:
                raise RuntimeError(
                    f"无法获取报告生成锁：session {session_id} 的报告可能正在由另一进程生成"
                )

    # ── 创建或复用报告记录 ──
    report = interview_repo.create_report(session_id=session_id) if existing is None else existing

    # ── 标记开始生成 ──
    try:
        interview_repo.start_report_generation(report.id)
    except ValueError:
        # 条件更新失败：另一进程已抢先标记为 GENERATING
        # 检查是否已有 COMPLETED 结果
        refreshed = interview_repo.get_report_by_session(session_id)
        if refreshed is not None and refreshed.state is ReportState.COMPLETED:
            #已有报告，从持久化评价重建候选人的长期技能画像
            reconcile_skill_progress(
                interview_repo, skill_progress_service, session_id
            )
            return {
                "report_id": refreshed.id,
                "session_id": session_id,
                "state": "COMPLETED",
            }
        raise

    try:
        # ── 聚合生成 ──
        content = builder.build(session_id)
        # ── 持久化完成 ──
        completed = interview_repo.complete_report(
            report_id=report.id, content=content
        )
        logger.info(
            "报告生成完成",
            extra={
                "job_id": job.id,
                "session_id": session_id,
                "report_id": completed.id,
                "evaluation_count": len(content.get("evaluations", [])),
                "overall_score": content.get("overall_score"),
            },
        )
    except Exception as exc:
        error_msg = str(exc) or type(exc).__name__
        interview_repo.fail_report(report_id=report.id, error_message=error_msg)
        logger.error(
            "报告生成失败",
            extra={"job_id": job.id, "session_id": session_id, "error": error_msg},
        )
        raise

    else:
        #已有报告，从持久化评价重建候选人的长期技能画像
        reconcile_skill_progress(
            interview_repo, skill_progress_service, session_id
        )
        return {
            "report_id": completed.id,
            "session_id": session_id,
            "state": "COMPLETED",
        }
    finally:
        # ── 释放锁 ──
        if lock_token is not None and redis_queue is not None:
            try:
                await redis_queue.release_lock(
                    job_type="report_generation",
                    resource_id=session_id,
                    lock_token=lock_token,
                )
            except Exception:
                logger.warning(
                    "释放报告生成锁失败（锁可能已过期）",
                    extra={"job_id": job.id, "session_id": session_id},
                )


__all__ = ["get_handler", "register", "registered_types"]
