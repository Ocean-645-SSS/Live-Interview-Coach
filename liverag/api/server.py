"""前端的产品管理 API

前端
  ↓
api/server.py（9821，产品管理层）
  ↓ RagGateway
rag/server.py（9721，RAG 核心层）
  ↓
LightRAG、文档存储、索引与查询"""


import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from liverag.api.interview_profile_source import RagGatewayProfileSource
from liverag.api.interview_routes import (
    configure_interview_service,
    configure_job_dependencies,
)
from liverag.api.interview_routes import (
    router as interview_router,
)
from liverag.api.rag_gateway import GatewayResponse, RagGateway, envelope
from liverag.config.settings import (
    RagToolMode,
    is_masked_secret,
    load_app_settings,
    load_context_model_settings,
    load_environment,
    load_rag_client_settings,
    load_voice_settings,
    merge_runtime_rag_config,
    public_context_model_config,
    public_model_options,
    public_rag_client_config,
    public_voice_config,
    read_runtime_context_model_config,
    read_runtime_model_config,
    validate_voice_config_selection,
    voice_config_for_storage,
    write_runtime_context_model_config,
    write_runtime_model_config,
)
from liverag.context.overview import KnowledgeOverviewGenerator
from liverag.context.store import ContextStore
from liverag.interview.application.evaluator import (
    AnswerEvaluator,
    OpenAIAnswerEvaluationProvider,
    OpenAIAnswerEvaluationSettings,
)
from liverag.interview.application.profile_service import InterviewProfileService
from liverag.interview.application.service import InterviewService
from liverag.interview.intelligence.nowcoder_provider import NowcoderSpiderProvider
from liverag.interview.intelligence.service import (
    IntelligenceService,
    IntelligenceServiceConfig,
)
from liverag.interview.persistence.db import create_database_engine, create_session_factory
from liverag.interview.persistence.sqlalchemy_repository import SQLAlchemyInterviewRepository
from liverag.interview.question_bank.catalog import QuestionBank
from liverag.interview.skill_progress.service import SkillProgressService
from liverag.interview.skill_progress.taxonomy import SkillTaxonomy
from liverag.rag.metadata_store import MetadataStore
from liverag.rag.schemas import QueryRequest, TextDocumentRequest
from liverag.rag.service import wait_for_rag_ready
from liverag.runtime.paths import build_runtime_paths

logger = logging.getLogger("liverag.api.server")

load_environment() #导入.env.local
settings=load_app_settings()
paths=build_runtime_paths(settings.user_data_dir)
metadata_store=MetadataStore(paths.db_file,paths.rag_knowledge_bases_dir)
metadata_store.initialize()
store=ContextStore(paths)
store.initialize()
rag_gateway = RagGateway(settings)

_SECRET_FIELDS = {
    "api_key",
    "access_token",
    "app_id",
}

