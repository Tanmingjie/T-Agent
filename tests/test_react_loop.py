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
from input.models import Phase


def _plan(n: int) -> StepPlan:
    """单阶段 n 步(n=0 → 空 plan)。"""
    if n == 0:
        return StepPlan([])
    return StepPlan([Phase(steps=[f"点击按钮{i}" for i in range(1, n + 1)])])


def _multi_phase_plan(*sizes: int) -> StepPlan:
    """多阶段:每个 size 一个阶段。"""
    return StepPlan([Phase(steps=[f"步骤{j}" for j in range(s)]) for s in sizes])


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


async def test_capture_screenshot_attached_to_step():
    """ReActLoop 每步执行后调 capture_screenshot,返回的文件名落到 ActionStep.screenshot。"""
    plan = _plan(1)
    shots: list[tuple[int, str]] = []

    async def capture(step_no, tool_name):
        shots.append((step_no, tool_name))
        return f"step_{step_no:03d}.png"

    llm = _ScriptedLLM(
        [
            _resp(calls=[("browser_click", {"element": "按钮1", "ref": "e1"})]),
            _resp(calls=[("mark_step_done", {"step": 1})]),
        ]
    )
    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
        capture_screenshot=capture,
    )
    result = await loop.run()
    click_step = next(s for s in result.action_steps if s.tool_name == "browser_click")
    assert click_step.screenshot == "step_001.png"
    assert "browser_click" in [t for _, t in shots]


async def test_step_fail_budget_fast_fails():
    """#1:同一业务步累计定位失败达预算 → STEP_FAILED 快速失败,标明卡死步(不磨到 max_steps)。"""
    plan = _plan(2)
    # 不同 ref 的失败 click(签名不同 → 不触发循环检测;考验单步失败预算)
    llm = _ScriptedLLM(
        [
            _resp(content="点", calls=[("browser_click", {"element": "按钮1", "ref": "e1"})]),
            _resp(content="再点", calls=[("browser_click", {"element": "按钮1", "ref": "e2"})]),
            _resp(content="还点", calls=[("browser_click", {"element": "按钮1", "ref": "e3"})]),
            _resp(content="TEST_RESULT: PASS"),  # 不应到达
        ]
    )
    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan, fail_tools={"browser_click"}),
        step_plan=plan,
        build_system=_build_system,
        step_fail_budget=3,
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.STEP_FAILED
    assert result.failed_step_no == 1
    assert "按钮1" in result.failed_step_target
    assert not plan.all_done()


async def test_on_phase_end_fires_at_each_phase_boundary():
    """阶段边界 Validator:每个阶段最后一步 mark_step_done 落定 → on_phase_end(phase_index) 触发。"""
    plan = _multi_phase_plan(2, 1)  # 阶段0 两步、阶段1 一步
    fired: list[int] = []

    async def on_phase(pi: int) -> None:
        fired.append(pi)

    llm = _ScriptedLLM(
        [
            _resp(calls=[("browser_click", {"ref": "e1"})]),
            _resp(calls=[("mark_step_done", {"step_no": 1})]),  # 阶段0 未完(还有 step2)→ 不触发
            _resp(calls=[("browser_click", {"ref": "e2"})]),
            _resp(calls=[("mark_step_done", {"step_no": 2})]),  # 阶段0 最后一步 → 触发 pi=0
            _resp(calls=[("browser_click", {"ref": "e3"})]),
            _resp(calls=[("mark_step_done", {"step_no": 3})]),  # 阶段1 最后一步 → 触发 pi=1
            _resp(content="完成 TEST_RESULT: PASS"),
        ]
    )
    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
        on_phase_end=on_phase,
    )
    await loop.run()
    assert fired == [0, 1]  # 仅阶段边界触发,按序


