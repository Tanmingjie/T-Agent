"""Tests for permission API."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from api.routers.execution import _permission_events, _permission_results
from api.server import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
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
