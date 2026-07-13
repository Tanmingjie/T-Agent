"""drop run_queue executor_backend

Revision ID: 0005_drop_run_queue_executor_backend
Revises: 0004_run_queue_executor_backend
Create Date: 2026-07-13

Midscene is now the only execution kernel. The queue no longer needs to carry
an execution backend selector.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_drop_run_queue_executor_backend"
down_revision = "0004_run_queue_executor_backend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "run_queue" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("run_queue")}
    if "executor_backend" in cols:
        with op.batch_alter_table("run_queue") as batch:
            batch.drop_column("executor_backend")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "run_queue" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("run_queue")}
    if "executor_backend" not in cols:
        op.add_column(
            "run_queue",
            sa.Column("executor_backend", sa.String(), nullable=True),
        )
        op.execute("UPDATE run_queue SET executor_backend = 'midscene'")
