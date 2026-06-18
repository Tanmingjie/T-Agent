"""凭据登录助手(供主动扫描登录用)。

历史:本模块曾承载「SessionProfile + Cookie 缓存复用」(规格 §5.4)。2026-06-18 起
**会话/登录复用退役**——cookie 抓取/注入/TTL 那套全部移除(对 SPA/Token 型登录不对症,
且 TTL 与真实会话寿命脱节)。登录态的跨用例复用改由后续「环境管理」主线维护;Hook 回归
纯通用扩展点(``harness/hooks.py``),不再内建登录实现。

此处只保留 ``make_mcp_credential_login``:一个最简「账号 + 密码表单登录」回调,
被主动扫描(``intelligence/active_scan.py`` / ``api/routers/vocabulary.py``)用于扫描前登录。
"""

from __future__ import annotations

import json


def make_mcp_credential_login(
    mcp,
    login_url: str,
    username: str,
    password: str,
    *,
    settle=None,
):
    """构造「账号 + 密码」表单登录回调(最简登录方式)。

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
