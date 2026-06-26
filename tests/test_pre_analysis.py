"""TestSpec 生成(阶段化重设计后,2026-06-22)。"""

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
    user = msgs[1]["content"]
    assert "登录并提交订单" in user
    assert "1. 打开订单列表" in user
    assert "状态变为待审批" in user


def test_knowledge_injected_when_provided():
    """提供翻译知识时,作为「理解用」背景块进入 user 消息,排在用例之前。"""
    msgs = build_spec_messages(_case(), knowledge="本系统提交订单前必须先选审批人。")
    user = msgs[1]["content"]
    assert "业务知识/操作指南" in user
    assert "提交订单前必须先选审批人" in user
    # 知识块在用例之前
    assert user.index("提交订单前必须先选审批人") < user.index("用例名称")


def test_no_knowledge_block_when_absent():
    """不提供知识时不注入空块(行为与旧版一致)。"""
    user = build_spec_messages(_case())[1]["content"]
    assert "业务知识/操作指南" not in user


def test_system_prompt_has_knowledge_guardrails():
    """系统 prompt 必须钉死两条护栏:用知识但仍不接地、不脑补 expected。"""
    system = build_spec_messages(_case())[0]["content"]
    assert "业务知识/操作指南" in system
    assert "不写 selector" in system  # 护栏①:仍不接地
    assert "理想态" in system  # 护栏②:不把指南理想态当页面必现写进 expected


def test_system_prompt_describes_phase_structure():
    """翻译 prompt 引导:只产意图不接地、阶段化分组、组级 expected、driving/验证分离。"""
    system = build_spec_messages(_case())[0]["content"]
    assert "只产意图,不接地" in system
    assert "phases" in system and "expected" in system
    assert "intent" in system
    assert "preconditions" in system
    # expected 是验证依据,不进驱动
    assert "不会" in system  # "...不会拿它驱动 agent..."


# ── 响应解析 ──────────────────────────────────────────────────


def _good_json() -> str:
    return json.dumps(
        {
            "intent": "验证能登录并提交订单进入待审批",
            "preconditions": ["已登录系统", "新建一条草稿订单"],
            "phases": [
                {
                    "steps": ["打开订单列表页", "找到目标订单"],
                    "expected": "进入订单列表，看到目标订单",
                },
                {
                    "steps": ["点击提交按钮"],
                    "expected": "订单状态变为待审批",
                },
            ],
        },
        ensure_ascii=False,
    )


def test_parse_good_response():
    spec = parse_spec_response(_good_json(), _case())
    assert spec.case_id == "TC001"
    assert spec.base_url == "http://intranet.example"
    assert spec.intent == "验证能登录并提交订单进入待审批"
    assert spec.preconditions == ["已登录系统", "新建一条草稿订单"]
    assert len(spec.phases) == 2
    assert spec.phases[0].steps == ["打开订单列表页", "找到目标订单"]
    assert spec.phases[1].expected == "订单状态变为待审批"


def test_parse_tolerates_fenced_and_prose():
    content = "好的,翻译如下:\n```json\n" + _good_json() + "\n```\n以上。"
    spec = parse_spec_response(content, _case())
    assert len(spec.phases) == 2


def test_parse_drops_empty_phases_and_coerces():
    content = json.dumps(
        {
            "intent": "x",
            "preconditions": ["", "有效前置"],  # 空串过滤
            "phases": [
                {"steps": ["点登录"], "expected": "已登录"},
                {"steps": [], "expected": "空阶段丢弃"},  # 无步骤 → 丢弃
                "不是dict",  # 丢弃
                {"steps": ["a", "", "  ", "b"], "expected": ""},  # 步骤内空串过滤
            ],
        }
    )
    spec = parse_spec_response(content, _case())
    assert spec.preconditions == ["有效前置"]
    assert len(spec.phases) == 2
    assert spec.phases[0].steps == ["点登录"]
    assert spec.phases[1].steps == ["a", "b"]


def test_parse_invalid_json_raises():
    with pytest.raises(ValueError):
        parse_spec_response("根本不是 json", _case())


# ── 降级(近乎无损) ──────────────────────────────────────────


def test_naive_fallback_single_phase_lossless():
    spec = naive_fallback_spec(_case())
    assert len(spec.phases) == 1
    assert spec.phases[0].steps == ["打开订单列表", "点击提交按钮"]  # Excel 原文
    assert "状态变为待审批" in spec.phases[0].expected
    assert spec.preconditions == ["已登录系统", "新建一条草稿订单"]
    assert spec.intent == "登录并提交订单"  # 用例名兜底


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
    assert len(spec.phases) == 2


async def test_generator_falls_back_on_bad_json():
    gen = SpecGenerator(_FakeLLM(content="模型胡言乱语"), fallback_on_error=True)
    spec = await gen.generate(_case())
    assert spec.phases[0].steps == ["打开订单列表", "点击提交按钮"]  # 降级单阶段


async def test_generator_falls_back_on_llm_exception():
    gen = SpecGenerator(_FakeLLM(raise_exc=RuntimeError("LLM 挂了")), fallback_on_error=True)
    spec = await gen.generate(_case())
    assert len(spec.phases) == 1


async def test_generator_reraises_when_fallback_disabled():
    gen = SpecGenerator(_FakeLLM(content="坏的"), fallback_on_error=False)
    with pytest.raises(ValueError):
        await gen.generate(_case())
