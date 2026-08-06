"""Background Worker 独立进程入口。

启动方式：python -m liverag.interview.jobs.worker_main
职责：
1. 加载环境变量和配置
2. 建立 PostgreSQL 连接（通过 JobRepository）
3. 建立 Redis 连接（通过 RedisQueue）
4. 注册 SIGTERM(Ctrl C)/SIGINT(kill) 优雅关闭
5. 启动 BackgroundWorker 主循环
"""

from __future__ import annotations

import asyncio
import logging
import signal

import redis.asyncio as redis  # pyright: ignore[reportMissingImports]

from liverag.config.settings import (
    AppSettings,
    load_app_settings,
    load_environment,
)
from liverag.interview.jobs.queue import RedisQueue
from liverag.interview.jobs.repository import JobRepository
from liverag.interview.jobs.tasks import registered_types
from liverag.interview.jobs.tasks import demo_task  # noqa: F401  # pyright: ignore[reportUnusedImport]  — 导入时触发 @register("demo") 装饰器
from liverag.interview.jobs.worker import BackgroundWorker
from liverag.interview.persistence.db import create_database_engine, create_session_factory

logger = logging.getLogger("liverag.interview.jobs.worker_main")


def _build_worker(
    settings: AppSettings,
    redis_conn: redis.Redis,
) -> BackgroundWorker:
    """组装 Worker 依赖：PostgreSQL JobRepository + Redis Queue + BackgroundWorker。"""

    #创建数据库engine
    engine = create_database_engine(
        settings.interview_database.url,
    )
    #创建会话工厂
    session_factory = create_session_factory(engine)

    #注册Job持久层Repository
    job_repo = JobRepository(session_factory)
    #注册Redis队列+锁
    redis_queue = RedisQueue(
        redis_conn,
        lock_ttl_seconds=settings.redis.lock_ttl_seconds,
    )

    return BackgroundWorker(
        job_repo=job_repo,
        redis_queue=redis_queue,
        poll_timeout=settings.worker.poll_timeout_seconds,
        task_timeout=settings.worker.task_timeout_seconds,
        max_retries=settings.worker.max_retries,
    )


async def _run_worker() -> None:
    """创建redis连接->创建、启动 Worker->等待关闭信号。"""

    #获取AppSettings
    settings = load_app_settings()

    #建立redis连接
    redis_conn = redis.from_url(
        settings.redis.url,
        decode_responses=True,
    )
    try:
        #连接成功
        await redis_conn.ping()
        logger.info("Redis 连接成功", extra={"url": settings.redis.url})
    except Exception as exc:
        #连接失败
        logger.error("Redis 连接失败", extra={"error": f"{type(exc).__name__}: {exc}"})
        raise

    #创建worker
    worker = _build_worker(settings, redis_conn)

    #信号处理
    loop = asyncio.get_running_loop()

    def _on_shutdown_signal() -> None:
        """发送关闭信号，worker退出"""
        logger.info("收到终止信号")
        worker.request_shutdown()

    #监听kill通知，确保当前任务干完再退出，防止丢失数据
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_shutdown_signal)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler，用 signal.signal 代替
            signal.signal(sig, lambda _signum, _frame: _on_shutdown_signal())

    #记录Background Worker运行日志
    logger.info(
        "Background Worker 启动中",
        extra={
            "database": settings.interview_database.url.split("@")[-1]
            if "@" in settings.interview_database.url
            else settings.interview_database.url,
            "registered_job_types": registered_types(),
        },
    )
    try:
        #启动worker主循环
        await worker.run()
    finally:
        #最终任务关闭redis连接
        await redis_conn.aclose()
        logger.info("Redis 连接已关闭")


def main() -> None:
    """Worker 进程入口。"""

    load_environment()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        asyncio.run(_run_worker())
    except KeyboardInterrupt:
        logger.info("Worker 被用户中断")
    except Exception:
        logger.exception("Worker 异常退出")
        raise


if __name__ == "__main__":
    main()


__all__ = ["main"]
