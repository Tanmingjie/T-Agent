"""Custom Tool 注册(规格 §5.4 Custom Tools,T-19)。

两种注册方式:

- ``@registry.tool(...)`` 装饰器(借鉴 browser-use @tools.action 风格),注册 Python 函数
  (同步/异步均可)。
- ``register_command`` / ``register_yaml``:以 shell ``command`` 接入(YAML 配置常用),
  command 支持 ``{arg}`` 占位用调用参数替换。

特性:
- LLM **按需调用**(区别于 Hook 强制执行):``to_litellm_tools()`` 导出工具 schema 供 LLM
  tool-calling,执行经 ``call()``。
- **数据断言**靠 Custom Tool 实现(查库/调接口取真值)。
- 容错:函数工具抛异常 / 命令非零退出,都转成结果文本返回给 LLM,不冒泡炸循环。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_EMPTY_SCHEMA = {"type": "object", "properties": {}}


@dataclass
class _Tool:
    name: str
    description: str
    parameters: dict
    when_to_use: str = ""
    timeout_seconds: int = 30
    func: Callable | None = None  # 函数工具
    command: str | None = None  # 命令工具(shell,{arg} 占位)
    # HTTP 工具(平台化 M2:替代 shell,受控 HTTP 调用,{arg} 占位 + SSRF 防护)
    http: dict | None = None  # {method, url, headers, body}


# HTTP 工具响应体读取上限(防大响应撑爆内存/上下文)
_HTTP_MAX_BYTES = 64 * 1024


class SSRFError(Exception):
    """HTTP 工具目标地址未通过 SSRF 校验(非内网 / 元数据地址等)。"""


def _check_ssrf(url: str) -> None:
    """SSRF 防护:解析目标 host→IP,默认**只放行内网/环回**,拦截公网与云元数据地址。

    `HTTP_TOOL_ALLOW_PUBLIC=1` 可放开公网(谨慎);`HTTP_TOOL_ALLOW_HOSTS=a,b` 额外白名单 host。
    """
    import ipaddress
    import os
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"仅允许 http/https:{parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise SSRFError("URL 缺少 host")

    allow_hosts = {
        h.strip() for h in os.getenv("HTTP_TOOL_ALLOW_HOSTS", "").split(",") if h.strip()
    }
    if host in allow_hosts:
        return
    allow_public = os.getenv("HTTP_TOOL_ALLOW_PUBLIC", "0") == "1"

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        raise SSRFError(f"无法解析 host {host}:{e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # 云元数据地址永远拦(link-local 169.254/16、fd00 等)
        if ip.is_link_local:
            raise SSRFError(f"拦截 link-local 地址 {ip}(疑似云元数据)")
        if not allow_public and ip.is_global:
            raise SSRFError(f"拦截公网地址 {ip}(仅放行内网;HTTP_TOOL_ALLOW_PUBLIC=1 可放开)")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, _Tool] = {}

    # ── 注册 ──────────────────────────────────────────────────

    def tool(
        self,
        *,
        name: str,
        description: str,
        parameters: dict | None = None,
        when_to_use: str = "",
        timeout_seconds: int = 30,
    ):
        """装饰器:把一个 Python 函数注册为 Custom Tool。"""

        def deco(func: Callable) -> Callable:
            self._tools[name] = _Tool(
                name=name,
                description=description,
                parameters=parameters or _EMPTY_SCHEMA,
                when_to_use=when_to_use,
                timeout_seconds=timeout_seconds,
                func=func,
            )
            return func

        return deco

    def register_command(
        self,
        *,
        name: str,
        description: str,
        command: str,
        parameters: dict | None = None,
        when_to_use: str = "",
        timeout_seconds: int = 30,
    ) -> None:
        """注册一个以 shell command 实现的工具(command 支持 {arg} 占位)。"""
        self._tools[name] = _Tool(
            name=name,
            description=description,
            parameters=parameters or _EMPTY_SCHEMA,
            when_to_use=when_to_use,
            timeout_seconds=timeout_seconds,
            command=command,
        )

    def register_http(
        self,
        *,
        name: str,
        description: str,
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str = "",
        parameters: dict | None = None,
        when_to_use: str = "",
        timeout_seconds: int = 30,
    ) -> None:
        """注册一个 HTTP 型 Custom Tool(平台化 M2:受控 HTTP 调用,替代 shell)。

        url/body 支持 ``{arg}`` 占位(用调用参数替换,自动 URL 编码 url 中的值)。
        执行时做 SSRF 校验(默认仅内网)、超时、响应体限长。
        """
        self._tools[name] = _Tool(
            name=name,
            description=description,
            parameters=parameters or _EMPTY_SCHEMA,
            when_to_use=when_to_use,
            timeout_seconds=timeout_seconds,
            http={"method": method.upper(), "url": url, "headers": headers or {}, "body": body},
        )

    def register_yaml(self, config: dict) -> None:
        """从 YAML/字典配置注册(需含 name/description + command)。"""
        self.register_command(
            name=config["name"],
            description=config.get("description", config["name"]),
            command=config["command"],
            parameters=config.get("parameters"),
            when_to_use=config.get("when_to_use", ""),
            timeout_seconds=int(config.get("timeout_seconds", 30)),
        )

    # ── 查询 ──────────────────────────────────────────────────

    @property
    def names(self) -> list[str]:
        return list(self._tools.keys())

    def has(self, name: str) -> bool:
        return name in self._tools

    def to_litellm_tools(self) -> list[dict]:
        """导出 LiteLLM/OpenAI function 格式,供 LLM tool-calling。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    # ── 执行 ──────────────────────────────────────────────────

    async def call(self, name: str, arguments: dict | None = None) -> str:
        """执行工具,返回结果文本。未知工具抛 KeyError;执行异常转文本。"""
        if name not in self._tools:
            raise KeyError(f"未注册的 Custom Tool: {name}")
        tool = self._tools[name]
        args = arguments or {}
        try:
            if tool.func is not None:
                return await self._call_func(tool, args)
            if tool.http is not None:
                return await self._call_http(tool, args)
            return await self._call_command(tool, args)
        except Exception as e:  # noqa: BLE001 — 工具失败不应炸 ReAct 循环
            logger.warning("Custom Tool %s 执行失败:%s", name, e)
            return f"[工具 {name} 执行失败] {type(e).__name__}: {e}"

    async def _call_func(self, tool: _Tool, args: dict) -> str:
        result = tool.func(**args)
        if inspect.isawaitable(result):
            result = await result
        return str(result)

    async def _call_http(self, tool: _Tool, args: dict) -> str:
        from urllib.parse import quote

        import httpx

        spec = tool.http or {}
        url = spec.get("url", "")
        body = spec.get("body", "") or ""
        for k, v in args.items():
            url = url.replace(f"{{{k}}}", quote(str(v), safe=""))
            body = body.replace(f"{{{k}}}", str(v))
        _check_ssrf(url)  # 防 SSRF:默认仅内网,拦元数据/公网
        method = spec.get("method", "GET")
        headers = dict(spec.get("headers", {}))
        async with httpx.AsyncClient(timeout=tool.timeout_seconds, follow_redirects=False) as cli:
            resp = await cli.request(
                method, url, headers=headers, content=body.encode() if body else None
            )
            text = resp.text[: _HTTP_MAX_BYTES // 2]  # 字符数粗略限长
            return f"[{resp.status_code}] {text}"

    async def _call_command(self, tool: _Tool, args: dict) -> str:
        import shlex

        cmd = tool.command or ""
        for k, v in args.items():
            cmd = cmd.replace(f"{{{k}}}", shlex.quote(str(v)))
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=tool.timeout_seconds
            )
        except asyncio.TimeoutError:
            proc.kill()
            return f"[工具 {tool.name} 超时] >{tool.timeout_seconds}s"
        if proc.returncode != 0:
            return f"[工具 {tool.name} 退出码 {proc.returncode}] {stderr.decode().strip()}"
        return stdout.decode().strip()


