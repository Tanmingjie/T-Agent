"""Tests for permission API."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from api.repository import SQLModelRepository
from api.routers.execution import _permission_events, _permission_results
from api.server import app
from storage.db import Store


@pytest.fixture
async def client():
    # 端点同时支持内存(embedded)与 DB(queue)审批通道,需有 store
    store = Store(url="sqlite+aiosqlite://")
    await store.init()
    import api.server as srv

    srv._store = store
    srv._repo = SQLModelRepository(store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await store.close()
    srv._store = None
    srv._repo = None
    _permission_events.clear()
    _permission_results.clear()


@pytest.mark.asyncio
async def test_approve(client):
    event = asyncio.Event()
    _permission_events["p1"] = event
    _permission_results["p1"] = {"approved": False}

    r = await client.post("/api/suites/s1/permission/p1", json={"choice": "approve"})
    assert r.status_code == 200
    assert event.is_set()
    assert _permission_results["p1"]["approved"] is True


@pytest.mark.asyncio
async def test_reject(client):
    event = asyncio.Event()
    _permission_events["p2"] = event
    _permission_results["p2"] = {"approved": False}

    r = await client.post("/api/suites/s1/permission/p2", json={"choice": "reject"})
    assert r.status_code == 200
    assert _permission_results["p2"]["approved"] is False


@pytest.mark.asyncio
async def test_not_found(client):
    r = await client.post("/api/suites/s1/permission/ghost", json={"choice": "approve"})
    assert r.status_code == 404
