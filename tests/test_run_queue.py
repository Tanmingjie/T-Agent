"""T-P08 单元测试:执行任务队列(领取互斥语义 / 超时回收 / 配额 / 心跳)。"""

from __future__ import annotations

import time

import pytest

from storage.db import RunQueueRow, Store


@pytest.fixture
async def store(tmp_path):
    s = Store(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await s.init()
    yield s
    await s.close()


async def test_enqueue_and_claim_fifo(store):
    await store.enqueue_run("r1", "s1", "p1")
    time.sleep(0.01)
    await store.enqueue_run("r2", "s1", "p1")
    c1 = await store.claim_next_run("w1")
    c2 = await store.claim_next_run("w1")
    assert c1.run_id == "r1" and c2.run_id == "r2"  # FIFO
    assert c1.status == "claimed" and c1.claimed_by == "w1" and c1.attempts == 1


async def test_claim_empty_returns_none(store):
    assert await store.claim_next_run("w1") is None


async def test_claimed_not_reclaimed_while_fresh(store):
    await store.enqueue_run("r1", "s1", "p1")
    assert (await store.claim_next_run("w1")).run_id == "r1"
    # 第二次领取:r1 已 claimed 且心跳新 → 无可领
    assert await store.claim_next_run("w2") is None


async def test_stale_claim_reclaimed(store):
    await store.enqueue_run("r1", "s1", "p1")
    claimed = await store.claim_next_run("w1")
    # 手动把心跳改旧(模拟 worker 崩溃)
    async with store._sf() as s:
        row = await s.get(RunQueueRow, "r1")
        row.claimed_at = time.time() - 9999
        s.add(row)
        await s.commit()
    # 另一 worker 用短 stale 阈值 → 回收并领到
    again = await store.claim_next_run("w2", stale_seconds=1.0)
    assert again is not None and again.run_id == "r1"
    assert again.claimed_by == "w2" and again.attempts == 2  # 重试计数


async def test_heartbeat_updates_claimed_at(store):
    await store.enqueue_run("r1", "s1", "p1")
    c = await store.claim_next_run("w1")
    old = c.claimed_at
    time.sleep(0.02)
    await store.heartbeat_run("r1")
    row = await store.get_queued_run("r1")
    assert row.claimed_at > old


async def test_complete_sets_status(store):
    await store.enqueue_run("r1", "s1", "p1")
    await store.claim_next_run("w1")
    await store.complete_queued_run("r1", "done")
    assert (await store.get_queued_run("r1")).status == "done"


async def test_project_concurrency_quota(store):
    # p1 两条任务,配额 1:领一条后第二条被配额挡住(其它项目不受影响)
    await store.enqueue_run("r1", "s1", "p1")
    await store.enqueue_run("r2", "s2", "p1")
    await store.enqueue_run("r3", "s3", "p2")
    c1 = await store.claim_next_run("w1", max_project_concurrency=1)
    assert c1.run_id == "r1"
    # p1 已有 1 个 claimed,配额 1 → 跳过 r2,领到 p2 的 r3
    c2 = await store.claim_next_run("w2", max_project_concurrency=1)
    assert c2.run_id == "r3"
    # 仍受配额限:p1 满、p2 满 → 无可领
    assert await store.claim_next_run("w3", max_project_concurrency=1) is None
