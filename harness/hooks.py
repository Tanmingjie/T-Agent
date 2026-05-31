"""Hook 生命周期(规格 §5.4 Hooks,T-13)。

六个生命周期事件:
``before_suite`` / ``before_case``★ / ``after_case``★ / ``on_heal`` / ``on_failure`` /
``after_suite``。

要点(§5.4):
- 顺序执行队列;同一事件的多个 hook 按注册顺序依次跑。
- 共享 ``ExecutionContext`` 在 hook 间、hook 与 Agent 间传递状态(如登录 Cookie)。
- **before_case 失败 → 用例直接 FAIL,不进 Agent**(由调用方据 HookResult.ok 处理)。
- after_case 用于清理/登出,通常无论成败都跑。

Hook 可为同步或异步可调用,签名 ``hook(ctx: ExecutionContext)``;抛 ``HookError``
(或任意异常)表示该 hook 失败,队列即停并返回失败结果。
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# 事件名(顺序仅作文档)
BEFORE_SUITE = "before_suite"
BEFORE_CASE = "before_case"
AFTER_CASE = "after_case"
ON_HEAL = "on_heal"
ON_FAILURE = "on_failure"
AFTER_SUITE = "after_suite"

EVENTS = (BEFORE_SUITE, BEFORE_CASE, AFTER_CASE, ON_HEAL, ON_FAILURE, AFTER_SUITE)

Hook = Callable[["ExecutionContext"], Any | Awaitable[Any]]


class HookError(Exception):
    """hook 主动表示失败(如 before_case 的登录失败)。"""


@dataclass
class ExecutionContext:
    """跨 hook / Agent 共享的执行上下文。"""

    case: Any = None  # TestCase
    suite: Any = None  # Suite
    session: Any = None  # SessionProfile(T-14)
    data: dict = field(default_factory=dict)  # 任意共享状态(cookies/env/…)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass
class HookResult:
    event: str
    ok: bool = True
    ran: int = 0  # 成功执行的 hook 数
    error: str = ""
    failed_hook: str = ""

    def __bool__(self) -> bool:
        return self.ok


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _hook_name(hook: Hook) -> str:
    return getattr(hook, "__name__", hook.__class__.__name__)


class HookManager:
    """注册并按事件顺序执行 hook。"""

    def __init__(self) -> None:
        self._hooks: dict[str, list[Hook]] = {e: [] for e in EVENTS}

    def register(self, event: str, hook: Hook) -> None:
        if event not in self._hooks:
            raise ValueError(f"未知 hook 事件:{event}(合法:{', '.join(EVENTS)})")
        self._hooks[event].append(hook)

    def register_many(self, mapping: dict[str, list[Hook]]) -> None:
        """批量注册 {event: [hooks]}(如来自 Suite.hooks 解析后的可调用列表)。"""
        for event, hooks in mapping.items():
            for h in hooks:
                self.register(event, h)

    def hooks_for(self, event: str) -> list[Hook]:
        return list(self._hooks.get(event, []))

    async def run(self, event: str, ctx: ExecutionContext) -> HookResult:
        """顺序执行某事件的所有 hook。任一失败即停并返回失败结果。"""
        hooks = self._hooks.get(event, [])
        ran = 0
        for hook in hooks:
            name = _hook_name(hook)
            try:
                await _maybe_await(hook(ctx))
                ran += 1
            except HookError as e:
                logger.warning("hook %s(%s)失败:%s", name, event, e)
                return HookResult(event=event, ok=False, ran=ran, error=str(e), failed_hook=name)
            except Exception as e:  # noqa: BLE001 — hook 抛错也算失败,不炸主流程
                logger.warning("hook %s(%s)异常:%s", name, event, e)
                return HookResult(
                    event=event,
                    ok=False,
                    ran=ran,
                    error=f"{type(e).__name__}: {e}",
                    failed_hook=name,
                )
        return HookResult(event=event, ok=True, ran=ran)
