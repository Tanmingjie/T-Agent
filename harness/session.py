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
import re
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
        if store is None:
            return []
        return list(store.get("cookies", []))

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
    """before_case hook:Cookie 复用 / 过期重登。

    ``optional``:无 ``login_runner``(未接 login_aw)且 Cookie 失效时的行为开关。
    - False(默认,贴合规格 §5.4):抛 ``HookError`` → before_case 失败 → 用例 FAIL。
    - True(无 login_aw 的「Cookie 复用」模式):**不报错、不重登**,仅 log 后放行,
      让 Agent 用例步骤自行登录;配合 ``CaptureSessionHook`` 在登录成功后落盘 Cookie,
      使后续用例复用。这样不接 login_aw 也能做到「跨用例 Cookie 复用」。
    """

    def __init__(
        self,
        profile: SessionProfile,
        manager: SessionManager | None = None,
        *,
        login_runner: LoginRunner | None = None,
        cookie_injector: CookieInjector | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        optional: bool = False,
    ) -> None:
        self.profile = profile
        self.manager = manager or SessionManager()
        self.login_runner = login_runner
        self.cookie_injector = cookie_injector
        self.ttl_seconds = ttl_seconds
        self.optional = optional

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
            if self.optional:
                logger.info(
                    "Session %s:无有效 Cookie 且未接 login_aw,Agent 将自行登录(复用模式)",
                    self.profile.name,
                )
                ctx.set("login_via", "agent")
                return
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


class CaptureSessionHook:
    """after_case hook:用例(成功登录)后从浏览器抓 Cookie 落盘,供后续用例复用。

    这是「不接 login_aw 也能跨用例复用」的另一半:LoginHook(optional)放行让 Agent
    自行登录,本 hook 在之后把浏览器里的 Cookie 持久化。已有有效 Cookie 时跳过(免churn)。

    ``cookie_capturer(ctx) -> CookieList``:从浏览器读取当前 Cookie(环境相关,可注入)。
    默认实现见 ``make_mcp_cookie_capturer``(依赖运行时浏览器,不在单测覆盖)。
    """

    def __init__(
        self,
        profile: SessionProfile,
        manager: SessionManager | None = None,
        *,
        cookie_capturer: "Callable[[ExecutionContext], CookieList | Awaitable[CookieList]] | None" = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        only_on_pass: bool = True,
    ) -> None:
        self.profile = profile
        self.manager = manager or SessionManager()
        self.cookie_capturer = cookie_capturer
        self.ttl_seconds = ttl_seconds
        self.only_on_pass = only_on_pass

    __name__ = "CaptureSessionHook"

    async def __call__(self, ctx: ExecutionContext) -> None:
        if self.cookie_capturer is None:
            return
        if self.manager.is_valid(self.profile):
            return  # 已有有效 Cookie,无需重复抓取
        if self.only_on_pass and not ctx.get("passed", False):
            return  # 仅在用例通过(通常意味着登录成功)后才落盘
        try:
            cookies = await _maybe_await(self.cookie_capturer(ctx))
        except Exception as e:  # noqa: BLE001 — 抓 Cookie 失败不影响用例结果
            logger.warning("Session %s:抓取 Cookie 失败:%s", self.profile.name, e)
            return
        if not cookies:
            return
        self.manager.save_cookies(self.profile, cookies, self.ttl_seconds)
        logger.info(
            "Session %s:已捕获并落盘 %d 条 Cookie,供后续用例复用", self.profile.name, len(cookies)
        )


def make_mcp_cookie_capturer(mcp):
    """构造基于 playwright-mcp 的 Cookie 抓取器(``context.cookies()``)。

    通过 ``browser_run_code_unsafe`` 读取当前上下文 Cookie 并解析成列表。注:依赖运行时
    浏览器,不在单测覆盖范围(解析逻辑见 ``_parse_cookies_result``,可单测)。
    """

    async def capture(ctx: ExecutionContext) -> CookieList:
        code = "async (page) => { return JSON.stringify(await page.context().cookies()); }"
        result = await mcp.call_tool("browser_run_code_unsafe", {"code": code})
        text = mcp.result_to_text(result) if hasattr(mcp, "result_to_text") else str(result)
        return _parse_cookies_result(text)

    return capture


