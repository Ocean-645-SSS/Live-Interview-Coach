"""HTTP Gateway：管理 API 访问内部 RAG Core Service 的统一网关。"""

import asyncio
from dataclasses import dataclass
import json
import logging
from typing import Any
import uuid

import aiohttp
from fastapi import UploadFile

from liverag.config.settings import AppSettings,load_rag_client_settings
from liverag.rag.service import wait_for_rag_ready


logger = logging.getLogger("liverag.api.rag_gateway")

@dataclass(slots=True)
class GatewayResponse:
    """统一描述管理 API 返回给前端的结果。"""

    status_code:int
    body:dict[str,Any]

@dataclass(slots=True)
class GatewayFileResponse:
    """统一描述内部RAG文件响应"""

    status_code: int
    body: bytes
    headers:dict[str,Any]
    error_body:dict[str,Any]|None=None

def envelope(
    *,
    request_id:str|None=None,
    data:dict[str, Any]|list[Any]|None = None,
    metrics:dict[str, Any]|None=None,
    error:dict[str,Any]|None=None,
    status:str="ok",
):
    """生成统一管理的API响应"""

    return {
        "request_id": request_id or str(uuid.uuid4()),
        "status": status,
        "data": data,
        "metrics": metrics or {},
        "error": error,
    }


