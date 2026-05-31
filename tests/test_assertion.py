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


# ── 不支持类型 ────────────────────────────────────────────────


async def test_unsupported_types_skipped():
    eng = AssertionEngine(DictProbe())
    for t in ("custom_tool", "llm_judge"):
        r = await eng.verify(Assertion(type=t, target="x", confidence="low"))
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
