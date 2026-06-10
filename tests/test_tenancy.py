"""T-P04 单元测试:多租户数据模型(Project / Version / User / ProjectMember)。

纯增量:只验新实体的 round-trip / 列表过滤 / upsert / 复合主键 / 删除。
不触碰现有 Suite/Run/词汇表(那些的 project_id 接入是后续子任务)。
"""

from __future__ import annotations

import pytest

from input.models import Project, ProjectMember, User, Version
from storage.db import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await s.init()
    yield s
    await s.close()


# ── Project ──────────────────────────────────────────────────


async def test_project_roundtrip(store):
    await store.save_project(Project(id="p1", name="支付产品线", description="x", owner="alice"))
    got = await store.get_project("p1")
    assert got is not None
    assert got.name == "支付产品线"
    assert got.owner == "alice"


async def test_project_upsert_and_list(store):
    await store.save_project(Project(id="p1", name="旧名"))
    await store.save_project(Project(id="p1", name="新名"))
    await store.save_project(Project(id="p2", name="另一个"))
    assert (await store.get_project("p1")).name == "新名"
    assert {p.id for p in await store.list_projects()} == {"p1", "p2"}


async def test_project_delete(store):
    await store.save_project(Project(id="p1", name="x"))
    assert await store.delete_project("p1") is True
    assert await store.get_project("p1") is None
    assert await store.delete_project("nope") is False


# ── Version(按 project 过滤)─────────────────────────────────


async def test_version_list_filtered_by_project(store):
    await store.save_version(Version(id="v1", project_id="p1", name="1.0"))
    await store.save_version(Version(id="v2", project_id="p1", name="1.1"))
    await store.save_version(Version(id="v3", project_id="p2", name="2.0"))
    p1 = await store.list_versions(project_id="p1")
    assert {v.id for v in p1} == {"v1", "v2"}
    assert len(await store.list_versions()) == 3


async def test_version_status_default_active(store):
    await store.save_version(Version(id="v1", project_id="p1", name="1.0"))
    assert (await store.get_version("v1")).status == "active"


# ── User ─────────────────────────────────────────────────────


async def test_user_roundtrip_and_admin_flag(store):
    await store.save_user(User(id="bob", display_name="Bob", is_platform_admin=True))
    got = await store.get_user("bob")
    assert got.display_name == "Bob"
    assert got.is_platform_admin is True


# ── ProjectMember(复合主键)──────────────────────────────────


async def test_member_composite_key_upsert(store):
    # 同 (project, user) 改角色 = upsert,不产生两行
    await store.save_member(ProjectMember(project_id="p1", user_id="alice", role="tester"))
    await store.save_member(ProjectMember(project_id="p1", user_id="alice", role="admin"))
    got = await store.get_member("p1", "alice")
    assert got.role == "admin"
    assert len(await store.list_members("p1")) == 1


async def test_member_list_by_project_and_by_user(store):
    await store.save_member(ProjectMember(project_id="p1", user_id="alice", role="admin"))
    await store.save_member(ProjectMember(project_id="p1", user_id="bob", role="tester"))
    await store.save_member(ProjectMember(project_id="p2", user_id="alice", role="tester"))
    assert {m.user_id for m in await store.list_members("p1")} == {"alice", "bob"}
    assert {m.project_id for m in await store.list_memberships("alice")} == {"p1", "p2"}


async def test_member_delete(store):
    await store.save_member(ProjectMember(project_id="p1", user_id="alice"))
    assert await store.delete_member("p1", "alice") is True
    assert await store.get_member("p1", "alice") is None
    assert await store.delete_member("p1", "nope") is False
