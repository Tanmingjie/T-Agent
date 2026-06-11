"""T-P03 单元测试:Alembic 迁移基线。

同步测试(非 async):env.py 内部用 asyncio.run 跑异步迁移,若在 pytest-asyncio 的
运行中事件循环里调用会炸,故这里用普通 def(无运行中 loop)。

验证:空库 ``upgrade head`` 建出与 SQLModel.metadata 一致的全部表;``downgrade base`` 清空。
"""

from __future__ import annotations

import os
import sqlite3

from alembic import command
from alembic.config import Config
from sqlmodel import SQLModel

import storage.db  # noqa: F401  注册表到 metadata

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cfg(db_path: str) -> Config:
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.replace(os.sep, '/')}"
    cfg = Config(os.path.join(_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_ROOT, "migrations"))
    return cfg


def _tables(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def test_upgrade_head_creates_all_model_tables(tmp_path):
    db = str(tmp_path / "mig.db")
    prev = os.environ.get("DATABASE_URL")
    try:
        cfg = _cfg(db)
        command.upgrade(cfg, "head")
        tables = _tables(db)
        # 所有模型表都建出来了
        model_tables = {t.name for t in SQLModel.metadata.sorted_tables}
        assert model_tables <= tables
        # 多租户新表在内
        assert {"project", "version", "app_user", "project_member"} <= tables
        assert "alembic_version" in tables
    finally:
        if prev is not None:
            os.environ["DATABASE_URL"] = prev
        else:
            os.environ.pop("DATABASE_URL", None)


def test_downgrade_base_drops_model_tables(tmp_path):
    db = str(tmp_path / "mig.db")
    prev = os.environ.get("DATABASE_URL")
    try:
        cfg = _cfg(db)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        tables = _tables(db)
        # 模型表被清掉(alembic_version 保留是正常的)
        assert "suite" not in tables
        assert "project" not in tables
    finally:
        if prev is not None:
            os.environ["DATABASE_URL"] = prev
        else:
            os.environ.pop("DATABASE_URL", None)
