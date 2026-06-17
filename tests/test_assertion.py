"""T-08 单元测试:断言规则引擎(★核心)。

用内存 PageProbe 驱动,验证确定性比较 / 归因 / 裁决,无 LLM 无浏览器。
"""

from __future__ import annotations

import pytest

from harness.assertion import (
    AssertionEngine,
    AssertionStatus,
    ElementQuery,
    PageProbe,
)
from input.models import Assertion


class DictProbe:
    """内存探针:用一个 url + {target: ElementQuery} 字典模拟页面。"""

    def __init__(self, url: str = "", elements: dict | None = None):
        self._url = url
        self._elements = elements or {}

    async def current_url(self) -> str:
        return self._url

    async def query(self, target: str, selector=None) -> ElementQuery:
        return self._elements.get(target, ElementQuery(found=False))


def test_dictprobe_satisfies_protocol():
    assert isinstance(DictProbe(), PageProbe)


# ── URL ───────────────────────────────────────────────────────


async def test_url_contains_pass_and_fail():
    eng = AssertionEngine(DictProbe(url="http://x/order/list?id=1"))
    r = await eng.verify(Assertion(type="url_contains", target="URL", expected="/order/list"))
    assert r.passed
    assert r.actual == "http://x/order/list?id=1"

    r2 = await eng.verify(Assertion(type="url_contains", target="URL", expected="/detail"))
    assert r2.status == AssertionStatus.FAIL
    assert not r2.healable


async def test_url_equals():
    eng = AssertionEngine(DictProbe(url="http://x/a"))
    assert (
        await eng.verify(Assertion(type="url_equals", target="URL", expected="http://x/a"))
    ).passed
    assert not (
        await eng.verify(Assertion(type="url_equals", target="URL", expected="http://x/b"))
    ).passed


# ── element_visible ───────────────────────────────────────────


async def test_element_visible_pass():
    probe = DictProbe(elements={"成功提示": ElementQuery(found=True, visible=True)})
    eng = AssertionEngine(probe)
    r = await eng.verify(Assertion(type="element_visible", target="成功提示"))
    assert r.passed


async def test_element_visible_present_but_hidden_is_real_fail():
    probe = DictProbe(elements={"弹窗": ElementQuery(found=True, visible=False)})
    eng = AssertionEngine(probe)
    r = await eng.verify(Assertion(type="element_visible", target="弹窗"))
    assert r.status == AssertionStatus.FAIL
    assert not r.healable  # 元素在,只是不可见 → 真失败


async def test_element_not_found_is_healable():
    eng = AssertionEngine(DictProbe(elements={}))
    r = await eng.verify(Assertion(type="element_visible", target="不存在的元素"))
    assert r.status == AssertionStatus.FAIL
    assert r.healable  # 找不到 → 可能 selector 失效,阶段二自愈


# ── element_count ─────────────────────────────────────────────


async def test_element_count_match():
    probe = DictProbe(elements={"行": ElementQuery(found=True, count=3)})
    eng = AssertionEngine(probe)
    assert (await eng.verify(Assertion(type="element_count", target="行", expected="3"))).passed
    assert not (await eng.verify(Assertion(type="element_count", target="行", expected="5"))).passed


async def test_element_count_bad_expected():
    probe = DictProbe(elements={"行": ElementQuery(found=True, count=3)})
    eng = AssertionEngine(probe)
    r = await eng.verify(Assertion(type="element_count", target="行", expected="很多"))
    assert r.status == AssertionStatus.FAIL
    assert "非整数" in r.reason


# ── 文本类(元素内匹配) ──────────────────────────────────────


async def test_text_equals_scoped_to_element():
    # 关键:只看目标元素内的文本,而非全页(避免全页有"待审批"就误判)
    probe = DictProbe(
        elements={
            "订单状态": ElementQuery(found=True, text="待审批"),
            "页面标题": ElementQuery(found=True, text="订单管理 - 已审批列表"),
        }
    )
    eng = AssertionEngine(probe)
    r = await eng.verify(Assertion(type="text_equals", target="订单状态", expected="待审批"))
    assert r.passed
    # 即便别的元素含相同词,针对错误目标的精确匹配应失败
    r2 = await eng.verify(Assertion(type="text_equals", target="页面标题", expected="待审批"))
    assert r2.status == AssertionStatus.FAIL


async def test_text_equals_trims_whitespace():
    probe = DictProbe(elements={"状态": ElementQuery(found=True, text="  待审批  ")})
    eng = AssertionEngine(probe)
    assert (
        await eng.verify(Assertion(type="text_equals", target="状态", expected="待审批"))
    ).passed


async def test_text_contains():
    probe = DictProbe(elements={"提示": ElementQuery(found=True, text="提交成功,请等待审批")})
    eng = AssertionEngine(probe)
    assert (
        await eng.verify(Assertion(type="text_contains", target="提示", expected="提交成功"))
    ).passed
    assert not (
        await eng.verify(Assertion(type="text_contains", target="提示", expected="失败"))
    ).passed


