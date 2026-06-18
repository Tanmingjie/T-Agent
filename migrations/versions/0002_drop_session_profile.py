"""drop session_profile:会话/Cookie 复用退役(2026-06-18)

Revision ID: 0002_drop_session_profile
Revises: 0001_baseline
Create Date: 2026-06-18

会话复用从「Cookie 抓取/注入 + SessionProfile」整体退役(对 SPA/Token 型登录不对症,
TTL 与真实会话寿命脱节)。登录态复用改由后续「环境管理」主线维护;Hook 回归纯通用扩展点。
本迁移:drop ``session_profile`` 表 + 去掉 ``suite.session_profile`` 列(SQLite 走 batch 重建)。

downgrade 重建表/列(空,仅供回滚,不恢复历史数据)。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_drop_session_profile"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "session_profile" in tables:
        op.drop_table("session_profile")

    if "suite" in tables:
        cols = {c["name"] for c in insp.get_columns("suite")}
        if "session_profile" in cols:
            with op.batch_alter_table("suite") as batch:
                batch.drop_column("session_profile")


def downgrade() -> None:
    # 回滚:重建表/列(不恢复历史数据)。
    with op.batch_alter_table("suite") as batch:
        batch.add_column(sa.Column("session_profile", sa.String(), nullable=True))
    op.create_table(
        "session_profile",
        sa.Column("name", sa.String(), primary_key=True),
        sa.Column("login_aw", sa.String(), nullable=True),
        sa.Column("cookie_store", sa.String(), nullable=True),
        sa.Column("valid_until", sa.Float(), nullable=True),
        sa.Column("base_url", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("cookies_encrypted", sa.String(), nullable=True),
        sa.Column("owner", sa.String(), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=True),
    )
