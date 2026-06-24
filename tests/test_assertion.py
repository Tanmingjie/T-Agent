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


async def test_url_equals_tolerates_trailing_slash():
    # 浏览器常把 https://x 补成 https://x/;url_equals 容差结尾斜杠,免"打开首页"步 false-fail(AE03)。
    eng = AssertionEngine(DictProbe(url="https://automationexercise.com/"))
    r = await eng.verify(
        Assertion(type="url_equals", target="URL", expected="https://automationexercise.com")
    )
    assert r.passed
    # 反向亦然 + 路径差异仍判不等(不放松精确语义)
    eng2 = AssertionEngine(DictProbe(url="https://x/a"))
    assert (
        await eng2.verify(Assertion(type="url_equals", target="URL", expected="https://x/a/"))
    ).passed
    assert not (
        await eng2.verify(Assertion(type="url_equals", target="URL", expected="https://x/b"))
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
    # custom_tool 未接 ToolRegistry → skipped(非阶段化路径,不在 G1 收 FAIL 范围)。
    # llm_judge 未接 LLM 的 FAIL 行为见 test_llm_judge_fails_without_llm。
    eng = AssertionEngine(DictProbe())
    r = await eng.verify(Assertion(type="custom_tool", target="x", confidence="low"))
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


async def test_llm_judge_fails_without_llm():
    # G1:阶段化下 LLM 是主裁决,未接入 LLM = 主裁决缺失 → FAIL(不再 skipped 默认放过)
    reg = _registry_with("x", "y")
    eng = AssertionEngine(DictProbe(), tool_registry=reg)
    r = await eng.verify(Assertion(type="llm_judge", target="x", confidence="low"))
    assert r.status == AssertionStatus.FAIL
    assert r.ai_judged is True


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


async def test_llm_judge_unclear_verdict_fails():
    # G1:解析不出明确 verdict = 裁判输出不可用 = 主裁决缺失 → FAIL(不再 skipped)
    llm = _JudgeLLM('{"reason":"说不准"}')  # 无明确 verdict
    eng = AssertionEngine(DictProbe(), llm=llm)
    r = await eng.verify(Assertion(type="llm_judge", target="x"))
    assert r.status == AssertionStatus.FAIL
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


async def test_llm_judge_no_verdict_fails_fail_closed():
    # 完全没有 verdict 字样 → fail-closed:判 FAIL(绝不默认绿)。G1:从 skipped 收成 FAIL。
    llm = _JudgeLLM("这是一段没有任何裁决字样的解释文字。")
    eng = AssertionEngine(DictProbe(), llm=llm)
    r = await eng.verify(Assertion(type="llm_judge", target="x"))
    assert r.status == AssertionStatus.FAIL


class _CapturingJudgeLLM:
    """假 LLM:记录最近一次 user 消息,验证喂给裁判的证据(URL 锚点 + 期望)。"""

    def __init__(self, content: str):
        self._content = content
        self.last_user = ""

    async def chat(self, messages, tools=None, **kwargs):
        self.last_user = messages[-1]["content"] if messages else ""

        class _R:
            content = self._content

        return _R()


async def test_llm_judge_feeds_url_anchor_and_expectation():
    # Fix 3 ①:终态裁判显式收到「免费 URL 锚点」(实时 URL)+ 期望原文作引证证据。
    # evidence 逐字摘自 URL → 通过确定性核验,判 PASS。
    llm = _CapturingJudgeLLM('{"verdict":"PASS","evidence":"/order/done","reason":"URL 命中"}')
    eng = AssertionEngine(DictProbe(url="https://intranet/order/done"), llm=llm)
    r = await eng.verify(
        Assertion(
            type="llm_judge", target="下单成功页", expected="显示订单成功提示", confidence="low"
        )
    )
    assert "当前页面 URL:https://intranet/order/done" in llm.last_user
    assert "显示订单成功提示" in llm.last_user
    assert r.status == AssertionStatus.PASS  # evidence 在 URL 里 → 核验通过


async def test_llm_judge_pass_with_fabricated_evidence_overridden_to_fail():
    # Fix 3 收尾:判 PASS 但引证的证据不在当前页(脑补) → 确定性核验失败 → fail-closed 推翻为 FAIL。
    # 直击弱模型把"中间页/别页预期"在终态页脑补判过(如 inventory 页判"用户名框=standard_user")。
    probe = SnapshotProbe(url="https://x/inventory", snapshot='- button "Remove" [ref=e1]')
    llm = _JudgeLLM(
        '{"verdict":"PASS","evidence":"用户名输入框值为 standard_user","reason":"应已登录"}'
    )
    eng = AssertionEngine(probe, llm=llm)
    r = await eng.verify(Assertion(type="llm_judge", target="用户名显示 standard_user"))
    assert r.status == AssertionStatus.FAIL
    assert r.ai_judged is True
    assert "疑似脑补" in r.reason


async def test_llm_judge_pass_with_compound_summarized_evidence_stays_pass():
    # 误伤修复(eval_fg 实测):复合预期(导航含多项)模型把证据写成概括句、非单一逐字串;
    # 只要其中一个具体锚点(如 "Products"/"API Testing")逐字落在页上就算有据 → 不误伤推翻。
    probe = SnapshotProbe(
        url="https://x/",
        snapshot='- link "Products" [ref=e1]\n- link "Cart" [ref=e2]\n- link "API Testing" [ref=e3]',
    )
    llm = _JudgeLLM(
        '{"verdict":"PASS","evidence":"快照顶部导航栏含链接:Home, Products, Cart, API Testing 等入口",'
        '"reason":"导航齐全"}'
    )
    eng = AssertionEngine(probe, llm=llm)
    r = await eng.verify(
        Assertion(type="llm_judge", target="顶部导航含 Products、Cart、API Testing")
    )
    assert r.status == AssertionStatus.PASS  # 锚点 Products/Cart/API Testing 命中 → 有据,不推翻
    assert r.ai_judged is True


async def test_llm_judge_pass_with_verifiable_evidence_stays_pass():
    # 判 PASS 且 evidence 逐字出现在快照里 → 核验通过,实证回写 reason(可审计)。
    probe = SnapshotProbe(
        url="https://x/done", snapshot='- heading "Thank you for your order!" [ref=e9]'
    )
    llm = _JudgeLLM(
        '{"verdict":"PASS","evidence":"Thank you for your order!","reason":"下单完成页"}'
    )
    eng = AssertionEngine(probe, llm=llm)
    r = await eng.verify(Assertion(type="llm_judge", target="显示下单成功"))
    assert r.status == AssertionStatus.PASS
    assert r.ai_judged is True
    assert "Thank you for your order!" in r.reason  # 实证回写,可审计


async def test_llm_judge_evidence_check_skipped_without_page_text():
    # 无可核验来源(快照/URL 均空,如纯内存单测探针)→ 不做证据核验,不误伤(行为同旧)。
    llm = _JudgeLLM('{"verdict":"PASS","reason":"无快照场景"}')
    eng = AssertionEngine(DictProbe(), llm=llm)  # url="" 且无 raw_snapshot
    r = await eng.verify(Assertion(type="llm_judge", target="x"))
    assert r.status == AssertionStatus.PASS


# ── E5:expected 自带锚点佐证 ────────────────────────────────


def test_expected_anchors_extraction():
    """E5 _expected_anchors:**只取强信号**——引号字面值 + URL-like 片段。

    刻意保守:一般 CJK/ASCII 词不取(常被 expected 文风与页面表达不一致误伤)。
    """
    from harness.assertion import _expected_anchors

    # 引号片段(各种引号类型) + URL 文件后缀都被抽
    anchors = _expected_anchors("显示「订单成功」提示,URL 含 inventory.html")
    assert "订单成功" in anchors
    assert "inventory.html" in anchors

    # 路径段(带 /)被抽
    anchors2 = _expected_anchors("跳转到 /order/done 页面")
    assert any("/order/done" in a or "order/done" in a for a in anchors2)

    # 一般中文短语 / 一般英文词**不抽**(避免文风误伤)
    assert _expected_anchors("显示订单成功") == []
    assert _expected_anchors("登录成功") == []
    assert _expected_anchors("Display Welcome message") == []
    # 纯数字、空文本不抽
    assert _expected_anchors("1") == []
    assert _expected_anchors("") == []


async def test_llm_judge_expected_anchor_missing_overrides_pass_to_fail():
    """E5:judge 判 PASS 且 evidence 接地能过,但 expected 的强锚点(URL / 引号)全不在页 → 推翻 FAIL。"""
    # 页面 URL 是 /login,但 expected 要求跳到 /inventory.html → 强锚点矛盾
    probe = SnapshotProbe(url="https://x/login", snapshot='- link "Products" [ref=e1]')
    llm = _JudgeLLM(
        '{"verdict":"PASS","evidence":"Products 链接显示,说明已经进入商品页","reason":"已登录"}'
    )
    eng = AssertionEngine(probe, llm=llm)
    r = await eng.verify(
        Assertion(
            type="llm_judge",
            target="登录后跳转",
            expected="URL 含 inventory.html,已进入商品列表",
        )
    )
    assert r.status == AssertionStatus.FAIL
    assert "期望中的关键锚点" in r.reason
    assert "inventory.html" in r.reason


async def test_llm_judge_expected_anchor_present_keeps_pass():
    """E5 反证:expected 的强锚点有一个落在页/URL 上 → 不推翻。"""
    probe = SnapshotProbe(url="https://x/inventory.html", snapshot='- text "Products"')
    llm = _JudgeLLM('{"verdict":"PASS","evidence":"Products","reason":"商品页"}')
    eng = AssertionEngine(probe, llm=llm)
    r = await eng.verify(
        Assertion(
            type="llm_judge",
            target="进入商品页",
            expected="URL 含 inventory.html,显示商品列表",
        )
    )
    assert r.status == AssertionStatus.PASS  # inventory.html 命中 URL


async def test_llm_judge_expected_without_strong_anchors_unchanged():
    """E5:expected 抽不到强锚点(无引号无 URL)→ 不参与判断,evidence 接地通过即 PASS。"""
    probe = SnapshotProbe(url="https://x/", snapshot='- text "Hello"')
    llm = _JudgeLLM('{"verdict":"PASS","evidence":"Hello","reason":"显示了"}')
    eng = AssertionEngine(probe, llm=llm)
    r = await eng.verify(Assertion(type="llm_judge", target="x", expected="显示成功提示"))
    assert r.status == AssertionStatus.PASS  # expected 无强锚点 → E5 跳过不参与


async def test_llm_judge_broken_json_pass_rescued_by_expected_anchor():
    """裁判 JSON 炸(evidence 解析不出=空),但 expected 强锚点(inventory.html)确定性落在 URL
    → 平台独立佐证,保留 PASS(治用户实证 false-FAIL:登录已成功却因 evidence='' 被推翻)。"""
    probe = SnapshotProbe(
        url="https://www.saucedemo.com/inventory.html", snapshot='- text "Products"'
    )
    # JSON 含未转义引号炸解析 → 正则只捞回 PASS,evidence 丢失为空
    broken = '{"verdict":"PASS","reason":"页面已跳转到"inventory"页,登录成功","evidence":""}'
    llm = _JudgeLLM(broken)
    eng = AssertionEngine(probe, llm=llm)
    r = await eng.verify(
        Assertion(
            type="llm_judge",
            target="登录成功",
            expected="登录成功,跳转到商品列表页,URL 包含 inventory.html,页面出现商品列表",
        )
    )
    assert r.status == AssertionStatus.PASS  # 不再被误判脑补
    assert "独立核验" in r.reason


async def test_llm_judge_empty_evidence_no_anchor_still_overturned():
    """反证:evidence 空 且 expected 无强锚点(无 URL/引号)→ 无可独立核验 → 仍 fail-closed 推翻。"""
    probe = SnapshotProbe(url="https://x/somepage", snapshot='- text "随便什么"')
    llm = _JudgeLLM('{"verdict":"PASS","evidence":"","reason":"我觉得成功了"}')
    eng = AssertionEngine(probe, llm=llm)
    r = await eng.verify(Assertion(type="llm_judge", target="x", expected="操作成功完成"))
    assert r.status == AssertionStatus.FAIL  # 无据 + 无可独立核验锚点 → 推翻
    assert "疑似脑补" in r.reason


# ── E6:多模态裁判通道(开关默认关) ─────────────────────────


class _VisualCapturingLLM:
    """记录最近一次 messages 结构,验证多模态裁判通道是否正确组装图像消息。"""

    def __init__(self, content: str):
        self._content = content
        self.last_messages = []
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.last_messages = messages
        self.calls += 1

        class _R:
            content = self._content

        return _R()


class _VisualSnapshotProbe(SnapshotProbe):
    """提供 raw_screenshot 返回 base64 PNG 字节串(模拟有截图通道)。"""

    def __init__(self, url="", elements=None, snapshot="", screenshot=""):
        super().__init__(url=url, elements=elements, snapshot=snapshot)
        self._shot = screenshot

    async def raw_screenshot(self):
        return self._shot or None


async def test_judge_visual_off_by_default(monkeypatch):
    """E6 默认关:_JUDGE_VISUAL_DEFAULT=False 时,judge 走纯文本通道,不抓截图。"""
    from harness import assertion as a_mod

    monkeypatch.setattr(a_mod, "_JUDGE_VISUAL_DEFAULT", False)
    probe = _VisualSnapshotProbe(url="https://x/", snapshot="- text", screenshot="FAKE_B64")
    llm = _VisualCapturingLLM('{"verdict":"PASS","evidence":"text","reason":"ok"}')
    eng = AssertionEngine(probe, llm=llm)
    await eng.verify(Assertion(type="llm_judge", target="x"))
    user = llm.last_messages[-1]
    assert isinstance(user["content"], str)  # 纯文本通道


async def test_judge_visual_on_attaches_screenshot(monkeypatch):
    """E6 开启时:有 raw_screenshot → user content 变为含 image_url 的 list。"""
    from harness import assertion as a_mod

    monkeypatch.setattr(a_mod, "_JUDGE_VISUAL_DEFAULT", True)
    probe = _VisualSnapshotProbe(url="https://x/", snapshot="- text", screenshot="ZZZ_B64")
    llm = _VisualCapturingLLM('{"verdict":"PASS","evidence":"text","reason":"ok"}')
    eng = AssertionEngine(probe, llm=llm)
    await eng.verify(Assertion(type="llm_judge", target="x"))
    user = llm.last_messages[-1]
    assert isinstance(user["content"], list)
    types = [b.get("type") for b in user["content"]]
    assert "image_url" in types
    img_block = next(b for b in user["content"] if b.get("type") == "image_url")
    assert img_block["image_url"]["url"].startswith("data:image/png;base64,")
    assert "ZZZ_B64" in img_block["image_url"]["url"]


async def test_judge_visual_falls_back_when_model_rejects_image(monkeypatch):
    """E6:多模态调用失败 → 标记 _vision_unsupported + 当次退回纯文本重试,后续不再尝试图像。"""
    from harness import assertion as a_mod

    monkeypatch.setattr(a_mod, "_JUDGE_VISUAL_DEFAULT", True)

    class _RejectingLLM:
        def __init__(self):
            self.calls = []

        async def chat(self, messages, tools=None, **kwargs):
            user = messages[-1]
            self.calls.append("multi" if isinstance(user["content"], list) else "text")
            if isinstance(user["content"], list):
                raise RuntimeError("model has no vision support")

            class _R:
                content = '{"verdict":"PASS","evidence":"text","reason":"ok"}'

            return _R()

    probe = _VisualSnapshotProbe(url="https://x/", snapshot="- text", screenshot="ZZZ")
    llm = _RejectingLLM()
    eng = AssertionEngine(probe, llm=llm)
    r1 = await eng.verify(Assertion(type="llm_judge", target="x"))
    assert r1.status == AssertionStatus.PASS  # 退回纯文本成功
    assert llm.calls == ["multi", "text"]
    assert eng._vision_unsupported is True
    # 第二次调用同 engine 应直接走纯文本(不再尝试 multi)
    r2 = await eng.verify(Assertion(type="llm_judge", target="y"))
    assert r2.status == AssertionStatus.PASS
    assert llm.calls == ["multi", "text", "text"]


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
    # F2:phase_index 是 schema 一等字段,默认 -1(非阶段裁决);裁决路径不显式设值时 to_dict 自带出
    assert d["phase_index"] == -1


def test_result_to_dict_carries_phase_index():
    """F2:阶段裁决场景下,AssertionResult.phase_index 应一等字段直出 to_dict(不依赖外塞)。"""
    from harness.assertion import AssertionResult, AssertionStatus

    r = AssertionResult(
        assertion=Assertion(type="llm_judge", target="出现待审批", expected="出现待审批"),
        status=AssertionStatus.PASS,
        ai_judged=True,
        phase_index=2,
    )
    d = r.to_dict()
    assert d["phase_index"] == 2
    assert d["expected"] == "出现待审批"  # 来自 Assertion.expected,无外塞覆盖
    assert d["ai_judged"] is True
