"""M2 单元测试:HTTP 型 Custom Tool(SSRF 防护 / 加密落库 / registry)。"""

from __future__ import annotations

import pytest

from harness.tools import SSRFError, ToolRegistry, _check_ssrf, build_http_tool_registry
from input.models import ProjectHttpTool
from storage.db import ProjectHttpToolRow, Store

# ── SSRF 防护 ────────────────────────────────────────────────


def test_ssrf_blocks_metadata_link_local():
    with pytest.raises(SSRFError):
        _check_ssrf("http://169.254.169.254/latest/meta-data/")


def test_ssrf_blocks_public_by_default(monkeypatch):
    monkeypatch.delenv("HTTP_TOOL_ALLOW_PUBLIC", raising=False)
    with pytest.raises(SSRFError):
        _check_ssrf("https://8.8.8.8/")


def test_ssrf_allows_private():
    _check_ssrf("http://10.1.2.3/api")  # 内网放行,不抛
    _check_ssrf("http://192.168.0.1/")


def test_ssrf_allows_public_when_opted_in(monkeypatch):
    monkeypatch.setenv("HTTP_TOOL_ALLOW_PUBLIC", "1")
    _check_ssrf("https://8.8.8.8/")  # 放开后不抛


def test_ssrf_rejects_non_http():
    with pytest.raises(SSRFError):
        _check_ssrf("file:///etc/passwd")


def test_ssrf_allow_hosts_whitelist(monkeypatch):
    monkeypatch.setenv("HTTP_TOOL_ALLOW_HOSTS", "example.com")
    _check_ssrf("https://example.com/")  # 白名单跳过 IP 校验


# ── registry 构建 + SSRF 在 call 时拦截 ──────────────────────


async def test_http_tool_call_blocked_by_ssrf_returns_text():
    reg = build_http_tool_registry(
        [ProjectHttpTool(project_id="p1", name="meta", url="http://169.254.169.254/")]
    )
    assert reg.has("meta")
    out = await reg.call("meta", {})
    # 工具失败转文本(不炸循环),含 SSRF 提示
    assert "失败" in out or "SSRF" in out


def test_http_tool_in_litellm_schema():
    reg = build_http_tool_registry(
        [
            ProjectHttpTool(
                project_id="p1",
                name="health",
                url="http://10.0.0.1/health",
                parameters={"type": "object", "properties": {"id": {"type": "string"}}},
            )
        ]
    )
    schema = reg.to_litellm_tools()
    assert schema[0]["function"]["name"] == "health"


# ── Store 加密落库 ───────────────────────────────────────────


@pytest.fixture
async def store(tmp_path):
    s = Store(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await s.init()
    yield s
    await s.close()


async def test_http_tool_headers_encrypted_at_rest(store):
    await store.save_http_tool(
        ProjectHttpTool(
            project_id="p1",
            name="api",
            url="http://10.0.0.1/x",
            headers={"Authorization": "Bearer secret-token"},
        )
    )
    # 读表行:headers 密文,不含明文
    async with store._sf() as s:
        row = await s.get(ProjectHttpToolRow, ("p1", "api"))
    assert "secret-token" not in row.headers_encrypted
    # 读回解密
    tools = await store.list_http_tools("p1")
    assert tools[0].headers["Authorization"] == "Bearer secret-token"


async def test_http_tool_list_and_delete(store):
    await store.save_http_tool(ProjectHttpTool(project_id="p1", name="a", url="http://10.0.0.1/"))
    await store.save_http_tool(ProjectHttpTool(project_id="p1", name="b", url="http://10.0.0.1/"))
    await store.save_http_tool(ProjectHttpTool(project_id="p2", name="c", url="http://10.0.0.1/"))
    assert {t.name for t in await store.list_http_tools("p1")} == {"a", "b"}
    assert await store.delete_http_tool("p1", "a") is True
    assert {t.name for t in await store.list_http_tools("p1")} == {"b"}
    assert await store.delete_http_tool("p1", "nope") is False
