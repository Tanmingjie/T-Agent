"""Tests for vocabulary API routes."""

import pytest
from httpx import ASGITransport, AsyncClient

from api.repository import SQLModelRepository
from api.server import app
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
async def test_list_empty(client):
    r = await client.get("/api/vocabulary")
    assert r.status_code == 200
    assert r.json()["items"] == []


@pytest.mark.asyncio
async def test_create_and_list(client):
    r = await client.post(
        "/api/vocabulary",
        json={
            "url_pattern": "/login",
            "page_title": "Login",
            "login_role": "user",
            "vocabulary": {
                "username": {
                    "role": "textbox",
                    "name": "Username",
                    "confidence": 0.9,
                }
            },
            "action_map": [],
        },
    )
    assert r.status_code == 200

    r = await client.get("/api/vocabulary")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1
    assert r.json()["items"][0]["url_pattern"] == "/login"


@pytest.mark.asyncio
async def test_search(client):
    await client.post(
        "/api/vocabulary",
        json={
            "url_pattern": "/login",
            "page_title": "Login",
            "login_role": "user",
            "vocabulary": {},
            "action_map": [],
        },
    )
    await client.post(
        "/api/vocabulary",
        json={
            "url_pattern": "/dashboard",
            "page_title": "Dashboard",
            "login_role": "user",
            "vocabulary": {},
            "action_map": [],
        },
    )
    r = await client.get("/api/vocabulary?query=login")
    assert len(r.json()["items"]) == 1


@pytest.mark.asyncio
async def test_scan_trigger(client):
    r = await client.post("/api/vocabulary/scan")
    assert r.status_code == 200
    assert r.json()["ok"] is True