async def test_phase_validator_fail_stops_with_phase_failed():
    """阶段失败即失败:on_phase_end 返回原因 → PHASE_FAILED,记录失败阶段(不 replan/重试)。"""
    plan = _multi_phase_plan(1, 1)

    async def on_phase(pi: int) -> str | None:
        return "该阶段预期未达成" if pi == 0 else None

    llm = _ScriptedLLM(
        [
            _resp(calls=[("browser_click", {"ref": "e1"})]),
            _resp(calls=[("mark_step_done", {"step_no": 1})]),  # 阶段0 → Validator FAIL → 停
            _resp(content="不该到这"),
        ]
    )
    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
        on_phase_end=on_phase,
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.PHASE_FAILED
    assert result.failed_phase_index == 0
    assert "未达成" in result.failed_phase_reason
    assert not plan.all_done()


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


async def test_guard_premature_mark_then_act():
    """B-软护栏:没操作就 mark_done → 被软拦,推模型先实操,再标记完成。"""
    plan = _plan(1)
    llm = _ScriptedLLM(
        [
            _resp(content="直接完成", calls=[("mark_step_done", {"step_no": 1})]),  # 过早 → 拦
            _resp(content="先点按钮", calls=[("browser_click", {"ref": "b1"})]),  # 实操
            _resp(content="现在完成", calls=[("mark_step_done", {"step_no": 1})]),  # 放行
            _resp(content="done"),
        ]
    )
    loop = ReActLoop(
        llm, tools=[], execute=_make_executor(plan), step_plan=plan, build_system=_build_system
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.COMPLETED
    assert plan.all_done()
    # 被推着先操作再标记(无护栏则 r0 直接 mark 完成、不会有 click)
    assert any(s.tool_name == "browser_click" for s in result.action_steps)


async def test_guard_premature_mark_at_most_once():
    """B-软护栏每步至多拦一次:模型坚持「无需操作」再次 mark → 放行(覆盖纯校验步)。"""
    plan = _plan(1)
    llm = _ScriptedLLM(
        [
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),  # 过早 → 拦一次
            _resp(content="确实无需操作", calls=[("mark_step_done", {"step_no": 1})]),  # 放行
            _resp(content="done"),
        ]
    )
    loop = ReActLoop(
        llm, tools=[], execute=_make_executor(plan), step_plan=plan, build_system=_build_system
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.COMPLETED
    assert plan.all_done()
    assert not any(s.tool_name == "browser_click" for s in result.action_steps)


async def test_stream_dropped_toolcall_recovered_by_nonstream_recheck():
    """流式偶发丢 tool_call → 无调用且有未完成步骤时非流式复核捞回,不误判哑火空转。"""
    plan = _plan(1)
    seq: list[bool] = []  # 记录每次 chat 是否走流式(on_delta 非空)

    class _StreamDropLLM(LLMClient):
        async def chat(self, messages, tools=None, on_delta=None, **kwargs):
            streamed = on_delta is not None
            seq.append(streamed)
            if streamed:
                return _resp(content="思考但没发工具调用")  # 流式:丢了 tool_call
            return _resp(calls=[("browser_click", {"ref": "e1"})])  # 非流式复核:捞回

    loop = ReActLoop(
        _StreamDropLLM(),
        tools=[{"x": 1}],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
        on_llm_delta=lambda t: None,  # 触发流式分支
        max_steps=3,
    )
    result = await loop.run()
    # 第一轮:流式(丢调用)→ 紧接非流式复核(捞回 click)
    assert seq[0] is True and seq[1] is False
    # 复核捞回的 browser_click 被真正执行(没被当哑火丢弃)
    assert any(s.tool_name == "browser_click" for s in result.action_steps)


async def test_reasoning_captures_full_streamed_text_across_idle_round():
    """执行中流式给前端的思考(含哑火续推轮)= 执行后步骤定格的 reasoning,两者一致。

    回归:旧实现 ActionStep.reasoning 只取动作轮的 resp.content,丢掉了「哑火/复核续推」轮
    流式给前端的思考 → 「执行中流式看到的」≠「执行完点开步骤看到的」。现取实际流式累积文本。
    """
    plan = _plan(1)
    streamed: list[str] = []  # 前端实际收到的流式片段

    class _StreamingLLM(LLMClient):
        def __init__(self):
            self.n = 0

        async def chat(self, messages, tools=None, on_delta=None, **kwargs):
            self.n += 1
            if on_delta is not None:
                if self.n == 1:  # 流式轮①:只思考、不调工具 → 哑火
                    await on_delta("哑火轮的思考。")
                    return _resp(content="哑火轮的思考。")
                await on_delta("动作轮的思考。")  # 流式轮②:真正动作
                return _resp(content="动作轮的思考。", calls=[("mark_step_done", {"step_no": 1})])
            return _resp(content="复核也没调用")  # 哑火后非流式复核:仍无调用,续推

    async def _sink(t):
        streamed.append(t)

    loop = ReActLoop(
        _StreamingLLM(),
        tools=[{"x": 1}],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
        on_llm_delta=_sink,
        max_steps=5,
    )
    result = await loop.run()
    act = [s for s in result.action_steps if s.tool_name == "mark_step_done"]
    assert act, "应产生 mark_step_done 步"
    # 该步 reasoning == 前端流式收到的全部(哑火轮 + 动作轮),不是只动作轮
    assert act[0].reasoning == "".join(streamed)
    assert "哑火轮的思考。" in act[0].reasoning
    assert "动作轮的思考。" in act[0].reasoning


async def test_llm_finished_without_toolcall():
    # 无待办步骤(空 plan,all_resolved 为真)时,模型不调工具并自报结果 → 正常结束
    plan = _plan(0)
    llm = _ScriptedLLM([_resp(content="无需操作 TEST_RESULT: FAIL")])
    loop = ReActLoop(
        llm, tools=[], execute=_make_executor(plan), step_plan=plan, build_system=_build_system
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.LLM_FINISHED
    assert result.llm_result == "FAIL"
    assert result.action_steps == []


async def test_premature_test_result_does_not_stop_with_pending_steps():
    """回归:模型完成第1步后提前吐 TEST_RESULT(不调工具),但第2步还没做。

    旧逻辑会因 maybe_result 命中而立即终止(真实环境 DeepSeek 登录后即停的根因);
    新逻辑不采信自报结果,推它继续,直到所有步骤真正完成。"""
    plan = _plan(2)
    llm = _ScriptedLLM(
        [
            _resp(content="点第一个", calls=[("browser_click", {"ref": "b1"})]),
            _resp(content="完成第一步", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="我觉得可以了 TEST_RESULT: PASS"),  # 提前收尾:无 tool_call
            _resp(content="继续点第二个", calls=[("browser_click", {"ref": "b2"})]),
            _resp(content="完成第二步", calls=[("mark_step_done", {"step_no": 2})]),
        ]
    )
    loop = ReActLoop(
        llm, tools=[], execute=_make_executor(plan), step_plan=plan, build_system=_build_system
    )
    result = await loop.run()
    # 没有被提前的 TEST_RESULT 终止,第2步被推着做完
    assert result.stop_reason == StopReason.COMPLETED
    assert plan.all_done()


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


async def test_idle_nudge_feeds_fresh_snapshot_and_recovers():
    # 哑火时主动喂最新快照 + 强指令,逼出动作(修 DeepSeek 抓完快照后退化叙述卡死)
    plan = _plan(1)
    snaps: list[int] = []

    async def get_snap():
        snaps.append(1)
        return '### Snapshot\n- button "Login" [ref=e9]'

    seen_nudge: dict = {}

    class _CapturingLLM(LLMClient):
        def __init__(self):
            self._i = 0

        async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
            self._i += 1
            if self._i == 1:
                return _resp(content="我先想想")  # 哑火:无 tool_call
            # 第二轮:记录收到的最近 user 消息(应含喂回的快照),然后点击完成
            seen_nudge["last_user"] = messages[-1]["content"]
            return _resp(content="点登录", calls=[("browser_click", {"ref": "e9"})])

    plan2 = _plan(1)

    async def execute(name, arguments):
        handled = plan2.apply_tool_call(name, arguments)
        if handled is not None:
            return ToolOutcome(text=handled)
        # 点击后标记完成,使循环收敛
        plan2.apply_tool_call("mark_step_done", {"step_no": 1})
        return ToolOutcome(text="已执行 " + name, url="http://x")

    loop = ReActLoop(
        _CapturingLLM(),
        tools=[],
        execute=execute,
        step_plan=plan2,
        build_system=_build_system,
        get_snapshot=get_snap,
    )
    result = await loop.run()
    assert snaps, "哑火时应主动抓取快照"
    assert "[当前页面快照]" in seen_nudge["last_user"]
    assert "Login" in seen_nudge["last_user"]  # 快照内容被喂回
    assert plan2.all_done()


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


async def test_idle_outputs_capture_model_text_and_kind():
    # #2 哑火可观测:每个哑火轮记一条模型原文 + 性质(narration_only),供"卡死"事后定性。
    plan = _plan(2)
    llm = _ScriptedLLM([_resp(content="我先停下来想想,不确定点哪里")])  # 永远叙述、不调工具
    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
        max_idle_nudges=2,
    )
    result = await loop.run()
    assert result.idle_outputs, "哑火轮应被记录"
    assert all(o["kind"] == "narration_only" for o in result.idle_outputs)
    assert "我先停下来想想" in result.idle_outputs[0]["text"]
    assert result.idle_outputs[0]["rechecked"] is False  # 非流式(on_llm_delta=None)不复核


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


async def test_tool_call_error_persistent_stops():
    """持续的 tool_call 格式错误(超过哑火预算)才真正终止为 TOOL_CALL_ERROR。"""
    plan = _plan(1)  # 1 步未完成
    err = LLMToolCallError("解析不了")
    # max_idle_nudges=3:需第 4 次仍报错才终止(前 3 次哑火续推)
    llm = _ScriptedLLM([_resp()], raise_on={0: err, 1: err, 2: err, 3: err})
    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
        max_idle_nudges=3,
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.TOOL_CALL_ERROR


async def test_tool_call_error_transient_recovers():
    """铁律3:偶发 tool_call 错误不得搞崩循环——纠偏续推后仍能完成剩余步骤。"""
    plan = _plan(1)
    # 第 0 次报错(该槽被 raise 消费)→ 续推;第 1 次 mark 被 B-软护栏拦一次(该步无实操)→
    # 第 2 次再 mark 放行收尾(护栏每步至多拦一次)。
    llm = _ScriptedLLM(
        [
            _resp(content="坏"),  # idx0 槽:被 raise 占用
            _resp(calls=[("mark_step_done", {"step_no": 1})]),  # idx1:护栏拦一次
            _resp(calls=[("mark_step_done", {"step_no": 1})]),  # idx2:放行,完成该步
            _resp(content="完成"),
        ],
        raise_on={0: LLMToolCallError("偶发坏格式")},
    )
    loop = ReActLoop(
        llm, tools=[], execute=_make_executor(plan), step_plan=plan, build_system=_build_system
    )
    result = await loop.run()
    assert result.stop_reason != StopReason.TOOL_CALL_ERROR
    assert plan.all_resolved()


# ── 操作侧自愈(T-11) ─────────────────────────────────────────

_HEAL_SNAPSHOT = (
    '### Page\n- Page URL: http://x/p\n### Snapshot\n```yaml\n- button "提交" [ref=e3]\n```\n'
)


def test_normalize_ref_target_fills_target_from_ref():
    from harness.react_loop import ToolCall, _normalize_ref_target

    # 模型把 ref 写进 `ref`(训练先验),而本 playwright-mcp 要 `target` → 补进 target
    tc = ToolCall(id="1", name="browser_click", arguments={"ref": "e54", "element": "加购按钮"})
    _normalize_ref_target(tc)
    assert tc.arguments["target"] == "e54"

    # element_ref / ref_id 同样兜
    tc2 = ToolCall(id="2", name="browser_type", arguments={"element_ref": "e7", "text": "x"})
    _normalize_ref_target(tc2)
    assert tc2.arguments["target"] == "e7"

    # 已有 target → 不覆盖
    tc3 = ToolCall(id="3", name="browser_click", arguments={"target": "e1", "ref": "e9"})
    _normalize_ref_target(tc3)
    assert tc3.arguments["target"] == "e1"

    # 非 browser_* / 无 ref 别名 / 别名不像 ref → 不动
    tc4 = ToolCall(id="4", name="mark_step_done", arguments={"ref": "e1"})
    _normalize_ref_target(tc4)
    assert "target" not in tc4.arguments
    tc5 = ToolCall(id="5", name="browser_navigate", arguments={"url": "http://x"})
    _normalize_ref_target(tc5)
    assert "target" not in tc5.arguments


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


async def test_step_captures_request_prompt():
    """每步落定时记下本轮请求(System Prompt + 最近输入),供「查看 prompt」。"""
    plan = _plan(1)
    llm = _ScriptedLLM(
        [
            _resp(calls=[("browser_click", {"element": "按钮1", "ref": "e1"})]),
            _resp(calls=[("mark_step_done", {"step": 1})]),
        ]
    )
    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
    )
    result = await loop.run()
    step0 = result.action_steps[0]
    assert "### System Prompt" in step0.prompt
    assert "SYSTEM" in step0.prompt  # _build_system 的输出确实进了 prompt
    assert "### 最近输入" in step0.prompt