def _parse_cookies_result(text: str) -> CookieList:
    """从工具返回文本里宽松解析出 Cookie 列表。

    兼容两种形态:
    - **双重编码**(playwright-mcp 实测):返回值被再 ``JSON.stringify`` 一次 → 文本里是
      带引号的 JSON 字符串字面量,如 ``"[{\\"name\\":...}]"``。需先 loads 外层字符串、
      再 loads 内层数组。
    - 直接 JSON 数组 ``[{...}]``。
    """
    if not text:
        return []

    def _ok(data) -> CookieList:
        return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []

    # 1) 双重编码:带引号的 JSON 字符串字面量 → loads 两次
    m = re.search(r'"\[.*?\]"', text, re.DOTALL)
    if m:
        try:
            inner = json.loads(m.group(0))
            data = json.loads(inner) if isinstance(inner, str) else inner
            cookies = _ok(data)
            if cookies:
                return cookies
        except (json.JSONDecodeError, ValueError):
            pass

    # 2) 直接数组
    i, j = text.find("["), text.rfind("]")
    if i != -1 and j > i:
        try:
            return _ok(json.loads(text[i : j + 1]))
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def make_mcp_credential_login(
    mcp,
    login_url: str,
    username: str,
    password: str,
    *,
    settle=None,
):
    """构造「账号 + 密码」表单登录回调(最简登录方式,无需预配 Session Profile)。

    流程:导航到 ``login_url`` → 用启发式选择器在 DOM 里填用户名/密码 → 点提交按钮
    (无则在密码框回车)→ 等页面稳定。登录态随浏览器上下文保留,供后续扫描页复用。

    选择器启发式(覆盖绝大多数登录表单,无需用户给定位):
    - 密码:``input[type=password]``;
    - 用户名:优先 text/email 及 name/id 含 user/account/email/login 的输入框,
      退回首个非密码/隐藏/勾选类输入框;
    - 提交:``button[type=submit]`` / ``input[type=submit]`` / 文案含「登录/Login」的按钮,
      无则密码框 ``Enter``。

    注:依赖运行时浏览器(``browser_run_code_unsafe``),不在单测覆盖范围。
    """

    async def login() -> None:
        await mcp.call_tool("browser_navigate", {"url": login_url})
        if settle is not None:
            await settle(mcp)
        user_js = json.dumps(username, ensure_ascii=False)
        pw_js = json.dumps(password, ensure_ascii=False)
        code = (
            "async (page) => {"
            "  const fill = async (sel, val) => {"
            "    const el = page.locator(sel).first();"
            "    if (await el.count()) { await el.fill(val); return true; }"
            "    return false;"
            "  };"
            f"  await fill('input[type=\"password\"]', {pw_js});"
            "  const userSels = ["
            "    'input[type=\"text\"]', 'input[type=\"email\"]',"
            "    'input[name*=\"user\" i]', 'input[name*=\"account\" i]',"
            "    'input[name*=\"email\" i]', 'input[name*=\"login\" i]',"
            "    'input[id*=\"user\" i]', 'input[id*=\"account\" i]'"
            "  ];"
            "  let done = false;"
            f"  for (const s of userSels) {{ if (await fill(s, {user_js})) {{ done = true; break; }} }}"
            "  if (!done) {"
            '    await fill(\'input:not([type="password"]):not([type="hidden"])'
            ':not([type="checkbox"]):not([type="radio"]):not([type="submit"])'
            f':not([type="button"])\', {user_js});'
            "  }"
            '  const btn = page.locator(\'button[type="submit"], input[type="submit"], '
            'button:has-text("登录"), button:has-text("登 录"), button:has-text("Login"), '
            'button:has-text("Sign in")\').first();'
            "  if (await btn.count()) { await btn.click(); }"
            "  else { await page.locator('input[type=\"password\"]').first().press('Enter'); }"
            "  return 'login submitted';"
            "}"
        )
        await mcp.call_tool("browser_run_code_unsafe", {"code": code})
        if settle is not None:
            await settle(mcp)

    return login


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
