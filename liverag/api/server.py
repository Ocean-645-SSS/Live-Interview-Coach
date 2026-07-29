"""前端的产品管理 API

前端
  ↓
api/server.py（9821，产品管理层）
  ↓ RagGateway
rag/server.py（9721，RAG 核心层）
  ↓
LightRAG、文档存储、索引与查询"""


import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from liverag.api.rag_gateway import RagGateway,GatewayResponse,envelope
from liverag.config.settings import (
    RagClientSettings,
    RagToolMode,
    is_masked_secret,
    load_app_settings,
    load_context_model_settings,
    load_environment,
    load_rag_client_settings,
    load_voice_settings,
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
from liverag.context.store import ContextStore
from liverag.runtime.paths import build_runtime_paths
from liverag.rag.metadata_store import MetadataStore
from liverag.rag.schemas import QueryRequest,TextDocumentRequest
from liverag.rag.service import wait_for_rag_ready


load_environment() #导入.env.local
settings=load_app_settings()
paths=build_runtime_paths(settings.user_data_dir)
metadata_store=MetadataStore(paths.db_file,paths.rag_knowledge_bases_dir)
metadata_store.initialize()
store=ContextStore(paths)
store.initialize()
rag_gateway = RagGateway(settings)

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

app=FastAPI(title="LiveRAG Agent API",version="0.1.0",lifespan=lifespan)

UPLOAD_FILES = File(...)


class TextPayload(BaseModel):
    """通用文本更新请求。"""

    content: str


class SessionKnowledgeBasePayload(BaseModel):
    """会话知识库选择请求。"""
    kb_id: str

class KnowledgeBasePayload(BaseModel):
    """知识库创建/更新请求"""

    name:str|None=None
    description:str|None=None


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

@app.get("/sessions/{session_id}/knowledge-base")
async def get_session_knowledge_base(session_id:str) -> dict[str,Any]:
    """读取指定 Session 锁定的知识库及当前预选配置"""

    return await _session_knowledge_base_state(session_id)

@app.put("/session/knowledge-base")
async def put_session_knowledge_base(payload:SessionKnowledgeBasePayload)->dict[str,Any]:
    """管理页面选择下一场通话要使用的知识库

    前端选择知识库 B
    ↓
    PUT /session/knowledge-base
    ↓
    确认知识库 B 存在
    ↓
    预热知识库 B
    ↓
    保存 configured = B
    ↓
    下一场新通话读取 B
    ↓
    新 Session 锁定 B"""

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
    return {
        "configured":{
            "kb_id":kb["kb_id"],
            "name":kb["name"],
        }
    }

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
    """添加知识库"""

    return _json_response(await rag_gateway.post_json(
        "/v1/knowledge-bases",
        payload=payload.model_dump(exclude_none=True)
        )
    )

@app.get("/rag/knowledge-bases/{kb_id}")
async def rag_knowledge_base_detail(kb_id: str)->JSONResponse:
    """返回单个知识库详情。"""

    return _json_response(await rag_gateway.get(f"/v1/knowledge-bases/{kb_id}"))

@app.patch("/rag/knowledge-bases/{kb_id}")
async def rag_patch_knowledge_base(kb_id:str,payload:KnowledgeBasePayload) ->JSONResponse:
    """修改单个知识库"""

    return _json_response(await rag_gateway.patch_json(
        f"/v1/knowledge-bases/{kb_id}",
        payload=payload.model_dump(exclude_none=True)   #排除所有None的字段
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


@app.post("/rag/knowledge-bases/{kb_id}/documents/files}")
async def rag_kb_documents_files(kb_id: str, files:list[UploadFile]=UPLOAD_FILES)->JSONResponse:
    """向指定知识库上传文档"""

    response=await rag_gateway.post_files(
        f"/v1/knowledge-bases/{kb_id}/documents/files",
        files=files,
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


async def _session_knowledge_base_state(session_id:str)->dict[str,Any]:
    """返回session中知识库配置和锁定状态"""

    #管理后台选中的知识库，下一场新通话准备用
    configured=await _configured_knowledge_base()
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
    """返回后台选中的知识库，如果没有则返回default"""

    #获取新session的知识库配置
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

#TODO
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
    except Exception:
        # 后台概览生成失败不影响任务查询接口；具体失败会在 generator 内部写入 meta。
        return 
    
