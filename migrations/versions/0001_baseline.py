"""baseline:从 SQLModel.metadata 建全量当前 schema(平台化 T-P03)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-10

基线迁移:用 ``metadata.create_all`` 一次建出当前所有 ``*Row`` 表(含多租户表)。
此后改模型 → ``alembic revision --autogenerate`` 生成增量迁移(autogenerate 对比模型 vs
库,基线由同一份 metadata 建,故首次无 diff)。downgrade 整体 drop_all。
"""

from __future__ import annotations

import sqlmodel  # noqa: F401  (迁移约定:SQLModel 列类型需要)
from alembic import op
from sqlmodel import SQLModel

import storage.db  # noqa: F401  导入副作用:把所有表注册进 metadata

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    SQLModel.metadata.create_all(op.get_bind())


def downgrade() -> None:
    SQLModel.metadata.drop_all(op.get_bind())