async def test_action_healing_uses_vocabulary_first():
    """操作侧自愈:vocab_resolver 命中业务词 → 作为词汇表候选传给 healer(规格 §5.4)。"""
    plan = _plan(1)
    llm = _ScriptedLLM(
        [
            _resp(calls=[("browser_click", {"element": "提交按钮", "ref": "e9"})]),
            _resp(calls=[("mark_step_done", {"step_no": 1})]),
        ]
    )

    seen: dict = {}

    class _RecordingHealer:
        async def relocate(
            self, *, intent, target, snapshot_text, expected=None, vocabulary=None, screenshot=None
        ):
            seen["vocabulary"] = vocabulary
            seen["screenshot"] = screenshot
            from harness.healing import HealCandidate, HealResult

            cand = HealCandidate(target="提交", strategy="P1_role", confidence=0.95)
            return HealResult(healed=True, chosen=cand, candidates=[cand], summary="ok")

    class _Resolver:
        login_role = ""

        async def resolve(self, target, *, url="", title=""):
            return {"role": "button", "name": "保存并提交"}

    async def get_snap():
        return _HEAL_SNAPSHOT

    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan, fail_tools={"browser_click"}),
        step_plan=plan,
        build_system=_build_system,
        healer=_RecordingHealer(),
        get_snapshot=get_snap,
        vocab_resolver=_Resolver(),
    )
    await loop.run()
    assert seen["vocabulary"] == {"提交按钮": "保存并提交"}


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


