"""execute_run 异常兜底(2026-06-25):中断时标 run failed + 给未落记录的用例补占位记录。

治内网实测的两个 bug:① setup 阶段异常 / 进程被中断 → run 僵尸 running(漏标状态);
② 中途被杀 → /result 全空、抽屉无详情。兜底后:run 必落终态,未跑完的用例有「执行中断」占位。
"""

from __future__ import annotations

import pytest

from api.repository import SQLModelRepository
from api.run_executor import execute_run
from input.models import Suite, TestCase
from storage.db import Store


@pytest.mark.asyncio
async def test_execute_run_marks_failed_and_saves_placeholders_on_interruption(
    tmp_path, monkeypatch
):
    # 文件型 sqlite:execute_run 自建独立 Store,需与种数据的 Store 共享同一文件(内存 sqlite 每连接独立)
    db_url = f"sqlite+aiosqlite:///{tmp_path}/t.db"
    store = Store(url=db_url)
    await store.init()
    repo = SQLModelRepository(store)
    await repo.create(Suite(id="sx", name="SX", base_url="https://x.com"))
    await repo.bulk_insert(
        [
            TestCase(id="t1", name="C1", steps=["a"], base_url="https://x.com", suite_id="sx"),
            TestCase(id="t2", name="C2", steps=["b"], base_url="https://x.com", suite_id="sx"),
        ]
    )
    run_id = "run123"
    await repo.create_run(run_id, "sx", 2, None, None)

    # 模拟「setup 之后、未落任何记录就中断」:orchestrator.run_suite 抛错
    import harness.orchestrator as orch_mod

    class _BoomOrch:
        def __init__(self, *a, **k):
            pass

        async def run_suite(self, *a, **k):
            raise RuntimeError("模拟进程中断")

    monkeypatch.setattr(orch_mod, "Orchestrator", _BoomOrch)

    events: list[str] = []

    async def sse(ev, data):
        events.append(ev)

    await execute_run(db_url=db_url, run_id=run_id, suite_id="sx", sse_cb=sse)

    # run 落 failed(不再僵尸 running)
    run = await repo.get_run(run_id)
    assert run is not None and run["status"] == "failed"

    # 两个用例都有「执行中断」占位记录(不再 /result 全空)
    recs = {r.case_id: r for r in await repo.list_records_by_run(run_id)}
    assert set(recs) == {"t1", "t2"}
    for r in recs.values():
        assert r.passed is False
        assert "执行中断" in r.final_result

    # 前端 SSE 收到 error + suite_done(/stream 能收尾,不永挂)
    assert "error" in events and "suite_done" in events


@pytest.mark.asyncio
async def test_execute_run_no_placeholder_for_already_saved_case(tmp_path, monkeypatch):
    """已落记录的用例不被占位覆盖:run_suite 落了 t1 的真实记录后才抛错,t1 保留真记录、t2 才补占位。"""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/t2.db"
    store = Store(url=db_url)
    await store.init()
    repo = SQLModelRepository(store)
    await repo.create(Suite(id="sx", name="SX", base_url="https://x.com"))
    await repo.bulk_insert(
        [
            TestCase(id="t1", name="C1", steps=["a"], base_url="https://x.com", suite_id="sx"),
            TestCase(id="t2", name="C2", steps=["b"], base_url="https://x.com", suite_id="sx"),
        ]
    )
    run_id = "runX"
    await repo.create_run(run_id, "sx", 2, None, None)

    import harness.orchestrator as orch_mod
    from input.models import ExecutionRecord

    class _PartialOrch:
        def __init__(self, *a, **k):
            pass

        async def run_suite(self, cases, *, on_record=None, **k):
            # t1 真实跑完落库,t2 跑一半中断
            await on_record(
                ExecutionRecord(exec_id="real-t1", case_id="t1", passed=True, final_result="真PASS")
            )
            raise RuntimeError("t2 跑一半中断")

    monkeypatch.setattr(orch_mod, "Orchestrator", _PartialOrch)

    async def sse(ev, data):
        return None

    await execute_run(db_url=db_url, run_id=run_id, suite_id="sx", sse_cb=sse)

    recs = {r.case_id: r for r in await repo.list_records_by_run(run_id)}
    assert recs["t1"].passed is True and recs["t1"].final_result == "真PASS"  # 真记录未被覆盖
    assert recs["t2"].passed is False and "执行中断" in recs["t2"].final_result  # t2 补占位
