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
            return await self._call_command(tool, args)
        except Exception as e:  # noqa: BLE001 — 工具失败不应炸 ReAct 循环
            logger.warning("Custom Tool %s 执行失败:%s", name, e)
            return f"[工具 {name} 执行失败] {type(e).__name__}: {e}"

    async def _call_func(self, tool: _Tool, args: dict) -> str:
        result = tool.func(**args)
        if inspect.isawaitable(result):
            result = await result
        return str(result)

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