class RagGateway:
    """把前端管理 API 请求转发到内部 RAG Core Service。"""

    def __init__(self,settings:AppSettings):
        self.settings = settings

    async def get(self,path:str,*,params:dict[str,Any]|None=None)->GatewayResponse:
        """GET 请求"""

        return await self._request("GET",path,params=params)

    async def post_json(self, path: str, *, payload: dict[str, Any]) -> GatewayResponse:
        """转发 JSON POST 请求。"""

        return await self._request("POST", path, json_body=payload)

    async def patch_json(self, path: str, *, payload: dict[str, Any]) -> GatewayResponse:
        """转发 JSON PATCH 请求：局部修改一个已经存在的资源"""

        return await self._request("PATCH", path, json_body=payload)
    
    async def delete(self, path: str, *, params: dict[str, Any] | None = None) -> GatewayResponse:
        """转发 DELETE 请求。"""

        return await self._request("DELETE", path, params=params)

    async def get_documents(self, path: str, *, params: dict[str, Any] | None = None) -> GatewayResponse:
        """转发文档列表请求并归一化字段。"""

        result = await self.get(path, params=params)
        return self._map_data(result, self._normalize_documents_payload)

    async def get_document_detail(self, path: str)->GatewayResponse:
        """转发文档详情请求并归一化字段。"""

        result=await self.get(path)
        return self._map_data(result,self._normalize_document_detail)

    async def get_job(self, path: str) -> GatewayResponse:
        """转发任务查询请求并归一化文档字段。"""

        result = await self.get(path)
        return self._map_data(result, self._normalize_job_payload)

    async def get_file(self,path:str,*,params: dict[str, Any] | None = None)->GatewayFileResponse:
        """转发原文件请求，保留请求头
        收到文件请求
            ↓
        等待 RAG 服务就绪
            ├─ 未就绪 → 503
            ↓
        向 RAG 发起 GET 请求
            ├─ HTTP 错误或 JSON → 规范化错误响应
            ├─ 文件响应 → 返回文件字节和文件响应头
            └─ 网络/程序异常 → 502"""

        #创建备用请求ID
        fallback_request_id=str(uuid.uuid4())
        #线程中等待RAG ready
        ready_state=await asyncio.to_thread(
            wait_for_rag_ready,
            timeout_ms=self.settings.api.rag_ready_timeout_ms
        )
        if not ready_state.ready:
            return GatewayFileResponse(
                status_code=503,
                body=b"",
                headers={},
                error_body=envelope(
                    request_id=fallback_request_id,
                    status="error",
                    error={"type": "RagServiceNotReady", "message": ready_state.error or "RAG 服务未就绪"},
                    metrics={"rag_service_status": ready_state.status},
                ),
            )
        #RAG客户端配置
        rag_settings=load_rag_client_settings(self.settings.user_data_dir)
        #超时设置
        timeout = aiohttp.ClientTimeout(
            total=max(self.settings.api.rag_gateway_timeout_ms,100,) / 1000.0
        )
        #构造header+URL
        headers=self._headers(rag_settings.api_key,has_json=False, has_form=False)
        target_url=self._target_url(rag_settings.base_url,path=path)

        try:
            #发起文件请求
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    target_url,
                    headers=headers,
                    params=self._query_params(params=params)
                )as response:
                    content_type = response.headers.get("content-type", "")
                    #响应是错误
                    if response.status >= 400:
                        payload = await self._read_payload(response)
                        normalized = self._normalize_payload(
                            payload,
                            status_code=response.status,
                            fallback_request_id=fallback_request_id,
                        )
                        return GatewayFileResponse(
                            status_code=normalized.status_code,
                            body=b"",
                            headers={},
                            error_body=normalized.body,
                        )

                    #成功，读取文件
                    body = await response.read()
                    headers = self._file_headers(response)
                    return GatewayFileResponse(status_code=response.status, body=body, headers=headers)
        except Exception as exc:
            return GatewayFileResponse(
                status_code=502,
                body=b"",
                headers={},
                error_body=envelope(
                    request_id=fallback_request_id,
                    status="error",
                    error={"type": type(exc).__name__, "message": str(exc)},
                ),
            ) 


    async def post_files(self, path: str, *, files: list[UploadFile]) -> GatewayResponse:
        """把前端上传到管理API的文件，继续转发给RAG Core
        前端上传文件
        ↓ multipart/form-data
        管理 API 接收 UploadFile
        ↓ post_files()
        读取文件并重新组装 multipart/form-data
        ↓ HTTP POST
        RAG Core 接收文件"""

        #创建multipart表单
        form = aiohttp.FormData()
        #遍历上传文件
        for uploaded in files:
            #读取文件内容
            raw = await uploaded.read()
            #加入表单
            form.add_field(
                "files",
                raw,
                filename=uploaded.filename or "uploaded_file",
                content_type=uploaded.content_type or "application/octet-stream",
            )
        #发给RAG Core
        return await self._request("POST", path, form_data=form, upload=True)


    async def _request(
        self,
        method:str, #GET/POST/DELETE/PATCH
        path:str,  #RAG Core 内部路径
        *,
        params:dict[str, Any] | None = None,    #URL 查询参数
        json_body:dict[str,Any] | None=None,    #JSON 请求体
        form_data:aiohttp.FormData | None = None,   #文件上传用的multipart表单
        upload:bool=False     #是否为上传请求，用于选择更长的timeout
    )->GatewayResponse:
        """执行一次统一内部 RAG 请求"""

        #创建备用request id
        fallback_request_id=str(uuid.uuid4())

        #等待RAG Core就绪
        ready_state=await asyncio.to_thread(
            wait_for_rag_ready,
            timeout_ms=self.settings.api.rag_ready_timeout_ms
        )
        #RAG未就绪返回503
        if not ready_state.ready:
            return GatewayResponse(
                status_code=503,
                body=envelope(
                    request_id=fallback_request_id,
                    status="error",
                    error={"type": "RagServiceNotReady", "message": ready_state.error or "RAG 服务未就绪"},
                    metrics={"rag_service_status": ready_state.status},
                )
            )

        #读取RAG 最新配置
        rag_settings=load_rag_client_settings(self.settings.user_data_dir)
        #根据请求类型设置timeout：有上传文件就久一点
        timeout_ms = (
                        max(self.settings.api.rag_gateway_upload_timeout_ms,30_000,)
                        if upload
                        else max(self.settings.api.rag_gateway_timeout_ms,100,)
                    )
        timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000.0)

        #配置请求头和目标URL
        headers=self._headers(
            rag_settings.api_key,
            has_json=json_body is not None,
            has_form=form_data is not None,
        )
        target_url=self._target_url(rag_settings.base_url,path)

        try:
            #创建HTTP并发送请求
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.request(
                    method=method,
                    url=target_url,
                    params=self._query_params(params),
                    json=json_body,
                    data=form_data,
                    headers=headers,
                ) as response,
            ):
                #读取下游响应
                payload=await self._read_payload(response)
                return self._normalize_payload(
                    payload,
                    status_code=response.status,
                    fallback_request_id=fallback_request_id,
                )
        except Exception as exc:
             logger.exception(
                "RAG Gateway 请求失败",
                extra={
                    "request_id": fallback_request_id,
                    "method": method,
                    "path": path,
                    "error_type": type(exc).__name__,
                },
            )
             return GatewayResponse(
                status_code=502,
                body=envelope(
                    request_id=fallback_request_id,
                    status="error",
                    error={"type": "RagGatewayError", "message": str(exc)},
                ),
            )

    @staticmethod
    def _headers(api_key:str,*,has_json=bool,has_form:bool)->dict[str,str]:
        """构造转发请求头。"""

        if has_json and has_form:
            raise ValueError("JSON body and multipart form cannot be sent together")
        
        headers:dict[str,str]={}

        if has_json:
            headers["Content-Type"]="application/json"

        #文件上传不能手动固定Content-Type
        if has_form:
            headers.pop("Content-Type",None)

        #添加内部api_key
        if api_key:
            headers["X-API-Key"]=api_key

        return headers

    @staticmethod
    def _target_url(base_url:str,path:str)->str:
        """构造拼接请求URL
        例子：http://127.0.0.1:9721/v1/knowledge-bases"""

        #保证base_url末尾没有/   path开头有至少一个/    最终中间正好一个/
        normalized = path if path.startswith("/") else f"/{path}"
        return f"{base_url.rstrip('/')}{normalized}"
    
    @staticmethod
    def _query_params(params:dict[str,Any]|None)->dict[str,str|int|float]|None:
        """把查询参数params清洗成aiohttp能安全接受的格式"""

        if not params:
            return None

        normalized:dict[str,str|int|float]={}
        for key,value in params.items():
            if value is None:
                continue
            #布尔值转换为true/false字符串
            if isinstance(value,bool):
                normalized[key]="true" if value else "false"
            #符合
            elif isinstance(value,str|int|float):
                normalized[key]=value
            else:
                normalized[key]=str(value)

        return normalized or None

    @staticmethod
    async def _read_payload(response:aiohttp.ClientResponse)->Any:
        """根据内容类型输出上游响应"""

        content_type = response.headers.get("content-Type","")
        if "application/json" in content_type:
            return await response.json()
        return await response.text()

    @staticmethod
    def _file_headers(response: aiohttp.ClientResponse) -> dict[str, str]:
        """保留前端预览原文件需要的安全响应头。"""

        allowed = {
            "content-type",
            "content-disposition",
            "etag",
            "last-modified",
            "cache-control",
        }
        return {
            key: value
            for key, value in response.headers.items()
            if key.lower() in allowed
        }
    
    @staticmethod
    def _normalize_payload(
        payload:dict,
        *,
        status_code:int,
        fallback_request_id:str
    )->GatewayResponse:
        """把上游响应归一成统一 envelope"""

        #标准envelope
        if isinstance(payload,dict) and "status" in payload and "request_id" in payload:
            body={
                "request_id": payload.get("request_id") or fallback_request_id,
                "status": payload.get("status") or ("error" if payload.get("error") else "ok"),
                "data": payload.get("data"),
                "metrics": payload.get("metrics") or {},
                "error": payload.get("error"),
            }
            return GatewayResponse(status_code=status_code, body=body)
        #不是标准envelope，且HTTP请求失败
        if status_code >= 400:
            message = RagGateway._error_message(payload)
            return GatewayResponse(
                status_code=status_code,
                body=envelope(
                    request_id=fallback_request_id,
                    status="error",
                    error={"type": "UpstreamError", "message": message},
                ),
            )
        #不是标准envelope，但HTTP请求成功
        return GatewayResponse(
            status_code=status_code,
            body=envelope(request_id=fallback_request_id, data=payload),
        )

    @staticmethod
    def _error_message(payload:Any)->str:
        """把各种上游错误体压成一条可读信息。"""

        if isinstance(payload,dict):
            detail=payload.get("detail")
            if isinstance(detail,str):
                return detail
            if isinstance(detail,dict):
                message=(detail.get("message") or detail.get("detail"))
                if message:
                    return str(message)
            if isinstance(detail, list):
                messages = [
                    str(item.get("msg"))
                    for item in detail
                    if isinstance(item, dict) and item.get("msg")
                ]
                if messages:
                    return "; ".join(messages)

            error = payload.get("error")
            if isinstance(error, dict):
                message = (error.get("message") or error.get("detail"))
                if message:
                    return str(message)
            if error:
                return str(error)
            return json.dumps(
                payload,
                ensure_ascii=False,
            )

        if payload is None:
            return "上游服务返回空错误响应"
        if isinstance(payload, list):
            return json.dumps(
                payload,
                ensure_ascii=False,
            )

        return str(payload)

    @classmethod
    def _map_data(cls,result:GatewayResponse,mapper:Any)->GatewayResponse:
        """保留响应中status/request_id/metrics等字段，只转换成功响应里的data
        mapper:负责转换data的函数"""

        #状态不为200，直接返回原样
        if result.body.get("status")!="ok":
            return result

        body=dict(result.body)
        body["data"]=mapper(body.get("data"))
        return GatewayResponse(result.status_code,body=body)

    @classmethod
    def _normalize_documents_payload(cls,data:Any)->dict[str, Any]:
        """归一化文档列表响应"""

        if not isinstance(data, dict):
            return {"documents":[],"total":0}

        payload=dict(data)
        documents=data.get("documents")
        if isinstance(documents, list):
            payload["documents"]=[
                cls._normalize_document_summary(item)
                for item in documents if isinstance(item, dict)
            ]
        else:
            payload["documents"]=[]

        payload.setdefault("total",len(payload["documents"]))
        return payload

    @staticmethod
    def _normalize_document_summary(item: dict[str, Any]) -> dict[str, Any]:
        """归一化单个文档摘要字段。"""

        status_value = item.get("status")
        if isinstance(status_value, dict):
            status_raw = status_value
            status = status_raw.get("status") or "unknown"
        else:
            status_raw = item
            status = status_value or item.get("doc_status") or "unknown"
        chunks = item.get("chunks")
        chunks_count = item.get("chunks_count")
        if chunks_count is None:
            chunks_count = item.get("chunk_count")
        if chunks_count is None:
            chunks_list = item.get("chunks_list")
            chunks_count = len(chunks_list) if isinstance(chunks_list, list) else 0
        return {
            "document_id": item.get("document_id") or item.get("doc_id") or item.get("id") or "",
            "kb_id": item.get("kb_id") or "",
            "kb_name": item.get("kb_name") or "",
            "original_filename": item.get("original_filename") or "",
            "file_path": item.get("file_path") or item.get("file_source") or item.get("source") or "",
            "source_file_path": item.get("source_file_path") or "",
            "source_file_exists": bool(item.get("source_file_exists")),
            "source_file_size": item.get("source_file_size") or 0,
            "source_sha256": item.get("source_sha256") or "",
            "content_type": item.get("content_type") or "",
            "extension": item.get("extension") or "",
            "parse_status": item.get("parse_status") or "",
            "index_status": item.get("index_status") or "",
            "status": status,
            "chunks_count": chunks_count,
            "content": item.get("content") or "",
            "content_summary": item.get("content_summary") or item.get("summary") or "",
            "content_length": item.get("content_length") or item.get("content_len") or 0,
            "chunks": chunks if isinstance(chunks, list) else [],
            "error_msg": item.get("error_msg") or item.get("error") or item.get("message"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "track_id": item.get("track_id"),
            "status_raw": status_raw,
            "raw": item,
        }

    @classmethod
    def _normalize_document_detail(cls, data: Any) -> dict[str, Any]:
        """归一化文档详情响应。"""

        if not isinstance(data, dict):
            return {"document_id": "", "status": "unknown", "content": "", "chunks": [], "raw": data}

        status_payload = data.get("status") if isinstance(data.get("status"), dict) else {}
        summary_source = {**status_payload, **data}
        summary = cls._normalize_document_summary(summary_source)
        chunks = data.get("chunks")
        if not isinstance(chunks, list):
            chunks = []
        return {
            **summary,
            "content": data.get("content") or "",
            "chunks": chunks,
            "chunks_count": data.get("chunks_count") or summary.get("chunks_count") or len(chunks),
            "status_raw": status_payload,
            "raw": data,
        }

    @classmethod
    def _normalize_job_payload(cls, data: Any) -> dict[str,Any]:
        """归一化任务查询响应。"""
        
        if not isinstance(data, dict):
            return {"job_id": "", "documents": [], "total": 0, "raw": data}
        
        payload = dict(data)

        #归一化文档格式
        documents = data.get("documents")
        payload["documents"] = [
            cls._normalize_document_summary(item) for item in documents if isinstance(item, dict)
        ] if isinstance(documents, list) else []

        return payload
