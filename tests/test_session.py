"""T-14 单元测试:Session Profile + LoginHook。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.hooks import ExecutionContext, HookError
from harness.session import (
    LoginHook,
    SessionManager,
    make_mcp_cookie_injector,
)
from input.models import SessionProfile


def _profile(tmp: Path) -> SessionProfile:
    return SessionProfile(
        name="suiteA",
        login_aw="login_aw.py",
        cookie_store=str(tmp / "suiteA.cookies.json"),
        base_url="https://intranet",
    )


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ── SessionManager ───────────────────────────────────────────


def test_invalid_when_no_file(tmp_path):
    mgr = SessionManager(now=_Clock(1000))
    assert not mgr.is_valid(_profile(tmp_path))


def test_save_then_valid_and_load(tmp_path):
    clock = _Clock(1000)
    mgr = SessionManager(now=clock)
    p = _profile(tmp_path)
    mgr.save_cookies(p, [{"name": "sid", "value": "abc"}], ttl_seconds=100)
    assert mgr.is_valid(p)
    assert mgr.load_cookies(p) == [{"name": "sid", "value": "abc"}]
    assert p.valid_until == 1100


def test_expired_after_ttl(tmp_path):
    clock = _Clock(1000)
    mgr = SessionManager(now=clock)
    p = _profile(tmp_path)
    mgr.save_cookies(p, [{"name": "sid", "value": "abc"}], ttl_seconds=100)
    clock.t = 1101  # 超过有效期
    assert not mgr.is_valid(p)


def test_validity_persists_across_manager_instances(tmp_path):
    # 跨"进程"(新 manager 实例)读盘判定 → 跨用例共享
    p = _profile(tmp_path)
    SessionManager(now=_Clock(1000)).save_cookies(p, [{"name": "s"}], ttl_seconds=100)
    p2 = _profile(tmp_path)  # 新 profile 对象,valid_until 为空,只能靠盘
    assert SessionManager(now=_Clock(1050)).is_valid(p2)


def test_invalidate_removes_file(tmp_path):
    mgr = SessionManager(now=_Clock(1000))
    p = _profile(tmp_path)
    mgr.save_cookies(p, [{"name": "s"}], ttl_seconds=100)
    mgr.invalidate(p)
    assert not Path(p.cookie_store).exists()
    assert not mgr.is_valid(p)


def test_corrupt_file_is_invalid(tmp_path):
    p = _profile(tmp_path)
    Path(p.cookie_store).write_text("不是json", encoding="utf-8")
    assert not SessionManager(now=_Clock(1000)).is_valid(p)


# ── LoginHook ────────────────────────────────────────────────


async def test_login_hook_reuses_valid_cookie(tmp_path):
    clock = _Clock(1000)
    mgr = SessionManager(now=clock)
    p = _profile(tmp_path)
    mgr.save_cookies(p, [{"name": "sid", "value": "abc"}], ttl_seconds=1000)

    injected = []

    async def injector(ctx, cookies):
        injected.append(cookies)

    login_called = []

    def runner(profile, ctx):
        login_called.append(True)
        return [{"name": "new"}]

    hook = LoginHook(p, mgr, login_runner=runner, cookie_injector=injector)
    ctx = ExecutionContext()
    await hook(ctx)

    assert ctx.get("login_via") == "cookie"  # 复用,未重登
    assert login_called == []  # login_aw 没被调
    assert injected == [[{"name": "sid", "value": "abc"}]]
    assert ctx.session is p


async def test_login_hook_runs_login_when_expired(tmp_path):
    clock = _Clock(1000)
    mgr = SessionManager(now=clock)
    p = _profile(tmp_path)  # 无 Cookie

    async def runner(profile, ctx):
        return [{"name": "fresh", "value": "tok"}]

    injected = []

    async def injector(ctx, cookies):
        injected.append(cookies)

    hook = LoginHook(p, mgr, login_runner=runner, cookie_injector=injector, ttl_seconds=500)
    ctx = ExecutionContext()
    await hook(ctx)

    assert ctx.get("login_via") == "login_aw"
    assert injected == [[{"name": "fresh", "value": "tok"}]]
    # 重登后 Cookie 已存盘且有效
    assert mgr.is_valid(p)
    assert mgr.load_cookies(p) == [{"name": "fresh", "value": "tok"}]


async def test_login_hook_no_runner_raises(tmp_path):
    hook = LoginHook(_profile(tmp_path), SessionManager(now=_Clock(1000)))  # 无 runner
    with pytest.raises(HookError, match="login_runner"):
        await hook(ExecutionContext())


async def test_login_hook_empty_cookies_raises(tmp_path):
    async def runner(profile, ctx):
        return []  # 登录脚本没拿到 Cookie

    hook = LoginHook(_profile(tmp_path), SessionManager(now=_Clock(1000)), login_runner=runner)
    with pytest.raises(HookError, match="未返回有效 Cookie"):
        await hook(ExecutionContext())


# ── MCP cookie 注入器(构造正确的工具调用) ──────────────────


async def test_mcp_cookie_injector_builds_addcookies_call():
    calls = []

    class _FakeMCP:
        async def call_tool(self, name, arguments=None):
            calls.append((name, arguments))
            return name

    inject = make_mcp_cookie_injector(_FakeMCP(), "https://intranet/home")
    await inject(ExecutionContext(), [{"name": "sid", "value": "x"}])

    assert calls[0][0] == "browser_run_code_unsafe"
    code = calls[0][1]["code"]
    assert "addCookies" in code
    assert '"sid"' in code
    assert "https://intranet/home" in code
