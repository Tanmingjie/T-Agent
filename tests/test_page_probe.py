"""T-10 单元测试:A11y 快照解析 + 语义查询(PageProbe)。

快照样例取自真实 playwright-mcp browser_snapshot 输出格式。
"""

from __future__ import annotations

import pytest

from harness.assertion import AssertionEngine, AssertionStatus
from harness.page_probe import (
    DictVocabResolver,
    MCPPageProbe,
    parse_snapshot,
    query_nodes,
)
from input.models import Assertion

SNAPSHOT = """\
### Page
- Page URL: https://intranet/order/list?id=9
- Page Title: 订单管理
### Snapshot
```yaml
- generic [active] [ref=e1]:
  - heading "订单管理" [level=1] [ref=e2]
  - button "提交" [ref=e3]
  - generic [ref=e4]:
    - text: 用户名
    - textbox "用户名" [ref=e5]: admin
  - text: 待审批
  - link "返回列表" [ref=e6]
```
"""


# ── 解析 ──────────────────────────────────────────────────────


def test_parse_url_and_title():
    snap = parse_snapshot(SNAPSHOT)
    assert snap.url == "https://intranet/order/list?id=9"
    assert snap.title == "订单管理"


def test_parse_nodes():
    snap = parse_snapshot(SNAPSHOT)
    roles = [n.role for n in snap.nodes]
    assert "heading" in roles
    assert "button" in roles
    assert "textbox" in roles
    # 表单值解析到 value
    tb = next(n for n in snap.nodes if n.role == "textbox")
    assert tb.name == "用户名"
    assert tb.value == "admin"
    # 文本节点
    assert any(n.role == "text" and n.value == "待审批" for n in snap.nodes)


def test_parse_quoted_numeric_value():
    """YAML 会给纯数字文本加引号(购物车角标 `: "1"`),解析须剥掉字面引号,
    否则 text_equals 比较 '"1"' != '1' 误判。"""
    snap = parse_snapshot("```yaml\n- generic [ref=e26]: \"1\"\n- generic [ref=e7]: '2'\n```")
    vals = [n.text_content for n in snap.nodes]
    assert "1" in vals and "2" in vals
    assert '"1"' not in vals


def test_parse_empty():
    snap = parse_snapshot("")
    assert snap.url == ""
    assert snap.nodes == []


# ── 查询 ──────────────────────────────────────────────────────


def test_query_button_by_semantic_target():
    snap = parse_snapshot(SNAPSHOT)
    # "提交按钮" → 去后缀 "提交" 匹配 button "提交"
    q = query_nodes(snap.nodes, "提交按钮")
    assert q.found and q.visible


def test_query_textbox_returns_value_text():
    snap = parse_snapshot(SNAPSHOT)
    q = query_nodes(snap.nodes, "用户名输入框")
    assert q.found
    assert q.text == "admin"


def test_query_status_text_node():
    snap = parse_snapshot(SNAPSHOT)
    q = query_nodes(snap.nodes, "待审批")
    assert q.found
    assert q.text == "待审批"


def test_query_not_found():
    snap = parse_snapshot(SNAPSHOT)
    q = query_nodes(snap.nodes, "不存在的元素xyz")
    assert not q.found


# ── 与断言引擎联动 ────────────────────────────────────────────


class _FakeMCP:
    def __init__(self, snapshot_text):
        self._text = snapshot_text
        self.calls = []

    async def call_tool(self, name, arguments=None):
        self.calls.append(name)
        return name  # 直接返回,由 result_to_text 取文本

    def result_to_text(self, result):
        return self._text


async def test_mcp_probe_with_assertion_engine():
    probe = MCPPageProbe(_FakeMCP(SNAPSHOT))
    eng = AssertionEngine(probe)
    results = await eng.verify_all(
        [
            Assertion(type="url_contains", target="URL", expected="/order/list"),
            Assertion(type="element_visible", target="提交按钮"),
            Assertion(type="text_equals", target="订单状态", expected="待审批"),
        ]
    )
    # 注:"订单状态" 经去后缀为 "订单状态",与 text "待审批" 不直接同名,
    # 但页面无名为"订单状态"的元素 → 该断言找不到目标
    assert results[0].passed  # url_contains
    assert results[1].passed  # 提交按钮 可见
    # 第三条:页面没有"订单状态"这一可及名,匹配到"待审批"文本节点取决于策略
    # 这里验证引擎确定性执行且不抛错
    assert results[2].status in (AssertionStatus.PASS, AssertionStatus.FAIL)


