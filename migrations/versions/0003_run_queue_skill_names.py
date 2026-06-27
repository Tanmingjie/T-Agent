"""run_queue 加 skill_names:执行时强制加载指定项目 skill(2026-06-27)

Revision ID: 0003_run_queue_skill_names
Revises: 0002_drop_session_profile
Create Date: 2026-06-27

执行触发时可勾选若干项目 skill 强制加载(一次性,随本次 run)。embedded 模式经函数参数
透传;queue 模式需把选择落库随队列任务带给 worker → run_queue 加 JSON 列 ``skill_names``。
旧行回填 '[]'(无强制加载,全走渐进披露)。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_run_queue_skill_names"
down_revision = "0002_drop_session_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "run_queue" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("run_queue")}
    if "skill_names" not in cols:
        op.add_column("run_queue", sa.Column("skill_names", sa.JSON(), nullable=True))
        op.execute("UPDATE run_queue SET skill_names = '[]' WHERE skill_names IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("run_queue") as batch:
        batch.drop_column("skill_names")
