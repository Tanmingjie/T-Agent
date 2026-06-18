"""T-05 单元测试:TestSpec 生成(纯 LLM)。"""

from __future__ import annotations

import json

import pytest

from harness.llm import LLMClient, LLMResponse
from input.models import TestCase
from intelligence.pre_analysis import (
    SpecGenerator,
    build_spec_messages,
    naive_fallback_spec,
    parse_spec_response,
)


def _case() -> TestCase:
    return TestCase(
        id="TC001",
        name="登录并提交订单",
        preconditions=["已登录系统", "新建一条草稿订单"],
        steps=["打开订单列表", "点击提交按钮"],
        expected=["状态变为待审批", "跳转到列表页"],
        base_url="http://intranet.example",
    )


# ── prompt 组装 ───────────────────────────────────────────────


def test_build_messages_includes_case_content():
    msgs = build_spec_messages(_case())
    assert msgs[0]["role"] == "system"
    assert "expect_text" in msgs[0]["content"]
    user = msgs[1]["content"]
    assert "登录并提交订单" in user
    assert "1. 打开订单列表" in user
    assert "状态变为待审批" in user


def test_system_prompt_routes_final_expectations_to_case_assertions():
    """Fix 3 ③:翻译 prompt 引导每条最终预期落成用例级 assertion(难定位的用 llm_judge 承载),
    且澄清 expect_text 只是驱动信号、非最终裁决标准。"""
    system = build_spec_messages(_case())[0]["content"]
    # 用例级 assertions 是最终裁决依据、每条最终预期都要落成断言(不能漏)
    assert "最终裁决依据" in system
    assert "都必须落成一条用例级 assertion" in system
    # 难以稳定 selector 定位的最终预期 → 用 llm_judge 承载,且是默认主裁决(非"最末档兜底")
    assert "llm_judge" in system
    assert "默认的主裁决方式" in system
    # expect_text 被澄清为驱动信号、非最终裁决标准
    assert "不是最终业务裁决标准" in system
    # 收尾:解释终态裁判要逐字引证最后一页实证 → 放错的中间页预期会被判 FAIL(给模型"为何严格"的理由)
    assert "逐字引证" in system


# ── 响应解析 ──────────────────────────────────────────────────


def _good_json() -> str:
    return json.dumps(
        {
            "given": [{"action": "execute", "target": "新建草稿订单", "data": None}],
            "steps": [
                {"action": "navigate", "target": "订单列表页", "data": None, "expect": []},
                {
                    "action": "click",
                    "target": "提交按钮",
                    "data": None,
                    "expect_text": "弹出确认弹窗",
                    "expect": [
                        {"type": "element_visible", "target": "确认弹窗", "confidence": "high"}
                    ],
                },
            ],
            "assertions": [
                {
                    "type": "text_equals",
                    "target": "订单状态",
                    "expected": "待审批",
                    "confidence": "high",
                },
                {
                    "type": "url_contains",
                    "target": "页面URL",
                    "expected": "/list",
                    "confidence": "high",
                },
            ],
        },
        ensure_ascii=False,
    )


def test_url_assertion_malformed_expected_coerced():
    """弱模型把 URL 子串写进 target、expected 写成 'true' → 纠正:用 target 作期望子串。"""
    spec = parse_spec_response(
        json.dumps(
            {
                "steps": [{"action": "click", "target": "Finish", "expect_text": "完成"}],
                "assertions": [
                    {"type": "url_contains", "target": "checkout-complete", "expected": "true"}
                ],
            }
        ),
        _case(),
    )
    a = spec.assertions[0]
    assert a.type == "url_contains"
    assert a.expected == "checkout-complete"  # 子串纠正到 expected
    assert a.target == "URL"


def test_parse_good_response():
    spec = parse_spec_response(_good_json(), _case())
    assert spec.case_id == "TC001"
    assert spec.base_url == "http://intranet.example"
    assert [s.action for s in spec.steps] == ["navigate", "click"]
    assert spec.steps[1].expect[0].type == "element_visible"
    assert spec.steps[1].expect_text == "弹出确认弹窗"  # 完成判据解析
    assert len(spec.assertions) == 2
    assert spec.given[0].target == "新建草稿订单"


def test_parse_tolerates_fenced_and_prose():
    content = "好的,翻译如下:\n```json\n" + _good_json() + "\n```\n以上。"
    spec = parse_spec_response(content, _case())
    assert len(spec.steps) == 2