async def test_mcp_probe_caches_snapshot():
    mcp = _FakeMCP(SNAPSHOT)
    probe = MCPPageProbe(mcp)
    await probe.current_url()
    await probe.query("提交")
    # 仅抓取一次快照(缓存)
    assert mcp.calls.count("browser_snapshot") == 1
    await probe.refresh()
    assert mcp.calls.count("browser_snapshot") == 2


# ── P0-2:循环剥后缀 ───────────────────────────────────────────


def test_normalize_strips_multiple_suffixes():
    from harness.page_probe import _normalize_target

    # 旧实现只剥一个后缀,"购物车图标数量" 剥不到 "图标";现循环剥到 "购物车"
    assert _normalize_target("购物车图标数量") == "购物车"
    assert _normalize_target("用户名输入框") == "用户名"


# ── P1 方案A:词汇表接入 Probe 层(跨语言/图标类目标) ──────────

# saucedemo 加购后的终态快照:角标是 name="1" 的元素,无任何中文
CART_SNAPSHOT = """\
### Page
- Page URL: https://www.saucedemo.com/inventory.html
- Page Title: Swag Labs
### Snapshot
```yaml
- generic [ref=e1]:
  - link "1" [ref=e2]
  - button "Add to cart" [ref=e3]
  - text: A red light isn't ideal but 1 AAA battery is included.
```
"""


def test_query_exact_avoids_substring_trap():
    # 子串匹配会把 '1' 命中商品描述("...1 AAA battery...");精确匹配只命中角标
    snap = parse_snapshot(CART_SNAPSHOT)
    exact = query_nodes(snap.nodes, "1", exact=True)
    assert exact.found and exact.text == "1"
    # 精确模式 count 不应把那段长描述算进去
    assert exact.count == 1


def test_query_default_prefers_exact_over_substring():
    # 默认(非 exact)模式也应优先精确命中:'1' 取角标而非含 '1' 的长描述
    # (修复自愈重定位后用裸 '1' 复验误中商品描述的真实 bug)
    snap = parse_snapshot(CART_SNAPSHOT)
    q = query_nodes(snap.nodes, "1")
    assert q.found and q.text == "1"


async def test_probe_resolves_chinese_target_via_vocab():
    """中文断言目标「购物车图标数量」经词汇表解析 → 精确命中角标 '1',不误中商品描述。"""
    resolver = DictVocabResolver({"购物车图标": {"role": "link", "name": "1"}})
    probe = MCPPageProbe(_FakeMCP(CART_SNAPSHOT), resolver=resolver)
    eng = AssertionEngine(probe)
    results = await eng.verify_all(
        [Assertion(type="text_equals", target="购物车图标数量", expected="1")]
    )
    assert results[0].passed  # 跨语言断言经词汇表解析后通过


async def test_probe_without_vocab_cannot_match_cross_language():
    """无词汇表时,中文目标对英文角标无能为力(标 healable)——回归对照。"""
    probe = MCPPageProbe(_FakeMCP(CART_SNAPSHOT))
    eng = AssertionEngine(probe)
    results = await eng.verify_all(
        [Assertion(type="text_equals", target="购物车图标数量", expected="1")]
    )
    assert not results[0].passed
    assert results[0].healable


async def test_dict_vocab_resolver_substring_match():
    resolver = DictVocabResolver({"购物车图标": {"role": "link", "name": "1"}})
    # 子串命中:查询词更长也能对上
    assert await resolver.resolve("购物车图标数量") == {"role": "link", "name": "1"}
    assert await resolver.resolve("无关词") is None


# ── 方案(a):selector 型词汇表 → browser_evaluate DOM 求值 ─────


class _EvalMCP:
    """区分 browser_snapshot 与 browser_evaluate 的 fake:后者返回预设 JSON。"""

    def __init__(self, snapshot_text, eval_json):
        self._snap = snapshot_text
        self._eval = eval_json
        self.eval_calls = []

    async def call_tool(self, name, arguments=None):
        if name == "browser_evaluate":
            self.eval_calls.append(arguments)
            return ("eval", self._eval)
        return ("snap", self._snap)

    def result_to_text(self, result):
        kind, payload = result
        if kind != "eval":
            return payload
        # 复刻真实 playwright-mcp 的 browser_evaluate 返回:Result 段是**引号包裹的
        # JSON 字符串字面量**,其后**回显含花括号的 JS 代码**。对全文做贪婪 {..} 匹配
        # 会把回显代码一起吞进去 → 这正是 _extract_json 修复的 bug,故 fake 必须复刻。
        import json as _json

        return (
            "### Result\n"
            + _json.dumps(payload)  # -> "{\"found\":true,...}"
            + "\n### Ran Playwright code\n```js\n"
            "await page.evaluate('() => { return document.querySelector(\"x\"); }');\n"
            "```\n### Page\n- Page URL: https://x/\n"
        )


