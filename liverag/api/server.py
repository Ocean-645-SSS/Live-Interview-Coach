"""Interview Coach HTTP API and its knowledge-base gateway."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from liverag.api.interview_profile_source import RagGatewayProfileSource
from liverag.api.interview_routes import (
    configure_interview_service,
    configure_job_dependencies,
)
from liverag.api.interview_routes import router as interview_router
from liverag.api.rag_gateway import GatewayResponse, RagGateway
from liverag.config.settings import load_app_settings, load_environment, load_voice_settings
from liverag.interview.application.evaluator import (
    AnswerEvaluator,
    OpenAIAnswerEvaluationProvider,
    OpenAIAnswerEvaluationSettings,
)
from liverag.interview.application.profile_service import InterviewProfileService
from liverag.interview.application.service import InterviewService
from liverag.interview.intelligence.nowcoder_provider import NowcoderSpiderProvider
from liverag.interview.intelligence.service import IntelligenceService, IntelligenceServiceConfig
from liverag.interview.persistence.db import create_database_engine, create_session_factory
from liverag.interview.persistence.sqlalchemy_repository import SQLAlchemyInterviewRepository
from liverag.interview.question_bank.catalog import QuestionBank
from liverag.interview.skill_progress.service import SkillProgressService
from liverag.interview.skill_progress.taxonomy import SkillTaxonomy
from liverag.rag.schemas import QueryRequest, TextDocumentRequest
from liverag.rag.service import wait_for_rag_ready

logger = logging.getLogger("liverag.api.server")

load_environment()
settings = load_app_settings()
rag_gateway = RagGateway(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Observe RAG readiness without making API startup depend on it."""

    try:
        ready_state = await asyncio.to_thread(
            wait_for_rag_ready,
            timeout_ms=settings.api.rag_ready_timeout_ms,
            start_if_missing=False,
        )
    except Exception as exc:
        app.state.rag_ready = {
            "ready": False,
            "status": "check_failed",
            "data": None,
            "error": str(exc),
        }
    else:
        app.state.rag_ready = {
            "ready": ready_state.ready,
            "status": ready_state.status,
            "data": ready_state.data,
            "error": ready_state.error,
        }
    yield


app = FastAPI(title="Live Interview Coach API", version="1.0.0", lifespan=lifespan)

interview_engine = create_database_engine(settings.interview_database.url)
interview_repository = SQLAlchemyInterviewRepository(create_session_factory(interview_engine))

voice_settings = load_voice_settings()
interview_evaluator = None
if voice_settings.llm_api_key.strip():
    evaluation_provider = OpenAIAnswerEvaluationProvider(
        OpenAIAnswerEvaluationSettings.from_voice_settings(voice_settings)
    )
    interview_evaluator = AnswerEvaluator(interview_repository, evaluation_provider)

interview_question_bank = QuestionBank.from_file(
    Path(__file__).resolve().parents[1]
    / "interview"
    / "question_bank"
    / "data"
    / "question_bank.v1.json"
)
interview_profile_service = InterviewProfileService(RagGatewayProfileSource(rag_gateway))
interview_skill_progress_service = SkillProgressService(
    interview_repository,
    SkillTaxonomy.from_file(
        Path(__file__).resolve().parents[1]
        / "interview"
        / "skill_progress"
        / "data"
        / "skill_taxonomy.v1.json"
    ),
)

try:
    import redis.asyncio as _intel_redis

    _intel_redis_conn = _intel_redis.from_url(settings.redis.url, decode_responses=True)
    intelligence_service = IntelligenceService(
        redis_client=_intel_redis_conn,
        provider=NowcoderSpiderProvider(
            timeout=settings.interview_intelligence.provider_timeout_seconds,
        ),
        config=IntelligenceServiceConfig(
            enabled=settings.interview_intelligence.enabled,
            fresh_ttl_seconds=settings.interview_intelligence.cache_fresh_seconds,
            stale_ttl_seconds=settings.interview_intelligence.cache_stale_seconds,
        ),
    )
except ImportError:
    logger.info("Redis is not installed; Interview Intelligence is unavailable")
    intelligence_service = None
except Exception:
    logger.exception("Interview Intelligence initialization failed")
    intelligence_service = None

interview_service = InterviewService(
    interview_repository,
    evaluator=interview_evaluator,
    question_bank=interview_question_bank,
    profile_service=interview_profile_service,
    skill_progress_service=interview_skill_progress_service,
    intelligence_service=intelligence_service,
)
configure_interview_service(interview_service)
app.include_router(interview_router)

try:
    import redis.asyncio as _aredis

    from liverag.interview.jobs.queue import RedisQueue
    from liverag.interview.jobs.repository import JobRepository

    _redis_conn = _aredis.from_url(settings.redis.url, decode_responses=True)
    _job_repo = JobRepository(create_session_factory(interview_engine))
    _redis_queue = RedisQueue(
        _redis_conn,
        lock_ttl_seconds=settings.redis.lock_ttl_seconds,
    )
    configure_job_dependencies(_job_repo, _redis_queue)
except ImportError:
    logger.info("Redis is not installed; background jobs are unavailable")
except Exception:
    logger.exception("Background job infrastructure initialization failed")


class KnowledgeBasePayload(BaseModel):
    name: str | None = None
    description: str | None = None
    company: str | None = None
    role: str | None = None


UPLOAD_FILES = File(...)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/rag/ready")
async def rag_ready() -> JSONResponse:
    return _json_response(await rag_gateway.get("/v1/readyz"))