# ── 定位器对齐捕获:ref 别名 + 实际执行定位器抽取 ──────────────


def test_ref_alias_recovers_ref_from_target():
    from harness.react_loop import _ref_alias

    assert _ref_alias({"ref": "e11"}) == "e11"
    # 模型把 ref 放进 target(实测 DeepSeek)
    assert _ref_alias({"element": "用户名", "target": "e13"}) == "e13"
    # target 不像 ref(是普通文本)→ 不误认
    assert _ref_alias({"target": "提交按钮"}) is None
    assert _ref_alias({}) is None


def test_extract_executed_locator():
    from harness.react_loop import extract_executed_locator

    t1 = "### Ran Playwright code\n```js\nawait page.locator('[data-test=\"username\"]').fill('x');\n```"
    assert extract_executed_locator(t1) == "page.locator('[data-test=\"username\"]')"
    t2 = "await page.getByRole('button', { name: 'Login' }).click();"
    assert extract_executed_locator(t2) == "page.getByRole('button', { name: 'Login' })"
    assert extract_executed_locator("no code here") == ""


# ── E2:页面指纹 + 软护栏升级 + 卡住提醒 + 跨 phase 重置 ──────


def test_fingerprint_url_and_refs():
    """页面指纹由 URL + ref 集组成;同页相同、改 URL 或 ref 集都会变。"""
    from harness.react_loop import _fingerprint

    snap_a = "Page URL: http://x/page1\n[ref=e1]\n[ref=e2]"
    snap_b = "Page URL: http://x/page1\n[ref=e2]\n[ref=e1]"  # 顺序无关
    snap_c = "Page URL: http://x/page2\n[ref=e1]\n[ref=e2]"  # 改 URL
    snap_d = "Page URL: http://x/page1\n[ref=e1]\n[ref=e3]"  # 改 ref 集
    assert _fingerprint(snap_a) == _fingerprint(snap_b)
    assert _fingerprint(snap_a) != _fingerprint(snap_c)
    assert _fingerprint(snap_a) != _fingerprint(snap_d)
    assert _fingerprint("") == ""


