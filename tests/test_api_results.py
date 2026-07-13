"""Tests for results API routes."""

import pytest
from httpx import ASGITransport, AsyncClient

from api.repository import SQLModelRepository
from api.server import app
from input.models import ExecutionRecord, Suite, TestCase
from storage.artifacts import LocalArtifactStore
from storage.db import Store


@pytest.fixture
async def client(tmp_path):
    import api.routers.results as results_router

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
    artifact_store = LocalArtifactStore(tmp_path)
    artifact_dir = artifact_store.midscene_dir("r1", "t1")
    report_dir = artifact_dir / "midscene_run" / "report"
    report_dir.mkdir(parents=True)
    (report_dir / "midscene-report.html").write_text("<html>ok</html>", encoding="utf-8")
    (artifact_dir / "runner-stderr.log").write_text("runner log", encoding="utf-8")
    old_artifacts = results_router._artifacts
    results_router._artifacts = artifact_store
    await repo.save_record(
        ExecutionRecord(
            exec_id="e1",
            case_id="t1",
            suite_id="sx",
            run_id="r1",
            passed=True,
            metrics={
                "execution_kernel": "midscene",
                "midscene": {
                    "artifacts": {
                        "artifact_dir": str(artifact_dir),
                        "report": str(report_dir / "midscene-report.html"),
                    }
                },
            },
        )
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
    results_router._artifacts = old_artifacts


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
    assert r.json()["midscene_artifacts"]["available"] is True
    assert r.json()["midscene_artifacts"]["report_path"].endswith("midscene-report.html")


@pytest.mark.asyncio
async def test_get_midscene_artifact(client):
    r = await client.get(
        "/api/suites/sx/runs/r1/cases/t1/artifact?path=midscene_run/report/midscene-report.html"
    )
    assert r.status_code == 200
    assert "ok" in r.text


@pytest.mark.asyncio
async def test_result_not_found(client):
    r = await client.get("/api/suites/sx/runs/r1/cases/ghost/result")
    assert r.status_code == 404
