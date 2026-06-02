"""Tests for results API routes."""

import pytest
from httpx import ASGITransport, AsyncClient

from api.repository import SQLModelRepository
from api.server import app
from input.models import ExecutionRecord, Suite, TestCase
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
                steps=["do a"],
                base_url="https://x.com",
                suite_id="sx",
            ),
        ]
    )
    await repo.create_run("r1", "sx", 1)
    await repo.save_record(
        ExecutionRecord(exec_id="e1", case_id="t1", suite_id="sx", run_id="r1", passed=True)
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
async def test_list_runs(client):
    r = await client.get("/api/suites/sx/runs")
    assert r.status_code == 200
    assert len(r.json()) == 1


@pytest.mark.asyncio
async def test_get_run_overview(client):
    r = await client.get("/api/suites/sx/runs/r1")
    assert r.status_code == 200
    assert r.json()["status"] == "running"


@pytest.mark.asyncio
async def test_get_case_result(client):
    r = await client.get("/api/suites/sx/runs/r1/cases/t1/result")
    assert r.status_code == 200
    assert r.json()["passed"] is True
    assert "history" in r.json()


@pytest.mark.asyncio
async def test_result_not_found(client):
    r = await client.get("/api/suites/sx/runs/r1/cases/ghost/result")
    assert r.status_code == 404