async def test_fp_guard_operation_with_no_effect_triggers_soft_nudge():
    """E2 分支 B:做了操作但页面指纹未变 → 软拦,提示「操作似乎没生效」。"""
    plan = _plan(1)
    # 关键:click 后返回**同样**的快照文本(url+ref 不变)→ 指纹未变
    same_snapshot = "Page URL: http://x/p\n- button [ref=e1]"

    async def execute(name, arguments):
        handled = plan.apply_tool_call(name, arguments)
        if handled is not None:
            return ToolOutcome(text=handled)
        # 浏览器工具都返回同一份快照(模拟点了但没生效)
        return ToolOutcome(text=same_snapshot)

    async def get_snapshot():
        return same_snapshot

    llm = _ScriptedLLM(
        [
            # r1:先抓快照 → last_snapshot_text 有内容,step_start_fp 在 r2 顶端记录
            _resp(calls=[("browser_snapshot", {})]),
            # r2:点击(指纹不变)
            _resp(calls=[("browser_click", {"ref": "e1"})]),
            # r3:试图 mark done → fp_unchanged=True → 被软拦
            _resp(calls=[("mark_step_done", {"step_no": 1})]),
            # r4:换思路再点一次,这次执行器仍返回同一快照(测试不在乎是否真生效,只看护栏)
            #     由于该步已被提示过,r4 再 mark 即放行
            _resp(calls=[("mark_step_done", {"step_no": 1})]),
        ]
    )
    loop = ReActLoop(
        llm,
        tools=[],
        execute=execute,
        step_plan=plan,
        build_system=_build_system,
        get_snapshot=get_snapshot,
        max_steps=10,
    )
    result = await loop.run()
    # 至少跑到 r4 即被放行完成
    assert plan.all_done()
    # 验证软拦发生过:某轮 user 消息包含「操作似乎没生效」措辞——通过日志/messages 较难直查,
    # 间接看:r3 的 mark_step_done **没**被 StepPlan 处理(否则 r3 后 plan 已 all_done,
    # 不会触发 r4)。检查 r4 那次 mark 才是真正落定的一次。
    mark_steps = [s for s in result.action_steps if s.tool_name == "mark_step_done"]
    # 第一次 mark_done(r3)被软拦不执行 → action_steps 里只有 r4 那次真正执行的 mark
    assert len(mark_steps) == 1


