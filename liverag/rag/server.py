"""内部LightRAG Core Service，多知识库物理隔离实现"""

import hashlib
import logging
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from lightrag.utils import generate_track_id
from pydantic import BaseModel, Field

from liverag.rag.doc_parser import parse_file_content
from liverag.rag.engine import RagEngineError, RagQueryTimeoutError
from liverag.rag.engine_manager import RagEngineManager
from liverag.rag.rag_settings import RAGSettings
from liverag.rag.schemas import Envelope, QueryRequest, TextDocumentRequest
from liverag.rag.metadata_store import DEFAULT_KB_ID,DEFAULT_KB_NAME

settings=RAGSettings()
manager=RagEngineManager(settings)
logger=logging.getLogger("liverag.rag.server")

class KnowledgeBaseCreateRequest(BaseModel):
    """创建知识库请求模型"""

    name:str=Field(min_length=1)
    description:str=""

class KnowledgeBasePatchRequest(BaseModel):
    """更新知识库请求模型"""

    name:str | None = None
    description:str | None = None


class DocumentAlreadyExistsError(ValueError):
    """前端指定的文档 ID 已存在。"""


@asynccontextmanager #异步生命周期管理器
async def lifespan(app:FastAPI):
    """启动和关闭LightRAG engine manager"""
    await manager.initialize()
    try:
        yield  #生命周期的分界点
    finally:
        await manager.finalize()


def envelope(
    *,
    request_id:str,
    data:dict[str, Any] | list[Any] | BaseModel | None = None,
    metrics:dict[str, Any] | None = None,
    error:dict[str, Any] | None = None,
    status:str="ok"
)->dict[str,Any]:
    """统一响应"""

    if isinstance(data,BaseModel):
        data=data.model_dump()

    return Envelope(
        request_id=request_id,
        status=status,
        data=data,
        metrics=metrics or {},
        error=error,
    ).model_dump()


def key_matches(candidate: str | None, expected: str) -> bool:
    if candidate is None:
        return False
    return secrets.compare_digest(candidate, expected)


async def require_api_key(
        x_api_key:str | None= Header(default=None,alias="X-API-KEY"),
        authorization:str | None= Header(default=None)
    ):
    """校验内部RAG api_key，通常在Depends()中使用，及时止损
    验证成功 → 正常返回
    验证失败 → 抛出异常"""

    #配置了api_key才开启鉴权
    expected=settings.api_key
    if not expected:
        return

    bearer="" #保存从Authorization请求头解析出来的Bearer Token
    if authorization and authorization.lower().startswith("bearer "):
        bearer=authorization.split(" ",1)[1].strip()

    if not(key_matches(x_api_key,expected) or key_matches(bearer,expected)):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},)


"""app入口"""
app=FastAPI(
    title="LightRAG Core Service",
    version="0.2.0",
    description="A lightweight multi-knowledge-base service around lightrag-hku core APIs.",
    lifespan=lifespan
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request:Request,exc:HTTPException):
    """把 FastAPI HTTPException 转换成统一错误 envelope。"""

    request_id=str(uuid.uuid4())
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content=envelope(
            request_id=request_id,
            status="error",
            error={
                "type":"HTTPException",
                "message":str(exc.detail),
            },
        ),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request:Request,
    exc:RequestValidationError,
):
    """把请求体、路径和查询参数校验错误转换成统一 envelope。"""

    request_id=str(uuid.uuid4())
    return JSONResponse(
        status_code=422,
        content=envelope(
            request_id=request_id,
            status="error",
            error={
                "type":"RequestValidationError",
                "message":"请求参数校验失败",
                "details":jsonable_encoder(exc.errors()),
            },
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request:Request,exc:Exception):
    """兜底异常响应"""

    request_id=str(uuid.uuid4()) #生成请求追踪ID

    logger.exception( #记录日志
        "RAG Core 请求发生未处理异常",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "error_type": type(exc).__name__,
        },
    )

    return JSONResponse(
        status_code=500,  #服务器内部错误
        content=envelope( #响应体
            request_id=request_id,
            status="error",
            error={
                "type":"InternalServerError",
                "message":"RAG Core 处理请求时发生内部错误",
            },
        ),
    )

