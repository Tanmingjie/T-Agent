"""add selected case ids to run queue

Revision ID: 0007_run_queue_case_ids
Revises: 0006_suite_login_setup_case
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_run_queue_case_ids"
down_revision = "0006_suite_login_setup_case"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "run_queue" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("run_queue")}
    if "case_ids" not in cols:
        op.add_column("run_queue", sa.Column("case_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "run_queue" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("run_queue")}
    if "case_ids" in cols:
        with op.batch_alter_table("run_queue") as batch:
            batch.drop_column("case_ids")
