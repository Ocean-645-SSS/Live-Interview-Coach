"""add background job leases

Revision ID: c2f6d4a9e710
Revises: b8a4c1d6e2f0
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c2f6d4a9e710"
down_revision: str | Sequence[str] | None = "b8a4c1d6e2f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("interview_background_jobs", sa.Column("lease_token", sa.String(64)))
    op.add_column(
        "interview_background_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True))
    )
    op.create_index(
        "idx_background_jobs_running_lease",
        "interview_background_jobs",
        ["status", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_background_jobs_running_lease", table_name="interview_background_jobs")
    op.drop_column("interview_background_jobs", "lease_expires_at")
    op.drop_column("interview_background_jobs", "lease_token")
