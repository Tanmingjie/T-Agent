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
