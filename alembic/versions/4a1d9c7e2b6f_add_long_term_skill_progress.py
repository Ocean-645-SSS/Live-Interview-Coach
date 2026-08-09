"""add long term skill progress

Revision ID: 4a1d9c7e2b6f
Revises: 75e3f27927f0
Create Date: 2026-08-09
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op

revision: str = "4a1d9c7e2b6f"
down_revision: str | Sequence[str] | None = "75e3f27927f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _candidate_profile_id_for_kb(kb_id: str) -> str:
    normalized = " ".join(kb_id.strip().casefold().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"candidate_profile_{digest}"


def upgrade() -> None:
    """创建候选人聚合根、技能画像和评价证据结构。"""

    op.create_table(
        "candidate_profiles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("kb_id", sa.String(length=255), nullable=False),
        sa.Column("latest_profile_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kb_id"),
    )
    op.add_column(
        "interviews",
        sa.Column("candidate_profile_id", sa.String(length=64), nullable=True),
    )

    connection = op.get_bind()
    interviews = sa.table(
        "interviews",
        sa.column("id", sa.String(length=64)),
        sa.column("config_json", sa.Text()),
        sa.column("candidate_profile_id", sa.String(length=64)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    candidate_profiles = sa.table(
        "candidate_profiles",
        sa.column("id", sa.String(length=64)),
        sa.column("kb_id", sa.String(length=255)),
        sa.column("latest_profile_json", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    existing_candidate_ids: set[str] = set()
    rows = connection.execute(
        sa.select(
            interviews.c.id,
            interviews.c.config_json,
            interviews.c.created_at,
            interviews.c.updated_at,
        )
    ).mappings()
    for row in rows:
        config = json.loads(row["config_json"])
        kb_id = str(config.get("candidate_kb_id") or "default")
        candidate_profile_id = _candidate_profile_id_for_kb(kb_id)
        if candidate_profile_id not in existing_candidate_ids:
            now = datetime.now(timezone.utc)
            connection.execute(
                candidate_profiles.insert().values(
                    id=candidate_profile_id,
                    kb_id=kb_id,
                    latest_profile_json=None,
                    created_at=row["created_at"] or now,
                    updated_at=row["updated_at"] or now,
                )
            )
            existing_candidate_ids.add(candidate_profile_id)
        connection.execute(
            interviews.update()
            .where(interviews.c.id == row["id"])
            .values(candidate_profile_id=candidate_profile_id)
        )

    with op.batch_alter_table("interviews") as batch_op:
        batch_op.alter_column(
            "candidate_profile_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.create_foreign_key(
            "fk_interviews_candidate_profile_id",
            "candidate_profiles",
            ["candidate_profile_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_interviews_candidate_profile_id",
            ["candidate_profile_id"],
            unique=False,
        )

    op.create_table(
        "skill_progress",
        sa.Column("candidate_profile_id", sa.String(length=64), nullable=False),
        sa.Column("skill_key", sa.String(length=64), nullable=False),
        sa.Column("taxonomy_version", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("average_score", sa.Float(), nullable=False),
        sa.Column("current_score", sa.Float(), nullable=False),
        sa.Column("latest_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("weak_points_json", sa.Text(), nullable=False),
        sa.Column("source_evaluation_ids_json", sa.Text(), nullable=False),
        sa.Column("first_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("taxonomy_version >= 1", name="ck_skill_progress_taxonomy"),
        sa.CheckConstraint("attempts >= 1", name="ck_skill_progress_attempts"),
        sa.CheckConstraint(
            "average_score >= 0 AND average_score <= 100",
            name="ck_skill_progress_average_score",
        ),
        sa.CheckConstraint(
            "current_score >= 0 AND current_score <= 100",
            name="ck_skill_progress_current_score",
        ),
        sa.CheckConstraint(
            "latest_score >= 0 AND latest_score <= 100",
            name="ck_skill_progress_latest_score",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_skill_progress_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_profile_id"],
            ["candidate_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("candidate_profile_id", "skill_key"),
        sa.UniqueConstraint(
            "candidate_profile_id",
            "skill_key",
            name="uq_skill_progress_candidate_skill",
        ),
    )
    op.create_table(
        "skill_progress_evidence",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("candidate_profile_id", sa.String(length=64), nullable=False),
        sa.Column("skill_key", sa.String(length=64), nullable=False),
        sa.Column("evaluation_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("question_id", sa.String(length=255), nullable=False),
        sa.Column("taxonomy_version", sa.Integer(), nullable=False),
        sa.Column("rubric_version", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("weak_points_json", sa.Text(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "taxonomy_version >= 1", name="ck_skill_progress_evidence_taxonomy"
        ),
        sa.CheckConstraint(
            "rubric_version >= 1", name="ck_skill_progress_evidence_rubric"
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_skill_progress_evidence_score",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_profile_id", "skill_key"],
            ["skill_progress.candidate_profile_id", "skill_progress.skill_key"],
            name="fk_skill_progress_evidence_progress",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_id"], ["answer_evaluations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["interview_sessions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_profile_id",
            "skill_key",
            "evaluation_id",
            name="uq_skill_progress_evidence_candidate_skill_evaluation",
        ),
    )
    op.create_index(
        "idx_skill_progress_evidence_candidate_skill_time",
        "skill_progress_evidence",
        ["candidate_profile_id", "skill_key", "evaluated_at"],
        unique=False,
    )


def downgrade() -> None:
    """逐项撤销长期技能画像数据库对象。"""

    op.drop_index(
        "idx_skill_progress_evidence_candidate_skill_time",
        table_name="skill_progress_evidence",
    )
    op.drop_table("skill_progress_evidence")
    op.drop_table("skill_progress")
    with op.batch_alter_table("interviews") as batch_op:
        batch_op.drop_index("ix_interviews_candidate_profile_id")
        batch_op.drop_constraint(
            "fk_interviews_candidate_profile_id", type_="foreignkey"
        )
        batch_op.drop_column("candidate_profile_id")
    op.drop_table("candidate_profiles")
