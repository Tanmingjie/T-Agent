"""LLM 接入封装(规格 §5.4 LLM 接入 / §8 风险 / T-03)。

- LiteLLM 封装,连本地 Ollama(Qwen3 397B)。**部署细节不硬编码**:模型名 /
  base_url / api_key 全走环境变量(``LLM_MODEL`` / ``LLM_API_BASE`` /
  ``LLM_API_KEY``),验收时指向任意可达模型即可。
- **tool_call 格式容错**(本地模型偶发格式错误,不能炸掉整个 ReAct 循环):
  ①标准 tool_calls 字段 → ②宽松 JSON 修复 → ③从 content 里提取
  (覆盖 Qwen 的 ``<tool_call>{...}</tool_call>`` / ```json 围栏 / 裸 JSON) →
  ④追加纠偏提示**重试 1 次** → 仍失败抛 ``LLMToolCallError`` 交上层决定。
- token 用量统计。
- 模型接口抽象(``LLMClient``),可切换实现(快速失败后换模型)。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# 内网无法访问 GitHub:LiteLLM 默认在 import 时联网拉模型价目表(成本估算用),
# 握手超时后回退本地备份并刷 warning。强制用包内自带的本地备份,既消 warning
# 又免去每次启动的握手等待。需在 import litellm **之前**设置。(用户已显式可覆盖)
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402

# ── 数据结构 ────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    """规范化后的工具调用。"""

    name: str
    arguments: dict[str, Any]
    id: str = ""


@dataclass
class Usage:
    """token 用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens


