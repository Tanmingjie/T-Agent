"""Tests for api/repository.py using SQLite in-memory."""

import pytest

from api.repository import SQLModelRepository, resolve_effective_cases
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


def _case(case_id: str) -> TestCase:
    return TestCase(
        id=case_id,
        name=case_id,
        steps=[case_id],
        base_url="https://x.com",
        suite_id="s1",
    )


def test_resolve_effective_cases_supports_full_single_and_multi_selection():
    cases = [_case("A"), _case("B"), _case("C")]

    full, _ = resolve_effective_cases(cases)
    single, _ = resolve_effective_cases(cases, requested_case_ids=["B"])
    selected, _ = resolve_effective_cases(cases, requested_case_ids=["C", "A"])

    assert [case.id for case in full] == ["A", "B", "C"]
    assert [case.id for case in single] == ["B"]
    assert [case.id for case in selected] == ["A", "C"]


@pytest.mark.parametrize(
    ("requested", "message"),
    [([], "不能为空"), (["A", "A"], "重复"), (["missing"], "不存在于该套件")],
)
def test_resolve_effective_cases_rejects_invalid_selection(requested, message):
    with pytest.raises(ValueError, match=message):
        resolve_effective_cases([_case("A")], requested_case_ids=requested)


def test_resolve_effective_cases_inserts_login_once():
    cases = [_case("login"), _case("A"), _case("B")]

    selected, login = resolve_effective_cases(
        cases,
        requested_case_ids=["B", "A"],
        login_setup_case_id="login",
    )
    includes_login, _ = resolve_effective_cases(
        cases,
        requested_case_ids=["login", "B"],
        login_setup_case_id="login",
    )
    login_only, _ = resolve_effective_cases(
        cases,
        requested_case_ids=["login"],
        login_setup_case_id="login",
    )

    assert login is not None and login.id == "login"
    assert [case.id for case in selected] == ["login", "A", "B"]
    assert [case.id for case in includes_login] == ["login", "B"]
    assert [case.id for case in login_only] == ["login"]


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
