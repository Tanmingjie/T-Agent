"""T-11 单元测试:Healing Subagent 重定位 + 断言引擎自愈集成。"""

from __future__ import annotations

import json

import pytest

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


@pytest.fixture(autouse=True)
def _isolate_heal_visual(monkeypatch):
    """隔离 HEAL_VISUAL 环境变量,避免跨文件污染本文件的视觉自愈用例。

    根因(全量 pytest 顺序才复现):任一 test_api_* 用例 import ``api.server`` 会在
    **导入时** ``_load_dotenv(.env)``,把项目根 ``.env`` 里的 ``HEAL_VISUAL=0`` 灌进
    ``os.environ`` 并**持续整个进程**。本文件的视觉自愈用例默认视觉开(env 未设=默认
    "1"),被污染成 "0" 后不再发图 → sends_image / falls_back / vision_unsupported_cached
    三例失败;单独跑本文件不 import api、不加载 .env,故不复现。

    每个用例前删除该 env 回到默认(视觉开);需要关视觉的 ``test_visual_disabled_by_env``
    仍可在用例体内自行 ``setenv`` 覆盖,且两者均随 monkeypatch 在用例后自动还原。
    """
    monkeypatch.delenv("HEAL_VISUAL", raising=False)


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


# ── 视觉自愈双通道(TODO #1)──────────────────────────────────


class _CapturingLLM(LLMClient):
    """记录最后一次 messages,用于验证是否带图;可对多模态消息抛错以测退回。"""

    def __init__(self, content="", raise_on_image=False):
        self._content = content
        self.raise_on_image = raise_on_image
        self.calls: list = []

    async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
        self.calls.append(messages)
        user = messages[-1]["content"]
        has_image = isinstance(user, list) and any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in user
        )
        if has_image and self.raise_on_image:
            raise RuntimeError("model has no vision")
        return LLMResponse(content=self._content)


def _cands(rows):
    return json.dumps({"candidates": rows}, ensure_ascii=False)


async def test_visual_heal_validates_by_ref_and_rewrites_target():
    """视觉候选给的 target 不在快照名里,但 ref 命中 → 接受,并用该 ref 节点真实名复写 target。"""
    llm = _FakeLLM(
        _cands([{"ref": "e6", "target": "回到上一页", "strategy": "P5_visual", "confidence": 0.8}])
    )
    healer = HealingSubagent(llm)
    res = await healer.relocate(
        intent="点返回", target="回到上一页", snapshot_text=SNAPSHOT, screenshot="ZmFrZQ=="
    )
    assert res.healed is True
    assert res.chosen.ref == "e6"
    assert res.chosen.target == "返回"  # 用 ref=e6 节点的真实可及名复写


async def test_visual_heal_sends_image_when_screenshot_present():
    llm = _CapturingLLM(_cands([{"ref": "e6", "target": "返回", "strategy": "P1_role"}]))
    healer = HealingSubagent(llm)
    await healer.relocate(intent="x", target="返回", snapshot_text=SNAPSHOT, screenshot="ZmFrZQ==")
    user = llm.calls[-1][-1]["content"]
    assert isinstance(user, list)
    img = [p for p in user if p.get("type") == "image_url"][0]
    assert img["image_url"]["url"].startswith("data:image/png;base64,")


async def test_visual_heal_falls_back_to_text_when_model_rejects_image():
    llm = _CapturingLLM(
        _cands([{"ref": "e6", "target": "返回", "strategy": "P1_role"}]), raise_on_image=True
    )
    healer = HealingSubagent(llm)
    res = await healer.relocate(
        intent="x", target="返回", snapshot_text=SNAPSHOT, screenshot="ZmFrZQ=="
    )
    assert res.healed is True  # 退回纯文本通道仍成功
    assert len(llm.calls) == 2  # 第一次带图失败,第二次纯文本
    assert isinstance(llm.calls[-1][-1]["content"], str)  # 第二次是纯文本


async def test_visual_disabled_by_env(monkeypatch):
    monkeypatch.setenv("HEAL_VISUAL", "0")
    llm = _CapturingLLM(_cands([{"ref": "e6", "target": "返回", "strategy": "P1_role"}]))
    healer = HealingSubagent(llm)
    await healer.relocate(intent="x", target="返回", snapshot_text=SNAPSHOT, screenshot="ZmFrZQ==")
    assert isinstance(llm.calls[-1][-1]["content"], str)  # 关闭视觉 → 不带图


async def test_text_only_unchanged_without_screenshot():
    """不传 screenshot 时行为与原纯文本通道一致(向后兼容)。"""
    llm = _CapturingLLM(_cands([{"target": "返回", "strategy": "P2_text", "confidence": 0.9}]))
    healer = HealingSubagent(llm)
    res = await healer.relocate(intent="x", target="返回", snapshot_text=SNAPSHOT)
    assert res.healed is True
    assert isinstance(llm.calls[-1][-1]["content"], str)


async def test_vision_unsupported_cached_after_first_rejection():
    """模型拒图一次后,后续 relocate 不再发图像请求(只走纯文本)。"""
    llm = _CapturingLLM(
        _cands([{"ref": "e6", "target": "返回", "strategy": "P1_role"}]), raise_on_image=True
    )
    healer = HealingSubagent(llm)
    await healer.relocate(intent="x", target="返回", snapshot_text=SNAPSHOT, screenshot="ZmFrZQ==")
    assert healer._vision_unsupported is True
    n_before = len(llm.calls)
    # 第二次:应直接纯文本,不再发图(故不会再触发 raise_on_image 的额外一次)
    await healer.relocate(intent="y", target="返回", snapshot_text=SNAPSHOT, screenshot="ZmFrZQ==")
    assert len(llm.calls) == n_before + 1  # 只多了一次(纯文本),没有图像尝试
    assert isinstance(llm.calls[-1][-1]["content"], str)
