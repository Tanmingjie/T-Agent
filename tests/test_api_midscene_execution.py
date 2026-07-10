from __future__ import annotations

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
    await repo.create(Suite(id="sx", name="SX", base_url="https://x.com"))
    await repo.bulk_insert(
        [TestCase(id="t1", name="C1", steps=["do a"], base_url="https://x.com", suite_id="sx")]
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
async def test_run_queue_mode_persists_executor_backend(client, monkeypatch):
    monkeypatch.setenv("RUN_MODE", "queue")

    r = await client.post("/api/suites/sx/run", json={"executor_backend": "midscene"})

    assert r.status_code == 200
    run_id = r.json()["run_id"]

    import api.server as srv

    queued = await srv._store.get_queued_run(run_id)
    assert queued is not None
    assert queued.executor_backend == "midscene"


@pytest.mark.asyncio
async def test_run_queue_mode_defaults_executor_backend_to_react(client, monkeypatch):
    monkeypatch.setenv("RUN_MODE", "queue")

    r = await client.post("/api/suites/sx/run", json={"skill_names": ["登录流程"]})

    assert r.status_code == 200
    run_id = r.json()["run_id"]

    import api.server as srv

    queued = await srv._store.get_queued_run(run_id)
    assert queued is not None
    assert queued.skill_names == ["登录流程"]
    assert queued.executor_backend == "react"


@pytest.mark.asyncio
async def test_run_rejects_unknown_executor_backend(client):
    r = await client.post("/api/suites/sx/run", json={"executor_backend": "unknown"})

    assert r.status_code == 422
