"""执行期捕获真实 a11y role+name → ActionStep → codegen 稳健定位。

覆盖三段:① 快照 ref 索引解析;② ReAct 循环按操作的 ref 回查真实 (role,name) 记入
ActionStep;③ codegen 解析层据此产出 get_by_role 且优先级高于词汇表。
不连真实 LLM/浏览器,fake 驱动。
"""

from __future__ import annotations

from codegen.locators import Locator, LocatorStrategy, locators_from_steps
from harness.llm import LLMClient, LLMResponse, ToolCall
from harness.page_probe import build_ref_index
from harness.react_loop import ReActLoop
from harness.step_plan import StepPlan
from input.models import ActionStep, SpecStep

_SNAPSHOT = """\
### Snapshot
```yaml
- button "登录" [ref=e5]
- textbox "用户名" [ref=e3]: admin
- generic [ref=e9]
```
"""


def test_build_ref_index_parses_role_name():
    idx = build_ref_index(_SNAPSHOT)
    assert set(idx) == {"e5", "e3", "e9"}
    assert (idx["e5"].role, idx["e5"].name) == ("button", "登录")
    assert (idx["e3"].role, idx["e3"].name) == ("textbox", "用户名")
    assert idx["e9"].name == ""  # 无可及名


class _ScriptedLLM(LLMClient):
    def __init__(self, responses):
        self._responses = responses
        self._i = 0

    async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
        idx = min(self._i, len(self._responses) - 1)
        self._i += 1
        return self._responses[idx]


def _resp(content="", calls=None):
    return LLMResponse(
        content=content,
        tool_calls=[ToolCall(name=n, arguments=a) for n, a in (calls or [])],
    )


async def test_react_loop_captures_real_role_name():
    """先快照(观察含 ref),再点击 ref → 该步 ActionStep 记下真实 role+name + 步骤 target。"""
    plan = StepPlan([SpecStep(action="click", target="登录按钮")])

    async def execute(name, arguments):
        handled = plan.apply_tool_call(name, arguments)
        if handled is not None:
            from harness.react_loop import ToolOutcome

            return ToolOutcome(text=handled)
        from harness.react_loop import ToolOutcome

        if name == "browser_snapshot":
            return ToolOutcome(text=_SNAPSHOT, url="http://x/login")
        return ToolOutcome(text="已点击", url="http://x/login")

    llm = _ScriptedLLM(
        [
            _resp(calls=[("browser_snapshot", {})]),
            _resp(calls=[("browser_click", {"element": "登录", "ref": "e5"})]),
            _resp(calls=[("mark_step_done", {"step_no": 1})]),
        ]
    )
    loop = ReActLoop(
        llm,
        tools=[],
        execute=execute,
        step_plan=plan,
        build_system=lambda p: "SYS",
        max_steps=6,
    )
    result = await loop.run()

    click = next(s for s in result.action_steps if s.tool_name == "browser_click")
    assert (click.element_role, click.element_name) == ("button", "登录")
    assert click.step_target == "登录按钮"
    # 快照步骤无 ref,不应误捕获
    snap = next(s for s in result.action_steps if s.tool_name == "browser_snapshot")
    assert snap.element_role == "" and snap.element_name == ""


def test_locators_from_steps_builds_role_and_skips_incomplete():
    steps = [
        ActionStep(step_no=1, tool_name="browser_snapshot"),  # 无捕获
        ActionStep(
            step_no=2,
            tool_name="browser_click",
            element_role="button",
            element_name="登录",
            step_target="登录按钮",
        ),
        ActionStep(  # role 无 name → 跳过(过于宽泛)
            step_no=3,
            tool_name="browser_click",
            element_role="generic",
            element_name="",
            step_target="某区域",
        ),
    ]
    out = locators_from_steps(steps)
    assert set(out) == {"登录按钮"}
    loc = out["登录按钮"]
    assert (loc.strategy, loc.role, loc.name) == (LocatorStrategy.ROLE, "button", "登录")


def test_execution_capture_overrides_vocab_priority():
    """同一 target,执行捕获(真实身份)应覆盖词汇表解析结果。"""
    vocab = {"登录按钮": Locator(LocatorStrategy.CSS, value="#login", target="登录按钮")}
    captured = locators_from_steps(
        [
            ActionStep(
                step_no=1,
                tool_name="browser_click",
                element_role="button",
                element_name="登录",
                step_target="登录按钮",
            )
        ]
    )
    merged = {**vocab, **captured}
    assert merged["登录按钮"].strategy == LocatorStrategy.ROLE