#除字母、数字、点、下划线和连字符以外的连续字符，都替换成 _
_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._\-\u4e00-\u9fff]+")
#为了文件拒绝一些名称
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

@app.get("/v1/healthz")
async def healthz()->dict[str,Any]:
    """健康检查：RAG Core 这个 HTTP 服务进程本身是否还活着、能不能正常响应请求"""

    return envelope(request_id=str(uuid.uuid4()),data={"service":"ok"})


@app.get("/v1/readyz",dependencies=[Depends(require_api_key)])
async def readyz()->dict[str,Any]:
    """检查RAG Core Service是否准备就绪"""

    state=await manager.ready_state()
    ready=bool(state["initialized"] and state["provider_configured"])
    return envelope(request_id=str(uuid.uuid4()),data={"ready":ready,**state},)


#===========================知识库接口==================================
@app.get("/v1/knowledge-bases",dependencies=[Depends(require_api_key)])
async def knowledge_bases()->dict[str,Any]:
    """列出所有知识库"""

    items=manager.kb_store.list()
    return envelope(request_id=str(uuid.uuid4()),data={"knowledge_bases":items,"total":len(items)})


@app.post("/v1/knowledge-bases", dependencies=[Depends(require_api_key)])
async def create_knowledge_base(request:KnowledgeBaseCreateRequest)->dict[str,Any]:
    """创建知识库"""

    request_id=str(uuid.uuid4())
    try:
       meta = manager.kb_store.create(
            name=request.name,
            description=request.description,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    return envelope(
        request_id=request_id,
        data=manager.kb_store.public_detail(meta.kb_id),
    )


@app.delete("/v1/knowledge-bases/{kb_id}",dependencies=[Depends(require_api_key)])
async def delete_knowledge_base(kb_id:str)->dict[str,Any]:
    """删除知识库"""

    request_id=str(uuid.uuid4())
    if kb_id == DEFAULT_KB_ID:
        raise HTTPException(status_code=409, detail="default knowledge base cannot be deleted")
    try:
        await manager.delete_knowledge_base(kb_id)
        return envelope(request_id=request_id,data={"deleted": True, "kb_id": kb_id})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/knowledge-bases/{kb_id}/ready", dependencies=[Depends(require_api_key)])
async def knowledge_base_ready(kb_id:str)->dict[str,Any]:
    """预热知识库，并返回知识库ready状态"""

    request_id=str(uuid.uuid4())
    try:
        engine=await manager.get_engine(kb_id)
        return envelope(request_id=request_id,data=engine.ready_state())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/v1/knowledge-bases/{kb_id}",dependencies=[Depends(require_api_key)])
async def knowledge_base_detail(kb_id:str)->dict[str,Any]:
    """获取知识库详情"""

    request_id=str(uuid.uuid4())
    try:
        detail=manager.kb_store.public_detail(kb_id)
    except KeyError as exc:
        message=str(exc.args[0] if exc.args else "知识库不存在")
        raise HTTPException(status_code=404, detail=message) from exc

    return envelope(request_id=request_id,data=detail)
 
@app.patch("/v1/knowledge-bases/{kb_id}",dependencies=[Depends(require_api_key)])
async def patch_knowledge_base(kb_id:str,request:KnowledgeBasePatchRequest) -> dict[str,Any]:
    """更新某个知识库元数据"""

    request_id=str(uuid.uuid4())
    try:
        meta=manager.kb_store.update(kb_id,name=request.name,description=request.description)
        engine=manager.get_engine(kb_id)
        #更新engine配置
        engine.settings=manager._settings_for(meta)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        return envelope(
            request_id=request_id,
            status="error",
            error={"type": "KnowledgeBaseValidationError", "message": str(exc)},
        )

@app.post("/v1/knowledge-bases/{kb_id}/query/context",dependencies=[Depends(require_api_key)])
async def query_context(kb_id:str,request:QueryRequest):
    """只查询指定知识库的上下文"""

    request_id=str(uuid.uuid4())
    started=time.perf_counter()

    try:
        engine=await manager.get_engine(kb_id)
        #查询
        data,metrics=await engine.query_context(
            query=request.query,
            profile=request.profile,
            options=request.merged_options(), #合并QueryOptions
            conversation=request.merged_conversation() #合并ConversationOptions
        )
        #计算耗时
        metrics["request_total_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return envelope(request_id=request_id,data=data,metrics=metrics)
    except KeyError as exc:
        message = str(exc.args[0]) if exc.args else "知识库不存在"
        raise HTTPException(
            status_code=404,
            detail=message,
        ) from exc
    except RagQueryTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except RagEngineError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/knowledge-bases/{kb_id}/query/answer",dependencies=[Depends(require_api_key)])
async def query_answer(kb_id:str,request:QueryRequest):
    """只查询指定知识库并生成答案"""

    request_id=str(uuid.uuid4())

    try:
        engine=await manager.get_engine(kb_id)
        #查询
        data,metrics=await engine.query_answer(
            query=request.query,
            profile=request.profile,
            options=request.merged_options(), #合并QueryOptions
            conversation=request.merged_conversation() #合并ConversationOptions
        )
        return envelope(request_id=request_id,data=data,metrics=metrics)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc.args[0]) if exc.args else "知识库不存在",
        ) from exc
    except RagQueryTimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail=str(exc),
        ) from exc
    except RagEngineError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

