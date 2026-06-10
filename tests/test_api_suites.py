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
async def test_update_precondition_item_endpoint(client):
    import api.server as srv
    from input.models import PreconditionItem

    tc = TestCase(
        id="tc1",
        name="Case",
        preconditions=["准备好测试环境"],
        precondition_items=[
            PreconditionItem(text="准备好测试环境", type="ambiguous", confidence=0.3)
        ],
        suite_id="s1",
    )
    await srv._repo.bulk_insert([tc])

    # 用户标黄确认为 state_hook + 指定 Hook
    r = await client.put(
        "/api/suites/s1/cases/tc1/precondition-item",
        json={"index": 0, "type": "state_hook", "hook_ref": "LoginHook"},
    )
    assert r.status_code == 200

    r = await client.get("/api/suites/s1/cases/tc1")
    item = r.json()["precondition_items"][0]
    assert item["type"] == "state_hook"
    assert item["hook_ref"] == "LoginHook"
    assert item["confirmed_by_user"] is True

    # 非法 type → 422
    r = await client.put(
        "/api/suites/s1/cases/tc1/precondition-item",
        json={"index": 0, "type": "bogus"},
    )
    assert r.status_code == 422

    # 用例不存在 → 404
    r = await client.put(
        "/api/suites/s1/cases/nope/precondition-item",
        json={"index": 0, "type": "ignore"},
    )
    assert r.status_code == 404
