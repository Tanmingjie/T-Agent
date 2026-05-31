"""T-21 单元测试:SQLModel 持久化(storage/db.py)。

TDD:先定义仓储 round-trip / 过滤 / upsert 期望,再实现。
业务侧只与领域模型(input.models)打交道,不直接写 SQL。
"""

from __future__ import annotations

import pytest

from input.models import (
    ActionStep,
    ExecutionRecord,
    PageVocabulary,
    SessionProfile,
    Suite,
    TestCase,
)
from storage.db import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await s.init()
    yield s
    await s.close()


# ── TestCase ─────────────────────────────────────────────────


async def test_case_roundtrip_preserves_lists(store):
    tc = TestCase(
        id="TC001",
        name="登录",
        preconditions=["已登录", "有订单"],
        steps=["打开页面", "点击提交"],
        expected=["状态为待审批"],
        base_url="https://x",
        suite_id="S1",
    )
    await store.save_case(tc)
    got = await store.get_case("TC001")
    assert got is not None
    assert got.preconditions == ["已登录", "有订单"]
    assert got.steps == ["打开页面", "点击提交"]
    assert got.suite_id == "S1"


async def test_get_missing_case_returns_none(store):
    assert await store.get_case("nope") is None


async def test_list_cases_filter_by_suite(store):
    await store.save_case(TestCase(id="A", name="a", suite_id="S1"))
    await store.save_case(TestCase(id="B", name="b", suite_id="S2"))
    await store.save_case(TestCase(id="C", name="c", suite_id="S1"))
    s1 = await store.list_cases(suite_id="S1")
    assert {c.id for c in s1} == {"A", "C"}
    assert len(await store.list_cases()) == 3


async def test_case_upsert_updates_not_duplicates(store):
    await store.save_case(TestCase(id="TC1", name="旧名"))
    await store.save_case(TestCase(id="TC1", name="新名"))
    assert len(await store.list_cases()) == 1
    assert (await store.get_case("TC1")).name == "新名"


# ── ExecutionRecord(含嵌套 ActionStep / case_assertions)─────


async def test_record_roundtrip_with_nested_steps(store):
    rec = ExecutionRecord(
        exec_id="e1",
        case_id="TC001",
        suite_id="S1",
        steps=[
            ActionStep(
                step_no=1, tool_name="browser_click", tool_input={"ref": "e3"}, url="http://x"
            ),
            ActionStep(step_no=2, tool_name="mark_step_done", reasoning="完成"),
        ],
        case_assertions=[{"type": "url_contains", "status": "pass"}],
        passed=True,
        final_result="[PASS] ...",
        token_usage=1234,
    )
    await store.save_record(rec)
    got = await store.get_record("e1")
    assert got is not None
    assert got.passed is True
    assert got.token_usage == 1234
    assert len(got.steps) == 2
    assert got.steps[0].tool_name == "browser_click"
    assert got.steps[0].tool_input == {"ref": "e3"}
    assert got.case_assertions[0]["status"] == "pass"


async def test_list_records_filter_by_case(store):
    await store.save_record(ExecutionRecord(exec_id="e1", case_id="TC1"))
    await store.save_record(ExecutionRecord(exec_id="e2", case_id="TC2"))
    await store.save_record(ExecutionRecord(exec_id="e3", case_id="TC1"))
    recs = await store.list_records(case_id="TC1")
    assert {r.exec_id for r in recs} == {"e1", "e3"}


# ── Suite / SessionProfile / PageVocabulary ──────────────────


async def test_suite_roundtrip_with_hooks(store):
    suite = Suite(id="S1", name="冒烟", base_url="https://x", hooks={"before_case": ["LoginHook"]})
    await store.save_suite(suite)
    got = await store.get_suite("S1")
    assert got.hooks == {"before_case": ["LoginHook"]}


async def test_session_profile_roundtrip(store):
    p = SessionProfile(
        name="prof",
        login_aw="aw.py",
        cookie_store="/tmp/c.json",
        base_url="https://x",
        valid_until=123.0,
    )
    await store.save_session_profile(p)
    got = await store.get_session_profile("prof")
    assert got.login_aw == "aw.py"
    assert got.valid_until == 123.0


async def test_vocabulary_roundtrip_and_cache_key(store):
    v = PageVocabulary(
        url_pattern="/order/{id}",
        page_title="订单",
        login_role="admin",
        vocabulary={"提交": {"role": "button", "name": "提交"}},
    )
    await store.save_vocabulary(v)
    got = await store.get_vocabulary("/order/{id}", "订单", "admin")
    assert got is not None
    assert got.vocabulary["提交"]["name"] == "提交"


async def test_vocabulary_upsert_by_cache_key(store):
    key = dict(url_pattern="/p", page_title="t", login_role="r")
    await store.save_vocabulary(PageVocabulary(**key, vocabulary={"a": 1}))
    await store.save_vocabulary(PageVocabulary(**key, vocabulary={"a": 2}, stale=True))
    got = await store.get_vocabulary("/p", "t", "r")
    assert got.vocabulary == {"a": 2}
    assert got.stale is True
    assert len(await store.list_vocabularies()) == 1


# ── 预留同步字段 ─────────────────────────────────────────────


async def test_updated_at_refreshed_on_save(store):
    tc = TestCase(id="TC1", name="x", updated_at=1.0)
    await store.save_case(tc)
    got = await store.get_case("TC1")
    assert got.updated_at > 1.0  # 保存时刷新
