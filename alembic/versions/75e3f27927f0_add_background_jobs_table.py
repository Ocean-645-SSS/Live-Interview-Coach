"""add background_jobs table

Revision ID: 75e3f27927f0
Revises: 975f61b782e0
Create Date: 2026-08-06 22:01:57.651491

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "75e3f27927f0"
down_revision: Union[str, Sequence[str], None] = "975f61b782e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 interview_background_jobs 表。"""
    op.create_table(
        "interview_background_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "QUEUED",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                name="ck_background_jobs_status_values",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("business_resource_id", sa.String(length=255), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt >= 0", name="ck_background_jobs_attempt"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_background_jobs_max_attempts"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_type", "idempotency_key", name="uq_background_jobs_idempotency"
        ),
    )
    op.create_index(
        "idx_background_jobs_resource",
        "interview_background_jobs",
        ["job_type", "business_resource_id"],
        unique=False,
    )
    op.create_index(
        "idx_background_jobs_status",
        "interview_background_jobs",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """删除 interview_background_jobs 表。"""
    op.drop_index("idx_background_jobs_status", table_name="interview_background_jobs")
    op.drop_index("idx_background_jobs_resource", table_name="interview_background_jobs")
    op.drop_table("interview_background_jobs")
