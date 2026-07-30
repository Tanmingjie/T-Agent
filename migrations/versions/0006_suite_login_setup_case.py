"""add suite login setup case

Revision ID: 0006_suite_login_setup_case
Revises: 0005_drop_run_queue_executor_backend
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_suite_login_setup_case"
down_revision = "0005_drop_run_queue_executor_backend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "suite_settings" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("suite_settings")}
    if "login_setup_case_id" not in cols:
        op.add_column(
            "suite_settings",
            sa.Column("login_setup_case_id", sa.String(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "suite_settings" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("suite_settings")}
    if "login_setup_case_id" in cols:
        with op.batch_alter_table("suite_settings") as batch:
            batch.drop_column("login_setup_case_id")
