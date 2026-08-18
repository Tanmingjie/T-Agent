"""add case execution memory

Revision ID: 0008_case_execution_memory
Revises: 0007_run_queue_case_ids
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_case_execution_memory"
down_revision = "0007_run_queue_case_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "run_queue" in tables:
        cols = {c["name"] for c in insp.get_columns("run_queue")}
        if "retranslate_case_ids" not in cols:
            op.add_column("run_queue", sa.Column("retranslate_case_ids", sa.JSON(), nullable=True))
    if "case_execution_memory" in tables:
        return
    op.create_table(
        "case_execution_memory",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("version_id", sa.String(), nullable=True),
        sa.Column("suite_id", sa.String(), nullable=True),
        sa.Column("case_id", sa.String(), nullable=True),
        sa.Column("base_url", sa.String(), nullable=True),
        sa.Column("case_hash", sa.String(), nullable=True),
        sa.Column("spec", sa.JSON(), nullable=True),
        sa.Column("experience", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_run_id", sa.String(), nullable=True),
        sa.Column("source_exec_id", sa.String(), nullable=True),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Float(), nullable=False, server_default="0"),
    )
    for column in ("project_id", "version_id", "suite_id", "case_id", "case_hash"):
        op.create_index(f"ix_case_execution_memory_{column}", "case_execution_memory", [column])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "case_execution_memory" in tables:
        op.drop_table("case_execution_memory")
    if "run_queue" in tables:
        cols = {c["name"] for c in insp.get_columns("run_queue")}
        if "retranslate_case_ids" in cols:
            with op.batch_alter_table("run_queue") as batch:
                batch.drop_column("retranslate_case_ids")
