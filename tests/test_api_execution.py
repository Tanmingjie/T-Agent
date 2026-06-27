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


@pytest.mark.asyncio
async def test_run_single_case_unknown_id_404(client):
    r = await client.post("/api/suites/sx/run?case_id=nope")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_run_single_case_filters_to_one(client, monkeypatch):
    # 不真正起 worker 线程,只验证单用例过滤 + run 以 1 条用例建立
    import api.routers.execution as execmod

    # 强制 embedded 路径(否则环境/.env 的 RUN_MODE=queue 会走入队分支,不调 spawn_run)
    monkeypatch.setenv("RUN_MODE", "embedded")

    captured = {}

    def _fake_spawn(run_id, main):
        captured["run_id"] = run_id  # 不执行 main

    monkeypatch.setattr(execmod, "spawn_run", _fake_spawn)

    r = await client.post("/api/suites/sx/run?case_id=t1")
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert captured.get("run_id") == run_id

    import api.server as srv

    run = await srv._repo.get_run(run_id)
    assert run["total_cases"] == 1  # 只跑 1 条


@pytest.mark.asyncio
async def test_run_queue_mode_persists_skill_names(client, monkeypatch):
    """queue 模式:执行触发带 skill_names → 落 run_queue,worker 领取后透传强制加载。"""
    monkeypatch.setenv("RUN_MODE", "queue")
    r = await client.post("/api/suites/sx/run", json={"skill_names": ["登录流程", "下单校验"]})
    assert r.status_code == 200
    assert r.json()["status"] == "queued"
    run_id = r.json()["run_id"]

    import api.server as srv

    queued = await srv._store.get_queued_run(run_id)
    assert queued is not None
    assert queued.skill_names == ["登录流程", "下单校验"]


@pytest.mark.asyncio
async def test_run_embedded_no_skill_names_defaults_empty(client, monkeypatch):
    """embedded 不带 options.skill_names 时按空走(全渐进披露),不报错。"""
    monkeypatch.setenv("RUN_MODE", "embedded")
    import api.routers.execution as execmod

    monkeypatch.setattr(execmod, "spawn_run", lambda run_id, main: None)
    r = await client.post("/api/suites/sx/run")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_stream_queue_mode_uses_repo_get_run(client):
    """queue 模式 SSE(run 不在内存 _sse_queues)走 repo.get_run,不再 AttributeError。

    回归:此前 stream_events 误调 store.get_run(get_run 只在 repo 上)→ 切 RUN_MODE=queue
    后 /stream 直接 500。
    """
    import api.server as srv

    run_id = "qrun1"
    await srv._repo.create_run(run_id, "sx", 1, None, None)
    # 写一条 suite_done run_event,让 queue 分支首轮轮询即收尾返回(不依赖内存队列)
    await srv._store.append_run_event(run_id, "suite_done", {"run_id": run_id})

    r = await client.get(f"/api/suites/sx/stream?run_id={run_id}")
    assert r.status_code == 200  # 不再 500
    assert "event: suite_done" in r.text


@pytest.mark.asyncio
async def test_stream_queue_mode_unknown_run_404(client):
    """queue 模式:run 既不在内存队列、repo 也查不到、未入队 → 404(非 500)。"""
    r = await client.get("/api/suites/sx/stream?run_id=does-not-exist")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_stop_running_run_flags_cancel(client):
    """停止端点:对 running 的 run 置 cancel_requested + 落 aborting 事件;幂等于已结束/不存在。"""
    import api.server as srv

    run_id = "stoprun1"
    await srv._repo.create_run(run_id, "sx", 1, None, None)

    r = await client.post(f"/api/suites/sx/runs/{run_id}/stop")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert await srv._repo.is_cancel_requested(run_id) is True
    # 落了 aborting run_event(供 /stream 订阅者即时看「停止中」)
    events = await srv._store.list_run_events(run_id)
    assert any(e.event_type == "aborting" for e in events)


@pytest.mark.asyncio
async def test_stop_finished_run_is_noop(client):
    """已结束的 run:停止幂等返回 ok=false,不置标志。"""
    import api.server as srv

    run_id = "stoprun2"
    await srv._repo.create_run(run_id, "sx", 1, None, None)
    await srv._repo.update_run(run_id, status="completed")

    r = await client.post(f"/api/suites/sx/runs/{run_id}/stop")
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert await srv._repo.is_cancel_requested(run_id) is False


@pytest.mark.asyncio
async def test_stop_unknown_run_404(client):
    r = await client.post("/api/suites/sx/runs/nope/stop")
    assert r.status_code == 404
