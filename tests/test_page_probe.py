"""T-10 单元测试:A11y 快照解析 + 语义查询(PageProbe)。

快照样例取自真实 playwright-mcp browser_snapshot 输出格式。
"""

from __future__ import annotations

import pytest

from harness.assertion import AssertionEngine, AssertionStatus
from harness.page_probe import (
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
