"""add case executability assessment

Revision ID: 0009_case_executability_assessment
Revises: 0008_case_execution_memory
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_case_executability_assessment"
down_revision = "0008_case_execution_memory"
branch_labels = None
depends_on = None

ASSESSMENT_COLUMNS = (
    ("assessment_version", sa.Column("assessment_version", sa.String(), nullable=True)),
    ("context_hash", sa.Column("context_hash", sa.String(), nullable=True)),
    (
        "cache_hit",
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
    ),
    ("source_assessment_id", sa.Column("source_assessment_id", sa.String(), nullable=True)),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "run_queue" in tables:
        cols = {c["name"] for c in insp.get_columns("run_queue")}
        if "quality_gate_enabled" not in cols:
            op.add_column(
                "run_queue",
                sa.Column(
                    "quality_gate_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
                ),
            )
        if "force_low_quality_cases" not in cols:
            op.add_column(
                "run_queue",
                sa.Column(
                    "force_low_quality_cases",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
            )
        if "quality_override_reason" not in cols:
            op.add_column(
                "run_queue",
                sa.Column("quality_override_reason", sa.String(), nullable=True),
            )
    if "case_executability_assessment" in tables:
        cols = {c["name"] for c in insp.get_columns("case_executability_assessment")}
        for name, column in ASSESSMENT_COLUMNS:
            if name not in cols:
                op.add_column("case_executability_assessment", column)
    else:
        op.create_table(
            "case_executability_assessment",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("version_id", sa.String(), nullable=True),
            sa.Column("suite_id", sa.String(), nullable=True),
            sa.Column("case_id", sa.String(), nullable=True),
            sa.Column("run_id", sa.String(), nullable=True),
            sa.Column("case_hash", sa.String(), nullable=True),
            sa.Column("base_url", sa.String(), nullable=True),
            *(column.copy() for _, column in ASSESSMENT_COLUMNS),
            sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("risk_level", sa.String(), nullable=True),
            sa.Column("gate_decision", sa.String(), nullable=True),
            sa.Column("dimensions", sa.JSON(), nullable=True),
            sa.Column("issues", sa.JSON(), nullable=True),
            sa.Column("rewrite_suggestions", sa.JSON(), nullable=True),
            sa.Column("draft_steps", sa.JSON(), nullable=True),
            sa.Column("draft_expected", sa.JSON(), nullable=True),
            sa.Column("context_sources", sa.JSON(), nullable=True),
            sa.Column("override_reason", sa.String(), nullable=True),
            sa.Column("error", sa.String(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.Float(), nullable=False, server_default="0"),
        )
    existing_indexes = {
        index["name"] for index in insp.get_indexes("case_executability_assessment")
    }
    for column in (
        "project_id",
        "version_id",
        "suite_id",
        "case_id",
        "run_id",
        "case_hash",
        "assessment_version",
        "context_hash",
        "cache_hit",
        "source_assessment_id",
        "risk_level",
        "gate_decision",
    ):
        index_name = f"ix_case_executability_assessment_{column}"
        if index_name in existing_indexes:
            continue
        op.create_index(
            index_name,
            "case_executability_assessment",
            [column],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "case_executability_assessment" in tables:
        op.drop_table("case_executability_assessment")
    if "run_queue" in tables:
        cols = {c["name"] for c in insp.get_columns("run_queue")}
        with op.batch_alter_table("run_queue") as batch:
            for column in (
                "quality_gate_enabled",
                "force_low_quality_cases",
                "quality_override_reason",
            ):
                if column in cols:
                    batch.drop_column(column)
