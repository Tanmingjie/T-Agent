"""Tests for Suite CRUD API routes."""

import pytest
from httpx import ASGITransport, AsyncClient

from api.repository import SQLModelRepository
from api.server import app
from input.models import Suite, TestCase
from storage.db import Store


@pytest.fixture
async def client():
    store = Store(url="sqlite+aiosqlite://")
    await store.init()
    repo = SQLModelRepository(store)
    import api.server as srv

    srv._repo = repo
    srv._store = store
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await store.close()
    srv._repo = None
    srv._store = None


@pytest.mark.asyncio
async def test_create_and_list_suites(client):
    r = await client.post("/api/suites", json={"name": "S1", "base_url": "https://x.com"})
    assert r.status_code == 200
    sid = r.json()["id"]

    r = await client.get("/api/suites")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["name"] == "S1"


@pytest.mark.asyncio
async def test_get_suite_404(client):
    r = await client.get("/api/suites/nonexistent")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_suite(client):
    r = await client.post("/api/suites", json={"name": "Del", "base_url": "https://x.com"})
    sid = r.json()["id"]

    r = await client.delete(f"/api/suites/{sid}")
    assert r.status_code == 200

    r = await client.get(f"/api/suites/{sid}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_case_spec_prompt_includes_case_and_knowledge(client):
    """翻译 prompt 预览:返回 system+user,user 含用例步骤;项目有用例规范则注入 + 标记。"""
    import api.server as srv
    from input.models import Project

    await srv._store.save_project(
        Project(id="p1", name="P", translation_knowledge="提交前必须先选审批人。")
    )
    r = await client.post(
        "/api/suites",
        json={"name": "S", "base_url": "https://x.com", "project_id": "p1"},
    )
    sid = r.json()["id"]
    await srv._repo.bulk_insert(
        [
            TestCase(
                id="tc1",
                name="下单",
                steps=["填写订单", "点击提交"],
                base_url="https://x.com",
                suite_id=sid,
            )
        ]
    )
    r = await client.get(f"/api/suites/{sid}/cases/tc1/spec-prompt")
    assert r.status_code == 200
    body = r.json()
    assert "测试规格翻译器" in body["system"]  # 翻译 system prompt
    assert "点击提交" in body["user"]  # 用例步骤进了 user
    assert body["knowledge_used"] is True
    assert "提交前必须先选审批人" in body["user"]  # 用例规范注入


@pytest.mark.asyncio
async def test_case_spec_prompt_404_unknown_case(client):
    r = await client.post("/api/suites", json={"name": "S", "base_url": "https://x.com"})
    sid = r.json()["id"]
    r = await client.get(f"/api/suites/{sid}/cases/nope/spec-prompt")
    assert r.status_code == 404


# 〔2026-06-22 预置条件分类/确认端点随分类器退役,相关端点测试删除。〕


@pytest.mark.asyncio
async def test_cross_suite_same_case_number_no_collision(client):
    """不同套件上传同号用例(TC101)不再互相覆盖:各自 namespaced id,旧套件用例不丢。"""
    import api.server as srv
    from api.routers.suites import namespaced_case_id

    s1, s2 = "suiteA", "suiteB"
    c1 = TestCase(id=namespaced_case_id(s1, "TC101"), name="A的用例", suite_id=s1)
    c2 = TestCase(id=namespaced_case_id(s2, "TC101"), name="B的用例", suite_id=s2)
    await srv._repo.bulk_insert([c1])
    await srv._repo.bulk_insert([c2])

    a = await srv._repo.list_by_suite(s1)
    b = await srv._repo.list_by_suite(s2)
    assert len(a) == 1 and a[0].name == "A的用例"  # 旧套件用例仍在
    assert len(b) == 1 and b[0].name == "B的用例"
    assert a[0].id != b[0].id


def test_namespaced_case_id_idempotent():
    from api.routers.suites import namespaced_case_id

    once = namespaced_case_id("s1", "TC101")
    assert once == "s1--TC101"
    assert namespaced_case_id("s1", once) == once  # 已带前缀不重复加
