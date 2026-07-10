"""run_queue 加 executor_backend:支持可选 Midscene 执行后端

Revision ID: 0004_run_queue_executor_backend
Revises: 0003_run_queue_skill_names
Create Date: 2026-07-10

执行触发时可选择执行后端。embedded 模式经函数参数透传;queue 模式需把选择
落库随队列任务带给 worker。旧行回填 react,保持现有 ReAct 链路为默认。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_run_queue_executor_backend"
down_revision = "0003_run_queue_skill_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
        op.execute("UPDATE run_queue SET executor_backend = 'react' WHERE executor_backend IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("run_queue") as batch:
        batch.drop_column("executor_backend")
