"""T-06 单元测试:ReAct 循环。

用脚本化 fake LLM(按序返回预设响应)+ fake 执行器驱动,不连真实 LLM/浏览器。
"""

from __future__ import annotations

import json

import pytest

from harness.healing import HealingSubagent
from harness.llm import LLMClient, LLMResponse, LLMToolCallError, ToolCall
from harness.react_loop import (
    ReActLoop,
    StopReason,
    ToolOutcome,
    _is_tool_failure,
    parse_test_result,
)
from harness.step_plan import StepPlan
from input.models import SpecStep


def _plan(n: int) -> StepPlan:
    return StepPlan([SpecStep(action="click", target=f"按钮{i}") for i in range(1, n + 1)])


class _ScriptedLLM(LLMClient):
    """按序返回预设 LLMResponse;用尽后重复最后一个。可注入异常。"""

    def __init__(self, responses: list, raise_on=None):
        self._responses = responses
        self._i = 0
        self._raise_on = raise_on or {}

    async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
        idx = min(self._i, len(self._responses) - 1)
        self._i += 1
        if idx in self._raise_on:
            raise self._raise_on[idx]
        return self._responses[idx]


def _resp(content="", calls=None):
    return LLMResponse(
        content=content,
        tool_calls=[ToolCall(name=n, arguments=a) for n, a in (calls or [])],
    )


def _make_executor(plan: StepPlan, *, fail_tools=None):
    """执行器:先给 StepPlan,再当作浏览器工具返回观察文本。"""
    fail_tools = fail_tools or set()

    async def execute(name, arguments):
        if name in fail_tools:
            raise RuntimeError("工具炸了")
        handled = plan.apply_tool_call(name, arguments)
        if handled is not None:
            return ToolOutcome(text=handled)
        return ToolOutcome(text=f"已执行 {name}", url="http://x/page")

    return execute


def _build_system(plan: StepPlan) -> str:
    return "SYSTEM\n" + plan.to_prompt()


# ── TEST_RESULT 解析 ──────────────────────────────────────────


def test_parse_test_result_variants():
    assert parse_test_result("结论 TEST_RESULT: PASS") == "PASS"
    assert parse_test_result("TEST_RESULT：fail") == "FAIL"  # 全角冒号 + 小写
    assert parse_test_result("没有结论") is None
    assert parse_test_result(None) is None


# ── 正常路径 ──────────────────────────────────────────────────


async def test_happy_path_completes():
    plan = _plan(2)
    llm = _ScriptedLLM(
        [
            _resp(content="点第一个按钮", calls=[("browser_click", {"ref": "b1"})]),
            _resp(content="完成第一步", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="点第二个按钮", calls=[("browser_click", {"ref": "b2"})]),
            _resp(content="完成第二步", calls=[("mark_step_done", {"step_no": 2})]),
            _resp(content="都做完了 TEST_RESULT: PASS"),  # 无 tool_call
        ]
    )
    loop = ReActLoop(
        llm, tools=[], execute=_make_executor(plan), step_plan=plan, build_system=_build_system
    )
    result = await loop.run()
    # 所有步骤在第 4 轮 mark_step_done 后 all_resolved → COMPLETED
    assert result.stop_reason == StopReason.COMPLETED
    assert plan.all_done()
    # 记录了 4 个 ActionStep(2 次 click + 2 次 mark_step_done)
    assert len(result.action_steps) == 4
    assert result.action_steps[0].tool_name == "browser_click"
    assert result.action_steps[0].url == "http://x/page"
    assert result.action_steps[0].reasoning == "点第一个按钮"


