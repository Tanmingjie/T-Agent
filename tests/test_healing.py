"""T-11 单元测试:Healing Subagent 重定位 + 断言引擎自愈集成。"""

from __future__ import annotations

import json

from harness.assertion import AssertionEngine, AssertionStatus
from harness.healing import HealCandidate, HealingSubagent
from harness.llm import LLMClient, LLMResponse
from harness.page_probe import MCPPageProbe
from input.models import Assertion

SNAPSHOT = """\
### Page
- Page URL: https://intranet/order/detail?id=9
### Snapshot
```yaml
- heading "订单详情" [level=1] [ref=e2]
- generic [ref=e4]:
  - text: 当前状态
  - text: 待审批
- button "返回" [ref=e6]
```
"""


class _FakeLLM(LLMClient):
    def __init__(self, content="", raise_exc=None):
        self._content = content
        self._raise = raise_exc

    async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
        if self._raise:
            raise self._raise
        return LLMResponse(content=self._content)


class _FakeMCP:
    def __init__(self, snapshot):
        self._s = snapshot

    async def call_tool(self, name, arguments=None):
        return name

    def result_to_text(self, result):
        return self._s


# ── relocate 纯逻辑 ───────────────────────────────────────────


async def test_relocate_picks_valid_candidate():
    # LLM 把"订单状态"重定位到快照里真实存在的"待审批"
    llm = _FakeLLM(
        json.dumps(
            {
                "candidates": [
                    {
                        "target": "待审批",
                        "strategy": "P2_text",
                        "confidence": 0.9,
                        "reason": "状态文本",
                    }
                ]
            }
        )
    )
    healer = HealingSubagent(llm)
    res = await healer.relocate(
        intent="校验状态", target="订单状态", snapshot_text=SNAPSHOT, expected="待审批"
    )
    assert res.healed
    assert res.chosen.target == "待审批"
    assert res.chosen.strategy == "P2_text"
    assert "待审批" in res.summary


async def test_relocate_drops_hallucinated_candidates():
    # LLM 编了个快照里不存在的元素 → 被过滤,无可靠候选
    llm = _FakeLLM(
        json.dumps(
            {
                "candidates": [
                    {"target": "根本不存在的元素", "strategy": "P1_role", "confidence": 0.99}
                ]
            }
        )
    )
    res = await HealingSubagent(llm).relocate(intent="x", target="y", snapshot_text=SNAPSHOT)
    assert not res.healed
    assert res.chosen is None


async def test_relocate_sorts_by_priority_then_confidence():
    llm = _FakeLLM(
        json.dumps(
            {
                "candidates": [
                    {"target": "待审批", "strategy": "P5_visual", "confidence": 0.99},
                    {"target": "返回", "strategy": "P1_role", "confidence": 0.5},
                ]
            }
        )
    )
    res = await HealingSubagent(llm).relocate(intent="x", target="按钮", snapshot_text=SNAPSHOT)
    # P1 优先于 P5,即使后者置信度更高
    assert res.chosen.strategy == "P1_role"
    assert res.chosen.target == "返回"


async def test_relocate_vocabulary_hit_short_circuits():
    llm = _FakeLLM("不该被调用")
    res = await HealingSubagent(llm).relocate(
        intent="x", target="状态", snapshot_text=SNAPSHOT, vocabulary={"状态": "待审批"}
    )
    assert res.healed
    assert res.chosen.target == "待审批"
    assert res.attempts == 0  # 没调 LLM


async def test_relocate_llm_exception_no_crash():
    llm = _FakeLLM(raise_exc=RuntimeError("挂了"))
    res = await HealingSubagent(llm).relocate(intent="x", target="y", snapshot_text=SNAPSHOT)
    assert not res.healed
    assert "自愈失败" in res.summary


async def test_relocate_bad_json_no_candidates():
    res = await HealingSubagent(_FakeLLM("不是json")).relocate(
        intent="x", target="y", snapshot_text=SNAPSHOT
    )
    assert not res.healed


# ── 断言引擎集成自愈 ──────────────────────────────────────────


async def test_engine_heals_missing_assertion_target():
    # text_equals(订单状态==待审批):页面无"订单状态"可及名 → healable;
    # 自愈重定位到"待审批"文本节点 → 复验通过
    probe = MCPPageProbe(_FakeMCP(SNAPSHOT))
    await probe.refresh()
    llm = _FakeLLM(
        json.dumps({"candidates": [{"target": "待审批", "strategy": "P2_text", "confidence": 0.9}]})
    )
    engine = AssertionEngine(probe, healer=HealingSubagent(llm))

    r = await engine.verify(Assertion(type="text_equals", target="订单状态", expected="待审批"))
    assert r.passed
    assert r.healed
    assert "待审批" in r.heal_note


async def test_engine_heal_fail_preserves_original_failure():
    # 自愈也找不到可靠候选 → 保留原 FAIL,不放水
    probe = MCPPageProbe(_FakeMCP(SNAPSHOT))
    await probe.refresh()
    llm = _FakeLLM(
        json.dumps(
            {"candidates": [{"target": "查无此元素", "strategy": "P1_role", "confidence": 0.9}]}
        )
    )
    engine = AssertionEngine(probe, healer=HealingSubagent(llm))

    r = await engine.verify(Assertion(type="element_visible", target="不存在按钮"))
    assert r.status == AssertionStatus.FAIL
    assert not r.healed


async def test_engine_without_healer_unchanged():
    # 无 healer:healable 失败原样返回
    probe = MCPPageProbe(_FakeMCP(SNAPSHOT))
    await probe.refresh()
    engine = AssertionEngine(probe)  # 无 healer
    r = await engine.verify(Assertion(type="element_visible", target="不存在按钮"))
    assert r.status == AssertionStatus.FAIL
    assert r.healable
    assert not r.healed


async def test_engine_heal_relocated_but_value_wrong_still_fails():
    # 重定位到了元素,但值不符 → 仍 FAIL(自愈只解决"找不到",不改变事实)
    probe = MCPPageProbe(_FakeMCP(SNAPSHOT))
    await probe.refresh()
    llm = _FakeLLM(
        json.dumps({"candidates": [{"target": "待审批", "strategy": "P2_text", "confidence": 0.9}]})
    )
    engine = AssertionEngine(probe, healer=HealingSubagent(llm))
    # 期望"已通过"但实际文本是"待审批" → 重定位到该元素后复验仍不等
    r = await engine.verify(Assertion(type="text_equals", target="订单状态", expected="已通过"))
    assert not r.passed
    assert r.healed  # 自愈确实重定位了,只是事实不符