async def test_fp_changes_lets_mark_through():
    """E2 分支 B 反证:操作让页面指纹变了 → 不触发 fp_unchanged 软拦,mark 放行。"""
    plan = _plan(1)
    seq_snaps = [
        "Page URL: http://x/p1\n- button [ref=e1]",
        "Page URL: http://x/p2\n- button [ref=e2]",  # click 后跳到 p2,指纹改变
    ]
    snap_i = [0]

    async def execute(name, arguments):
        handled = plan.apply_tool_call(name, arguments)
        if handled is not None:
            return ToolOutcome(text=handled)
        out = seq_snaps[min(snap_i[0], len(seq_snaps) - 1)]
        snap_i[0] += 1
        return ToolOutcome(text=out)

    llm = _ScriptedLLM(
        [
            _resp(calls=[("browser_snapshot", {})]),  # r1:得到 p1 快照
            _resp(calls=[("browser_click", {"ref": "e1"})]),  # r2:点击 → 跳 p2
            _resp(calls=[("mark_step_done", {"step_no": 1})]),  # r3:fp 变了 → 放行
            _resp(content="done"),
        ]
    )
    loop = ReActLoop(
        llm,
        tools=[],
        execute=execute,
        step_plan=plan,
        build_system=_build_system,
        max_steps=10,
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.COMPLETED
    assert plan.all_done()
    # mark_done 被采信(只发了一次,没被软拦回放)
    mark_steps = [s for s in result.action_steps if s.tool_name == "mark_step_done"]
    assert len(mark_steps) == 1


async def test_stuck_reminder_injected_after_no_progress_rounds():
    """E2 步级卡住主动提醒:连续 N 轮指纹未变且未推进 → 注入诊断引导(每步至多一次)。

    用**不同**的浏览器工具调用避免触发循环检测(loop_window=3 会更早终止),
    专门考验 stuck 检测路径。
    """
    plan = _plan(1)
    fixed = "Page URL: http://x/p\n- button [ref=e1]"

    async def execute(name, arguments):
        handled = plan.apply_tool_call(name, arguments)
        if handled is not None:
            return ToolOutcome(text=handled)
        return ToolOutcome(text=fixed)  # 同一份快照,永远不变

    # 捕获 messages 流以检查注入
    captured: list[list[dict]] = []

    class _Capturing(_ScriptedLLM):
        async def chat(self, messages, tools=None, **kwargs):
            captured.append([dict(m) for m in messages])  # 拷贝当时 messages
            return await super().chat(messages, tools=tools, **kwargs)

    # 用每轮**不同 args** 的浏览器工具调用,跳过循环检测,只考验指纹未变 → stuck 提醒。
    llm = _Capturing(
        [
            _resp(calls=[("browser_snapshot", {})]),  # r1:抓快照,r2 顶端记 step_start_fp
            _resp(calls=[("browser_hover", {"ref": "e1"})]),  # r2:hover(fp 没变),stuck=1
            _resp(calls=[("browser_hover", {"ref": "e2"})]),  # r3:hover 另一 ref,stuck=2 → 提醒
            _resp(calls=[("browser_click", {"ref": "e1"})]),  # r4:看到提醒后真点击
            _resp(calls=[("mark_step_done", {"step_no": 1})]),
        ]
    )
    loop = ReActLoop(
        llm,
        tools=[],
        execute=execute,
        step_plan=plan,
        build_system=_build_system,
        stuck_round_budget=2,
        loop_window=5,  # 放宽避免循环检测干扰本测试
        max_steps=10,
    )
    await loop.run()
    # 第 4 轮 LLM 调用时,messages 里应已包含「卡住提醒」(r3 末尾注入)
    assert len(captured) >= 4, f"only {len(captured)} rounds captured"
    msg_texts = [m.get("content", "") for m in captured[3] if isinstance(m.get("content"), str)]
    assert any("[卡住提醒]" in t for t in msg_texts), "stuck reminder 未注入"


async def test_cross_phase_reset_smoke():
    """E2 跨 phase 重置 smoke:多阶段 plan 跑通,确认顶部跨 phase 重置块未破坏控制流。

    单测难以直接观察内部 idle_nudges/recent_sigs 重置(它们是局部变量),这里至少保证
    跨 phase 路径在正常多阶段流程下不会引入 regression。
    """
    plan = _multi_phase_plan(1, 1)
    llm = _ScriptedLLM(
        [
            _resp(calls=[("browser_click", {"ref": "e1"})]),  # phase 0
            _resp(calls=[("mark_step_done", {"step_no": 1})]),  # phase 0 done
            _resp(calls=[("browser_click", {"ref": "e2"})]),  # phase 1(顶端触发跨 phase 重置)
            _resp(calls=[("mark_step_done", {"step_no": 2})]),
            _resp(content="done"),
        ]
    )
    loop = ReActLoop(
        llm,
        tools=[],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
        max_steps=10,
    )
    result = await loop.run()
    assert result.stop_reason == StopReason.COMPLETED
    assert plan.all_done()


def test_step_fail_budget_default_is_5():
    """E2:默认单步定位失败预算从 3 放宽到 5(给诊断换法留空间)。"""
    plan = _plan(1)
    loop = ReActLoop(
        _ScriptedLLM([_resp()]),
        tools=[],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
    )
    assert loop.step_fail_budget == 5


def test_stuck_round_budget_default_is_2():
    """E2:默认卡住提醒预算 2(给一次机会再提醒)。"""
    plan = _plan(1)
    loop = ReActLoop(
        _ScriptedLLM([_resp()]),
        tools=[],
        execute=_make_executor(plan),
        step_plan=plan,
        build_system=_build_system,
    )
    assert loop.stuck_round_budget == 2
