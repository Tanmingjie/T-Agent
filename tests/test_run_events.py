"""T-P09 单元测试:跨进程进度事件 + 审批工单(Store + API DB 通道)。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.repository import SQLModelRepository
from api.server import app
from storage.db import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await s.init()
    yield s
    await s.close()


# ── run_event 尾随 ───────────────────────────────────────────


async def test_run_events_append_and_tail(store):
    await store.append_run_event("r1", "case_start", {"case_id": "c1"})
    await store.append_run_event("r1", "step_change", {"n": 1})
    await store.append_run_event("r2", "case_start", {"case_id": "x"})  # 别的 run
    evs = await store.list_run_events("r1")
    assert [e.event_type for e in evs] == ["case_start", "step_change"]
    # after_seq 尾随:只拿新事件
    last = evs[-1].seq
    await store.append_run_event("r1", "suite_done", {"sentinel": True})
    new = await store.list_run_events("r1", after_seq=last)
    assert [e.event_type for e in new] == ["suite_done"]


# ── 审批工单 ─────────────────────────────────────────────────


async def test_permission_request_lifecycle(store):
    await store.create_permission_request("req1", "r1", "browser_click", "高危操作")
    pend = await store.list_pending_permission_requests("r1")
    assert len(pend) == 1 and pend[0].id == "req1"
    assert await store.resolve_permission_request("req1", True) is True
    assert (await store.get_permission_request("req1")).status == "approved"
    # 已解决的不再 pending,重复解决返回 False
    assert await store.list_pending_permission_requests("r1") == []
    assert await store.resolve_permission_request("req1", True) is False


# ── API:permission 解决走 DB 通道 ───────────────────────────


@pytest.fixture
async def client(tmp_path):
    store = Store(f"sqlite+aiosqlite:///{tmp_path}/api.db")
    await store.init()
    import api.server as srv

    srv._store = store
    srv._repo = SQLModelRepository(store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, store
    await store.close()
    srv._store = None
    srv._repo = None


@pytest.mark.asyncio
async def test_api_permission_list_and_resolve_db(client):
    ac, store = client
    await store.create_permission_request("reqA", "run1", "browser_click", "确认删除?")
    # 列待审批
    r = await ac.get("/api/suites/s1/runs/run1/permission")
    assert r.status_code == 200 and r.json()[0]["event_id"] == "reqA"
    # 批准(DB 通道)
    r = await ac.post("/api/suites/s1/permission/reqA", json={"choice": "approve"})
    assert r.status_code == 200
    assert (await store.get_permission_request("reqA")).status == "approved"
    # 再次解决 → 404
    r = await ac.post("/api/suites/s1/permission/reqA", json={"choice": "approve"})
    assert r.status_code == 404