async def test_text_on_missing_element_healable():
    eng = AssertionEngine(DictProbe(elements={}))
    r = await eng.verify(Assertion(type="text_equals", target="状态", expected="x"))
    assert r.healable


class SnapshotProbe(DictProbe):
    """带 raw_snapshot 的内存探针:模拟整页快照文本(供全页文本兜底测试)。"""

    def __init__(self, url: str = "", elements: dict | None = None, snapshot: str = ""):
        super().__init__(url=url, elements=elements)
        self._snapshot = snapshot

    def raw_snapshot(self) -> str:
        return self._snapshot


async def test_text_page_fallback_hits_when_element_unlocated():
    # 元素名「成功提示区域」与英文页面文案对不上 → 元素级找不到,但 expected 明确在页面里
    probe = SnapshotProbe(
        elements={},
        snapshot='- heading "Thank you for your order!" [level=2] [ref=e9]',
    )
    eng = AssertionEngine(probe)
    r = await eng.verify(
        Assertion(type="text_contains", target="成功提示区域", expected="Thank you for your order!")
    )
    assert r.passed
    assert "全页文本兜底" in r.reason  # 标注可审计,区分于元素级绿


async def test_text_page_fallback_miss_stays_healable_fail():
    # expected 不在整页文本里 → 不兜底,维持 _not_found(healable, FAIL)
    probe = SnapshotProbe(elements={}, snapshot='- heading "Checkout" [ref=e1]')
    eng = AssertionEngine(probe)
    r = await eng.verify(
        Assertion(type="text_contains", target="成功提示区域", expected="Thank you for your order!")
    )
    assert r.status == AssertionStatus.FAIL
    assert r.healable


# ── 不支持类型 ────────────────────────────────────────────────


async def test_unsupported_types_skipped():
    eng = AssertionEngine(DictProbe())
    for t in ("custom_tool", "llm_judge"):
        r = await eng.verify(Assertion(type=t, target="x", confidence="low"))
        assert r.status == AssertionStatus.SKIPPED


# ── custom_tool 数据断言(经 ToolRegistry 取业务真值) ──────────


def _registry_with(name: str, result: str):
    from harness.tools import ToolRegistry

    reg = ToolRegistry()

    @reg.tool(name=name, description="d")
    def _t(**kwargs):
        return result

    return reg


async def test_custom_tool_pass_when_expected_substring_present():
    reg = _registry_with("db_order_status", "已审批")
    eng = AssertionEngine(DictProbe(), tool_registry=reg)
    r = await eng.verify(Assertion(type="custom_tool", target="db_order_status", expected="已审批"))
    assert r.status == AssertionStatus.PASS
    assert r.actual == "已审批"


async def test_custom_tool_fail_when_expected_missing():
    reg = _registry_with("db_order_status", "待审批")
    eng = AssertionEngine(DictProbe(), tool_registry=reg)
    r = await eng.verify(Assertion(type="custom_tool", target="db_order_status", expected="已审批"))
    assert r.status == AssertionStatus.FAIL


async def test_custom_tool_skipped_when_tool_not_registered():
    reg = _registry_with("other", "x")
    eng = AssertionEngine(DictProbe(), tool_registry=reg)
    r = await eng.verify(Assertion(type="custom_tool", target="missing", expected="x"))
    assert r.status == AssertionStatus.SKIPPED


async def test_custom_tool_passes_args_from_selector_json():
    from harness.tools import ToolRegistry

    reg = ToolRegistry()
    seen = {}

    @reg.tool(name="echo_id", description="d")
    def _t(order_id=None):
        seen["order_id"] = order_id
        return f"id={order_id}"

    eng = AssertionEngine(DictProbe(), tool_registry=reg)
    r = await eng.verify(
        Assertion(
            type="custom_tool",
            target="echo_id",
            selector='{"order_id": "A100"}',
            expected="id=A100",
        )
    )
    assert r.status == AssertionStatus.PASS
    assert seen["order_id"] == "A100"


async def test_custom_tool_fail_when_tool_errors():
    from harness.tools import ToolRegistry

    reg = ToolRegistry()

    @reg.tool(name="boom", description="d")
    def _t(**kwargs):
        raise RuntimeError("db down")

    eng = AssertionEngine(DictProbe(), tool_registry=reg)
    r = await eng.verify(Assertion(type="custom_tool", target="boom", expected="ok"))
    assert r.status == AssertionStatus.FAIL
    assert "执行失败" in r.reason


async def test_llm_judge_skipped_without_llm():
    # 未接入 LLM 时 llm_judge 仍 skipped(不静默放过)
    reg = _registry_with("x", "y")
    eng = AssertionEngine(DictProbe(), tool_registry=reg)
    r = await eng.verify(Assertion(type="llm_judge", target="x", confidence="low"))
    assert r.status == AssertionStatus.SKIPPED


