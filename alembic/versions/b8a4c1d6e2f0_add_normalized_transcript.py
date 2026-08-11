"""add normalized transcript to interview answers

Revision ID: b8a4c1d6e2f0
Revises: 4a1d9c7e2b6f
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8a4c1d6e2f0"
down_revision: str | Sequence[str] | None = "4a1d9c7e2b6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store the evaluator-derived transcript without replacing raw ASR text."""

    op.add_column(
        "interview_answers",
        sa.Column("normalized_transcript", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove the derived transcript column."""

    op.drop_column("interview_answers", "normalized_transcript")