def test_parse_drops_invalid_entries():
    content = json.dumps(
        {
            "steps": [
                {"action": "click", "target": "ok"},
                {"action": "", "target": "缺action"},  # 丢弃
                {"target": "缺action字段"},  # 丢弃
                "不是dict",  # 丢弃
            ],
            "assertions": [
                {"type": "bogus_type", "target": "x"},  # 非法类型丢弃
                {"type": "element_visible", "target": ""},  # 空 target 丢弃
                {"type": "url_contains", "target": "URL", "expected": "/a"},  # 保留
            ],
        }
    )
    spec = parse_spec_response(content, _case())
    assert len(spec.steps) == 1
    assert len(spec.assertions) == 1
    assert spec.assertions[0].type == "url_contains"


def test_parse_llm_judge_forced_low_confidence():
    content = json.dumps(
        {
            "steps": [],
            "assertions": [{"type": "llm_judge", "target": "页面正常", "confidence": "high"}],
        }
    )
    spec = parse_spec_response(content, _case())
    assert spec.assertions[0].confidence == "low"


def test_parse_invalid_json_raises():
    with pytest.raises(ValueError):
        parse_spec_response("根本不是 json", _case())


# ── 降级 ──────────────────────────────────────────────────────


def test_naive_fallback_maps_1to1():
    spec = naive_fallback_spec(_case())
    assert [s.target for s in spec.steps] == ["打开订单列表", "点击提交按钮"]
    assert all(s.action == "execute" for s in spec.steps)
    assert all(a.type == "llm_judge" and a.confidence == "low" for a in spec.assertions)


# ── SpecGenerator(fake LLM) ─────────────────────────────────


class _FakeLLM(LLMClient):
    def __init__(self, content: str = "", raise_exc: Exception | None = None):
        self._content = content
        self._raise = raise_exc

    async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
        if self._raise:
            raise self._raise
        return LLMResponse(content=self._content)


async def test_generator_happy_path():
    gen = SpecGenerator(_FakeLLM(content=_good_json()))
    spec = await gen.generate(_case())
    assert len(spec.steps) == 2


async def test_generator_falls_back_on_bad_json():
    gen = SpecGenerator(_FakeLLM(content="模型胡言乱语"), fallback_on_error=True)
    spec = await gen.generate(_case())
    # 降级:步骤 1:1 映射
    assert [s.target for s in spec.steps] == ["打开订单列表", "点击提交按钮"]


async def test_generator_falls_back_on_llm_exception():
    gen = SpecGenerator(_FakeLLM(raise_exc=RuntimeError("LLM 挂了")), fallback_on_error=True)
    spec = await gen.generate(_case())
    assert len(spec.steps) == 2  # 降级仍产出


async def test_generator_reraises_when_fallback_disabled():
    gen = SpecGenerator(_FakeLLM(content="坏的"), fallback_on_error=False)
    with pytest.raises(ValueError):
        await gen.generate(_case())


# ── 合并调用:一次 LLM 同时分类 + 翻译 ───────────────────────


class _CountingLLM(LLMClient):
    def __init__(self, content: str):
        self._content = content
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content=self._content)


def _merged_json() -> str:
    return json.dumps(
        {
            "given": [{"action": "execute", "target": "新建草稿订单", "data": None}],
            "steps": [{"action": "click", "target": "提交按钮", "data": None, "expect": []}],
            "assertions": [{"type": "url_contains", "target": "URL", "expected": "/list"}],
            "preconditions": [
                {"text": "已登录系统", "type": "state_hook", "confidence": 0.95},
                {"text": "新建一条草稿订单", "type": "action_step", "confidence": 0.9},
            ],
        },
        ensure_ascii=False,
    )


def test_build_messages_request_classification_adds_appendix():
    msgs = build_spec_messages(_case(), request_classification=True)
    assert "预置条件分类" in msgs[0]["content"]
    assert "preconditions" in msgs[0]["content"]


async def test_generate_with_classification_single_call():
    llm = _CountingLLM(_merged_json())
    gen = SpecGenerator(llm)
    spec, raw = await gen.generate_with_classification(_case())
    assert llm.calls == 1  # 一次调用拿到 spec + 分类
    assert len(spec.steps) == 1
    assert raw["已登录系统"]["type"] == "state_hook"
    assert raw["新建一条草稿订单"]["type"] == "action_step"


async def test_generate_with_classification_falls_back_on_bad_json():
    gen = SpecGenerator(_FakeLLM(content="胡言乱语"), fallback_on_error=True)
    spec, raw = await gen.generate_with_classification(_case())
    assert [s.target for s in spec.steps] == ["打开订单列表", "点击提交按钮"]  # 降级
    assert raw == {}  # 分类为空 → 下游全 ambiguous
