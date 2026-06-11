"""T-P05 单元测试:鉴权原语 + 角色解析。"""

from __future__ import annotations

import pytest

from api.auth import (
    ROLE_ADMIN,
    ROLE_TESTER,
    HeaderAuthProvider,
    role_in_project,
    set_auth_provider,
)
from input.models import ProjectMember, User
from storage.db import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture
async def auth_enabled(store):
    """启用真实 RBAC(装 provider);role_in_project 才做角色解析而非开放模式短路。"""
    set_auth_provider(HeaderAuthProvider(store))
    yield
    set_auth_provider(None)


async def test_role_in_project_open_mode_without_provider(store):
    """未配 provider(单机/开放模式)→ 全员等效项目管理员。"""
    set_auth_provider(None)
    assert await role_in_project(store, "anyone", "p1") == ROLE_ADMIN


async def test_header_auth_resolves_principal(store):
    prov = HeaderAuthProvider(store)
    # 无 header → None
    assert await prov.authenticate(None) is None
    # 普通用户(未登记)→ Principal,非平台管理员
    p = await prov.authenticate("alice")
    assert p.user_id == "alice" and p.is_platform_admin is False


async def test_header_auth_platform_admin_flag(store):
    await store.save_user(User(id="root", is_platform_admin=True))
    p = await HeaderAuthProvider(store).authenticate("root")
    assert p.is_platform_admin is True


async def test_role_in_project_member(store, auth_enabled):
    await store.save_member(ProjectMember(project_id="p1", user_id="alice", role=ROLE_TESTER))
    assert await role_in_project(store, "alice", "p1") == ROLE_TESTER


async def test_role_in_project_non_member_none(store, auth_enabled):
    assert await role_in_project(store, "stranger", "p1") is None


async def test_role_in_project_platform_admin_is_admin_everywhere(store, auth_enabled):
    await store.save_user(User(id="root", is_platform_admin=True))
    # 即便不是成员,平台管理员对任意项目 admin 等效
    assert await role_in_project(store, "root", "any-project") == ROLE_ADMIN
