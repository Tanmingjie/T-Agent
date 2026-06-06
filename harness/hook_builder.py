"""Hook 组装(把 Session/Login 接进执行链,P2)。

执行链此前从不构造 HookManager(``agent.hooks`` 恒为 ``None``)→ LoginHook/Session
是孤儿代码。本模块把它们接通:按 Suite 绑定的 SessionProfile 组装一个 HookManager,
注册到 before_case 的 ``LoginHook`` + after_case 的 ``CaptureSessionHook``,实现
**跨用例 Cookie 复用**(不接 login_aw 也工作:首个用例 Agent 自行登录→落盘,后续复用)。

设计取舍:
- 没接 login_aw 时 LoginHook 用 ``optional=True``(失效不报错,放行让 Agent 自行登录),
  避免「Cookie 缺失 → before_case 失败 → 所有用例 FAIL」的回归。
- 接了 login_aw(future)时传入 ``login_runner`` 即恢复规格 §5.4 的「过期重登」语义。
"""

from __future__ import annotations

import logging

from harness.hooks import AFTER_CASE, BEFORE_CASE, HookManager
from harness.session import (
    CaptureSessionHook,
    LoginHook,
    SessionManager,
    make_mcp_cookie_capturer,
    make_mcp_cookie_injector,
)
from input.models import SessionProfile

logger = logging.getLogger(__name__)


def build_session_hooks(
    profile: SessionProfile,
    mcp,
    *,
    manager: SessionManager | None = None,
    login_runner=None,
) -> HookManager:
    """按 SessionProfile 组装含 Login/Capture 的 HookManager(跨用例 Cookie 复用)。

    - before_case:``LoginHook``——有有效 Cookie 则注入并跳过登录;否则(无 login_aw)
      放行让 Agent 自行登录。
    - after_case:``CaptureSessionHook``——用例通过后抓浏览器 Cookie 落盘,供后续复用。
    """
    manager = manager or SessionManager()
    hooks = HookManager()
    base_url = profile.base_url
    hooks.register(
        BEFORE_CASE,
        LoginHook(
            profile,
            manager,
            login_runner=login_runner,
            cookie_injector=make_mcp_cookie_injector(mcp, base_url) if base_url else None,
            optional=login_runner is None,  # 无 login_aw → 复用模式(失效不报错)
        ),
    )
    hooks.register(
        AFTER_CASE,
        CaptureSessionHook(
            profile,
            manager,
            cookie_capturer=make_mcp_cookie_capturer(mcp),
        ),
    )
    logger.info(
        "Session %s:已接通 Hook(%s 模式)",
        profile.name,
        "login_aw" if login_runner else "Cookie 复用",
    )
    return hooks