@dataclass
class LLMResponse:
    """一次 chat 的规范化结果。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw: Any = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class LLMToolCallError(Exception):
    """tool_call 格式经容错+重试仍无法解析。由 ReAct 上层决定如何处理。"""

    def __init__(self, message: str, raw: Any = None) -> None:
        super().__init__(message)
        self.raw = raw


# ── tool_call 容错:纯函数(便于单测) ─────────────────────────────

# Qwen / 一些本地模型把工具调用塞进 content 的常见包裹
_TOOL_CALL_TAG_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```(?:json|tool_code)?\s*(.*?)\s*```", re.DOTALL)


def loads_lenient(text: str) -> dict[str, Any]:
    """宽松解析一段可能不规整的 JSON 文本为 dict。

    依次尝试:严格解析 → 去围栏 → 截取首个平衡 ``{...}`` → 去尾逗号 →
    单引号转双引号。全部失败抛 ``ValueError``。
    """
    if text is None:
        raise ValueError("空内容")
    s = str(text).strip()
    if not s:
        raise ValueError("空内容")

    candidates: list[str] = [s]

    # 去 ```json 围栏
    fence = _JSON_FENCE_RE.search(s)
    if fence:
        candidates.append(fence.group(1).strip())

    # 截取首个 { 到末个 } 的平衡片段
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(s[start : end + 1])

    for cand in candidates:
        for variant in _json_repair_variants(cand):
            try:
                obj = json.loads(variant)
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, ValueError):
                continue
    raise ValueError(f"无法解析为 JSON 对象: {s[:200]!r}")


def _json_repair_variants(s: str):
    """产出若干修复变体:原样 → 去尾逗号 → 单引号转双引号。"""
    yield s
    # 去掉对象/数组结尾的多余逗号
    no_trailing = re.sub(r",\s*([}\]])", r"\1", s)
    if no_trailing != s:
        yield no_trailing
    # 单引号 → 双引号(本地模型常见;仅在无双引号时尝试,避免破坏合法内容)
    if "'" in s and '"' not in s:
        yield no_trailing.replace("'", '"')


def extract_tool_calls_from_content(content: str | None) -> list[dict[str, Any]]:
    """从 content 文本里提取工具调用(模型未走标准 tool_calls 字段时的兜底)。

    支持:``<tool_call>{...}</tool_call>`` 标签、```json 围栏、裸 JSON。
    每个结果形如 ``{"name": ..., "arguments": {...}}``;无则返回空列表。
    """
    if not content:
        return []

    raw_blocks: list[str] = []
    raw_blocks += _TOOL_CALL_TAG_RE.findall(content)
    if not raw_blocks:
        raw_blocks += _JSON_FENCE_RE.findall(content)
    if not raw_blocks:
        # 整段当作可能的裸 JSON
        if "{" in content and "}" in content:
            raw_blocks.append(content)

    calls: list[dict[str, Any]] = []
    for block in raw_blocks:
        try:
            obj = loads_lenient(block)
        except ValueError:
            continue
        norm = _normalize_call_dict(obj)
        if norm is not None:
            calls.append(norm)
    return calls


def _normalize_call_dict(obj: dict[str, Any]) -> dict[str, Any] | None:
    """把各种字段命名统一成 {"name", "arguments"}。"""
    name = obj.get("name") or obj.get("tool") or obj.get("function")
    if isinstance(name, dict):  # 形如 {"function": {"name":..., "arguments":...}}
        inner = name
        name = inner.get("name")
        args = inner.get("arguments", {})
    else:
        args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
    if not name:
        return None
    if isinstance(args, str):
        try:
            args = loads_lenient(args)
        except ValueError:
            args = {}
    if not isinstance(args, dict):
        args = {}
    return {"name": str(name), "arguments": args}


_RETRY_NUDGE = (
    "你上一条回复的工具调用格式无法解析。请严格只通过 tool_call 机制返回一个工具调用,"
    "参数为合法 JSON,不要把工具调用写进普通文本。"
)


# ── 客户端抽象 + LiteLLM 实现 ──────────────────────────────────────


class LLMClient(ABC):
    """模型接口抽象,可切换实现(规格:快速失败后换模型)。"""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse: ...


class LiteLLMClient(LLMClient):
    """LiteLLM 实现。默认从环境变量读取部署配置(不硬编码)。"""

    def __init__(
        self,
        model: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        *,
        max_tool_retries: int = 1,
        temperature: float = 0.0,
        extra_completion_kwargs: dict[str, Any] | None = None,
    ) -> None:
        # ollama/qwen3 仅为占位默认;验收时由 env 覆盖到真实可达模型
        self.model = model or os.getenv("LLM_MODEL", "ollama/qwen3")
        self.api_base = api_base or os.getenv("LLM_API_BASE") or None
        self.api_key = api_key or os.getenv("LLM_API_KEY") or None
        self.max_tool_retries = max_tool_retries
        self.temperature = temperature
        self.extra_completion_kwargs = extra_completion_kwargs or {}
        self.total_usage = Usage()
        # 单次 LLM 调用超时(秒)。内网慢模型可经 env 调大。
        self.timeout = float(os.getenv("LLM_TIMEOUT", "120"))

    # —— 公开接口 ——

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """一次对话。tools 非空时启用 tool-calling 并做格式容错。"""
        expect_tools = bool(tools)
        resp = await self._complete(messages, tools, **kwargs)
        parsed = self._parse(resp, expect_tools)

        # 仅当「模型疑似尝试调用工具但格式坏了」时才重试
        attempts = 0
        convo = list(messages)
        while parsed.error and attempts < self.max_tool_retries:
            attempts += 1
            convo = convo + [
                {"role": "assistant", "content": parsed.raw_content or ""},
                {"role": "user", "content": _RETRY_NUDGE},
            ]
            resp = await self._complete(convo, tools, **kwargs)
            parsed = self._parse(resp, expect_tools)

        if parsed.error:
            raise LLMToolCallError(parsed.error, raw=resp)

        return LLMResponse(
            content=parsed.content,
            tool_calls=parsed.tool_calls,
            usage=parsed.usage,
            raw=resp,
        )

    def usage_summary(self) -> Usage:
        """累计 token 用量(跨多次 chat)。"""
        return self.total_usage

    def reset_usage(self) -> None:
        self.total_usage = Usage()

    # —— 内部 ——

    async def _complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> Any:
        call_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            **self.extra_completion_kwargs,
            **kwargs,
        }
        if self.api_base:
            call_kwargs["api_base"] = self.api_base
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        if tools:
            call_kwargs["tools"] = tools
        call_kwargs["timeout"] = self.timeout
        # 关键:用同步 litellm.completion + to_thread 把整次调用(含 litellm 的同步开销:
        # tiktoken 计数 / 日志 / 回调,会随快照历史增大而变重)挪到**工作线程**,不占用
        # uvicorn 的事件循环。否则执行期间 LLM 调用会周期性冻结单一事件循环,导致所有
        # HTTP 接口在用例执行中/收尾时 pending(实测现象)。串行执行,单次仅一线程。
        return await asyncio.to_thread(litellm.completion, **call_kwargs)

    def _parse(self, resp: Any, expect_tools: bool = True) -> "_Parsed":
        """从 litellm 响应解析出规范化结果 + 容错。

        ``expect_tools=False``(调用方未传 tools,如 Scanner / SpecGenerator 要的是
        纯文本/JSON 内容)时**跳过 content 里的 tool_call 兜底与报错**——否则正常
        内容里只要含 ``"name"`` 子串就会被误判成坏掉的工具调用而抛错。
        """
        usage = _extract_usage(resp)
        self.total_usage.add(usage)

        try:
            message = resp.choices[0].message
        except (AttributeError, IndexError, KeyError):
            return _Parsed(content="", tool_calls=[], usage=usage, error="响应缺少 choices/message")

        content = getattr(message, "content", None) or ""
        raw_tool_calls = getattr(message, "tool_calls", None) or []

        calls: list[ToolCall] = []
        had_error = False

        # ① 标准 tool_calls 字段(含宽松 JSON 修复)
        for tc in raw_tool_calls:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", None) if fn else None
            args_raw = getattr(fn, "arguments", None) if fn else None
            if not name:
                had_error = True
                continue
            args: dict[str, Any]
            if isinstance(args_raw, dict):
                args = args_raw
            elif args_raw in (None, ""):
                args = {}
            else:
                try:
                    args = loads_lenient(args_raw)
                except ValueError:
                    had_error = True
                    continue
            calls.append(ToolCall(name=name, arguments=args, id=getattr(tc, "id", "") or ""))

        # ② 标准字段没拿到调用 → 尝试从 content 兜底提取(仅当调用方期望工具调用)
        if expect_tools and not calls and content:
            for c in extract_tool_calls_from_content(content):
                calls.append(ToolCall(name=c["name"], arguments=c["arguments"]))
            # content 里确实像工具调用却没提取出来,记为错误以触发重试
            if not calls and _looks_like_tool_call(content):
                had_error = True

        # 拿到任何可用调用就不算失败(部分容错优于全盘失败)
        if had_error and calls:
            logger.warning(
                "部分 tool_call 解析失败,已丢弃%d个无效调用", len(raw_calls) - len(calls)
            )
        error = "" if calls or not had_error else "tool_call 格式无法解析"
        return _Parsed(
            content=content, tool_calls=calls, usage=usage, error=error, raw_content=content
        )


def _looks_like_tool_call(content: str) -> bool:
    return "<tool_call>" in content or '"name"' in content or "'name'" in content


@dataclass
class _Parsed:
    content: str
    tool_calls: list[ToolCall]
    usage: Usage
    error: str = ""
    raw_content: str = ""


def _extract_usage(resp: Any) -> Usage:
    u = getattr(resp, "usage", None)
    if u is None:
        return Usage()

    def _get(key: str) -> int:
        val = getattr(u, key, None)
        if val is None and isinstance(u, dict):
            val = u.get(key)
        return int(val or 0)

    return Usage(
        prompt_tokens=_get("prompt_tokens"),
        completion_tokens=_get("completion_tokens"),
        total_tokens=_get("total_tokens"),
    )
