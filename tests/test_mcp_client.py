"""T-02 单元测试:MCP 客户端封装。

- 纯函数格式转换(MCP Tool → LiteLLM,CallToolResult → 文本):不需 live server。
- MCPClient 生命周期 / call_tool:用 fake session(mock)驱动。
- 可选:真连 npx @playwright/mcp 冒烟(环境不可用自动跳过)。
"""

from __future__ import annotations

import shutil

import pytest
from mcp import types

from mcp_client.client import (
    MCPClient,
    call_result_to_image_bytes,
    call_result_to_text,
    mcp_tool_to_litellm,
    mcp_tools_to_litellm,
)

# ── 格式转换:MCP Tool → LiteLLM ────────────────────────────────


def test_tool_to_litellm_basic():
    tool = types.Tool(
        name="browser_navigate",
        description="导航到 URL",
        inputSchema={"type": "object", "properties": {"url": {"type": "string"}}},
    )
    out = mcp_tool_to_litellm(tool)
    assert out["type"] == "function"
    assert out["function"]["name"] == "browser_navigate"
    assert out["function"]["description"] == "导航到 URL"
    assert out["function"]["parameters"]["properties"]["url"]["type"] == "string"


def test_tool_to_litellm_description_fallback():
    # 无 description 时回退到 title,再回退到 name
    tool = types.Tool(name="t1", title="标题", inputSchema={"type": "object"})
    assert mcp_tool_to_litellm(tool)["function"]["description"] == "标题"

    tool2 = types.Tool(name="t2", inputSchema={"type": "object"})
    assert mcp_tool_to_litellm(tool2)["function"]["description"] == "t2"


def test_tools_to_litellm_list():
    tools = [
        types.Tool(name="a", inputSchema={"type": "object"}),
        types.Tool(name="b", inputSchema={"type": "object"}),
    ]
    out = mcp_tools_to_litellm(tools)
    assert [t["function"]["name"] for t in out] == ["a", "b"]


# ── 格式转换:CallToolResult → 文本 ────────────────────────────


def test_result_to_text_text_blocks():
    result = types.CallToolResult(
        content=[
            types.TextContent(type="text", text="第一段"),
            types.TextContent(type="text", text="第二段"),
        ]
    )
    assert call_result_to_text(result) == "第一段\n第二段"


def test_result_to_text_image_placeholder():
    result = types.CallToolResult(
        content=[
            types.TextContent(type="text", text="页面快照"),
            types.ImageContent(type="image", data="base64...", mimeType="image/png"),
        ]
    )
    text = call_result_to_text(result)
    assert "页面快照" in text
    assert "[image:image/png]" in text


def test_result_to_text_structured_content():
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text="ok")],
        structuredContent={"count": 3},
    )
    text = call_result_to_text(result)
    assert "ok" in text
    assert "[structured]" in text
    assert "3" in text


def test_result_to_text_empty():
    assert call_result_to_text(types.CallToolResult(content=[])) == ""


def test_result_to_image_bytes_decodes_base64():
    import base64

    raw = b"\x89PNG\r\n fake image bytes"
    result = types.CallToolResult(
        content=[
            types.TextContent(type="text", text="忽略"),
            types.ImageContent(
                type="image", data=base64.b64encode(raw).decode(), mimeType="image/png"
            ),
        ]
    )
    assert call_result_to_image_bytes(result) == raw


def test_result_to_image_bytes_none_when_no_image():
    result = types.CallToolResult(content=[types.TextContent(type="text", text="纯文本")])
    assert call_result_to_image_bytes(result) is None


# ── MCPClient 生命周期 / 调用(fake session) ──────────────────


class _FakeSession:
    def __init__(self):
        self.initialized = False
        self.calls = []

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        return types.ListToolsResult(
            tools=[
                types.Tool(name="browser_navigate", inputSchema={"type": "object"}),
                types.Tool(name="browser_click", inputSchema={"type": "object"}),
            ]
        )

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"called {name}")])


async def test_client_requires_connect_before_call():
    client = MCPClient()
    with pytest.raises(RuntimeError, match="未连接"):
        await client.call_tool("browser_navigate", {"url": "x"})


async def test_client_lifecycle_with_fake_session():
    client = MCPClient()
    # 注入 fake session,跳过真实 stdio 子进程
    fake = _FakeSession()
    client.session = fake
    await fake.initialize()
    await client.refresh_tools()

    # 工具缓存 + LiteLLM 转换
    assert [t.name for t in client.list_tools()] == ["browser_navigate", "browser_click"]
    litellm_tools = client.to_litellm_tools()
    assert litellm_tools[0]["function"]["name"] == "browser_navigate"

    # 调用工具
    result = await client.call_tool("browser_navigate", {"url": "http://x"})
    assert MCPClient.result_to_text(result) == "called browser_navigate"
    assert fake.calls == [("browser_navigate", {"url": "http://x"})]

    # 默认 arguments 为空字典
    await client.call_tool("browser_click")
    assert fake.calls[-1] == ("browser_click", {})


def test_default_stdio_params_use_npx_not_http():
    # 防回归:默认必须是 npx stdio,绝不能出现 CDP HTTP 连接
    client = MCPClient()
    assert client._params.command == "npx"
    assert any("@playwright/mcp" in a for a in client._params.args)


# ── 可选:真连 playwright-mcp 冒烟(环境不可用自动跳过) ─────────


@pytest.mark.skipif(shutil.which("npx") is None, reason="npx 不可用,跳过真连冒烟")
@pytest.mark.skipif(True, reason="默认跳过真连(会启动浏览器);手动去掉此 skip 做集成验证")
async def test_smoke_real_playwright_mcp():
    async with MCPClient() as client:
        tools = client.list_tools()
        names = [t.name for t in tools]
        assert any("navigate" in n for n in names)