def build_http_tool_registry(tools: list) -> ToolRegistry:
    """从项目级 HTTP 工具(``input.models.ProjectHttpTool``)组装 ToolRegistry(平台化 M2)。

    供执行链按需调用 + ``custom_tool`` 数据断言;语义与 YAML/命令型一致,只是受控 HTTP。
    """
    reg = ToolRegistry()
    for t in tools:
        reg.register_http(
            name=t.name,
            description=t.description or t.name,
            url=t.url,
            method=t.method,
            headers=dict(t.headers or {}),
            body=t.body or "",
            parameters=t.parameters or None,
            when_to_use=t.when_to_use or "",
            timeout_seconds=int(t.timeout_seconds or 30),
        )
    logger.info("从项目 HTTP 工具加载 %d 个:%s", len(reg.names), reg.names)
    return reg


def load_tool_registry_from_yaml(path: str | Path) -> ToolRegistry:
    """从 YAML 配置文件加载 Custom Tool(规格 §5.4「也支持 YAML 配置接入」),组装成
    ``ToolRegistry``,供执行链按需调用 + 数据断言(custom_tool)使用。

    支持两种顶层结构:
    - ``{"tools": [ {name, description, command, parameters?, when_to_use?, ...}, ... ]}``
    - 直接是工具配置数组 ``[ {...}, ... ]``

    每个工具必须含 ``name`` 与 ``command``(命令型;函数型只能用 ``@registry.tool`` 装饰器,
    不经 YAML)。缺字段/解析失败抛 ``ValueError``。
    """
    import yaml

    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Custom Tool 配置文件不存在:{p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if isinstance(data, dict):
        configs = data.get("tools", [])
    elif isinstance(data, list):
        configs = data
    else:
        raise ValueError(f"Custom Tool 配置应为 dict 或 list,得到 {type(data).__name__}")
    reg = ToolRegistry()
    for cfg in configs:
        if not isinstance(cfg, dict) or "name" not in cfg or "command" not in cfg:
            raise ValueError(f"非法工具配置(需含 name+command):{cfg!r}")
        reg.register_yaml(cfg)
    logger.info("从 %s 加载 %d 个 Custom Tool:%s", p, len(reg.names), reg.names)
    return reg
