"""T-P05/T-P06 API 测试:项目 CRUD + RBAC + LLM 配置(加密/掩码/自检)。"""

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import HeaderAuthProvider, set_auth_provider
from api.repository import SQLModelRepository
from api.server import app
from input.models import User
from storage.db import Store


@pytest.fixture
async def ctx():
    store = Store(url="sqlite+aiosqlite://")
    await store.init()
    repo = SQLModelRepository(store)
    import api.server as srv

    srv._repo = repo
    srv._store = store
    set_auth_provider(HeaderAuthProvider(store))
    # 平台管理员(bypass 所有 RBAC),用于搭建测试数据
    await store.save_user(User(id="root", display_name="root", is_platform_admin=True))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, store
    await store.close()
    srv._repo = None
    srv._store = None
    set_auth_provider(None)


def _h(user: str) -> dict:
    return {"X-User": user}


# ── 认证 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_user_header_401(ctx):
    client, _ = ctx
    r = await client.get("/api/projects")  # 无 X-User
    assert r.status_code == 401


# ── 项目 CRUD + 自助开通 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_create_project_makes_creator_admin(ctx):
    client, store = ctx
    r = await client.post("/api/projects", json={"name": "支付"}, headers=_h("alice"))
    assert r.status_code == 200
    pid = r.json()["id"]
    # 创建者是 admin → 能改配置
    r = await client.put(
        f"/api/projects/{pid}/llm-config",
        json={"model": "openai/x", "api_key": "k"},
        headers=_h("alice"),
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_list_projects_scoped_to_membership(ctx):
    client, _ = ctx
    p1 = (await client.post("/api/projects", json={"name": "A"}, headers=_h("alice"))).json()["id"]
    await client.post("/api/projects", json={"name": "B"}, headers=_h("bob"))
    # alice 只看到自己的项目
    r = await client.get("/api/projects", headers=_h("alice"))
    assert {p["id"] for p in r.json()} == {p1}
    # 平台管理员看全部
    r = await client.get("/api/projects", headers=_h("root"))
    assert len(r.json()) == 2


# ── RBAC 权限矩阵 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_member_forbidden(ctx):
    client, _ = ctx
    pid = (await client.post("/api/projects", json={"name": "A"}, headers=_h("alice"))).json()["id"]
    r = await client.get(f"/api/projects/{pid}", headers=_h("stranger"))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_tester_cannot_change_config_but_can_read(ctx):
    client, _ = ctx
    pid = (await client.post("/api/projects", json={"name": "A"}, headers=_h("alice"))).json()["id"]
    # alice(admin)加 bob 为 tester
    r = await client.post(
        f"/api/projects/{pid}/members",
        json={"user_id": "bob", "role": "tester"},
        headers=_h("alice"),
    )
    assert r.status_code == 200
    # tester 能读配置
    assert (
        await client.get(f"/api/projects/{pid}/llm-config", headers=_h("bob"))
    ).status_code == 200
    # tester 不能改配置
    r = await client.put(
        f"/api/projects/{pid}/llm-config",
        json={"model": "m", "api_key": "k"},
        headers=_h("bob"),
    )
    assert r.status_code == 403
    # tester 不能加成员
    r = await client.post(
        f"/api/projects/{pid}/members", json={"user_id": "carol"}, headers=_h("bob")
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_platform_admin_bypasses(ctx):
    client, _ = ctx
    pid = (await client.post("/api/projects", json={"name": "A"}, headers=_h("alice"))).json()["id"]
    # root 不是成员,但平台管理员等效 admin
    r = await client.put(
        f"/api/projects/{pid}/llm-config",
        json={"model": "m", "api_key": "k"},
        headers=_h("root"),
    )
    assert r.status_code == 200


# ── 版本 + 克隆 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_version_create_and_clone(ctx):
    client, store = ctx
    pid = (await client.post("/api/projects", json={"name": "A"}, headers=_h("alice"))).json()["id"]
    v1 = (
        await client.post(
            f"/api/projects/{pid}/versions", json={"name": "1.0"}, headers=_h("alice")
        )
    ).json()["id"]
    v2 = (
        await client.post(
            f"/api/projects/{pid}/versions", json={"name": "1.1"}, headers=_h("alice")
        )
    ).json()["id"]
    # 在 v1 下建一个 suite(带租户字段)
    from input.models import Suite

    await store.save_suite(Suite(id="s1", name="冒烟", base_url="x", project_id=pid, version_id=v1))
    r = await client.post(
        f"/api/projects/{pid}/versions/{v2}/clone-suites?from_version_id={v1}", headers=_h("alice")
    )
    assert r.status_code == 200
    assert r.json()["cloned"] == 1
    assert len(await store.list_suites(version_id=v2)) == 1


# ── LLM 配置(加密/掩码/自检)─────────────────────────────────


@pytest.mark.asyncio
async def test_llm_config_masks_key_and_preserves(ctx):
    client, store = ctx
    pid = (await client.post("/api/projects", json={"name": "A"}, headers=_h("alice"))).json()["id"]
    r = await client.put(
        f"/api/projects/{pid}/llm-config",
        json={"model": "openai/foo", "api_key": "sk-secret123"},
        headers=_h("alice"),
    )
    assert r.json()["api_key_masked"].endswith("123")
    assert "sk-secret123" not in r.json()["api_key_masked"]
    # 传掩码 → 原 key 保留
    await client.put(
        f"/api/projects/{pid}/llm-config",
        json={"model": "openai/bar", "api_key": "••••••123"},
        headers=_h("alice"),
    )
    cfg = await store.get_llm_config(pid)
    assert cfg.api_key == "sk-secret123" and cfg.model == "openai/bar"


@pytest.mark.asyncio
async def test_suite_tenant_isolation(ctx):
    """带 project_id 的 suite:非成员 403,成员 200;list 按项目作用域。"""
    client, _ = ctx
    pid = (await client.post("/api/projects", json={"name": "A"}, headers=_h("alice"))).json()["id"]
    # alice 在自己项目下建 suite
    r = await client.post(
        "/api/suites",
        json={"name": "S", "base_url": "https://x", "project_id": pid},
        headers=_h("alice"),
    )
    assert r.status_code == 200
    sid = r.json()["id"]
    # 非成员看不到 / 进不去
    assert (await client.get(f"/api/suites/{sid}", headers=_h("stranger"))).status_code == 403
    assert (await client.get(f"/api/suites/{sid}", headers=_h("alice"))).status_code == 200
    # 非成员不能在该项目建 suite
    r = await client.post(
        "/api/suites",
        json={"name": "X", "base_url": "https://x", "project_id": pid},
        headers=_h("stranger"),
    )
    assert r.status_code == 403
    # 按项目 list:alice 成员可列,stranger 403
    assert (
        await client.get(f"/api/suites?project_id={pid}", headers=_h("alice"))
    ).status_code == 200
    assert (
        await client.get(f"/api/suites?project_id={pid}", headers=_h("stranger"))
    ).status_code == 403


@pytest.mark.asyncio
async def test_llm_check_mocked(ctx, monkeypatch):
    client, _ = ctx
    pid = (await client.post("/api/projects", json={"name": "A"}, headers=_h("alice"))).json()["id"]
    await client.put(
        f"/api/projects/{pid}/llm-config",
        json={"model": "openai/foo", "api_key": "k"},
        headers=_h("alice"),
    )
    from harness.llm import LLMResponse, Usage

    class _FakeLLM:
        model = "openai/foo"

        async def chat(self, messages, tools=None, **kw):
            return LLMResponse(content="正常", usage=Usage(total_tokens=5))

    import api.routers.projects as proj

    monkeypatch.setattr(proj, "build_llm_client", lambda cfg: _FakeLLM())
    r = await client.post(f"/api/projects/{pid}/llm-config/check", headers=_h("alice"))
    assert r.status_code == 200 and r.json()["ok"] is True