@asynccontextmanager
async def lifespan(
    app: FastAPI,
) -> AsyncIterator[None]:
    """管理 API 生命周期：只观察 RAG Core 状态
    使得RAG Core未启动时：管理API最多等到超时；
    9821仍可以启动；不会自己启动9721；
    RAG状态保存在app.store.rag_ready"""

    try:
        ready_state = await asyncio.to_thread(
            wait_for_rag_ready,
            timeout_ms=(settings.api.rag_ready_timeout_ms),
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

#创建整个FastAPI应用
app=FastAPI(title="LiveRAG Agent API",version="0.1.0",lifespan=lifespan)

#创建Interview板块使用的 SQLAlchemy Engine
interview_engine = create_database_engine(
    settings.interview_database.url
)
#创建SQLAlchemy Session工厂 -> SQLAlchemy Repository -> Interview Service层
interview_repository = SQLAlchemyInterviewRepository(
    create_session_factory(interview_engine)
)

#加载语音配置
voice_settings = load_voice_settings()
interview_evaluator = None
#配置评价LLM
if voice_settings.llm_api_key.strip():
    evaluation_provider = OpenAIAnswerEvaluationProvider(
        OpenAIAnswerEvaluationSettings.from_voice_settings(voice_settings)
    )
    interview_evaluator = AnswerEvaluator(interview_repository, evaluation_provider)

#获取固定题库
interview_question_bank = QuestionBank.from_file(
    Path(__file__).resolve().parents[1]
    / "interview"
    / "question_bank"
    / "data"
    / "question_bank.v1.json"
)

#配置profile service
interview_profile_service = InterviewProfileService(
    RagGatewayProfileSource(rag_gateway),
)
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

# Intelligence Service：公司面经情报（Redis 可选，不可用时降级）
try:
    import redis.asyncio as _intel_redis

    _intel_redis_conn = _intel_redis.from_url(
        settings.redis.url,
        decode_responses=True,
    )
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
    logger.info("Intelligence Service 已初始化")
except ImportError:
    logger.info("redis 库未安装，Intelligence Service 不可用")
    intelligence_service = None
except Exception:
    logger.exception("Intelligence Service 初始化失败，继续启动")
    intelligence_service = None

#注册interview的service层
interview_service = InterviewService(
    interview_repository,
    evaluator=interview_evaluator,
    question_bank=interview_question_bank,
    profile_service=interview_profile_service,
    skill_progress_service=interview_skill_progress_service,
    intelligence_service=intelligence_service,
)
#把InterviewService注册给Interview API路由
configure_interview_service(interview_service)
#把Interview Router安装进FastAPI app
app.include_router(interview_router)

#Background Job 基础设施（Redis 可选
try:
    import redis.asyncio as _aredis

    from liverag.interview.jobs.queue import RedisQueue
    from liverag.interview.jobs.repository import JobRepository

    #建立redis连接
    _redis_conn = _aredis.from_url(
        settings.redis.url,
        decode_responses=True,
    )
    #创建job关联数据库 repository层
    _job_repo = JobRepository(create_session_factory(interview_engine))
    #创建redis队列+锁管理
    _redis_queue = RedisQueue(
        _redis_conn,
        lock_ttl_seconds=settings.redis.lock_ttl_seconds,
    )
    #注入两者
    configure_job_dependencies(_job_repo, _redis_queue)
    logger.info("Background Job 基础设施已就绪")
except ImportError:
    logger.info("redis 库未安装，跳过 Background Job 基础设施")
except Exception:
    logger.exception("Background Job 基础设施初始化失败，继续启动")

UPLOAD_FILES = File(...)


class TextPayload(BaseModel):
    """通用文本更新请求。"""

    content: str

class RagConfigPayload(BaseModel):
    """RAG配置更新请求"""
    enabled: bool | None = None
    base_url: str | None = None
    api_key: str | None = None
    query_mode: str | None = None
    timeout_ms: int | None = None
    top_k: int | None = None
    chunk_top_k: int | None = None
    context_max_chars: int | None = None
    cache_ttl_s: float | None = None
    enable_rerank: bool | None = None
    rag_tool_mode: RagToolMode | None = None

class SessionKnowledgeBasePayload(BaseModel):
    """会话知识库选择请求。"""
    kb_id: str

class KnowledgeBasePayload(BaseModel):
    """知识库创建/更新请求"""

    kb_id: str | None = None
    name:str|None=None
    description:str|None=None
    company: str | None = None
    role: str | None = None

class ModelSttPayload(BaseModel):
    """语音 STT 模型配置更新请求。"""

    provider: str | None = None
    model: str | None = None
    app_id: str | None = None
    access_token: str | None = None


class ModelLlmPayload(BaseModel):
    """语音 LLM 模型配置更新请求。"""

    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class ModelTtsPayload(BaseModel):
    """语音 TTS 模型配置更新请求。"""

    provider: str | None = None
    model: str | None = None
    voice: str | None = None
    api_key: str | None = None

class ModelVoicePayload(BaseModel):
    """语音模型配置更新请求。"""

    stt: ModelSttPayload | None = None
    llm: ModelLlmPayload | None = None
    tts: ModelTtsPayload | None = None

class ModelConfigPayload(BaseModel):
    """模型配置更新请求。"""

    voice: ModelVoicePayload | None = None

class ContextModelPayload(BaseModel):
    """上下文模型配置更新请求。"""

    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    temperature: float | None = Field(default=None,ge=0,le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    max_session_chars: int | None = Field(default=None, gt=0)
    history_reference_limit: int | None = Field(default=None,ge=0)
    timeout_ms: int | None = Field(default=None, gt=0)


@app.get("/health")
async def health() -> dict[str,Any]:
    """返回前端接口健康状态"""

    return {"status":"ok"}


@app.get("/runtime/state")
async def runtime_state(session_id:str)->dict[str,Any]:
    """读取当前Agent运行状态，补充RAG模式、语音Model、KB状态，返回给前端"""

    state=store.read_runtime_state(session_id)
    if not state:
        raise HTTPException(status_code=404,detail=f"session not found: {session_id}",)

    #RAG模式
    state.setdefault("rag_tool_mode",load_rag_client_settings(settings.user_data_dir).rag_tool_mode)

    active_session=state.get("active_session")
    #语音Model
    if isinstance(active_session,dict):
        state.setdefault("active_voice_model",active_session.get("voice"))
        state.setdefault("model_pending_reconnect",_model_pending_reconnect(active_session.get("voice")))

    #KB状态
    state["knowledge_base"]=await _session_knowledge_base_state(session_id)

    return state


#================================ /model/... ================================
@app.get("/model/config")
async def model_config()->dict[str,Any]:
    """读取下一次通话的语音记录"""

    voice=load_voice_settings(settings.user_data_dir)
    return envelope(
        data={
            "voice":public_voice_config(voice,effective="next_session"),
            "options":public_model_options()
        }
    )

@app.put("/model/config")
async def put_model_config(payload:ModelConfigPayload)->dict[str,Any]:
    """更新下一次通话的语音记录"""

    #删除前端回填的掩码秘钥
    updates=_drop_masked_secret_updates(payload.model_dump(exclude_none=True))
    #校验请求字段
    _validate_model_config_updates(updates)
    #读取现有JSON覆盖配置
    current=read_runtime_model_config(settings.user_data_dir)

    try:
        #深度合并局部更新和旧设置
        merged=voice_config_for_storage(_deep_merge_dict(current,updates))
        #归一化Provider/model/voice
        validate_voice_config_selection(merged)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    #校验成功后一次性写入
    write_runtime_model_config(merged,settings.user_data_dir)
    voice=load_voice_settings(settings.user_data_dir)

    #返回下一场通话生效的配置
    return envelope(
        data={
            "voice": public_voice_config(voice, effective="next_session"),
            "options": public_model_options(),
        }
    )

@app.get("/model/options")
async def model_options()->dict[str,Any]:
    """读取前端模型选择页可用 provider、模型和音色"""

    return envelope(data=public_model_options())

@app.get("/model/effective-state/{session_id}")
async def model_effective_state(session_id:str)->dict[str,Any]:
    """读取当前模型配置和本次/最近通话实际生效状态"""

    #用户刚保存，下一次通话将用的配置
    configured={
        "voice":public_voice_config(load_voice_settings(settings.user_data_dir),effective="next_session"),
        "options":public_model_options()
    }

    #当前或最近通话真正用的配置
    active_session=store.read_runtime_state(session_id).get("active_session")
    if not isinstance(active_session,dict):
        active_session=None

    return envelope(
        data={
            "configured": configured,
            "active_session": active_session,
            #两者是否不同
            "pending_reconnect": _model_pending_reconnect(
                active_session.get("voice") if active_session else None
            ),
        }
    )

@app.get("/model/context-config")
async def context_model_config() -> dict[str, Any]:
    """读取上下文模型配置。"""

    config = load_context_model_settings(settings.user_data_dir)
    return envelope(data={"context_model": public_context_model_config(config)})

@app.put("/model/context-config")
async def put_context_model_config(payload:ContextModelPayload) -> dict[str, Any]:
    """更改上下文模型配置。"""

    updates=_drop_masked_secret_updates(payload.model_dump(exclude_none=True))
    _validate_context_model_updates(updates)
    current=read_runtime_context_model_config(settings.user_data_dir)
    merged = {**current, **updates}
    write_runtime_context_model_config(merged,settings.user_data_dir)
    config=load_context_model_settings(settings.user_data_dir)
    return envelope(data={"context_model":public_context_model_config(config)})

#================================ /prompt/...================================
@app.get("/prompt/soul")
async def get_soul()->dict[str,Any]:
    """读取用户定义的 agent 角色人格"""

    return {"content":store.read_soul()}

@app.put("/prompt/soul")
async def put_soul(payload:TextPayload)->dict[str,Any]:
    """更新 agent 角色人格"""

    store.write_soul(payload.content)
    return {"status":"ok"}


#================================ /session/...================================
@app.get("/sessions/{session_id}/messages")
async def session_messages(session_id:str,limit:int|None=None)->list[dict[str,Any]]:
    """读取当前会话信息"""

    return store.read_message(session_id=session_id,limit=limit)

@app.get("/sessions/{session_id}/turns")
async def session_turns(session_id:str,limit: int | None = None) -> list[dict[str, Any]]:
    """读取当前会话消息和RAG展示依据"""

    return store.read_session_turns(session_id=session_id,limit=limit)

@app.get("/sessions/{session_id}/rag-context")
async def session_rag_context(session_id:str,limit: int | None = None) -> list[dict[str, Any]]:
    """读取当前会话 RAG 查询记录。"""

    return store.read_rag_context(session_id=session_id,limit=limit)

@app.post("/sessions/{session_id}/end")
async def end_session(session_id: str) -> dict[str, Any]:
    """Immediately mark a user-ended call as ended."""

    state = store.read_runtime_state(session_id=session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")

    if state.get("state") == "active" and not state.get("ended_at"):
        store.end_session(session_id=session_id, state="ended")

    return {
        "status": "ok",
        "session_id": session_id,
        "state": "ended",
    }

@app.delete("/sessions/{session_id}")
async def delete_session(session_id:str) -> dict[str,Any]:
    """删除一个指定且已经结束的session"""

    state=store.read_runtime_state(session_id=session_id)
    if not state:
        raise HTTPException(status_code=404,detail=f"session not found: {session_id}")

    if state.get("state") == "active" and not state.get("ended_at"):
        raise HTTPException(status_code=409,detail="active session cannot be deleted")

    store.delete_session(session_id=session_id)

    return {
        "status":"ok",
        "deleted":True,
        "session_id":session_id,
    }

@app.get("/session/knowledge-base")
async def get_configured_knowledge_base() -> dict[str, Any]:
    """初始页面读取下次通话配置和当前通话锁定的知识库。"""

    return await _session_knowledge_base_state(None)

@app.get("/sessions/{session_id}/knowledge-base")
async def get_session_knowledge_base(session_id:str) -> dict[str,Any]:
    """读取指定 Session 锁定的知识库及当前预选配置"""

    return await _session_knowledge_base_state(session_id)

@app.put("/session/knowledge-base")
async def put_session_knowledge_base(payload:SessionKnowledgeBasePayload)->dict[str,Any]:
    """选择下一场通话使用的知识库，不改变已锁定的活动会话。"""

    #获取当前知识库信息
    kb=await _knowledge_base_detail(kb_id=payload.kb_id)

    #判断当前知识库是否ready
    ready_response=await rag_gateway.get(f"/v1/knowledge-bases/{kb['kb_id']}/ready")
    if ready_response.status_code >= 400:
        raise HTTPException(
            status_code=ready_response.status_code,
            detail="knowledge base is not ready",
        )

    #写入JSON配置：保存为新session的默认知识库
    metadata_store.set_session_config(
        "knowledge_base",
        {
            "kb_id":kb["kb_id"],
            "name":kb["name"],
        }
    )

    #返回最新选择状态
    return await _session_knowledge_base_state(None)

@app.get("/sessions")
async def list_sessions()->dict[str,Any]:
    """列出所有Sessions"""

    sessions=store.list_sessions()
    return {
        "sessions":sessions,
        "total":len(sessions),
    }

@app.get("/sessions/{session_id}")
async def session_detail(session_id:str)->dict[str,Any]:
    """读取指定session的状态和摘要"""

    try:
        return store.read_session_detail(session_id=session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc)
        )from exc

@app.get("/sessions/{session_id}/export")
async def export_session(session_id:str)->dict[str,Any]:
    """显式导出一个会话的完整数据"""

    try:
        return store.export_session(session_id=session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc)
        )from exc

#============================== /rag/... =========================
@app.get("/rag/ready")
async def rag_ready() -> JSONResponse:
    """返回内部 RAG 服务 ready 状态。"""

    return _json_response(await rag_gateway.get("/v1/readyz"))

@app.get("/rag/knowledge-bases")
async def rag_knowledge_bases()->JSONResponse:
    """读取所有知识库"""

    return _json_response(await rag_gateway.get("/v1/knowledge-bases"))

@app.post("/rag/knowledge-bases")
async def rag_create_knowledge_bases(payload:KnowledgeBasePayload)->JSONResponse:
    """根据公司和岗位创建面试资料库。"""

    company = (payload.company or "").strip()
    role = (payload.role or "").strip()
    if not company or not role:
        raise HTTPException(status_code=422, detail="创建岗位资料库必须填写公司名称和岗位名称")

    return _json_response(await rag_gateway.post_json(
        "/v1/knowledge-bases",
        payload={
            "name": f"{company} · {role}",
            "description": (payload.description or "").strip(),
        },
        )
    )

@app.get("/rag/knowledge-bases/{kb_id}")
async def rag_knowledge_base_detail(kb_id: str)->JSONResponse:
    """返回单个知识库详情。"""

    return _json_response(await rag_gateway.get(f"/v1/knowledge-bases/{kb_id}"))

@app.patch("/rag/knowledge-bases/{kb_id}")
async def rag_patch_knowledge_base(kb_id:str,payload:KnowledgeBasePayload) ->JSONResponse:
    """修改单个知识库"""

    if kb_id == "default":
        raise HTTPException(status_code=409, detail="个人简历资料库的名称和用途不可修改")

    company = (payload.company or "").strip()
    role = (payload.role or "").strip()
    update_payload = payload.model_dump(include={"name", "description"}, exclude_none=True)
    if company or role:
        if not company or not role:
            raise HTTPException(status_code=422, detail="公司名称和岗位名称必须同时填写")
        update_payload["name"] = f"{company} · {role}"

    return _json_response(await rag_gateway.patch_json(
        f"/v1/knowledge-bases/{kb_id}",
        payload=update_payload,
    ))

@app.delete("/rag/knowledge-bases/{kb_id}")
async def rag_delete_knowledge_base(kb_id:str,session_id:str|None=None)->JSONResponse:
    """删除单个知识库"""

    #判断要删除的知识库是否是当前在用的
    active=_active_knowledge_base(session_id) if session_id is not None else None
    if active and active.get("kb_id")==kb_id:
        return JSONResponse(
            envelope(
                status="error",
                error={"type": "KnowledgeBaseLocked", "message": "当前通话正在使用该知识库"},
            ),
            status_code=409,
        )

    #删除该知识库
    response=await rag_gateway.delete(f"/v1/knowledge-bases/{kb_id}")

    #删除之后，如果后台仍将它选作下一场通话的知识库，那么自动切回默认知识库
    configured=metadata_store.get_session_config("knowledge_base")
    if response.body.get("status")=="ok" and configured.get("kb_id")==kb_id:
        default_kb=await _knowledge_base_detail("default")
        metadata_store.set_session_config(
            "knowledge_base",
            {
                "kb_id":default_kb["kb_id"],
                "name":default_kb["name"],
            }
        )

    return _json_response(response)

@app.get("/rag/knowledge-bases/{kb_id}/ready")
async def rag_knowledge_base_ready(kb_id: str) -> JSONResponse:
    """预热知识库 engine。"""

    return _json_response(await rag_gateway.get(f"/v1/knowledge-bases/{kb_id}/ready"))


@app.get("/rag/knowledge-bases/{kb_id}/documents")
async def rag_kb_documents(
    kb_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> JSONResponse:
    """返回指定知识库文档列表"""

    return _json_response(await rag_gateway.get_documents(
        f"/v1/knowledge-bases/{kb_id}/documents",
        params={"page": page, "page_size": page_size},
    ))

@app.post("/rag/knowledge-bases/{kb_id}/documents/text")
async def rag_kb_documents_text(kb_id: str, payload:TextDocumentRequest)->JSONResponse:
    """向指定知识库上传文本"""

    response=await rag_gateway.post_json(
        f"/v1/knowledge-bases/{kb_id}/documents/text",
        payload=payload.model_dump(exclude_none=True)
    )
    #标记当前知识库概览过期：因为文档更新
    _mark_overview_stale_if_ok(response, kb_id, reason="documents_text_imported")
    return _json_response(response)


@app.get("/rag/knowledge-bases/{kb_id}/documents/{document_id}")
async def rag_kb_document_detail(kb_id: str, document_id: str) -> JSONResponse:
    """返回指定知识库文档详情。"""

    return _json_response(
        await rag_gateway.get_document_detail(f"/v1/knowledge-bases/{kb_id}/documents/{document_id}")
    )


@app.post("/rag/knowledge-bases/{kb_id}/documents/files")
async def rag_kb_documents_files(
    kb_id: str,
    files: list[UploadFile] = UPLOAD_FILES,
    pdf_password: str | None = Form(default=None),
) -> JSONResponse:
    """向指定知识库上传文档"""

    response=await rag_gateway.post_files(
        f"/v1/knowledge-bases/{kb_id}/documents/files",
        files=files,
        pdf_password=pdf_password,
    )

    #标记当前知识库概览过期：因为文档更新
    _mark_overview_stale_if_ok(response, kb_id, reason="documents_files_uploaded")

    return _json_response(response)

@app.get("/rag/knowledge-bases/{kb_id}/documents/{document_id}/source")
async def rag_kb_document_source(
    kb_id:str,
    document_id:str,
    disposition:str=Query(default="inline", pattern="^(inline|attachment)$"), #只能在incline/attachment里选
)->Response:
    """获取制定知识库的制定文档原文件
    inline:浏览器直接预览;attachment:作为附件下载"""

    response=await rag_gateway.get_file(
        path=f"/v1/knowledge-bases/{kb_id}/documents/{document_id}/source",
        params={"disposition":disposition}
    )
    #失败：返回JSON错误
    if response.error_body is not None:
        return JSONResponse(
            content=response.error_body,
            status_code=response.status_code,
        )
    #正确处理
    return Response(
        content=response.body,
        status_code=response.status_code,
        headers=response.headers
    )

@app.get("/rag/knowledge-bases/{kb_id}/jobs/{job_id}")
async def rag_kb_job(kb_id: str, job_id:str,background_tasks:BackgroundTasks)->JSONResponse:
    """查询指定知识库的制定入库信息"""

    response=await rag_gateway.get_job(f"/v1/knowledge-bases/{kb_id}/jobs/{job_id}")
    _schedule_overview_generation_after_completed_job(
        response,
        kb_id=kb_id,
        job_id=job_id,
        background_tasks=background_tasks,
    )
    return _json_response(response)

@app.delete("/rag/knowledge-bases/{kb_id}/documents/{document_id}")
async def rag_kb_delete_document(
    kb_id: str,
    document_id: str,
    delete_llm_cache: bool = Query(default=False),
) -> JSONResponse:
    """删除指定知识库文档。"""

    result = await rag_gateway.delete(
        f"/v1/knowledge-bases/{kb_id}/documents/{document_id}",
        params={"delete_llm_cache": delete_llm_cache},
    )
    _mark_overview_stale_if_ok(result, kb_id, reason="document_deleted")
    return _json_response(result)

@app.post("/rag/knowledge-bases/{kb_id}/query/context")
async def rag_kb_query_context(kb_id:str,payload:QueryRequest)->JSONResponse:
    """查询指定知识库上下文"""

    response=await rag_gateway.post_json(
        f"/v1/knowledge-bases/{kb_id}/query/context",
        payload=payload.model_dump(exclude_none=True),
    )
    return _json_response(response)

@app.post("/rag/knowledge-bases/{kb_id}/query/data")
async def rag_kb_query_data(kb_id:str,payload:QueryRequest)->JSONResponse:
    """查询指定知识库结构化证据"""

    response=await rag_gateway.post_json(
        f"/v1/knowledge-bases/{kb_id}/query/data",
        payload=payload.model_dump(exclude_none=True),
    )
    return _json_response(response)

@app.post("/rag/knowledge-bases/{kb_id}/query/answer")
async def rag_kb_query_answer(kb_id:str,payload:QueryRequest)->JSONResponse:
    """查询指定知识库并且生成答案"""

    response=await rag_gateway.post_json(
        f"/v1/knowledge-bases/{kb_id}/query/answer",
        payload=payload.model_dump(exclude_none=True),
    )
    return _json_response(response)

@app.post("/rag/session-query/context")
async def rag_session_query_context(session_id:str,payload:QueryRequest)->JSONResponse:
    """按照当前会话锁定的知识库查询上下文"""

    kb=await _effective_session_knowledge_base(session_id)
    return await rag_kb_query_context(kb["kb_id"],payload=payload)

@app.post("/rag/session-query/data")
async def rag_session_query_data(session_id:str,payload: QueryRequest)->JSONResponse:
    """按当前会话锁定知识库查询结构化数据。"""

    kb = await _effective_session_knowledge_base(session_id)
    return await rag_kb_query_data(kb["kb_id"], payload=payload)

@app.get("/rag/config")
async def get_rag_config()->dict[str,Any]:
    """获取当前语音链路的 RAG 配置"""

    config=load_rag_client_settings(settings.user_data_dir)
    return envelope(data={"config":public_rag_client_config(config)})

@app.put("/rag/config")
async def put_rag_config(payload: RagConfigPayload) -> dict[str, Any]:
    """更新语音链路 RAG 配置"""
    updates = payload.model_dump(exclude_none=True)

    merge_runtime_rag_config(updates, settings.user_data_dir)
    configured = load_rag_client_settings(settings.user_data_dir)

    return envelope(
        status="ok",
        data={"config":public_rag_client_config(configured)}
    )

@app.get("/rag/knowledge-bases/{kb_id}/context/overview")
async def rag_kb_context_overview(kb_id:str)->dict[str,Any]:
    """读取指定知识库的固定上下文概览。"""

    #确保知识库真实存在
    await _knowledge_base_detail(kb_id)
    return envelope(
        data={
            "kb_id": kb_id,
            "content": store.read_knowledge_overview(kb_id),
            "meta": store.read_knowledge_overview_meta(kb_id),
        }
    )

@app.put("/rag/knowledge-bases/{kb_id}/context/overview")
async def put_rag_kb_context_overview(kb_id: str, payload: TextPayload) -> dict[str, Any]:
    """手动覆盖指定知识库的固定上下文概览。"""

    kb=await _knowledge_base_detail(kb_id)

    content=payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="content cannot be empty")

    store.write_knowledge_overview(
        kb_id=kb_id,
        content=content,
        stale=False,
        reason="manual_update", #手动更新
        source="manual",
    )
    return envelope(
        data={
            "kb_id":kb["kb_id"],
            "kb_name":kb["name"],
            "content":store.read_knowledge_overview(kb_id),
            "meta":store.read_knowledge_overview_meta(kb_id)
        }
    )
#===========================辅助函数==============================
def _json_response(result: GatewayResponse) -> JSONResponse:
    """把 RagGateway 封装的下游响应，转换成 FastAPI 可以直接返回给前端的 JSONResponse"""

    return JSONResponse(result.body, status_code=result.status_code)


def _model_pending_reconnect(active_voice:Any)->bool:
    """判断当前配置是否还未通过重连生效"""

    if not isinstance(active_voice,dict):
        return False

    configured=public_voice_config(load_voice_settings(settings.user_data_dir),effective="next_session")
    #当前通话的语音配置，是否与后台为下一次通话保存的配置不同
    return _voice_config_identity(configured)!=_voice_config_identity(active_voice)

def _voice_config_identity(config:dict[str,Any])->dict[str,Any]:
    """去掉展示型effective字段之后，比较voice配置"""

    identity:dict[str,Any]={}

    for section,values in config.items():
        if isinstance(values,dict):
            identity[section]={key:value for key,value in values.items() if key!="effective"}
    return identity


async def _session_knowledge_base_state(session_id:str|None=None)->dict[str,Any]:
    """返回session中知识库配置和锁定状态"""

    #管理后台选中的知识库，下一场新通话准备用
    configured=await _configured_knowledge_base()

    #传入空，返回默认知识库
    if session_id is None:
        return {
            "configured":{"kb_id":configured["kb_id"],"name":configured["name"]},
            "active_session":None,
            "locked":False,
            "pending_reconnect":False
        }

    #当前正在进行的通话已经锁定的知识库
    active=_active_knowledge_base(session_id)

    return {
        "configured":{"kb_id":configured["kb_id"],"name":configured["name"]},
        "active_session":active,
        "locked":active is not None,
        #判断通话时为True：知识库设置已更新，将在下一次通话中生效。
        "pending_reconnect":bool(active and active.get("kb_id")!=configured["kb_id"]),
    }

async def _configured_knowledge_base()->dict[str,Any]:
    """返回后台选中的知识库，如果不存在则回退 default。"""

    configured=metadata_store.get_session_config("knowledge_base")
    kb_id=str(configured.get("kb_id") or "default")

    try:
        return await _knowledge_base_detail(kb_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return await _knowledge_base_detail("default")

async def _effective_session_knowledge_base(session_id:str)->dict[str,Any]:
    """获取当前查询实际应用的知识库"""

    active=_active_knowledge_base(session_id)
    if active and active.get("kb_id"):
        return {"kb_id": str(active["kb_id"]), "name": str(active.get("name") or active["kb_id"])}

    #没有当前通话在用的知识库，找后台选中的知识库
    return await _configured_knowledge_base()

async def _knowledge_base_detail(kb_id:str)->dict[str,Any]:
    """通过内部RAG服务读取知识库信息"""

    response=await rag_gateway.get(f"/v1/knowledge-bases/{kb_id}")

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail=f"knowledge base not found: {kb_id}")
    if response.body.get("status") != "ok" or not isinstance(response.body.get("data"), dict):
        error = response.body.get("error") or {}
        raise HTTPException(status_code=response.status_code, detail=error.get("message") or "knowledge base unavailable")

    return response.body["data"]

def _active_knowledge_base(session_id:str)->dict[str,Any]|None:
    """返回当前通话未结算的知识库"""

    state=store.read_runtime_state(session_id)
    if not state:
        return None
    if state.get("state")!="active" or state.get("ended_at"):
        return None

    active_session=state.get("active_session")
    if isinstance(active_session, dict):
        knowledge_base=active_session.get("knowledge_base")
        if isinstance(knowledge_base, dict):
            return knowledge_base

    # 兼容当前 Session 顶层保存 kb_id 的结构
    kb_id = state.get("kb_id")
    if not isinstance(kb_id, str) or not kb_id:
        return None

    return {
        "kb_id": kb_id,
        "name": state.get("kb_name"),
    }

def _mark_overview_stale_if_ok(response:GatewayResponse,kb_id:str,*,reason:str)->None:
    """文档更新成功之后，标记当前知识库概览过期"""

    if response.body.get("status")=="ok":
        #标记知识库概览过期
        store.mark_knowledge_overview_stale(kb_id=kb_id,reason=reason)

def _schedule_overview_generation_after_completed_job(
    response:GatewayResponse,
    *,
    kb_id:str,
    job_id:str,
    background_tasks:BackgroundTasks
)->None:
    """后台索引建立任务完成且文档构建成功时，安排后台生成新的知识库概览"""

    if response.body.get("status")!="ok":
        return

    #检查Job是否真正处理出新文档
    data=response.body.get("data")
    if not isinstance(data, dict) or not _job_has_new_processed_documents(data):
        return

    #避免同一个job重复生成当前概览:
    # 1.当前元数据中source_job_id就是job_id
    # 2.overview没有过期
    meta=store.read_knowledge_overview_meta(kb_id=kb_id)
    if meta.get("source_job_id")==job_id and not meta.get("stale"):
        return

    #生成任务放入后台
    background_tasks.add_task(_generate_overview_for_completed_job,kb_id=kb_id,job_id=job_id)

    #job响应中标记“已安排生成”
    data["overview_generation"] = {
        "scheduled": True,
        "trigger": "index_completed",
        "job_id": job_id,
    }

def _job_has_new_processed_documents(data:dict[str,Any]) ->bool:
    """判断任务是否已结束且至少有一个文档构建成功。"""

    status=str(data.get("status") or "").lower()
    #不在认可的状态范围内
    if status not in {"processed","partial_failed"}:
        return False

    documents=data.get("documents")
    if not isinstance(documents, list):
        return False

    for document in documents:
        if not isinstance(document, dict):
            continue
        index_status=str(document.get("index_status") or document.get("job_document_status") or "").lower()
        if index_status=="processed":
            return True
    return False

async def _generate_overview_for_completed_job(kb_id:str,job_id:str)->None:
    """为已完成索引任务生成知识库概览。"""

    try:
        kb = await _knowledge_base_detail(kb_id)
        raw_overview = await _raw_knowledge_overview(kb_id)
        await KnowledgeOverviewGenerator(
            store=store,
            settings=load_context_model_settings(settings.user_data_dir),
        ).generate(
            kb_id=kb["kb_id"],
            kb_name=kb["name"],
            raw_overview=raw_overview,
            rag_settings=load_rag_client_settings(settings.user_data_dir),
            reason="index_completed",
            source_job_id=job_id,
        )
    except Exception as exc:
        # 后台概览生成失败不影响任务查询接口，但必须保留可诊断信息。
        logger.exception(
            "knowledge_overview.background_generation_failed",
            extra={
                "kb_id": kb_id,
                "job_id": job_id,
                "error": str(exc),
            },
        )
        return

async def _raw_knowledge_overview(kb_id:str)->dict[str,Any]:
    """读取内部 RAG Core 的结构化知识库概览。"""

    result = await rag_gateway.get(
        f"/v1/knowledge-bases/{kb_id}/overview",
        params={
            "entity_limit": 20,
            "relation_limit": 12,
            "document_limit": 20,
            "topic_limit": 12,
        },
    )
    if result.body.get("status") != "ok":
        error = result.body.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        raise RuntimeError(
            message
            or f"RAG Core overview request failed with HTTP {result.status_code}"
        )

    data = result.body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("RAG Core returned invalid overview data")
    return data

def _drop_masked_secret_updates(payload:dict[str,Any])->dict[str,Any]:
    """移除前端原样回传的密钥掩码，避免覆盖真实密钥。"""

    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            #遇到嵌套对象，递归处理
            nested = _drop_masked_secret_updates(value)
            if nested:
                cleaned[key] = nested
            continue
        #跳过api_key
        if key in _SECRET_FIELDS and is_masked_secret(value):
            continue
        cleaned[key] = value
    return cleaned

def _validate_model_config_updates(updates:dict[str,Any])->None:
    """校验当前模型配置更新请求字段"""

    for path, value in _walk_model_update_values(updates):
        #不准为空字符串
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(status_code=422, detail=f"{path} cannot be empty")
        #限制stt供应商
        if path == "voice.stt.provider" and value.strip().lower() != "volcengine_bigmodel":
            raise HTTPException(status_code=422, detail="voice.stt.provider must be volcengine_bigmodel")
        #限制tts供应商
        if path == "voice.tts.provider" and value.strip().lower()!="dashscope_realtime":
            raise HTTPException(status_code=422, detail="voice.tts.provider must be dashscope_realtime")
        #检查url
        if path.endswith(".base_url") and not _is_http_url(value):
            raise HTTPException(status_code=422, detail=f"{path} must be an http(s) URL")

def _validate_context_model_updates(updates: dict[str, Any]) -> None:
    """校验 Context Model 字符串字段。"""

    for key in ("model", "base_url", "api_key"):
        value = updates.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise HTTPException(
                status_code=422,
                detail=f"{key} cannot be empty",
            )

    base_url = updates.get("base_url")
    if base_url is not None and not _is_http_url(base_url):
        raise HTTPException(
            status_code=422,
            detail="base_url must be an http(s) URL",
        )

def _walk_model_update_values(payload: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    """展开模型配置更新字段。
    例子：
        {
        "voice": {
            "stt": {
                "provider": "volcengine_bigmodel"
            },
            "llm": {
                "base_url": "https://example.com/v1"
            }
        }
    }
    变成：
        [
        ("voice.stt.provider", "volcengine_bigmodel"),
        ("voice.llm.base_url", "https://example.com/v1"),
        ]
    """

    items: list[tuple[str, Any]] = []
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            items.extend(_walk_model_update_values(value, path))
        else:
            items.append((path, value))
    return items

def _is_http_url(value: str) -> bool:
    """判断字符串是否是 http(s) URL。"""

    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

def _deep_merge_dict(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """递归合并配置字典。"""

    result = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result