async def test_probe_resolves_via_selector_eval():
    """selector 型词汇表:'购物车图标' → .shopping_cart_badge,DOM 求值取文本 '1'。"""
    resolver = DictVocabResolver({"购物车图标": {"selector": ".shopping_cart_badge"}})
    mcp = _EvalMCP(CART_SNAPSHOT, '{"found":true,"visible":true,"count":1,"text":"1"}')
    probe = MCPPageProbe(mcp, resolver=resolver)
    eng = AssertionEngine(probe)
    results = await eng.verify_all(
        [Assertion(type="text_equals", target="购物车图标数量", expected="1")]
    )
    assert results[0].passed
    # 求值函数里应带上该 selector
    assert any(".shopping_cart_badge" in (c.get("function") or "") for c in mcp.eval_calls)


async def test_selector_count_badge_two_items():
    """计数角标的稳健性:2 件时 text='2',selector 型不写死值(name 型做不到)。"""
    resolver = DictVocabResolver({"购物车图标": {"selector": ".shopping_cart_badge"}})
    mcp = _EvalMCP(CART_SNAPSHOT, '{"found":true,"visible":true,"count":1,"text":"2"}')
    probe = MCPPageProbe(mcp, resolver=resolver)
    eng = AssertionEngine(probe)
    results = await eng.verify_all(
        [Assertion(type="text_equals", target="购物车图标数量", expected="2")]
    )
    assert results[0].passed


async def test_explicit_assertion_selector_takes_precedence():
    """Assertion.selector 显式给定时直接走 DOM 求值,优先于词汇表/语义匹配。"""
    mcp = _EvalMCP(CART_SNAPSHOT, '{"found":true,"visible":true,"count":1,"text":"1"}')
    probe = MCPPageProbe(mcp)  # 无 resolver
    eng = AssertionEngine(probe)
    results = await eng.verify_all(
        [
            Assertion(
                type="text_equals",
                target="购物车数量",
                selector=".shopping_cart_badge",
                expected="1",
            )
        ]
    )
    assert results[0].passed
    assert len(mcp.eval_calls) == 1


async def test_selector_not_found_is_healable():
    """selector 求值 found=false(如购物车为空)→ 标 healable,交由裁决/自愈。"""
    mcp = _EvalMCP(CART_SNAPSHOT, '{"found":false}')
    probe = MCPPageProbe(mcp)
    eng = AssertionEngine(probe)
    results = await eng.verify_all(
        [Assertion(type="text_equals", target="x", selector=".missing", expected="1")]
    )
    assert not results[0].passed
    assert results[0].healable


# ── _extract_json:解析 browser_evaluate 返回(隔离回显的 JS 代码) ──────


def test_extract_json_ignores_echoed_js_braces():
    """真实格式:Result 是引号包裹的 JSON 字面量,后面回显的 JS 含花括号。

    旧实现对全文贪婪 {..} 匹配会吞进回显代码 → 解析失败;修复后只解析 Result 段。"""
    from harness.page_probe import _extract_json

    real = (
        "### Result\n"
        '"{\\"found\\":true,\\"count\\":1,\\"text\\":\\"Example Domain\\"}"\n'
        "### Ran Playwright code\n```js\n"
        "await page.evaluate('() => { const e = document.querySelector(\\'h1\\'); return {x:1}; }');\n"
        "```\n### Page\n- Page URL: https://example.com/\n"
    )
    assert _extract_json(real) == {"found": True, "count": 1, "text": "Example Domain"}


def test_extract_json_fenced_plain_object():
    from harness.page_probe import _extract_json

    assert _extract_json('### Result\n```\n{"found":false}\n```') == {"found": False}
    assert _extract_json("") is None
    assert _extract_json("no json here") is None


async def test_resolve_entry_guards_non_dict():
    """畸形词汇表(值非 dict)不应让 entry.get(...) 炸断言:resolve_entry 返回 None。"""

    class _BadResolver:
        async def resolve(self, target, *, url="", title=""):
            return "我是字符串不是dict"

    probe = MCPPageProbe(_FakeMCP(SNAPSHOT), resolver=_BadResolver())
    assert await probe.resolve_entry("任意") is None
    # query 不抛错,退回原始 a11y 语义匹配
    q = await probe.query("提交")
    assert q.found
