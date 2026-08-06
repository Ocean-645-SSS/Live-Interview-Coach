"""Background Job 的 PostgreSQL CRUD 操作。

Redis 只负责队列和临时协调，权威 Job 状态始终存储在 PostgreSQL 中。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from liverag.interview.persistence.db import session_scope
from liverag.interview.persistence.models import BackgroundJobModel
from liverag.interview.persistence.repository import JobRepository as AbstractJobRepository
from liverag.interview.records import BackgroundJobRecord, JobStatus, generate_id


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _required_iso(value: datetime) -> str:
    result = _to_iso(value)
    if result is None:
        raise TypeError("必填时间字段不能为 None")
    return result


def _job_record(model: BackgroundJobModel) -> BackgroundJobRecord:
    """ORM 模型 → 不可变 Record。"""
    return BackgroundJobRecord(
        id=model.id,
        job_type=model.job_type,
        idempotency_key=model.idempotency_key,
        status=model.status,
        business_resource_id=model.business_resource_id,
        payload_json=model.payload_json,
        result_json=model.result_json,
        error_message=model.error_message,
        attempt=model.attempt,
        max_attempts=model.max_attempts,
        started_at=_to_iso(model.started_at),
        completed_at=_to_iso(model.completed_at),
        created_at=_required_iso(model.created_at),
        updated_at=_required_iso(model.updated_at),
    )


class JobRepository(AbstractJobRepository):
    """Background Job 的 PostgreSQL 访问层（实现 JobRepository Protocol）。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    # ==================== 创建 ====================
    def create_job(
        self,
        *,
        job_type: str,  #任务类型
        idempotency_key: str,   #幂等键
        business_resource_id: str,  #涉及的业务资源
        payload: dict[str, Any] | None = None,  
        max_attempts: int = 3,  #最大重试次数
        job_id: str | None = None,  
    ) -> BackgroundJobRecord:
        """创建一条 PENDING 状态的 Job。"""

        clean_type = job_type.strip()
        if not clean_type:
            raise ValueError("job_type 不能为空")
        clean_key = idempotency_key.strip()
        if not clean_key:
            raise ValueError("idempotency_key 不能为空")

        model = BackgroundJobModel(
            id=job_id or generate_id("job"),
            job_type=clean_type,
            idempotency_key=clean_key,
            status=JobStatus.PENDING,   #状态为PENDING
            business_resource_id=business_resource_id.strip(),
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
            max_attempts=max_attempts,
        )
        with session_scope(self._session_factory) as db:
            db.add(model)
            db.flush()
            return _job_record(model)

    def find_by_idempotency(
        self,
        *,
        job_type: str,
        idempotency_key: str,
    ) -> BackgroundJobRecord | None:
        """按 任务类型+幂等键 查询已有 Job，用于防重。"""

        with self._session_factory() as db:
            model = db.scalar(
                select(BackgroundJobModel).where(
                    BackgroundJobModel.job_type == job_type,
                    BackgroundJobModel.idempotency_key == idempotency_key,
                )
            )
            return _job_record(model) if model else None

    # ==================== 查询 ====================
    def get_job(self, job_id: str) -> BackgroundJobRecord:
        """按主键 job_id 读取 Job 记录。"""

        with self._session_factory() as db:
            model = db.get(BackgroundJobModel, job_id)
            if model is None:
                raise LookupError(f"Job 不存在：{job_id}")
            return _job_record(model)

    def get_job_by_resource(
        self,
        *,
        job_type: str,
        business_resource_id: str,
    ) -> BackgroundJobRecord | None:
        """按 任务类型+业务资源 ID 查找最近一条 Job。"""

        with self._session_factory() as db:
            model = db.scalar(
                select(BackgroundJobModel)
                .where(
                    BackgroundJobModel.job_type == job_type,
                    BackgroundJobModel.business_resource_id == business_resource_id,
                )
                .order_by(BackgroundJobModel.created_at.desc())
                .limit(1)
            )
            return _job_record(model) if model else None

    def list_pending_jobs(self, *, job_type: str, limit: int = 10) -> list[BackgroundJobRecord]:
        """查询待处理的 PENDING/QUEUED Job。"""

        with self._session_factory() as db:
            models = (
                db.scalars(
                    select(BackgroundJobModel)
                    .where(
                        BackgroundJobModel.job_type == job_type,
                        BackgroundJobModel.status.in_((JobStatus.PENDING, JobStatus.QUEUED)),
                    )
                    .order_by(BackgroundJobModel.created_at.asc())
                    .limit(limit)
                )
                .all()
            )
            return [_job_record(m) for m in models]

    # ======================= 状态更新 =========================
    def mark_queued(self, job_id: str) -> BackgroundJobRecord:
        """PENDING → QUEUED。"""

        return self._update_status(job_id, JobStatus.QUEUED)

    def mark_running(self, job_id: str) -> BackgroundJobRecord:
        """QUEUED → RUNNING，记录开始时间并递增重试计数。"""

        with session_scope(self._session_factory) as db:
            model = db.get(BackgroundJobModel, job_id)
            if model is None:
                raise LookupError(f"Job 不存在：{job_id}")
            
            now = _utc_now()
            #状态更新为RUNNING
            model.status = JobStatus.RUNNING
            #增加尝试次数（如果次数超过3次自动退出）
            model.attempt = BackgroundJobModel.attempt + 1
            model.started_at = now
            model.updated_at = now

            db.flush()
            return _job_record(model)

    def mark_completed(self, job_id: str, result: dict[str, Any]) -> BackgroundJobRecord:
        """RUNNING → COMPLETED，保存结果。"""

        with session_scope(self._session_factory) as db:
            model = db.get(BackgroundJobModel, job_id)
            if model is None:
                raise LookupError(f"Job 不存在：{job_id}")

            now = _utc_now()
            #状态更新为COMPLETED
            model.status = JobStatus.COMPLETED
            #输出JSON格式结果
            model.result_json = json.dumps(result, ensure_ascii=False)
            model.error_message = None
            model.completed_at = now
            model.updated_at = now

            db.flush()
            return _job_record(model)

    def mark_failed(self, job_id: str, error: str) -> BackgroundJobRecord:
        """RUNNING → FAILED，记录错误信息。"""

        with session_scope(self._session_factory) as db:
            model = db.get(BackgroundJobModel, job_id)
            if model is None:
                raise LookupError(f"Job 不存在：{job_id}")
            
            now = _utc_now()
            model.status = JobStatus.FAILED
            model.error_message = error[:2000]  # 截断过长错误
            model.completed_at = now
            model.updated_at = now

            db.flush()
            return _job_record(model)

    def retry_job(self, job_id: str) -> BackgroundJobRecord:
        """FAILED → PENDING（重试），但不超过 max_attempts。"""

        with session_scope(self._session_factory) as db:
            model = db.get(BackgroundJobModel, job_id)
            if model is None:
                raise LookupError(f"Job 不存在：{job_id}")
            #超过3次直接结束
            if model.attempt >= model.max_attempts:
                raise RuntimeError(
                    f"Job {job_id} 已达最大重试次数 {model.max_attempts}"
                )
            
            model.status = JobStatus.PENDING
            model.error_message = None
            model.updated_at = _utc_now()
            
            db.flush()
            return _job_record(model)

    # ======================= 内部实现 =========================
    def _update_status(self, job_id: str, status: JobStatus) -> BackgroundJobRecord:
        """统一的状态更新语句"""

        with session_scope(self._session_factory) as db:
            #得到旧model
            model = db.get(BackgroundJobModel, job_id)
            if model is None:
                raise LookupError(f"Job 不存在：{job_id}")
            
            #更新status
            model.status = status
            #更新更新时间
            model.updated_at = _utc_now()
            db.flush()

            return _job_record(model)


__all__ = ["JobRepository"]
