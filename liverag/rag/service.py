"""等待独立运行的 RAG Core 服务进入 ready 状态,在主进程的后台线程中运行Uvicorn
主应用进程
├── 主线程：主应用
└── 后台 daemon 线程
    └── Uvicorn
        └── FastAPI RAG 服务"""

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from threading import Lock, Thread
from typing import Any

import uvicorn

from liverag.rag.rag_settings import RAGSettings

logger = logging.getLogger("liverag.rag.service")
_START_LOCK = Lock()
_START_THREAD: Thread | None = None #保存启动RAG服务的后台线程


class RagServiceStartStatus(str, Enum):
    """内置 RAG 服务启动状态。"""

    DISABLED = "disabled" #配置关闭了RAG
    ALREADY_RUNNING = "already_running" #端口已经打开
    STARTING = "starting"  #后台启动线程已经存在
    STARTED = "started"  #本次新建+启动了后台服务


@dataclass(frozen=True)
class RagReadyState:
    """描述一次RAG ready检查结果"""

    ready:bool
    status:str
    data:dict[str,Any] | None=None
    error:str | None=None


def port_is_open(host:str,port:str)->bool:
    """检查端口是否可以连接"""
    try:
        with socket.create_connection((host,port),timeout=0.3):
            return True
    except OSError:
        return False



def start_embedded_rag_service()->RagServiceStartStatus:
    """如果RAG Core没有运行，就在当前进程中创建一个后台进程，并在线程里启动Uvicorn，避免了重复启动"""

    #检查是否启用RAG
    if os.getenv("LIGHTRAG_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        return RagServiceStartStatus.DISABLED #RAG未启用

    #获取配置
    settings = RAGSettings()
    #检查端口
    if port_is_open(settings.host, settings.port):
        #记录日志
        logger.info("rag.service.already_running", extra={"host": settings.host, "port": settings.port})
        return RagServiceStartStatus.ALREADY_RUNNING

    #加启动锁，防止两个线程同时启动两个RAG服务
    with _START_LOCK:
        global _START_THREAD
        #是否已经有启动线程：之前创建过，或者线程还在运行
        if _START_THREAD is not None and _START_THREAD.is_alive():
            return RagServiceStartStatus.STARTING
        #加锁之后，再次检查端口
        if port_is_open(settings.host, settings.port):
            #记录日志
            logger.info("rag.service.already_running", extra={"host": settings.host, "port": settings.port})
            return RagServiceStartStatus.ALREADY_RUNNING

        #定义后台执行函数
        def _run_server() -> None:
            uvicorn.run(
                "liverag.rag.server:app",
                host=settings.host,
                port=settings.port,
                reload=False,
                log_level=os.getenv("RAG_UVICORN_LOG_LEVEL", "warning"),
            )

        #创建daemon后台线程
        _START_THREAD = Thread(target=_run_server, name="rag-service", daemon=True)
       #启动线程
        _START_THREAD.start()
        #记录日志
        logger.info(
            "rag.service.starting",
            extra={"host": settings.host, "port": settings.port, "working_dir": settings.absolute_working_dir},
        )
        return RagServiceStartStatus.STARTED #创建+启动了后台线程


def wait_for_rag_ready(
    *,
    timeout_ms:int=15000, #最长等待时间
    interval_ms:int=250, #两次检查之间的间隔
    start_if_missing:bool=False, #默认只等待外部 RAG Core，不负责启动
)->RagReadyState:
    """持续轮询 /v1/readyz，直到 RAG Core ready 或等待超时。

    默认只观察独立运行的 RAG Core；只有显式传入
    start_if_missing=True 时才保留旧的嵌入式启动行为。
    """

    #RAG 被禁用时，无论是否允许自动启动都直接返回失败。
    if os.getenv("LIGHTRAG_ENABLED", "true").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return RagReadyState(
            ready=False,
            status="disabled",
            data=None,
            error="RAG 服务被禁用"
        )

    if start_if_missing:
        #显式允许时才尝试在后台线程启动 RAG Core。
        start_status=start_embedded_rag_service()
        service_status=start_status.value
    else:
        #默认：只等待外部 RAG Core，不管理其进程生命周期。
        service_status="external"

    #读取配置
    settings=RAGSettings()

    #截止时间（保证超时至少1ms）
    deadline=time.monotonic()+max(timeout_ms,1)/1000.0
    #准备错误记录
    last_error=""

    #准备认证请求头
    headers=(
        {"X-API-KEY":settings.api_key}
        if settings.api_key
        else {}
    )

    #拼接ready URL
    url=f"http://{settings.host}:{settings.port}/v1/readyz"

    #循环请求/v1/readyz
    while time.monotonic()<=deadline:
        try:
            #构造HTTP请求
            request=urllib.request.Request(
                url,
                headers=headers,
                method="GET"
            )
            #发送请求
            with urllib.request.urlopen(
                request,
                timeout=max(interval_ms/1000.0,0.1),
            )as response:
                #读取json请求
                payload=json.loads(response.read().decode("utf-8"))
                #读取data
                data=(payload.get("data") if isinstance(payload,dict) else None)
                #ready=true，立即返回成果
            if (isinstance(data,dict) and data.get("ready") is True):
                return RagReadyState(
                    ready=True,
                    status=service_status,
                    data=data
                )
            last_error="RAG 服务尚未 ready"
        #HTTP错误
        except urllib.error.HTTPError as exc:
            last_error = f"readyz 返回 HTTP {exc.code}"

        #其他错误
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        #等待后重试
        interval_seconds = max(interval_ms, 1) / 1000.0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval_seconds, remaining))

    #超时返回
    return RagReadyState(
        ready=False,
        status=service_status,
        error=last_error or "等待 RAG ready 超时"
    )
