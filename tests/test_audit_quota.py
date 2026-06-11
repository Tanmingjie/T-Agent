"""M2-3 单元测试:审计日志 / 项目级配额 / 版本维度 run 列表。"""

from __future__ import annotations

import pytest

from input.models import Project
from storage.db import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await s.init()
    yield s
    await s.close()


# ── 审计日志 ─────────────────────────────────────────────────


async def test_audit_append_and_list_desc(store):
    await store.append_audit("alice", "project.create", project_id="p1", target="A")
    await store.append_audit("bob", "member.add", project_id="p1", target="carol")
    await store.append_audit("x", "run.trigger", project_id="p2")
    p1 = await store.list_audit("p1")
    assert {a.action for a in p1} == {"project.create", "member.add"}
    # 最新在前
    assert p1[0].action == "member.add"
    assert len(await store.list_audit()) == 3  # 不传 project = 全部


async def test_audit_limit(store):
    for i in range(5):
        await store.append_audit("u", "act", project_id="p1", target=str(i))
    assert len(await store.list_audit("p1", limit=3)) == 3


# ── 项目级并发配额(claim 处生效)──────────────────────────────


async def test_per_project_quota_from_project_table(store):
    # 项目 p1 配额=1;两条任务,领一条后第二条被挡
    await store.save_project(Project(id="p1", name="A", max_concurrency=1))
    await store.enqueue_run("r1", "s1", "p1")
    await store.enqueue_run("r2", "s2", "p1")
    c1 = await store.claim_next_run("w1")  # 不传全局配额 → 读 Project.max_concurrency
    assert c1.run_id == "r1"
    assert await store.claim_next_run("w2") is None  # p1 配额满


async def test_zero_quota_unlimited(store):
    await store.save_project(Project(id="p1", name="A", max_concurrency=0))
    await store.enqueue_run("r1", "s1", "p1")
    await store.enqueue_run("r2", "s2", "p1")
    assert (await store.claim_next_run("w1")).run_id == "r1"
    assert (await store.claim_next_run("w2")).run_id == "r2"  # 0=不限


# ── 版本维度 run 列表 ────────────────────────────────────────


async def test_list_runs_by_project_and_version(store):
    from api.repository import SQLModelRepository

    repo = SQLModelRepository(store)
    await repo.create_run("run1", "s1", 2, "p1", "v1")
    await repo.create_run("run2", "s1", 1, "p1", "v2")
    await repo.create_run("run3", "s9", 1, "p2", "v1")
    await repo.update_run("run1", passed_cases=2, failed_cases=0)

    p1 = await store.list_runs("p1")
    assert {r["id"] for r in p1} == {"run1", "run2"}
    v1 = await store.list_runs("p1", "v1")
    assert {r["id"] for r in v1} == {"run1"}
    assert v1[0]["passed_cases"] == 2
