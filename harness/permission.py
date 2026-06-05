"""Permission 拦截(规格 §5.4 Permission,T-17)。

在 ReAct 的 **Reason 后、Act 前**拦截高危操作:

- **高危词白名单**:工具名 / 参数值里出现 删除/提交/支付/确认 等 → 需审批。
- **环境锁**:当前 URL 命中 prod 标记 → 需审批。
- **审批流**:需审批时调 ``approver``(可注入,真实场景可用 asyncio.Event 暂停等待 UI 确认)。
  - ``trust_mode=True``:跳过一切审批(信任模式)。
  - 无 approver 且需审批:**默认拒绝**(最安全——拿不到确认就不放行高危操作)。

职责单一:只判定"是否放行",不执行工具;由 ReAct 循环据结果决定执行或回灌"被拒"观察。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

DEFAULT_DANGEROUS_WORDS = [
    "删除",
    "提交",
    "支付",
    "确认",
    "delete",
    "submit",
    "pay",
    "confirm",
    "remove",
]
DEFAULT_PROD_MARKERS = ["prod", "//prod.", ".prod.", "生产"]

Approver = Callable[["PermissionRequest"], "bool | Awaitable[bool]"]


@dataclass
class PermissionRequest:
    tool_name: str
    arguments: dict
    url: str
    reason: str  # 为什么需要审批(命中的高危词 / prod)


class PermissionChecker:
    def __init__(
        self,
        *,
        dangerous_words: list[str] | None = None,
        prod_markers: list[str] | None = None,
        trust_mode: bool = False,
        approver: Approver | None = None,
    ) -> None:
        self.dangerous_words = (
            dangerous_words if dangerous_words is not None else list(DEFAULT_DANGEROUS_WORDS)
        )
        self.prod_markers = prod_markers if prod_markers is not None else list(DEFAULT_PROD_MARKERS)
        self.trust_mode = trust_mode
        self.approver = approver

    def evaluate(self, tool_name: str, arguments: dict, url: str = "") -> PermissionRequest | None:
        """判定是否需要审批;不需要返回 None。"""
        reasons: list[str] = []

        haystack = f"{tool_name} " + " ".join(
            str(v) for v in (arguments or {}).values() if isinstance(v, (str, int, float))
        )
        hit = []
        for w in self.dangerous_words:
            if not w:
                continue
            # CJK words: substring match (no word boundaries in Chinese)
            if any(ord(c) > 0x4E00 for c in w):
                if w.lower() in haystack.lower():
                    hit.append(w)
            else:
                # ASCII words: match as whole word or as underscore/hyphen-separated segment
                pattern = rf"(?:^|[\s_\-]){re.escape(w)}(?:$|[\s_\-])"
                if re.search(pattern, haystack, re.IGNORECASE):
                    hit.append(w)
        if hit:
            reasons.append(f"高危词: {', '.join(hit)}")

        if url and any(m.lower() in url.lower() for m in self.prod_markers if m):
            reasons.append(f"prod 环境: {url}")

        if not reasons:
            return None
        return PermissionRequest(
            tool_name=tool_name, arguments=arguments, url=url, reason=";".join(reasons)
        )

    async def check(self, tool_name: str, arguments: dict, url: str = "") -> bool:
        """返回是否放行执行该工具。"""
        req = self.evaluate(tool_name, arguments, url)
        if req is None:
            return True  # 安全操作
        if self.trust_mode:
            logger.info("信任模式:放行高危操作(%s)", req.reason)
            return True
        if self.approver is None:
            logger.warning("需审批但未配置 approver,默认拒绝:%s", req.reason)
            return False
        result = self.approver(req)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)


def async_event_approver(event: asyncio.Event, result_holder: dict) -> Approver:
    """创建基于 asyncio.Event 的 approver。

    用于 Web 控制台 Permission 暂停交互:
    - 返回的 approver 等待 event.set()
    - 调用方在收到用户选择后 set event,并将结果放入 result_holder
    - 超时 30s 后自动拒绝

    Example:
        event = asyncio.Event()
        result = {"approved": False}
        checker = PermissionChecker(approver=async_event_approver(event, result))
        # ... later, in the API handler:
        result["approved"] = True
        event.set()
    """

    async def _wait_for_approval(req: PermissionRequest) -> bool:
        try:
            await asyncio.wait_for(event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("Permission 超时 30s, 自动拒绝: %s", req.reason)
            return False
        return result_holder.get("approved", False)

    return _wait_for_approval


def threading_event_approver(event, result_holder: dict, timeout: float = 300.0) -> Approver:
    """基于 ``threading.Event`` 的 approver(**跨线程**:执行在 worker loop,审批在 API loop)。

    asyncio.Event 不能跨事件循环 set;改用 threading.Event:worker 侧用
    ``run_in_executor`` 等待(不占事件循环),API 的审批端点在另一线程 ``event.set()``。
    """

    async def _wait(req: PermissionRequest) -> bool:
        loop = asyncio.get_running_loop()
        got = await loop.run_in_executor(None, event.wait, timeout)
        if not got:
            logger.warning("Permission 超时 %ss, 自动拒绝: %s", timeout, req.reason)
            return False
        return result_holder.get("approved", False)

    return _wait
