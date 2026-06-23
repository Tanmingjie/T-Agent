"""Tests for api/repository.py using SQLite in-memory."""

import pytest

from api.repository import SQLModelRepository
from input.models import ExecutionRecord, Suite, TestCase
from storage.db import Store


@pytest.fixture
async def repo():
    store = Store(url="sqlite+aiosqlite://")
    await store.init()
    r = SQLModelRepository(store)
    yield r
    await store.close()


@pytest.mark.asyncio
async def test_suite_crud(repo):
    s = Suite(id="s1", name="Test Suite", base_url="https://example.com")
    await repo.create(s)
    assert (await repo.get_suite("s1")).name == "Test Suite"
    assert len(await repo.list_all()) == 1
    assert await repo.delete("s1") is True
    assert await repo.get_suite("s1") is None


@pytest.mark.asyncio
async def test_case_bulk_insert_and_list(repo):
    cases = [
        TestCase(
            id="tc1",
            name="Case 1",
            steps=["step a"],
            base_url="https://x.com",
            suite_id="s1",
        ),
        TestCase(
            id="tc2",
            name="Case 2",
            steps=["step b"],
            base_url="https://x.com",
            suite_id="s1",
        ),
    ]
    n = await repo.bulk_insert(cases)
    assert n == 2
    result = await repo.list_by_suite("s1")
    assert len(result) == 2


@pytest.mark.asyncio
async def test_run_lifecycle(repo):
    await repo.create_run("r1", "s1", 5)
    assert (await repo.get_run("r1"))["status"] == "running"
    await repo.update_run("r1", status="completed", passed_cases=5, finished_at=1234.0)
    r = await repo.get_run("r1")
    assert r["status"] == "completed"
    assert r["passed_cases"] == 5


# 〔2026-06-22 预置条件分类/确认随分类器退役,update_precondition_item 测试删除。〕


@pytest.mark.asyncio
async def test_migration_adds_missing_json_column_and_backfills():
    """旧库缺新列(precondition_items)时 init 自动补列并把已有行回填 '[]',读回不报错。"""
    import os
    import tempfile

    from sqlalchemy import text

    from storage.db import Store

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        s = Store(url=f"sqlite+aiosqlite:///{path}")
        # 模拟旧 schema:test_case 无 precondition_items 列
        async with s.engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE test_case (id TEXT PRIMARY KEY, name TEXT, "
                    "preconditions JSON, precondition_confirmed JSON, steps JSON, "
                    "expected JSON, base_url TEXT, suite_id TEXT, external_id TEXT, "
                    "owner TEXT, updated_at REAL)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO test_case VALUES ('TC1','n','[]','[]','[]','[]',"
                    "'http://x','s1',NULL,NULL,0)"
                )
            )
        await s.init()  # 触发迁移
        cases = await s.list_cases(suite_id="s1")
        assert len(cases) == 1
        assert cases[0].precondition_items == []  # 回填为空列表,读回不抛
        await s.close()
    finally:
        os.path.exists(path) and os.unlink(path)
