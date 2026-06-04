"""MCP 客户端封装(规格 §5.4 MCP 客户端 / T-02)。

用**官方 mcp Python SDK** 的 stdio 传输连 playwright-mcp。

关键约束(规格 §0 / §7):**必须 stdio,不要 CDP HTTP 连接**。
stdio 是进程间管道通信,不走 HTTP,绕过内网代理(CDP HTTP 会被代理拦截 → 504)。
playwright-mcp 服务端默认就以 stdio 暴露 MCP 协议,并在本机用 Playwright 直接驱动
浏览器——这正是我们要的形态。

> 命名说明:本地包名为 ``mcp_client`` 而非 ``mcp``,以避免与官方 ``mcp`` SDK 顶层包
> 名冲突(同名会导致 ``import mcp`` 解析到本地包)。
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

# playwright-mcp 的默认启动方式:npx @playwright/mcp(stdio)
DEFAULT_COMMAND = "npx"
DEFAULT_ARGS: list[str] = ["@playwright/mcp@latest"]


# ── 纯函数:MCP ↔ LiteLLM 格式转换(无需 live server,可单测) ────────


def mcp_tool_to_litellm(tool: types.Tool) -> dict[str, Any]:
    """单个 MCP Tool → LiteLLM/OpenAI function-calling 工具格式。"""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or tool.title or tool.name,
            # MCP 的 inputSchema 本就是 JSON Schema,直接作为 parameters
            "parameters": tool.inputSchema or {"type": "object", "properties": {}},
        },
    }


def mcp_tools_to_litellm(tools: list[types.Tool]) -> list[dict[str, Any]]:
    """MCP 工具列表 → LiteLLM 工具列表。"""
    return [mcp_tool_to_litellm(t) for t in tools]


def call_result_to_text(result: types.CallToolResult) -> str:
    """把 CallToolResult 的内容块拍平成文本,喂回 LLM 观察(Observe)。

    - TextContent → 原文
    - ImageContent → 占位标记(截图另由 recorder 单独保存,不塞进文本上下文)
    - 其它资源 → 简短占位
    若 structuredContent 存在,附加其 JSON 概要(数据断言/结构化结果有用)。
    """
    parts: list[str] = []
    for block in result.content or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            parts.append(block.text)
        elif btype == "image":
            mime = getattr(block, "mimeType", "image")
            parts.append(f"[image:{mime}]")
        elif btype == "resource":
            parts.append("[resource]")
        else:
            # 兜底:尽量取 text 字段,否则给类型占位
            text = getattr(block, "text", None)
            parts.append(text if text is not None else f"[{btype or 'content'}]")

    if result.structuredContent:
        import json

        parts.append("[structured] " + json.dumps(result.structuredContent, ensure_ascii=False))

    return "\n".join(parts).strip()


def call_result_to_image_bytes(result: types.CallToolResult) -> bytes | None:
    """从 CallToolResult 里取第一块 ImageContent 的原始字节(base64 解码)。

    browser_take_screenshot 返回的是 ImageContent(base64 PNG/JPEG)。供 recorder
    落盘成 step_NNN.png,前端按 run_id/case_id/step 路径读取。无图块返回 None。
    """
    import base64

    for block in result.content or []:
        if getattr(block, "type", None) == "image":
            data = getattr(block, "data", None)
            if not data:
                return None
            try:
                return base64.b64decode(data)
            except (ValueError, TypeError):
                return None
    return None


# ── MCP 客户端 ─────────────────────────────────────────────────────


class MCPClient:
    """管理一个 playwright-mcp stdio 会话的生命周期。

    用法::

        async with MCPClient() as client:
            tools = client.to_litellm_tools()
            result = await client.call_tool("browser_navigate", {"url": "..."})
            text = client.result_to_text(result)
    """

    def __init__(
        self,
        command: str = DEFAULT_COMMAND,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._params = StdioServerParameters(
            command=command,
            args=list(args) if args is not None else list(DEFAULT_ARGS),
            env=env,
        )
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None
        self._tools: list[types.Tool] = []

    # —— 生命周期 ——

    async def connect(self) -> "MCPClient":
        """启动 stdio 子进程、建立 MCP 会话、初始化并拉取工具列表。"""
        stack = AsyncExitStack()
        read, write = await stack.enter_async_context(stdio_client(self._params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._stack = stack
        self.session = session
        await self.refresh_tools()
        return self

    async def aclose(self) -> None:
        """关闭会话与 stdio 子进程。"""
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self.session = None

    async def __aenter__(self) -> "MCPClient":
        return await self.connect()

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # —— 工具 ——

    async def refresh_tools(self) -> list[types.Tool]:
        """重新拉取工具列表(playwright-mcp 工具集相对固定,通常连后一次即可)。"""
        self._require_session()
        resp = await self.session.list_tools()  # type: ignore[union-attr]
        self._tools = list(resp.tools)
        return self._tools

    def list_tools(self) -> list[types.Tool]:
        """返回已缓存的原始 MCP 工具列表。"""
        return self._tools

    def to_litellm_tools(self) -> list[dict[str, Any]]:
        """返回 LiteLLM 工具格式列表(供 LLM tool-calling)。"""
        return mcp_tools_to_litellm(self._tools)

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, *, timeout: float = 120.0
    ) -> types.CallToolResult:
        """调用一个 MCP 工具,返回原始 CallToolResult。超时则抛 asyncio.TimeoutError。"""
        self._require_session()
        return await asyncio.wait_for(
            self.session.call_tool(name, arguments or {}),  # type: ignore[union-attr]
            timeout=timeout,
        )

    @staticmethod
    def result_to_text(result: types.CallToolResult) -> str:
        """CallToolResult → 文本(供 Observe 喂回 LLM)。"""
        return call_result_to_text(result)

    @staticmethod
    def result_to_image_bytes(result: types.CallToolResult) -> bytes | None:
        """CallToolResult → 截图字节(无图返回 None)。"""
        return call_result_to_image_bytes(result)

    def _require_session(self) -> None:
        if self.session is None:
            raise RuntimeError("MCPClient 未连接,请先 await connect()(或用 async with)。")
