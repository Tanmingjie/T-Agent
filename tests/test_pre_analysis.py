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
    assert "断言翻译规则" in msgs[0]["content"]
    user = msgs[1]["content"]
    assert "登录并提交订单" in user
    assert "1. 打开订单列表" in user
    assert "状态变为待审批" in user


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


def test_parse_good_response():
    spec = parse_spec_response(_good_json(), _case())
    assert spec.case_id == "TC001"
    assert spec.base_url == "http://intranet.example"
    assert [s.action for s in spec.steps] == ["navigate", "click"]
    assert spec.steps[1].expect[0].type == "element_visible"
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