@app.post("/v1/knowledge-bases/{kb_id}/query/data", dependencies=[Depends(require_api_key)])
async def query_data(kb_id: str, request: QueryRequest) -> dict[str, Any]:
    """查询指定知识库的结构化数据"""

    request_id = str(uuid.uuid4())
    try:
        engine = await manager.get_engine(kb_id)
        data, metrics = await engine.query_data(
            request.query,
            request.profile,
            request.merged_options(),
            request.merged_conversation(),
        )
        return envelope(request_id=request_id, data=data, metrics=metrics)
    
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        return envelope(
            request_id=request_id,
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )

@app.get("/v1/knowledge-bases/{kb_id}/overview",dependencies=[Depends(require_api_key)])
async def knowledge_overview(
    kb_id:str,
    entity_limit:int=Query(default=20, ge=0, le=100),
    relation_limit:int=Query(default=12, ge=0, le=100),
    document_limit:int=Query(default=10, ge=0, le=100),
    topic_limit:int=Query(default=8, ge=0, le=100),
)->dict[str,Any]:
    """读取指定知识库概览"""

    request_id=str(uuid.uuid4())

    try:
        engine=await manager.get_engine(kb_id)
        data=await engine.knowledge_overview(
            entity_limit=entity_limit,
            relation_limit=relation_limit,
            document_limit=document_limit,
            topic_limit=topic_limit,
        )
        return envelope(request_id=request_id,data=data)

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        return envelope(
            request_id=request_id,
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
#=============================documents 接口============================
@app.post("/v1/knowledge-bases/{kb_id}/documents/files",dependencies=[Depends(require_api_key)])
async def documents_files(
    kb_id:str,
    files:Annotated[
        list[UploadFile],
        File(json_schema_extra={"items": {"type": "string", "format": "binary"}}),
    ],
    pdf_password: str | None = Form(default=None),
)->dict[str,Any]:
    """批量向指定知识库上传文档、解析并且导入"""

    request_id=str(uuid.uuid4()) #请求id

    try:
        #创建上传任务
        meta=manager.kb_store.get(kb_id) #确认知识库存在
        track_id=generate_track_id("insert")
        #创建上传任务
        manager.metadata.create_job(job_id=track_id,kb_id=kb_id,total_files=len(files))

        #逐个保存和解析文件
        parsed_texts:list[str]=[]
        parsed_sources:list[str]=[]
        parsed_document_ids:list[str]=[]
        parsed_count=0
        failed_count=0
        file_payloads: list[dict[str, Any]] = [] #保存文件的信息
        errors: list[dict[str, Any]] = [] #保存失败的信息

        #汇总解析成功的文件
        for uploaded in files:
            #读取原始二进制文件
            raw=await uploaded.read()
            #清理文件名
            filename=_safe_filename(uploaded.filename or "uploaded_file")
            #获得扩展名
            extension=Path(filename).suffix.lower() or ".txt"
            #生成文档ID
            document_id=_new_document_id()
            #保存原文件
            source_path=_write_source_file(
                kb_id,
                document_id,
                filename,
                raw
            )
            #在数据库创建文档记录
            manager.metadata.create_document(
                document_id=document_id,
                kb_id=kb_id,
                original_filename=filename,
                source_file_path=source_path,
                source_file_size=len(raw),
                source_sha256=_sha256(raw), #文件哈希
                content_type=uploaded.content_type or "application/octet-stream",
                extension=extension
            )

            #解析文件内容
            try:
                text = parse_file_content(raw, extension, password=pdf_password)
            except ValueError as exc:
                failed_count+=1
                error_msg=str(exc)
                #修改文档状态
                manager.metadata.mark_document_failed(
                    kb_id=kb_id,
                    document_id=document_id,
                    error_msg=error_msg
                )
                #将当前文档和上传任务关联起来
                manager.metadata.link_job_document(
                    job_id=track_id,
                    document_id=document_id,
                    status="failed",
                    error_msg=error_msg
                )
                #重新获取更新后的文档信息
                document=manager.metadata.get_document(kb_id=kb_id,document_id=document_id)
                #失败文档也加入最终结果
                file_payloads.append(document)
                #失败信息
                errors.append({
                    "document_id":document_id,
                    "filename":filename,
                    "extension":extension,
                    "error":error_msg
                })
                #当前文件解析失败了，不再进行后面的逻辑，尝试解析下一个文件
                continue

            #解析成功了
            parsed_count+=1
            #文档状态修改为已解析
            manager.metadata.mark_document_parsed(kb_id,document_id,content_length=len(text))
            #把文档挂到当下任务，标记为处理中
            manager.metadata.link_job_document(job_id=track_id,document_id=document_id,status="processing")
            #统一收集解析成功的内容
            parsed_texts.append(text)
            parsed_sources.append(filename)
            parsed_document_ids.append(document_id)
            #把解析成功的文档加入返回结果
            file_payloads.append(manager.metadata.get_document(kb_id,document_id))

        #全部文件解析完，再统一索引
        if parsed_texts:
            try:
                engine=await manager.get_engine(kb_id)
                #文档入队
                await engine.enqueue_documents(
                    texts=parsed_texts,
                    file_sources=parsed_sources,
                    document_ids=parsed_document_ids,
                    track_id=track_id
                )
                #提交成功后，更新状态->processing
                for document_id in parsed_document_ids:
                    manager.metadata.mark_document_indexing(kb_id,document_id)
                status="processing"

            #索引建立失败
            except Exception as exc:
                error_msg=f"{type(exc).__name__}:{exc!s}"
                #遍历所有等待索引的文档
                for document_id in parsed_document_ids:
                    #全部标记为失败
                    manager.metadata.update_document_index_status(
                        kb_id=kb_id,
                        document_id=document_id,
                        index_status="failed",
                        error_msg=error_msg
                    )
                    #更新文档与任务关系
                    manager.metadata.link_job_document(
                        track_id,
                        document_id,
                        status="failed",
                        error_msg=error_msg
                    )
                #更新整个任务
                manager.metadata.update_job(
                    track_id,
                    status="failed",
                    parsed_count=parsed_count,
                    #两类失败：原本解析失败的文件+解析成功但索引失败的文件
                    failed_count=failed_count+len(parsed_document_ids),
                    error_msg=error_msg
                )
                #索引失败了，直接返回
                return envelope(
                    request_id=request_id,
                    status="error",
                    data={"track_id":track_id,"job_detail":manager.metadata.job_detail(kb_id,track_id)["documents"]},
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
        #如果没有任何文件解析成功
        else:
            status="failed" #直接短路，防止初始化engine造成浪费

        #更新最终任务状态
        manager.metadata.update_job(
            job_id=track_id,
            status=status,
            parsed_count=parsed_count,
            failed_count=failed_count,
            error_msg=("全部文件解析失败"
            if not parsed_texts and failed_count #没有任何解析成功的文本，且失败数大于0
            else None),
        )
        #返回
        return envelope(
            request_id=request_id,
            data={
                "track_id":track_id,
                "kb_id":meta.kb_id,
                "kb_name":meta.name,
                "parsed_count":parsed_count,
                "error_count":failed_count,
                "total_files":len(files),
                "files":file_payloads,
                "errors":errors,
            },
        )

    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        return envelope(
            request_id=request_id,
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )


@app.get("/v1/knowledge-bases/{kb_id}/documents", dependencies=[Depends(require_api_key)])
async def documents(
    kb_id:str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
)->dict[str,Any]:
    """读取指定知识库的文件列表"""

    request_id=str(uuid.uuid4())
    try:
        await _sync_documents_from_lightrag(kb_id) #分页读取该KB所有文档状态
        return envelope(
            request_id=request_id,
            data=manager.metadata.list_documents(kb_id,page=page,page_size=page_size)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        return envelope(
            request_id=request_id,
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )


@app.get("/v1/knowledge-bases/{kb_id}/documents/{document_id}", dependencies=[Depends(require_api_key)])
async def document_detail(kb_id:str,document_id:str)->dict[str,Any]:
    """获取某个文件的细节"""

    request_id=str(uuid.uuid4())
    try:
        document = manager.metadata.get_document(kb_id, document_id) #返回文档，保证了即使文档没有进入LightRAG，也保留了元数据
        content = "" #正文
        chunks: list[Any] = []
        status_raw: dict[str, Any] = {} #LightRAG状态
        light_detail = await _sync_one_document_from_lightrag(kb_id, document_id) #主动查询某个文件，并同步状态
        if light_detail is not None:
            content = str(light_detail.get("content") or "") #正文
            raw_chunks = light_detail.get("chunks") #原始chunks
            chunks = raw_chunks if isinstance(raw_chunks, list) else []
            status_payload = light_detail.get("status")
            status_raw = status_payload if isinstance(status_payload, dict) else {}
            document = manager.metadata.get_document(kb_id, document_id)  #获取文档最新状态
        return envelope(
            request_id=request_id,
            data={
                "document": document,
                "content": content,
                "chunks": chunks,
                "status_raw": status_raw,
            },
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        return envelope(
            request_id=request_id,
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )

@app.post(
    "/v1/knowledge-bases/{kb_id}/documents/text",
    dependencies=[Depends(require_api_key)],
    response_model=None,
)
async def document_text(
    kb_id: str,
    request: TextDocumentRequest,
) -> dict[str, Any] | JSONResponse:
    """先保存成原文件，再把文本异步写入知识库"""

    request_id = str(uuid.uuid4())
    try:
        #获得知识库元数据
        meta = manager.kb_store.get(kb_id)

        #清理document_id
        document_id = (
            _clean_document_id(request.document_id)
            if request.document_id
            else _new_document_id()
        )
        #确保文件不存在
        _ensure_document_not_exists(kb_id, document_id)
        #准备新文件参数：文件名，文本内容，文件路径
        filename = _safe_filename(request.file_source or f"{document_id}.txt")
        raw = request.text.encode("utf-8")
        source_path = _write_source_file(
            kb_id,
            document_id,
            filename,
            raw,
        )
        #创建文件
        manager.metadata.create_document(
            document_id=document_id,
            kb_id=kb_id,
            original_filename=filename,
            source_file_path=source_path,
            source_file_size=len(raw),
            source_sha256=_sha256(raw),
            content_type="text/plain; charset=utf-8",
            extension=Path(filename).suffix.lower() or ".txt",
        )
        #标记文件解析完成
        manager.metadata.mark_document_parsed(
            kb_id,
            document_id,
            content_length=len(request.text),
        )

        #创建入库任务
        track_id = generate_track_id("insert")
        manager.metadata.create_job(
            job_id=track_id,
            kb_id=kb_id,
            total_files=1,
        )
        #job关联新文件
        manager.metadata.link_job_document(
            job_id=track_id,
            document_id=document_id,
            status="processing",
        )

        engine = await manager.get_engine(kb_id)
        try:
            #文档入队，提交异步索引
            await engine.enqueue_documents(
                texts=[request.text],
                file_sources=[filename],
                document_ids=[document_id],
                track_id=track_id,
            )
            #标记开始建立索引
            manager.metadata.mark_document_indexing(kb_id, document_id)
            manager.metadata.update_job(
                track_id,
                status="processing",
                parsed_count=1,
                failed_count=0,
            )
        #索引失败
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            manager.metadata.update_document_index_status(
                kb_id,
                document_id,
                index_status="failed",
                error_msg=error_msg,
            )
            manager.metadata.link_job_document(
                job_id=track_id,
                document_id=document_id,
                status="failed",
                error_msg=error_msg,
            )
            manager.metadata.update_job(
                track_id,
                status="failed",
                parsed_count=1,
                failed_count=1,
                error_msg=error_msg,
            )
            return JSONResponse(
                status_code=500,
                content=envelope(
                    request_id=request_id,
                    status="error",
                    data={
                        "document": manager.metadata.get_document(
                            kb_id,
                            document_id,
                        ),
                        "track_id": track_id,
                    },
                    error={
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                ),
            )

        return envelope(
            request_id=request_id,
            data={
                "track_id": track_id,
                "processing_mode": "async",
                "kb_id": meta.kb_id,
                "kb_name": meta.name,
                "document": manager.metadata.get_document(
                    kb_id,
                    document_id,
                ),
            },
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DocumentAlreadyExistsError as exc:
        return JSONResponse(
            status_code=409,
            content=envelope(
                request_id=request_id,
                status="error",
                error={
                    "type": "DocumentAlreadyExists",
                    "message": str(exc),
                },
            ),
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content=envelope(
                request_id=request_id,
                status="error",
                error={
                    "type": "DocumentValidationError",
                    "message": str(exc),
                },
            ),
        )
    except Exception as exc:
        logger.exception(
            "Text document ingestion failed",
            extra={
                "request_id": request_id,
                "kb_id": kb_id,
                "error_type": type(exc).__name__,
            },
        )
        return JSONResponse(
            status_code=500,
            content=envelope(
                request_id=request_id,
                status="error",
                error={
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            ),
        )


@app.get("/v1/knowledge-bases/{kb_id}/documents/{document_id}/source", dependencies=[Depends(require_api_key)])
async def document_source(
    kb_id:str,
    document_id:str,
    disposition:str = Query(default="inline", pattern="^(inline|attachment)$"),
)->FileResponse:
    """读取指定文档的原文件，用于前端预览或下载。"""

    try:
        document=manager.metadata.get_document(kb_id=kb_id,document_id=document_id)

        #解析文件原路径(resolve:转移成规范化的绝对路径)
        source_file_path=document.get("source_file_path")
        if not source_file_path:
            raise HTTPException(
                status_code=404,
                detail="source file path is missing"
            )
        source_path=Path(str(source_file_path)).expanduser().resolve()

        #安全路径
        allowed_dir=manager.kb_store.source_document_dir(kb_id,document_id).expanduser().resolve()
        if allowed_dir not in source_path.parents: #防止目录穿越
            raise HTTPException(
                status_code=403,
                detail="surce file path is outside document source directory"
            )
        #检查文件是否存在
        if not source_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="source file not found"
            )

        #返回原文件
        return FileResponse(
            path=source_path,
            #文件MIME类型
            media_type=str(document.get("content_type") or "application/octet-stream"),
            filename=str(document.get("original_filename") or source_path.name),
            content_disposition_type=disposition    #预览/下载
        )
    
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except HTTPException:
        raise
        

@app.delete("/v1/knowledge-bases/{kb_id}/documents/{document_id}",dependencies=[Depends(require_api_key)])
async def delete_document(
    kb_id:str,
    document_id:str,
    delete_llm_cache: bool = Query(default=False)   #是否删除LLM缓存
)->dict[str,Any]:
    """删除指定知识库内文档元数据、原文件目录和LightRAG派生索引
    删索引->删元数据+原文件->删目录"""

    request_id=str(uuid.uuid4())

    try:
        document=manager.metadata.get_document(kb_id,document_id)

        #先删除索引，失败就停止，不删除本地文件和元数据
        engine=await manager.get_engine(kb_id)
        await engine.delete_document(document_id,delete_llm_cache=delete_llm_cache)

        source_dir=(manager.metadata.source_document_dir(kb_id, document_id)
                                        .expanduser().resolve())
        source_file_path = document.get("source_file_path")
        if source_file_path:
            source_path = Path(str(source_file_path)).expanduser().resolve()
            # 防止元数据被篡改后删除任意文件。
            if source_dir not in source_path.parents:
                raise HTTPException(
                    status_code=403,
                    detail="source file path is outside document source directory",
                )
            # 一次只删除元数据明确记录的这个文件。
            if source_path.exists():
                if not source_path.is_file():
                    raise HTTPException(
                        status_code=409,
                        detail="source file path is not a regular file",
                    )
                source_path.unlink()
        #只删除空目录
        if source_dir.exists():
            try:
                source_dir.rmdir()
            except OSError as exc:
                raise HTTPException(
                    status_code=409,
                    detail="document source directory is not empty",
                ) from exc

        #文件删除成功后删除元数据
        manager.metadata.delete_document_metadata(kb_id,document_id)
        return JSONResponse(
            status_code=200,
            content=envelope(
                request_id=request_id,
                data={
                    "deleted":True,
                    "document":document
                },
            )
        )
    
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=envelope(
                request_id=request_id,
                status="error",
                error={
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            ),
        )

#几个同步函数的区别：
#单篇详情查询 ──┐
#文档列表查询 ──┼→ _sync_document_from_lightrag() → SQLite
#任务状态查询 ──┘

def _sync_document_from_lightrag(kb_id:str,document_id:str,status_payload:dict[str,Any]):
    """同步单个文档状态到SQLite,没有返回完整文档详情"""

    #转换状态名称
    status=_map_lightrag_status(status_payload)
    #提取chunk数量
    chunks_count=int(status_payload.get("chunks_count") or status_payload.get("chunk_count") or 0)
    #更新SQLite文档记录
    manager.metadata.update_document_index_status(
        kb_id=kb_id,
        document_id=document_id,
        index_status=status,
        chunks_count=chunks_count,
        error_msg=_first_error(status_payload)
    )

    
def _sync_job_from_lightrag(kb_id:str,job_id:str,light_job:dict[str,Any]):
    """用 LightRAG 最新任务状态同步 SQLite 元数据。
    负责：
    LightRAG status
    → 映射为 processed/processing/failed
    → 提取 chunks_count
    → 提取错误信息
    → 更新 SQLite documents 表"""

    #遍历LightRAG返回的文档
    for item in light_job.get("documents",[]) or []:
        if not isinstance(item,dict):
            continue #过滤不合法文档
        document_id=str(item.get("document_id") or "")
        if not document_id:
            continue #跳过没有id的文档
        #逐个同步文档状态
        _sync_document_from_lightrag(kb_id,document_id,item)
        #更新job和documents的关联状态
        status=_map_lightrag_status(item)
        manager.metadata.link_job_document(job_id,document_id,status,error_msg=_first_error(item))

    #重新读取SQLite中整个任务
    detail=manager.metadata.job_detail(kb_id=kb_id,job_id=job_id)
    documents_payload=detail.get("documents",[])
    statuses=[
        str(
            item.get("job_document_status")
            or item.get("index_status")
        )
        for item in documents_payload
    ]
    #汇总成功、失败的数量
    parsed_count=sum(1 for item in documents_payload if item.get("parse_status")=="parsed")
    failed_count=sum(
        1 for item in documents_payload if item.get("parse_status")=="failed" #解析失败
        or item.get("index_status")=="failed" #解释成功，但索引失败
    )

    #计算整个job的最终状态
    #1.全部成功
    if statuses and all(status=="processed" for status in statuses):
        job_status="processed"
    #2.全部失败
    elif failed_count==detail.get("total_files"):
        job_status="failed"
    #3.全部结束，但是不是全部成功
    elif statuses and all(status in {"failed","processed"} for status in statuses):
        job_status=(
            "partial_failed" if failed_count else "processed"
        )
    else:
        job_status="processing"

    #更新ingest_jobs
    manager.metadata.update_job(
        job_id=job_id,
        status=job_status,
        parsed_count=parsed_count,
        failed_count=failed_count,
    )



async def _sync_documents_from_lightrag(kb_id:str)->None:
    """分页读取某个KB在LightRAG中全部文档状态，逐个同步到SQLite"""

    engine=await manager.get_engine(kb_id)
    #分页配置
    page=1
    page_size=200

    #循环读取LightRAG
    while True:
        light_page=await engine.documents(page=page,page_size=page_size)
        documents=light_page.get("documents")

        #抛弃没有数据、或者返回格式错误的文档
        if not isinstance(documents,list) or not documents:
            return
        #过滤文档
        for document in documents:
            if not isinstance(document,dict):
                continue #异常记录
            #提取id
            document_id=str(document.get("document_id") or "")
            if not document_id:
                continue
            #同步单个文档
            _sync_document_from_lightrag(kb_id,document_id,document)

        #判断是否还有下一页
        if not light_page.get("has_next"):
            return
        page+=1

async def _sync_one_document_from_lightrag(kb_id:str,document_id:str)->dict[str,Any]|None:
    """主动查询LightRAG中某一个文档，然后同步状态"""

    engine=await manager.get_engine(kb_id)
    try:
        #用LightRAG查询指定文档详情状态
        light_detail=await engine.document_detail(document_id)
    #找不到文档，暂时无法同步
    except KeyError:
        return None
    #取出LightRAG状态
    status_payload=light_detail.get("status")
    if isinstance(status_payload,dict):
        #映射状态，提取chunks_count、错误信息，更新SQLite documents表
        _sync_document_from_lightrag(kb_id,document_id,status_payload)
    return light_detail



def _map_lightrag_status(payload:dict[str,Any]):
    """把LightRAG状态同步到产品状态"""

    raw=str(payload.get("status") or payload.get("doc_status") or "processing").lower()
    if raw in {"ok","processed","done","success","completed"}:
        return "processed"
    if raw in {"error","failed"}:
        return "failed"
    return "processing"



def _safe_filename(filename:str)->str:
    """将文件名转换为安全的文件名"""

    name=Path(filename).name.strip() or "uploaded_file" #去除了目录部分，只保留文件名
    name=_FILENAME_RE.sub("_",name) #清理特殊字符
    name=name.strip(".")[:180] #限制长度

    if not name or name in {".", ".."}:
        return "uploaded_file"

    stem = Path(name).stem.upper()
    if stem in _WINDOWS_RESERVED:
        name = f"_{name}"

    return name

def _new_document_id()->str:
    """生成文档id"""

    return f"doc_{uuid.uuid4().hex[:16]}"


def _clean_document_id(document_id: str) -> str:
    """校验前端传入的文档 ID，避免路径穿越和非法标识符。"""

    clean = document_id.strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", clean):
        raise ValueError("invalid document_id")
    return clean


def _ensure_document_not_exists(kb_id: str, document_id: str) -> None:
    """避免指定 document_id 覆盖已有文档和原文件。"""

    try:
        manager.metadata.get_document(kb_id, document_id)
    except KeyError:
        return
    raise DocumentAlreadyExistsError(
        f"document already exists: {document_id}"
    )


def _write_source_file(kb_id:str,document_id:str,filename:str,raw:bytes)->Path:
    """保存上传原文件到sources目录"""

    directory=manager.metadata.source_document_dir(
        kb_id=kb_id,
        document_id=document_id,
    ).resolve()
    directory.mkdir(parents=True,exist_ok=True) #创建目录
    safe_filename=_safe_filename(filename)
    path=(directory / safe_filename).resolve()
    if path.parent != directory:
        raise ValueError("上传文件路径越过文档目录")
    path.write_bytes(raw) #写入原字节
    return path



#===========================ingest_job 接口====================================
@app.get("/v1/knowledge-bases/{kb_id}/jobs/{job_id}",dependencies=[Depends(require_api_key)])
async def job(kb_id:str, job_id:str):
    """查询指定知识库的入库任务"""

    request_id=str(uuid.uuid4())
    try:
        try:
            engine=await manager.get_engine(kb_id)
            light_job=await engine.job(job_id) #查询最新任务状态
            _sync_job_from_lightrag(kb_id,job_id,light_job) #更新SQLite文档和job状态
        except Exception:
            manager.metadata.job_detail(kb_id,job_id)
        return envelope(request_id=request_id, data=manager.metadata.job_detail(kb_id,job_id))
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc.args[0]) if exc.args else "知识库不存在",
        ) from exc




def _first_error(payload:dict[str,Any]):
    """从LightRAG状态提取错误信息"""

    for key in ("error","message","error_msg"):
        value=payload.get(key)
        if value:
            return str(value)
    return None



def _sha256(raw: bytes) -> str:
    """计算文件 SHA256，根据文件内容生成一个长度固定的“内容指纹”
    用于：
    判断文件是否重复
    校验文件是否被修改
    内容去重"""

    return hashlib.sha256(raw).hexdigest()
