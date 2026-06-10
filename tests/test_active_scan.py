"""主动扫描(ActiveScanner)单测:用 fake MCP + fake LLM 驱动,不连真实浏览器/模型。"""

from __future__ import annotations

import json

import pytest

from intelligence.active_scan import ActiveScanner, _join
from intelligence.scanner import Scanner
from intelligence.vocabulary import VocabularyManager
from storage.db import Store


def _snap(url: str, title: str, lines: list[str]) -> str:
    body = "\n".join(lines)
    return (
        f"### Page\n- Page URL: {url}\n- Page Title: {title}\n### Snapshot\n```yaml\n{body}\n```\n"
    )


class _FakeResult:
    def __init__(self, text: str):
        self.text = text


class _FakeMCP:
    """按 current url 返回预设快照;click 按 ref→子页 url 跳转。"""

    def __init__(self, snapshots: dict[str, str], child_for_ref: dict[str, str] | None = None):
        self.snapshots = snapshots
        self.child_for_ref = child_for_ref or {}
        self.current = ""
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "browser_navigate":
            self.current = args["url"]
            return _FakeResult("")
        if name == "browser_click":
            self.current = self.child_for_ref.get(args["ref"], self.current)
            return _FakeResult("")
        if name == "browser_snapshot":
            return _FakeResult(self.snapshots.get(self.current, ""))
        return _FakeResult("")

    def result_to_text(self, res):
        return res.text


class _FakeLLM:
    """提炼器假 LLM:每次返回同一份业务词→元素 JSON。"""

    def __init__(self, mapping: dict):
        self._content = json.dumps(mapping, ensure_ascii=False)
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        return type("R", (), {"content": self._content})()


@pytest.fixture
async def store():
    s = Store(url="sqlite+aiosqlite:///:memory:")
    await s.init()
    yield s
    await s.close()


def test_join():
    assert _join("https://x.com", "/login") == "https://x.com/login"
    assert _join("https://x.com/", "login") == "https://x.com/login"
    assert _join("https://x.com", "https://y.com/a") == "https://y.com/a"
    assert _join("https://x.com", "") == "https://x.com"


async def test_scan_entry_list_saves_per_page(store):
    base = "https://sys.intra"
    snapshots = {
        f"{base}/login": _snap(f"{base}/login", "登录", ['- button "登录" [ref=e1]']),
        f"{base}/orders": _snap(f"{base}/orders", "订单", ['- link "新建" [ref=e2]']),
    }
    mcp = _FakeMCP(snapshots)
    scanner = Scanner(_FakeLLM({"提交": {"role": "button", "name": "登录"}}))
    mgr = VocabularyManager(store)
    sc = ActiveScanner(mcp, scanner, mgr)

    report = await sc.scan(base, ["/login", "/orders"], shallow_crawl=False)

    assert report.page_count == 2
    assert {p["url"] for p in report.pages} == {f"{base}/login", f"{base}/orders"}
    # 两页都落库,且 base_url 作用域正确
    vocabs = await store.list_vocabularies()
    assert all(v.base_url == base for v in vocabs)
    assert len(vocabs) == 2


async def test_shallow_crawl_follows_nav_links(store):
    base = "https://sys.intra"
    snapshots = {
        f"{base}/home": _snap(
            f"{base}/home", "首页", ['- link "订单管理" [ref=e9]', '- button "删除账号" [ref=e8]']
        ),
        f"{base}/orders": _snap(f"{base}/orders", "订单", ['- text "订单列表"']),
    }
    mcp = _FakeMCP(snapshots, child_for_ref={"e9": f"{base}/orders"})
    scanner = Scanner(_FakeLLM({"x": {"role": "text", "name": "x"}}))
    sc = ActiveScanner(mcp, scanner, VocabularyManager(store), crawl_depth=1)

    report = await sc.scan(base, ["/home"], shallow_crawl=True)

    urls = {p["url"] for p in report.pages}
    assert f"{base}/home" in urls
    assert f"{base}/orders" in urls  # 点击 link "订单管理" 进入的子页被扫到


async def test_shallow_crawl_skips_dangerous_and_buttons(store):
    base = "https://sys.intra"
    # 危险词 link + 普通 button 都不应被点击(button 不在导航白名单)
    snapshots = {
        f"{base}/home": _snap(
            f"{base}/home",
            "首页",
            ['- link "删除订单" [ref=e1]', '- button "提交" [ref=e2]'],
        ),
        f"{base}/danger": _snap(f"{base}/danger", "危险", ['- text "x"']),
    }
    mcp = _FakeMCP(snapshots, child_for_ref={"e1": f"{base}/danger", "e2": f"{base}/danger"})
    sc = ActiveScanner(mcp, Scanner(_FakeLLM({})), VocabularyManager(store), crawl_depth=1)

    report = await sc.scan(base, ["/home"], shallow_crawl=True)

    urls = {p["url"] for p in report.pages}
    assert urls == {f"{base}/home"}  # 危险词 link + button 都没点 → 没进 /danger
    assert not any(c[0] == "browser_click" for c in mcp.calls)


async def test_login_callback_invoked(store):
    base = "https://sys.intra"
    snapshots = {f"{base}/": _snap(f"{base}/", "主页", ['- text "hi"'])}
    mcp = _FakeMCP(snapshots)
    called = {"n": 0}

    async def _login():
        called["n"] += 1

    sc = ActiveScanner(mcp, Scanner(_FakeLLM({})), VocabularyManager(store))
    await sc.scan(base, [], login=_login)
    assert called["n"] == 1  # 无入口清单时默认扫 "/",登录回调仍先调用


async def test_max_pages_caps_scan(store):
    base = "https://sys.intra"
    snapshots = {
        f"{base}/a": _snap(f"{base}/a", "A", ['- text "x"']),
        f"{base}/b": _snap(f"{base}/b", "B", ['- text "x"']),
        f"{base}/c": _snap(f"{base}/c", "C", ['- text "x"']),
    }
    mcp = _FakeMCP(snapshots)
    sc = ActiveScanner(mcp, Scanner(_FakeLLM({})), VocabularyManager(store), max_pages=2)
    report = await sc.scan(base, ["/a", "/b", "/c"])
    assert report.page_count == 2