async def test_llm_finished_without_toolcall():
    plan = _plan(1)
    llm = _ScriptedLLM([_resp(content="无需操作 TEST_RESULT: FAIL")])
    loop = ReActLoop(
        llm, tools=[], execute=_make_executor(plan), step_plan=plan, build_system=_build_system
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.LLM_FINISHED
    assert result.llm_result == "FAIL"
    assert result.action_steps == []


async def test_idle_nudge_pushes_model_to_continue():
    # 模型中途哑火(无 tool_call 也没 TEST_RESULT),但还有步骤没做 → 被推回继续
    plan = _plan(2)
    llm = _ScriptedLLM(
        [
            _resp(content="点第一个", calls=[("browser_click", {"ref": "b1"})]),
            _resp(content="完成第一步", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="我先停一下想想"),  # 哑火:无 tool_call、无 TEST_RESULT
            _resp(content="继续点第二个", calls=[("browser_click", {"ref": "b2"})]),
            _resp(content="完成第二步", calls=[("mark_step_done", {"step_no": 2})]),
        ]
    )
    loop = ReActLoop(
        llm, tools=[], execute=_make_executor(plan), step_plan=plan, build_system=_build_system
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.COMPLETED
    assert plan.all_done()  # 哑火没有让它提前结束,最终做完了


async def test_idle_nudge_cap_terminates():
    # 模型持续哑火,超过 max_idle_nudges 后兜底结束(不空转)
    plan = _plan(2)
    llm = _ScriptedLLM([_resp(content="嗯……")])  # 永远不调工具、不给结果
    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
        max_idle_nudges=2,
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.LLM_FINISHED
    assert not plan.all_done()


# ── 护栏 ──────────────────────────────────────────────────────


async def test_loop_detection():
    plan = _plan(3)
    # 一直重复同一个 click,从不 mark_step_done
    llm = _ScriptedLLM([_resp(content="再点一次", calls=[("browser_click", {"ref": "b1"})])])
    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
        loop_window=3,
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.LOOP_DETECTED
    # 第 3 轮检测到,执行了前 2 轮的工具
    assert len(result.action_steps) == 2


async def test_max_steps():
    plan = _plan(5)
    # 每轮点不同的 ref,避免触发循环检测,但永不结束
    responses = [
        _resp(content=f"点 {i}", calls=[("browser_click", {"ref": f"b{i}"})]) for i in range(100)
    ]
    llm = _ScriptedLLM(responses)
    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
        max_steps=4,
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.MAX_STEPS
    assert result.iterations == 4


async def test_tool_exception_does_not_crash():
    plan = _plan(1)
    llm = _ScriptedLLM(
        [
            _resp(content="点", calls=[("browser_click", {"ref": "b1"})]),
            _resp(content="标记完成", calls=[("mark_step_done", {"step_no": 1})]),
        ]
    )
    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan, fail_tools={"browser_click"}),
        step_plan=plan,
        build_system=_build_system,
    )
    result = await loop.run()
    # 工具异常被吞,循环继续,最终 step 标记完成 → COMPLETED
    assert result.stop_reason == StopReason.COMPLETED
    assert "[工具执行异常]" in result.action_steps[0].tool_result


async def test_tool_call_error_stops():
    plan = _plan(1)
    llm = _ScriptedLLM([_resp()], raise_on={0: LLMToolCallError("解析不了")})
    loop = ReActLoop(
        llm, tools=[], execute=_make_executor(plan), step_plan=plan, build_system=_build_system
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.TOOL_CALL_ERROR


# ── 操作侧自愈(T-11) ─────────────────────────────────────────

_HEAL_SNAPSHOT = (
    '### Page\n- Page URL: http://x/p\n### Snapshot\n```yaml\n- button "提交" [ref=e3]\n```\n'
)


def test_is_tool_failure_markers():
    assert _is_tool_failure('### Error Error: Unknown engine "ref"')
    assert _is_tool_failure("[工具执行异常] boom")
    assert _is_tool_failure("locator resolved to 0 elements")
    assert not _is_tool_failure("### Ran Playwright code ... ok")
    assert not _is_tool_failure(None)


async def test_action_healing_records_attempt_and_hints():
    plan = _plan(1)
    llm = _ScriptedLLM(
        [
            _resp(
                content="点提交", calls=[("browser_click", {"element": "提交按钮", "ref": "e9"})]
            ),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
        ]
    )
    # 自愈用的 fake LLM:把"提交按钮"重定位到快照里真实的"提交"
    heal_llm = _ScriptedLLM(
        [
            _resp(
                content=json.dumps(
                    {"candidates": [{"target": "提交", "strategy": "P1_role", "confidence": 0.9}]}
                )
            )
        ]
    )
    healer = HealingSubagent(heal_llm)

    async def get_snap():
        return _HEAL_SNAPSHOT

    # browser_click 抛错 → 触发操作侧自愈
    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan, fail_tools={"browser_click"}),
        step_plan=plan,
        build_system=_build_system,
        healer=healer,
        get_snapshot=get_snap,
    )
    result = await loop.run()
    step0 = result.action_steps[0]
    assert step0.heal_attempts, "失败的工具调用应记录自愈尝试"
    assert step0.heal_attempts[0]["healed"] is True
    assert step0.heal_attempts[0]["chosen"] == "提交"


async def test_no_healing_on_success():
    plan = _plan(1)
    llm = _ScriptedLLM(
        [
            _resp(content="点", calls=[("browser_click", {"element": "提交", "ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
        ]
    )
    healer = HealingSubagent(_ScriptedLLM([_resp(content="{}")]))

    async def get_snap():
        return _HEAL_SNAPSHOT

    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan),  # 不失败
        step_plan=plan,
        build_system=_build_system,
        healer=healer,
        get_snapshot=get_snap,
    )
    result = await loop.run()
    assert result.action_steps[0].heal_attempts == []  # 成功不触发自愈


async def test_intent_parsed_into_actionstep():
    plan = _plan(1)
    llm = _ScriptedLLM(
        [
            _resp(
                content="INTENT: 点击登录按钮以进入系统", calls=[("browser_click", {"ref": "b1"})]
            ),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
        ]
    )
    loop = ReActLoop(
        llm, tools=[], execute=_make_executor(plan), step_plan=plan, build_system=_build_system
    )
    result = await loop.run()
    assert result.action_steps[0].intent == "点击登录按钮以进入系统"
