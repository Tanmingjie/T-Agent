"""T-P06 API 测试:项目级 LLM 配置路由(加密落库 / 掩码回显 / 自检)。"""

import pytest
from httpx import ASGITransport, AsyncClient

from api.repository import SQLModelRepository
from api.server import app
from storage.db import Store


@pytest.fixture
async def client():
    store = Store(url="sqlite+aiosqlite://")
    await store.init()
    repo = SQLModelRepository(store)
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
async def test_put_then_get_masks_key(client):
    r = await client.put(
        "/api/projects/p1/llm-config",
        json={"model": "openai/foo", "api_base": "http://gw/v1", "api_key": "sk-secret123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "openai/foo"
    assert body["has_key"] is True
    assert "sk-secret123" not in body["api_key_masked"]  # 绝不返明文
    assert body["api_key_masked"].endswith("123")

    # GET 同样掩码
    r = await client.get("/api/projects/p1/llm-config")
    assert r.status_code == 200
    assert r.json()["api_key_masked"].endswith("123")


@pytest.mark.asyncio
async def test_get_unconfigured_returns_empty_shell(client):
    r = await client.get("/api/projects/none/llm-config")
    assert r.status_code == 200
    assert r.json()["has_key"] is False
    assert r.json()["model"] == ""


@pytest.mark.asyncio
async def test_put_with_mask_preserves_existing_key(client):
    await client.put(
        "/api/projects/p1/llm-config",
        json={"model": "m", "api_key": "sk-real"},
    )
    # 再次保存只改 model,api_key 传掩码 → 原 key 保留
    r = await client.put(
        "/api/projects/p1/llm-config",
        json={"model": "m2", "api_key": "••••••••real"},
    )
    assert r.status_code == 200
    assert r.json()["has_key"] is True
    # 用 store 验证原 key 仍在
    import api.server as srv

    cfg = await srv._store.get_llm_config("p1")
    assert cfg.api_key == "sk-real"
    assert cfg.model == "m2"


@pytest.mark.asyncio
async def test_delete_llm_config(client):
    await client.put("/api/projects/p1/llm-config", json={"model": "m", "api_key": "k"})
    r = await client.delete("/api/projects/p1/llm-config")
    assert r.status_code == 200
    r = await client.delete("/api/projects/p1/llm-config")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_check_requires_model(client):
    r = await client.post("/api/projects/p1/llm-config/check")
    assert r.status_code == 400  # 未配置 model


@pytest.mark.asyncio
async def test_check_reports_connectivity(client, monkeypatch):
    await client.put("/api/projects/p1/llm-config", json={"model": "openai/foo", "api_key": "k"})

    # mock build_llm_client → 返回一个假 LLM(不真打网络)
    from harness.llm import LLMResponse, Usage

    class _FakeLLM:
        model = "openai/foo"

        async def chat(self, messages, tools=None, **kw):
            return LLMResponse(content="正常", usage=Usage(total_tokens=5))

    import api.routers.projects as proj

    monkeypatch.setattr(proj, "build_llm_client", lambda cfg: _FakeLLM())
    r = await client.post("/api/projects/p1/llm-config/check")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["reply"] == "正常"
