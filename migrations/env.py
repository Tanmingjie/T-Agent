"""Alembic 异步 env(平台化 T-P03)。

要点:
- 连接串从 ``DATABASE_URL`` env 读(缺省 sqlite),与 ``storage.db.Store`` 一致 → 同一份
  schema 真相源。异步驱动(aiosqlite/asyncpg)直接复用,**不需要 psycopg2**。
- ``target_metadata = SQLModel.metadata``:导入 storage.db 把所有 ``*Row`` 表注册进来,
  autogenerate 才能对比模型与库。
- SQLModel 的列类型(AutoString 等)在迁移脚本里需 ``import sqlmodel``,见 script.py.mako。
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

# 导入副作用:注册所有表到 SQLModel.metadata
import storage.db  # noqa: F401

config = context.config

# 连接串:env 优先,与 Store 缺省一致
_db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///storage/ai_test.db")
config.set_main_option("sqlalchemy.url", _db_url)

target_metadata = SQLModel.metadata


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite ALTER 受限,用 batch 模式
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_db_url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
