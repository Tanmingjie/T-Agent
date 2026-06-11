"""T-P04 单元测试:多租户数据模型(Project / Version / User / ProjectMember)。

纯增量:只验新实体的 round-trip / 列表过滤 / upsert / 复合主键 / 删除。
不触碰现有 Suite/Run/词汇表(那些的 project_id 接入是后续子任务)。
"""

from __future__ import annotations

import pytest

from input.models import Project, ProjectMember, Suite, TestCase, User, Version
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


# ── Suite 租户过滤(T-P04b)───────────────────────────────────


async def test_suite_tenant_columns_roundtrip(store):
    await store.save_suite(
        Suite(id="s1", name="冒烟", base_url="https://x", project_id="p1", version_id="v1")
    )
    got = await store.get_suite("s1")
    assert got.project_id == "p1"
    assert got.version_id == "v1"


async def test_list_suites_filtered_by_project_and_version(store):
    await store.save_suite(Suite(id="s1", name="a", base_url="x", project_id="p1", version_id="v1"))
    await store.save_suite(Suite(id="s2", name="b", base_url="x", project_id="p1", version_id="v2"))
    await store.save_suite(Suite(id="s3", name="c", base_url="x", project_id="p2", version_id="v1"))
    # 不传 = 全部(向后兼容,单机/CLI)
    assert len(await store.list_suites()) == 3
    # 按项目隔离
    assert {s.id for s in await store.list_suites(project_id="p1")} == {"s1", "s2"}
    # 按项目+版本隔离
    assert {s.id for s in await store.list_suites(project_id="p1", version_id="v1")} == {"s1"}


async def test_legacy_suite_defaults_empty_tenant(store):
    # 旧建法不传 project_id/version_id → 空串(默认租户),不报错
    await store.save_suite(Suite(id="s1", name="a", base_url="x"))
    got = await store.get_suite("s1")
    assert got.project_id == ""
    assert got.version_id == ""


# ── 轻量迁移:旧库缺新增字符串列时回填空串(防 NULL 读崩)──────


async def test_migration_backfills_missing_str_column(tmp_path):
    """模拟旧库:page_vocabulary 缺 project_id 列、有一条旧行(列值 NULL)。
    init() 的轻量迁移应 ADD COLUMN + 回填 '' → 领域模型可正常读回(不抛 ValidationError)。
    """
    from sqlalchemy import text

    url = f"sqlite+aiosqlite:///{tmp_path}/legacy.db"
    # 1) 手工建一张缺 project_id 的旧表 + 插一条旧行
    s0 = Store(url)
    async with s0.engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE page_vocabulary ("
                "id INTEGER PRIMARY KEY, base_url TEXT, url_pattern TEXT, "
                "page_title TEXT, login_role TEXT, vocabulary TEXT, action_map TEXT, "
                "stale INTEGER, scanned_at REAL, updated_at REAL)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO page_vocabulary "
                "(base_url, url_pattern, page_title, login_role, vocabulary, action_map, "
                "stale, scanned_at, updated_at) VALUES "
                "('https://x', '/p', 't', 'r', '{}', '[]', 0, 0.0, 0.0)"
            )
        )
    await s0.close()

    # 2) 正常 Store.init() 触发迁移:补 project_id 列并回填 ''
    s = Store(url)
    await s.init()
    vocs = await s.list_vocabularies()  # 读回旧行 → 不应因 project_id=NULL 崩
    assert len(vocs) == 1
    assert vocs[0].project_id == ""  # 回填为默认空串
    await s.close()


# ── 版本克隆(T-P04c)─────────────────────────────────────────


async def test_clone_version_suites_copies_suites_and_cases(store):
    await store.save_project(Project(id="p1", name="x"))
    await store.save_version(Version(id="v1", project_id="p1", name="1.0"))
    await store.save_version(Version(id="v2", project_id="p1", name="1.1"))
    await store.save_suite(
        Suite(id="s1", name="冒烟", base_url="x", project_id="p1", version_id="v1")
    )
    await store.save_case(TestCase(id="c1", name="用例1", suite_id="s1"))
    await store.save_case(TestCase(id="c2", name="用例2", suite_id="s1"))

    n = await store.clone_version_suites("v1", "v2")
    assert n == 1

    # 新版本下有一份 Suite(新 id),挂在 v2
    v2_suites = await store.list_suites(version_id="v2")
    assert len(v2_suites) == 1
    new_suite = v2_suites[0]
    assert new_suite.id != "s1"  # 新 id
    assert new_suite.name == "冒烟"
    assert new_suite.project_id == "p1"
    # 用例随之拷贝(新 id,挂新 Suite)
    new_cases = await store.list_cases(suite_id=new_suite.id)
    assert {c.name for c in new_cases} == {"用例1", "用例2"}
    assert all(c.id not in ("c1", "c2") for c in new_cases)
    # 原版本不受影响
    assert len(await store.list_suites(version_id="v1")) == 1


async def test_clone_version_rejects_cross_project(store):
    await store.save_version(Version(id="v1", project_id="p1", name="1.0"))
    await store.save_version(Version(id="v2", project_id="p2", name="2.0"))
    with pytest.raises(ValueError):
        await store.clone_version_suites("v1", "v2")


async def test_clone_version_missing_raises(store):
    await store.save_version(Version(id="v1", project_id="p1", name="1.0"))
    with pytest.raises(ValueError):
        await store.clone_version_suites("v1", "nope")
