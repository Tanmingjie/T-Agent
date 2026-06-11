"""M2 单元测试:项目级 Skill + SessionProfile(cookie 加密落库 / 项目作用域)。"""

from __future__ import annotations

import pytest

from input.models import ProjectSkill, SessionProfile
from storage.db import SessionProfileRow, Store


@pytest.fixture
async def store(tmp_path):
    s = Store(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await s.init()
    yield s
    await s.close()


# ── ProjectSkill ─────────────────────────────────────────────


async def test_skill_crud_scoped_by_project(store):
    await store.save_skill(ProjectSkill(project_id="p1", name="表单", content="先填必填项"))
    await store.save_skill(ProjectSkill(project_id="p1", name="结算", content="确认金额"))
    await store.save_skill(ProjectSkill(project_id="p2", name="x", content="y"))
    assert {s.name for s in await store.list_skills("p1")} == {"表单", "结算"}
    assert await store.delete_skill("p1", "表单") is True
    assert {s.name for s in await store.list_skills("p1")} == {"结算"}
    assert await store.delete_skill("p1", "nope") is False


async def test_skill_upsert(store):
    await store.save_skill(ProjectSkill(project_id="p1", name="a", content="v1"))
    await store.save_skill(ProjectSkill(project_id="p1", name="a", content="v2"))
    skills = await store.list_skills("p1")
    assert len(skills) == 1 and skills[0].content == "v2"


# ── SessionProfile:cookie 加密 + 项目作用域 ─────────────────


async def test_session_cookies_encrypted_at_rest(store):
    cookies = [{"name": "sid", "value": "secret-sess-123"}]
    await store.save_session_profile(
        SessionProfile(
            name="prof",
            login_aw="",
            cookie_store="",
            base_url="https://x",
            project_id="p1",
            cookies=cookies,
        )
    )
    async with store._sf() as s:
        row = await s.get(SessionProfileRow, "prof")
    assert "secret-sess-123" not in row.cookies_encrypted
    assert row.cookies_encrypted != ""
    # 读回解密
    got = await store.get_session_profile("prof")
    assert got.cookies == cookies
    assert got.project_id == "p1"


async def test_session_empty_cookies_no_ciphertext(store):
    await store.save_session_profile(
        SessionProfile(name="p", login_aw="", cookie_store="/tmp/c.json", base_url="https://x")
    )
    async with store._sf() as s:
        row = await s.get(SessionProfileRow, "p")
    assert row.cookies_encrypted == ""  # 空 cookies 不产密文
    got = await store.get_session_profile("p")
    assert got.cookies == []


async def test_list_session_profiles_by_project(store):
    await store.save_session_profile(
        SessionProfile(name="a", login_aw="", cookie_store="", base_url="x", project_id="p1")
    )
    await store.save_session_profile(
        SessionProfile(name="b", login_aw="", cookie_store="", base_url="x", project_id="p2")
    )
    assert {p.name for p in await store.list_session_profiles("p1")} == {"a"}
    assert len(await store.list_session_profiles()) == 2
    assert await store.delete_session_profile("a") is True
