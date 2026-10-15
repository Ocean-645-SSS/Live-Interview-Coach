"""add recoverable answer drafts

Revision ID: d9a8b7c6e5f4
Revises: c2f6d4a9e710
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9a8b7c6e5f4"
down_revision: str | Sequence[str] | None = "c2f6d4a9e710"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_drafts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("question_id", sa.String(length=255), nullable=False),
        sa.Column("answer_number", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "OPEN",
                "SUBMITTED",
                name="ck_answer_drafts_state_values",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("answer_number >= 1", name="ck_answer_drafts_answer_number"),
        sa.ForeignKeyConstraint(["session_id"], ["interview_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "question_id",
            "answer_number",
            name="uq_answer_drafts_session_question_number",
        ),
    )
    op.create_index(
        "idx_answer_drafts_session_question_state",
        "answer_drafts",
        ["session_id", "question_id", "state"],
        unique=False,
    )
    op.create_table(
        "answer_draft_segments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("draft_id", sa.String(length=64), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("segment_index >= 0", name="ck_answer_draft_segments_index"),
        sa.CheckConstraint(
            "length(trim(transcript)) > 0",
            name="ck_answer_draft_segments_transcript",
        ),
        sa.ForeignKeyConstraint(["draft_id"], ["answer_drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "draft_id",
            "segment_index",
            name="uq_answer_draft_segments_draft_index",
        ),
    )


def downgrade() -> None:
    op.drop_table("answer_draft_segments")
    op.drop_index("idx_answer_drafts_session_question_state", table_name="answer_drafts")
    op.drop_table("answer_drafts")
