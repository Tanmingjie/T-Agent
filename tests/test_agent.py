"""T-10 单元测试:TestCaseAgent 集成 + 执行器路由。

用 fake LLM(脚本化)+ fake MCP 驱动,验证:断言驱动的最终判定(非 LLM 眼判)、
执行器把 mark_step_done 路由到 StepPlan、其余路由到 MCP。
"""

from __future__ import annotations

from harness.agent import TestCaseAgent, collect_assertions, make_executor
from harness.llm import LLMClient, LLMResponse, ToolCall
from harness.step_plan import StepPlan
from input.models import Assertion, SpecStep, TestCase, TestSpec

SNAPSHOT_OK = """\
### Page
- Page URL: https://intranet/order/list
### Snapshot
```yaml
- button "提交" [ref=e3]
- text: 待审批
```
"""

SNAPSHOT_NO_STATUS = """\
### Page
- Page URL: https://intranet/order/list
### Snapshot
```yaml
- button "提交" [ref=e3]
```
"""


class _ScriptedLLM(LLMClient):
    def __init__(self, responses):
        self._r = responses
        self._i = 0

    async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
        idx = min(self._i, len(self._r) - 1)
        self._i += 1
        return self._r[idx]


def _resp(content="", calls=None):
    return LLMResponse(
        content=content, tool_calls=[ToolCall(name=n, arguments=a) for n, a in (calls or [])]
    )


class _FakeMCP:
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.tool_calls = []

    def to_litellm_tools(self):
        return [{"type": "function", "function": {"name": "browser_click", "description": "点击"}}]

    async def call_tool(self, name, arguments=None):
        self.tool_calls.append((name, arguments))
        return name

    def result_to_text(self, result):
        if result == "browser_snapshot":
            return self._snapshot
        return self._snapshot  # 点击等也返回带快照的页面态

    @staticmethod
    def _noop():
        pass


def _spec():
    return TestSpec(
        case_id="TC001",
        name="提交订单",
        base_url="https://intranet",
        steps=[SpecStep(action="click", target="提交按钮")],
        assertions=[
            Assertion(type="url_contains", target="URL", expected="/order/list"),
            Assertion(type="text_equals", target="待审批", expected="待审批"),
        ],
    )


def _case():
    return TestCase(id="TC001", name="提交订单", steps=["点击提交"], base_url="https://intranet")


# ── 执行器路由 ────────────────────────────────────────────────


async def test_executor_routes_control_vs_mcp():
    plan = StepPlan([SpecStep(action="click", target="提交")])
    mcp = _FakeMCP(SNAPSHOT_OK)
    execute = make_executor(plan, mcp)

    # 控制工具 → StepPlan,不走 MCP
    out = await execute("mark_step_done", {"step_no": 1})
    assert "已完成第 1 步" in out.text
    assert mcp.tool_calls == []

    # 其余 → MCP,并从结果提取 URL
    out2 = await execute("browser_click", {"ref": "e3"})
    assert mcp.tool_calls == [("browser_click", {"ref": "e3"})]
    assert out2.url == "https://intranet/order/list"


# ── 断言驱动的最终判定 ────────────────────────────────────────


async def test_run_passes_when_assertions_pass():
    llm = _ScriptedLLM(
        [
            _resp(content="点提交", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="结束 TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=_spec())
    assert record.passed is True
    assert len(record.case_assertions) == 2
    assert all(a["status"] == "pass" for a in record.case_assertions)


async def test_verdict_is_assertion_driven_not_llm():
    # LLM 自报 PASS,但页面缺少"待审批" → 断言 FAIL → 最终 FAIL
    llm = _ScriptedLLM(
        [
            _resp(content="点提交", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="我觉得成功了 TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_NO_STATUS))
    record = await agent.run(_case(), spec=_spec())
    assert record.passed is False  # 不取 LLM 自报
    # text_equals(待审批) 因元素缺失而失败且可自愈
    status_assertion = [a for a in record.case_assertions if a["target"] == "待审批"][0]
    assert status_assertion["status"] == "fail"
    assert status_assertion["healable"] is True


def test_collect_assertions_aggregates_case_and_step_level():
    spec = TestSpec(
        case_id="TC",
        name="x",
        base_url="http://x",
        given=[
            SpecStep(
                action="execute",
                target="g",
                expect=[Assertion(type="url_contains", target="URL", expected="/a")],
            )
        ],
        steps=[
            SpecStep(
                action="click",
                target="登录",
                expect=[Assertion(type="url_contains", target="URL", expected="inventory.html")],
            ),
            SpecStep(
                action="click",
                target="加购",
                expect=[Assertion(type="text_equals", target="购物车数量", expected="1")],
            ),
        ],
        assertions=[Assertion(type="element_visible", target="标题")],
    )
    out = collect_assertions(spec)
    # 用例级 1 + given expect 1 + 两个 step expect 各 1 = 4
    assert len(out) == 4
    types = [a.type for a in out]
    assert "element_visible" in types  # 用例级
    assert types.count("url_contains") == 2  # given + step1


async def test_run_verifies_step_level_expect_when_case_assertions_empty():
    # 复现真实 bug:断言全在 step.expect,用例级为空 → 仍应被验证,不被漏掉
    spec = TestSpec(
        case_id="TC001",
        name="x",
        base_url="https://intranet",
        steps=[
            SpecStep(
                action="click",
                target="登录",
                expect=[Assertion(type="url_contains", target="URL", expected="/order/list")],
            )
        ],
        assertions=[],  # 用例级为空
    )
    llm = _ScriptedLLM(
        [
            _resp(content="点", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=spec)
    # step.expect 的 url_contains 被验证且通过 → 不再是"无断言空 FAIL"
    assert len(record.case_assertions) == 1
    assert record.case_assertions[0]["type"] == "url_contains"
    assert record.passed is True


async def test_run_records_steps():
    llm = _ScriptedLLM(
        [
            _resp(content="点提交", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=_spec())
    assert [s.tool_name for s in record.steps] == ["browser_click", "mark_step_done"]
    assert record.exec_id
