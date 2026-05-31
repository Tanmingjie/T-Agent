"""T-15 单元测试:预置条件三分类器。"""

from __future__ import annotations

import json

from harness.llm import LLMClient, LLMResponse
from harness.precondition import (
    ACTION_STEP,
    AMBIGUOUS,
    STATE_HOOK,
    PreconditionClassifier,
    needs_confirmation,
    to_given_steps,
)


class _FakeLLM(LLMClient):
    def __init__(self, content="", raise_exc=None):
        self._content = content
        self._raise = raise_exc
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
        self.calls += 1
        if self._raise:
            raise self._raise
        return LLMResponse(content=self._content)


def _classified(rows):
    return json.dumps(rows, ensure_ascii=False)


async def test_three_way_classification():
    llm = _FakeLLM(
        _classified(
            [
                {"text": "已登录系统", "type": "state_hook", "confidence": 0.95},
                {"text": "设置环境变量 CONF=10", "type": "action_step", "confidence": 0.9},
                {"text": "环境正常", "type": "ambiguous", "confidence": 0.4},
            ]
        )
    )
    clf = PreconditionClassifier(llm, hook_map={"已登录": "LoginHook"})
    items = await clf.classify(["已登录系统", "设置环境变量 CONF=10", "环境正常"])

    assert items[0].type == STATE_HOOK
    assert items[0].hook_ref == "LoginHook"
    assert items[1].type == ACTION_STEP
    assert items[2].type == AMBIGUOUS


async def test_low_confidence_downgraded_to_ambiguous():
    llm = _FakeLLM(_classified([{"text": "已登录", "type": "state_hook", "confidence": 0.3}]))
    clf = PreconditionClassifier(llm, hook_map={"已登录": "LoginHook"}, confidence_threshold=0.6)
    items = await clf.classify(["已登录"])
    assert items[0].type == AMBIGUOUS  # 低置信降级


async def test_state_hook_without_mapping_is_ambiguous():
    llm = _FakeLLM(_classified([{"text": "已部署环境", "type": "state_hook", "confidence": 0.9}]))
    clf = PreconditionClassifier(llm, hook_map={"已登录": "LoginHook"})  # 没有"部署"映射
    items = await clf.classify(["已部署环境"])
    assert items[0].type == AMBIGUOUS
    assert items[0].hook_ref is None


async def test_memory_skips_llm_on_second_call():
    llm = _FakeLLM(_classified([{"text": "已登录系统", "type": "state_hook", "confidence": 0.9}]))
    clf = PreconditionClassifier(llm, hook_map={"已登录": "LoginHook"})
    await clf.classify(["已登录系统"])
    assert llm.calls == 1
    await clf.classify(["已登录系统"])  # 命中 memory
    assert llm.calls == 1  # 没再调 LLM


async def test_llm_failure_all_ambiguous():
    clf = PreconditionClassifier(_FakeLLM(raise_exc=RuntimeError("挂")))
    items = await clf.classify(["条件A", "条件B"])
    assert all(i.type == AMBIGUOUS for i in items)
    assert all(i.confidence == 0.0 for i in items)


async def test_bad_json_all_ambiguous():
    clf = PreconditionClassifier(_FakeLLM(content="不是json"))
    items = await clf.classify(["条件A"])
    assert items[0].type == AMBIGUOUS


async def test_order_preserved_and_blank_filtered():
    llm = _FakeLLM(
        _classified(
            [
                {"text": "B", "type": "action_step", "confidence": 0.9},
                {"text": "A", "type": "state_hook", "confidence": 0.9},
            ]
        )
    )
    clf = PreconditionClassifier(llm, hook_map={"A": "LoginHook"})
    items = await clf.classify(["A", "", "  ", "B"])  # 空白被过滤
    assert [i.text for i in items] == ["A", "B"]  # 顺序按输入


async def test_to_given_steps_only_action():
    llm = _FakeLLM(
        _classified(
            [
                {"text": "已登录", "type": "state_hook", "confidence": 0.9},
                {"text": "新建订单", "type": "action_step", "confidence": 0.9},
            ]
        )
    )
    clf = PreconditionClassifier(llm, hook_map={"已登录": "LoginHook"})
    items = await clf.classify(["已登录", "新建订单"])
    given = to_given_steps(items)
    assert len(given) == 1
    assert given[0].action == "execute"
    assert given[0].target == "新建订单"


async def test_needs_confirmation_lists_ambiguous():
    llm = _FakeLLM(_classified([{"text": "环境正常", "type": "ambiguous", "confidence": 0.3}]))
    clf = PreconditionClassifier(llm)
    items = await clf.classify(["环境正常"])
    assert len(needs_confirmation(items)) == 1


async def test_llm_missing_text_falls_back_to_order():
    # LLM 漏写 text 字段 → 按顺序兜底匹配
    llm = _FakeLLM(_classified([{"type": "action_step", "confidence": 0.9}]))
    clf = PreconditionClassifier(llm)
    items = await clf.classify(["新建一条订单"])
    assert items[0].type == ACTION_STEP
    assert items[0].text == "新建一条订单"
