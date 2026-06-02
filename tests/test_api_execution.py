"""Tests for execution API routes (SSE and settings)."""

import asyncio

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
    s = Suite(id="sx", name="SX", base_url="https://x.com")
    await repo.create(s)
    await repo.bulk_insert(
        [
            TestCase(
                id="t1",
                name="C1",
                steps=["do a", "do b"],
                base_url="https://x.com",
                suite_id="sx",
            ),
        ]
    )
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
async def test_get_settings_default(client):
    r = await client.get("/api/suites/sx/settings")
    assert r.status_code == 200
    assert r.json()["permission_mode"] == "trust"


@pytest.mark.asyncio
async def test_update_settings(client):
    r = await client.put("/api/suites/sx/settings", json={"permission_mode": "approve"})
    assert r.status_code == 200

    r = await client.get("/api/suites/sx/settings")
    assert r.json()["permission_mode"] == "approve"
