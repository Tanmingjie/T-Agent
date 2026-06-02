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
