"""job_type → 异步执行函数的注册表。

每个 handler 接收 BackgroundJobRecord + JobRepository，
返回 dict[str, Any] 作为结果写入 job.result_json。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from liverag.interview.jobs.repository import BackgroundJobRecord, JobRepository

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
    repo: JobRepository,
    **deps: Any,
) -> dict[str, Any]:
    """演示任务：sleep 后返回成功消息。"""
    
    import json

    payload = json.loads(job.payload_json) if job.payload_json else {}
    delay = float(payload.get("delay_seconds", 3.0))
    await asyncio.sleep(delay)
    return {
        "message": "hello async",
        "job_id": job.id,
        "slept_seconds": delay,
    }


__all__ = ["get_handler", "register", "registered_types"]