# ── llm_judge 方案A:真判 PASS/FAIL 并计入裁决,但标 ai_judged 低置信 ────


class _JudgeLLM:
    """假 LLM:按预设 content 返回。"""

    def __init__(self, content: str):
        self._content = content
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1

        class _R:
            content = self._content

        return _R()


async def test_llm_judge_pass_counts_but_flagged_ai_judged():
    llm = _JudgeLLM('{"verdict":"PASS","reason":"页面显示成功提示"}')
    eng = AssertionEngine(DictProbe(), llm=llm)
    r = await eng.verify(Assertion(type="llm_judge", target="显示成功提示", confidence="low"))
    assert r.status == AssertionStatus.PASS
    assert r.ai_judged is True  # 可审计:AI 判绿与结构化绿区分
    assert llm.calls == 1


async def test_llm_judge_fail_counts():
    llm = _JudgeLLM('{"verdict":"FAIL","reason":"未见成功提示"}')
    eng = AssertionEngine(DictProbe(), llm=llm)
    r = await eng.verify(Assertion(type="llm_judge", target="显示成功提示"))
    assert r.status == AssertionStatus.FAIL
    assert r.ai_judged is True


async def test_llm_judge_unclear_verdict_skipped():
    llm = _JudgeLLM('{"reason":"说不准"}')  # 无明确 verdict
    eng = AssertionEngine(DictProbe(), llm=llm)
    r = await eng.verify(Assertion(type="llm_judge", target="x"))
    assert r.status == AssertionStatus.SKIPPED
    assert r.ai_judged is True


async def test_llm_judge_to_dict_carries_ai_judged():
    llm = _JudgeLLM('{"verdict":"PASS"}')
    eng = AssertionEngine(DictProbe(), llm=llm)
    r = await eng.verify(Assertion(type="llm_judge", target="x"))
    assert r.to_dict()["ai_judged"] is True


async def test_llm_judge_recovers_verdict_from_broken_json():
    # 2026-06-17 实测假绿根因:模型其实判 FAIL,但 reason 含未转义引号炸了 JSON。
    # 裁决路径必须正则兜底捞回 FAIL(而非降级 skipped/默认绿)。
    broken = '{"verdict":"FAIL","reason":"页面仍为登录页面，未出现"You have been logged in!"提示"}'
    llm = _JudgeLLM(broken)
    eng = AssertionEngine(DictProbe(), llm=llm)
    r = await eng.verify(Assertion(type="llm_judge", target="显示登录成功"))
    assert r.status == AssertionStatus.FAIL  # 捞回 FAIL,不再被 fail-open 误判通过
    assert r.ai_judged is True


async def test_llm_judge_no_verdict_stays_skipped_fail_closed():
    # 完全没有 verdict 字样 → fail-closed:判 skipped(绝不默认绿),不计入可信通过。
    llm = _JudgeLLM("这是一段没有任何裁决字样的解释文字。")
    eng = AssertionEngine(DictProbe(), llm=llm)
    r = await eng.verify(Assertion(type="llm_judge", target="x"))
    assert r.status == AssertionStatus.SKIPPED


# ── 裁决 / 聚合 ───────────────────────────────────────────────


async def test_verify_all_and_verdict():
    probe = DictProbe(
        url="http://x/list",
        elements={"提示": ElementQuery(found=True, visible=True)},
    )
    eng = AssertionEngine(probe)
    results = await eng.verify_all(
        [
            Assertion(type="url_contains", target="URL", expected="/list"),
            Assertion(type="element_visible", target="提示"),
        ]
    )
    assert all(r.passed for r in results)
    assert AssertionEngine.verdict(results) is True


def test_verdict_fail_if_any_fail():
    from harness.assertion import AssertionResult
    from input.models import Assertion as A

    ok = AssertionResult(A(type="url_contains", target="u"), AssertionStatus.PASS)
    bad = AssertionResult(A(type="url_contains", target="u"), AssertionStatus.FAIL)
    assert AssertionEngine.verdict([ok, bad]) is False
    assert AssertionEngine.verdict([ok, ok]) is True


def test_verdict_empty_or_all_skipped_not_trusted_pass():
    from harness.assertion import AssertionResult
    from input.models import Assertion as A

    assert AssertionEngine.verdict([]) is False
    skipped = AssertionResult(A(type="llm_judge", target="x"), AssertionStatus.SKIPPED)
    assert AssertionEngine.verdict([skipped]) is False  # 全 skipped 不算可信通过


async def test_result_to_dict():
    eng = AssertionEngine(DictProbe(url="http://x/list"))
    r = await eng.verify(Assertion(type="url_contains", target="URL", expected="/list"))
    d = r.to_dict()
    assert d["type"] == "url_contains"
    assert d["status"] == "pass"
    assert d["healable"] is False