@app.get("/rag/knowledge-bases")
async def rag_knowledge_bases() -> JSONResponse:
    return _json_response(await rag_gateway.get("/v1/knowledge-bases"))


@app.post("/rag/knowledge-bases")
async def rag_create_knowledge_base(payload: KnowledgeBasePayload) -> JSONResponse:
    company = (payload.company or "").strip()
    role = (payload.role or "").strip()
    if not company or not role:
        raise HTTPException(status_code=422, detail="创建岗位资料库必须填写公司名称和岗位名称")

    return _json_response(
        await rag_gateway.post_json(
            "/v1/knowledge-bases",
            payload={
                "name": f"{company} · {role}",
                "description": (payload.description or "").strip(),
            },
        )
    )


@app.get("/rag/knowledge-bases/{kb_id}")
async def rag_knowledge_base_detail(kb_id: str) -> JSONResponse:
    return _json_response(await rag_gateway.get(f"/v1/knowledge-bases/{kb_id}"))


@app.patch("/rag/knowledge-bases/{kb_id}")
async def rag_patch_knowledge_base(
    kb_id: str,
    payload: KnowledgeBasePayload,
) -> JSONResponse:
    if kb_id == "default":
        raise HTTPException(status_code=409, detail="个人简历资料库的名称和用途不可修改")

    company = (payload.company or "").strip()
    role = (payload.role or "").strip()
    update_payload = payload.model_dump(include={"name", "description"}, exclude_none=True)
    if company or role:
        if not company or not role:
            raise HTTPException(status_code=422, detail="公司名称和岗位名称必须同时填写")
        update_payload["name"] = f"{company} · {role}"

    return _json_response(
        await rag_gateway.patch_json(
            f"/v1/knowledge-bases/{kb_id}",
            payload=update_payload,
        )
    )


@app.delete("/rag/knowledge-bases/{kb_id}")
async def rag_delete_knowledge_base(kb_id: str) -> JSONResponse:
    return _json_response(await rag_gateway.delete(f"/v1/knowledge-bases/{kb_id}"))


@app.get("/rag/knowledge-bases/{kb_id}/ready")
async def rag_knowledge_base_ready(kb_id: str) -> JSONResponse:
    return _json_response(await rag_gateway.get(f"/v1/knowledge-bases/{kb_id}/ready"))


@app.get("/rag/knowledge-bases/{kb_id}/documents")
async def rag_kb_documents(
    kb_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> JSONResponse:
    return _json_response(
        await rag_gateway.get_documents(
            f"/v1/knowledge-bases/{kb_id}/documents",
            params={"page": page, "page_size": page_size},
        )
    )


@app.post("/rag/knowledge-bases/{kb_id}/documents/text")
async def rag_kb_documents_text(
    kb_id: str,
    payload: TextDocumentRequest,
) -> JSONResponse:
    return _json_response(
        await rag_gateway.post_json(
            f"/v1/knowledge-bases/{kb_id}/documents/text",
            payload=payload.model_dump(exclude_none=True),
        )
    )


@app.get("/rag/knowledge-bases/{kb_id}/documents/{document_id}")
async def rag_kb_document_detail(kb_id: str, document_id: str) -> JSONResponse:
    return _json_response(
        await rag_gateway.get_document_detail(
            f"/v1/knowledge-bases/{kb_id}/documents/{document_id}"
        )
    )


@app.post("/rag/knowledge-bases/{kb_id}/documents/files")
async def rag_kb_documents_files(
    kb_id: str,
    files: list[UploadFile] = UPLOAD_FILES,
    pdf_password: str | None = Form(default=None),
) -> JSONResponse:
    return _json_response(
        await rag_gateway.post_files(
            f"/v1/knowledge-bases/{kb_id}/documents/files",
            files=files,
            pdf_password=pdf_password,
        )
    )


@app.get("/rag/knowledge-bases/{kb_id}/documents/{document_id}/source")
async def rag_kb_document_source(
    kb_id: str,
    document_id: str,
    disposition: str = Query(default="inline", pattern="^(inline|attachment)$"),
) -> Response:
    response = await rag_gateway.get_file(
        path=f"/v1/knowledge-bases/{kb_id}/documents/{document_id}/source",
        params={"disposition": disposition},
    )
    if response.error_body is not None:
        return JSONResponse(content=response.error_body, status_code=response.status_code)
    return Response(
        content=response.body,
        status_code=response.status_code,
        headers=response.headers,
    )


@app.get("/rag/knowledge-bases/{kb_id}/jobs/{job_id}")
async def rag_kb_job(kb_id: str, job_id: str) -> JSONResponse:
    return _json_response(
        await rag_gateway.get_job(f"/v1/knowledge-bases/{kb_id}/jobs/{job_id}")
    )


@app.delete("/rag/knowledge-bases/{kb_id}/documents/{document_id}")
async def rag_kb_delete_document(
    kb_id: str,
    document_id: str,
    delete_llm_cache: bool = Query(default=False),
) -> JSONResponse:
    return _json_response(
        await rag_gateway.delete(
            f"/v1/knowledge-bases/{kb_id}/documents/{document_id}",
            params={"delete_llm_cache": delete_llm_cache},
        )
    )


@app.post("/rag/knowledge-bases/{kb_id}/query/context")
async def rag_kb_query_context(kb_id: str, payload: QueryRequest) -> JSONResponse:
    return _json_response(
        await rag_gateway.post_json(
            f"/v1/knowledge-bases/{kb_id}/query/context",
            payload=payload.model_dump(exclude_none=True),
        )
    )


def _json_response(result: GatewayResponse) -> JSONResponse:
    return JSONResponse(result.body, status_code=result.status_code)
