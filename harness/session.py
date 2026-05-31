"""Session Profile + LoginHook(规格 §5.4 Session Profile,T-14)。

账号 + login_aw + Cookie 缓存。形态:

- ``SessionManager``:Cookie 文件持久化 + 有效期判定。Cookie 落在 ``cookie_store``,
  连同 ``valid_until`` 一起存盘,**跨用例/跨进程共享**(有效期内不重复登录)。
- ``LoginHook``(注册到 before_case):
  · Cookie 未过期 → 读盘注入浏览器,**跳过登录**;
  · 过期/缺失 → 执行 ``login_aw``(用户已有的登录脚本)拿到新 Cookie → 存盘 → 注入。

环境相关的两处做成**可注入回调**,便于单测与适配:
- ``login_runner(profile, ctx) -> cookies``:跑 login_aw 产出 Cookie(用户环境特定)。
- ``cookie_injector(ctx, cookies)``:把 Cookie 推进浏览器。默认提供基于 playwright-mcp
  ``browser_run_code_unsafe`` 的实现(``make_mcp_cookie_injector``)。

Suite 绑定 Profile;登录失败时 LoginHook 抛 HookError → before_case 失败 → 用例 FAIL。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from harness.hooks import ExecutionContext, HookError, _maybe_await
from input.models import SessionProfile

logger = logging.getLogger(__name__)

CookieList = list[dict]
LoginRunner = Callable[[SessionProfile, ExecutionContext], "CookieList | Awaitable[CookieList]"]
CookieInjector = Callable[[ExecutionContext, CookieList], "Any | Awaitable[Any]"]

DEFAULT_TTL_SECONDS = 3600


class SessionManager:
    """Cookie 持久化 + 有效期判定。"""

    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._now = now

    def _read_store(self, profile: SessionProfile) -> dict | None:
        path = Path(profile.cookie_store)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("读取 Cookie 缓存失败(%s):%s", profile.cookie_store, e)
            return None

    def is_valid(self, profile: SessionProfile) -> bool:
        """Cookie 是否在有效期内(以盘上 valid_until 为准,回退到 profile)。"""
        store = self._read_store(profile)
        valid_until = None
        if store is not None:
            valid_until = store.get("valid_until")
        if valid_until is None:
            valid_until = profile.valid_until
        return (
            bool(valid_until) and valid_until > self._now() and bool(store and store.get("cookies"))
        )

    def load_cookies(self, profile: SessionProfile) -> CookieList:
        store = self._read_store(profile)
        return list(store.get("cookies", [])) if store else []

    def save_cookies(
        self, profile: SessionProfile, cookies: CookieList, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> float:
        """存盘 Cookie + 计算并写入 valid_until,同时回填 profile。返回 valid_until。"""
        valid_until = self._now() + ttl_seconds
        path = Path(profile.cookie_store)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"valid_until": valid_until, "cookies": cookies}, ensure_ascii=False),
            encoding="utf-8",
        )
        profile.valid_until = valid_until
        return valid_until

    def invalidate(self, profile: SessionProfile) -> None:
        """使缓存失效(如登出 / 检测到登录态过期)。"""
        path = Path(profile.cookie_store)
        if path.is_file():
            path.unlink()
        profile.valid_until = None


class LoginHook:
    """before_case hook:Cookie 复用 / 过期重登。"""

    def __init__(
        self,
        profile: SessionProfile,
        manager: SessionManager | None = None,
        *,
        login_runner: LoginRunner | None = None,
        cookie_injector: CookieInjector | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.profile = profile
        self.manager = manager or SessionManager()
        self.login_runner = login_runner
        self.cookie_injector = cookie_injector
        self.ttl_seconds = ttl_seconds

    __name__ = "LoginHook"  # 便于 HookResult.failed_hook 可读

    async def __call__(self, ctx: ExecutionContext) -> None:
        ctx.session = self.profile
        if self.manager.is_valid(self.profile):
            cookies = self.manager.load_cookies(self.profile)
            await self._inject(ctx, cookies)
            ctx.set("login_via", "cookie")
            logger.info("Session %s:Cookie 有效,复用并跳过登录", self.profile.name)
            return

        # 过期/缺失 → 重登
        if self.login_runner is None:
            raise HookError(
                f"Session {self.profile.name}:Cookie 失效且未配置 login_runner(login_aw),无法登录"
            )
        logger.info("Session %s:Cookie 失效,执行 login_aw 重登", self.profile.name)
        cookies = await _maybe_await(self.login_runner(self.profile, ctx))
        if not cookies:
            raise HookError(f"Session {self.profile.name}:login_aw 未返回有效 Cookie")
        self.manager.save_cookies(self.profile, cookies, self.ttl_seconds)
        await self._inject(ctx, cookies)
        ctx.set("login_via", "login_aw")

    async def _inject(self, ctx: ExecutionContext, cookies: CookieList) -> None:
        ctx.set("cookies", cookies)  # 始终放进上下文,供下游/调试
        if self.cookie_injector is not None:
            await _maybe_await(self.cookie_injector(ctx, cookies))


def make_mcp_cookie_injector(mcp, base_url: str) -> CookieInjector:
    """构造基于 playwright-mcp 的 Cookie 注入器。

    通过 ``browser_run_code_unsafe`` 调 ``context.addCookies`` 注入,再导航到 base_url
    使登录态生效。注:依赖运行时浏览器,不在单测覆盖范围。
    """

    async def inject(ctx: ExecutionContext, cookies: CookieList) -> None:
        payload = json.dumps(cookies, ensure_ascii=False)
        code = (
            "async (page) => {"
            f"  await page.context().addCookies({payload});"
            f"  await page.goto({json.dumps(base_url)});"
            "  return 'cookies injected';"
            "}"
        )
        await mcp.call_tool("browser_run_code_unsafe", {"code": code})

    return inject
