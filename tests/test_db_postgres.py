"""T-P01 Postgres 方言验证(平台化路径)。

仅当设置了 env ``DATABASE_URL_TEST_PG``(指向一个可写的本地 PG 测试库)时才运行,
否则整文件 skip——本机/CI 无 PG 时不影响全量单测。验证:同一套 Store 业务码在
asyncpg 方言下 round-trip / upsert / JSON 列 / 缺列迁移均与 SQLite 行为一致。

本地起库后:
    $env:DATABASE_URL_TEST_PG = "postgresql+asyncpg://postgres@localhost:5432/tagent_test"
    python -m pytest tests/test_db_postgres.py -q
"""

from __future__ import annotations

import os

import pytest

from input.models import (
    ExecutionRecord,
    PageVocabulary,
    Project,
    ProjectMember,
    Suite,
    TestCase,
    Version,
)
from storage.db import Store

_PG_URL = os.getenv("DATABASE_URL_TEST_PG")

pytestmark = pytest.mark.skipif(
    not _PG_URL, reason="未设置 DATABASE_URL_TEST_PG,跳过 Postgres 方言验证"
)


@pytest.fixture
async def pg_store():
    s = Store(_PG_URL)
    assert not s.is_sqlite  # 确认走的是 PG 分支
    # 每个测试干净起步:drop_all 再 init(create_all + 迁移)
    from sqlmodel import SQLModel

    async with s.engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await s.init()
    yield s
    await s.close()


async def test_case_roundtrip_pg(pg_store):
    tc = TestCase(
        id="TC001",
        name="登录",
        preconditions=["已登录", "有订单"],
        steps=["打开页面", "点击提交"],
        base_url="https://x",
        suite_id="S1",
    )
    await pg_store.save_case(tc)
    got = await pg_store.get_case("TC001")
    assert got is not None
    assert got.preconditions == ["已登录", "有订单"]
    assert got.steps == ["打开页面", "点击提交"]


async def test_case_upsert_pg(pg_store):
    await pg_store.save_case(TestCase(id="TC1", name="旧名"))
    await pg_store.save_case(TestCase(id="TC1", name="新名"))
    assert len(await pg_store.list_cases()) == 1
    assert (await pg_store.get_case("TC1")).name == "新名"


async def test_record_json_roundtrip_pg(pg_store):
    rec = ExecutionRecord(
        exec_id="e1",
        case_id="TC001",
        case_assertions=[{"type": "url_contains", "status": "pass"}],
        passed=True,
    )
    await pg_store.save_record(rec)
    got = await pg_store.get_record("e1")
    assert got is not None
    assert got.passed is True
    assert got.case_assertions[0]["status"] == "pass"  # JSON 列 round-trip


async def test_suite_hooks_json_pg(pg_store):
    await pg_store.save_suite(
        Suite(id="S1", name="冒烟", base_url="https://x", hooks={"before_case": ["LoginHook"]})
    )
    got = await pg_store.get_suite("S1")
    assert got.hooks == {"before_case": ["LoginHook"]}


async def test_vocabulary_cache_key_pg(pg_store):
    key = dict(url_pattern="/p", page_title="t", login_role="r", base_url="https://x")
    await pg_store.save_vocabulary(PageVocabulary(**key, vocabulary={"a": 1}))
    await pg_store.save_vocabulary(PageVocabulary(**key, vocabulary={"a": 2}, stale=True))
    got = await pg_store.get_vocabulary("/p", "t", "r", base_url="https://x")
    assert got.vocabulary == {"a": 2}
    assert got.stale is True
    assert len(await pg_store.list_vocabularies()) == 1


async def test_tenancy_tables_pg(pg_store):
    # 多租户新表在 PG 上 create_all + round-trip + 复合主键 upsert
    await pg_store.save_project(Project(id="p1", name="支付", owner="alice"))
    await pg_store.save_version(Version(id="v1", project_id="p1", name="1.0"))
    await pg_store.save_member(ProjectMember(project_id="p1", user_id="alice", role="tester"))
    await pg_store.save_member(ProjectMember(project_id="p1", user_id="alice", role="admin"))
    assert (await pg_store.get_project("p1")).name == "支付"
    assert {v.id for v in await pg_store.list_versions("p1")} == {"v1"}
    assert (await pg_store.get_member("p1", "alice")).role == "admin"  # upsert,非两行
    assert len(await pg_store.list_members("p1")) == 1
