"""TestCaseAgent 集成 + 执行器路由(阶段化重设计后,2026-06-22)。

用 fake LLM(脚本化)+ fake MCP 驱动,验证:阶段化执行 + 逐阶段 Validator 裁决、
执行器把 mark_step_done 路由到 StepPlan、其余路由到 MCP、阶段失败即失败、执行完整性。
"""

from __future__ import annotations

import json

from harness.agent import TestCaseAgent, make_executor
from harness.llm import LLMClient, LLMResponse, ToolCall
from harness.step_plan import StepPlan
from input.models import Phase, TestCase, TestSpec

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
        return self._snapshot  # snapshot / 点击都返回带快照的页面态


class _PhaseJudgeLLM(LLMClient):
    """区分 ReAct 调用(按脚本)与阶段 Validator 裁判调用(按 verdict 队列)。

    Validator 经 ``AssertionEngine._check_llm_judge``,system prompt 含「测试断言裁判」。
    evidence 逐字摘自 SNAPSHOT_OK(含「待审批」)→ 判 PASS 时能通过证据接地核验。
    """

    def __init__(self, react_responses, judge_verdicts):
        self._r = react_responses
        self._i = 0
        self._verdicts = list(judge_verdicts)
        self.last_judge_user = ""

    async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
        sys = messages[0]["content"] if messages else ""
        if "测试断言裁判" in sys:  # 阶段 Validator(_JUDGE_SYSTEM,偏-FAIL)
            self.last_judge_user = messages[-1]["content"] if messages else ""
            v = self._verdicts.pop(0) if self._verdicts else "FAIL"
            return LLMResponse(
                content=json.dumps(
                    {"verdict": v, "evidence": "待审批", "reason": "裁判:引证页面文案"}
                )
            )
        idx = min(self._i, len(self._r) - 1)
        self._i += 1
        return self._r[idx]


def _spec(expected=""):
    """单阶段单步 spec。默认 expected 为空(无 Validator 裁判,执行完整即通过)。"""
    return TestSpec(
        case_id="TC001",
        name="提交订单",
        base_url="https://intranet",
        phases=[Phase(steps=["点击提交按钮"], expected=expected)],
    )


def _case():
    return TestCase(id="TC001", name="提交订单", steps=["点击提交"], base_url="https://intranet")


def _react_one_step():
    return [
        _resp(content="点提交", calls=[("browser_click", {"ref": "e3"})]),
        _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
        _resp(content="结束 TEST_RESULT: PASS"),
    ]


# ── 执行器路由 ────────────────────────────────────────────────


async def test_executor_routes_control_vs_mcp():
    plan = StepPlan([Phase(steps=["点击提交"])])
    mcp = _FakeMCP(SNAPSHOT_OK)
    execute = make_executor(plan, mcp)

    out = await execute("mark_step_done", {"step_no": 1})
    assert "已完成第 1 步" in out.text
    assert mcp.tool_calls == []

    out2 = await execute("browser_click", {"ref": "e3"})
    assert mcp.tool_calls == [("browser_click", {"ref": "e3"})]
    assert out2.url == "https://intranet/order/list"


# ── 逐阶段 Validator 裁决 ─────────────────────────────────────


async def test_run_passes_when_phase_validator_passes():
    """单阶段带 expected;Validator 判 PASS(证据接地)→ 用例 PASS。"""
    llm = _PhaseJudgeLLM(_react_one_step(), ["PASS"])
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=_spec(expected="出现待审批状态"))
    assert record.passed is True
    assert len(record.case_assertions) == 1
    a = record.case_assertions[0]
    assert a["status"] == "pass" and a["ai_judged"] is True and a["phase_index"] == 0
    # 免费 URL 锚点喂进裁判
    assert "当前页面 URL:https://intranet/order/list" in llm.last_judge_user


async def test_run_fails_when_phase_validator_fails():
    """阶段失败即失败:Validator 判 FAIL → PHASE_FAILED,用例 FAIL(fail-closed)。"""
    llm = _PhaseJudgeLLM(_react_one_step(), ["FAIL"])
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=_spec(expected="出现一个不存在的状态"))
    assert record.passed is False
    a = record.case_assertions[0]
    assert a["status"] == "fail" and a["phase_index"] == 0
    assert "未达成" in record.final_result


async def test_run_fails_when_execution_incomplete():
    """两阶段;第二阶段步骤从不执行(哑火)→ 执行未完成 → FAIL,不靠半路裁决。"""
    spec = TestSpec(
        case_id="TC001",
        name="两阶段",
        base_url="https://intranet",
        phases=[
            Phase(steps=["点击提交按钮"], expected="进入下一页"),
            Phase(steps=["点击确认按钮"], expected="完成"),
        ],
    )
    llm = _PhaseJudgeLLM(
        [
            _resp(content="点提交", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成第一步", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="我觉得做完了 TEST_RESULT: PASS"),  # 第二阶段步骤永不执行
        ],
        ["PASS", "PASS"],
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=spec)
    assert record.passed is False
    assert "执行未完成" in record.final_result


async def test_empty_expected_phase_passes_on_completion():
    """阶段无 expected → Validator 记 skipped(不裁决),执行完整即通过。"""
    llm = _ScriptedLLM(_react_one_step())
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=_spec(expected=""))
    assert record.passed is True
    assert record.case_assertions[0]["status"] == "skipped"


async def test_verdict_not_taken_from_llm_self_report():
    """LLM 自报 PASS,但 Validator 判 FAIL → 用例 FAIL(不取自报)。"""
    llm = _PhaseJudgeLLM(_react_one_step(), ["FAIL"])
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=_spec(expected="某最终状态"))
    assert record.passed is False


async def test_run_records_steps():
    llm = _ScriptedLLM(_react_one_step())
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=_spec())
    assert [s.tool_name for s in record.steps] == ["browser_click", "mark_step_done"]
    assert record.exec_id


async def test_codegen_produced_on_pass():
    llm = _PhaseJudgeLLM(_react_one_step(), ["PASS"])
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=_spec(expected="出现待审批"))
    assert record.generated_code
    assert "Feature:" in record.generated_code
    assert "import" in record.generated_code


# ── 指标 / 实时进度 ───────────────────────────────────────────


async def test_metrics_populated_on_pass():
    llm = _PhaseJudgeLLM(_react_one_step(), ["PASS"])
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=_spec(expected="出现待审批"))
    m = record.metrics
    assert set(m) == {"tokens", "execution", "healing", "assertions"}
    assert m["execution"]["complete"] is True
    assert m["execution"]["done_steps"] == m["execution"]["total_steps"] == 1
    assert m["assertions"]["total"] == 1
    assert "total" in m["tokens"]


async def test_metrics_marks_incomplete_execution():
    spec = TestSpec(
        case_id="TC001",
        name="两阶段",
        base_url="https://intranet",
        phases=[
            Phase(steps=["点击提交按钮"], expected="进入下一页"),
            Phase(steps=["点击确认按钮"], expected="完成"),
        ],
    )
    llm = _PhaseJudgeLLM(
        [
            _resp(content="点提交", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成第一步", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="我觉得做完了 TEST_RESULT: PASS"),
        ],
        ["PASS"],
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=spec)
    assert record.metrics["execution"]["complete"] is False
    assert record.metrics["execution"]["done_steps"] == 1
    assert record.metrics["execution"]["total_steps"] == 2
    assert record.metrics["execution"]["idle_nudges"] >= 1


# ── E4:裁决判前 settle + 恰好一次 ──────────────────────────


async def test_settle_called_before_phase_validator(monkeypatch):
    """E4:`on_phase_end` 抓快照前先 settle_page,确保 judge 看的是稳定终态页。"""
    from harness import agent as agent_mod

    settle_calls: list[float] = []

    async def fake_settle(mcp, *, timeout, interval):
        settle_calls.append(timeout)
        return 1

    monkeypatch.setattr(agent_mod, "settle_page", fake_settle)
    monkeypatch.setattr(agent_mod, "_SETTLE_ENABLED", True)
    llm = _PhaseJudgeLLM(_react_one_step(), ["PASS"])
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=_spec(expected="出现待审批"))
    # 至少有一次 settle(阶段 Validator 触发的);settle 也会被 navigator 工具触发,
    # 但本测试的 _FakeMCP 只走 click,所以 settle 主要来自 Validator + click 后。
    assert len(settle_calls) >= 1
    assert record.passed is True


async def test_settle_disabled_skips_settle_before_validator(monkeypatch):
    """E4:MCP_SETTLE=0 时,Validator 前不调 settle(尊重开关)。"""
    from harness import agent as agent_mod

    settle_calls: list[float] = []

    async def fake_settle(mcp, *, timeout, interval):
        settle_calls.append(timeout)
        return 1

    monkeypatch.setattr(agent_mod, "settle_page", fake_settle)
    monkeypatch.setattr(agent_mod, "_SETTLE_ENABLED", False)
    llm = _PhaseJudgeLLM(_react_one_step(), ["PASS"])
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    await agent.run(_case(), spec=_spec(expected="出现待审批"))
    assert settle_calls == []


async def test_phase_validator_dedup_runs_once_per_phase():
    """E4 恰好一次:同一 phase 即便末步被重复 mark_done,Validator 只跑一次。"""
    llm = _PhaseJudgeLLM(
        [
            _resp(content="点提交", calls=[("browser_click", {"ref": "e3"})]),
            # 同一轮内重复 mark_step_done 同一末步(模拟弱模型奇怪行为)
            _resp(
                content="完成",
                calls=[
                    ("mark_step_done", {"step_no": 1}),
                    ("mark_step_done", {"step_no": 1}),
                ],
            ),
        ],
        ["PASS"],  # 只准备一个裁判结果——若 dedup 失败会爆 IndexError
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=_spec(expected="出现待审批"))
    assert record.passed is True
    # case_assertions 只有一条裁决证据(无重复)
    assert len([a for a in record.case_assertions if a.get("phase_index") == 0]) == 1


async def test_live_progress_streams_phases_and_steps_in_order():
    llm = _ScriptedLLM(_react_one_step())
    events: list[tuple[str, dict]] = []

    async def cb(event, data):
        events.append((event, data))

    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    await agent.run(_case(), spec=_spec(), step_callback=cb)

    phases = [d["phase"] for e, d in events if e == "phase"]
    # 阶段化重设计后撤掉 asserting 独立阶段(F1):Validator 在 ③ 内即时跑,无独立 SSE 阶段事件
    assert phases == ["spec", "executing", "codegen"]
    exec_idx = next(
        i for i, (e, d) in enumerate(events) if e == "phase" and d["phase"] == "executing"
    )
    first_step = next(i for i, (e, _) in enumerate(events) if e == "step_change")
    assert exec_idx < first_step
    sc = [d for e, d in events if e == "step_change"]
    assert any("browser_click" in d["description"] for d in sc)


# ── 页面稳定等待 settle ───────────────────────────────────────


async def test_settle_page_waits_until_stable():
    from harness.agent import settle_page

    seq = [
        "### Snapshot\n(loading)",
        "### Snapshot\n(loading)",
        "- button [ref=e1]\n- button [ref=e2]\n- textbox [ref=e3]",
        "- button [ref=e1]\n- button [ref=e2]\n- textbox [ref=e3]",
    ]

    class _SeqMCP:
        def __init__(self):
            self.i = 0

        async def call_tool(self, name, arguments=None):
            return self.i

        def result_to_text(self, result):
            text = seq[min(self.i, len(seq) - 1)]
            self.i += 1
            return text

    n = await settle_page(_SeqMCP(), timeout=5.0, interval=0.0)
    assert n == 3


async def test_settle_page_times_out_on_persistent_blank():
    from harness.agent import settle_page

    class _BlankMCP:
        async def call_tool(self, name, arguments=None):
            return None

        def result_to_text(self, result):
            return "### Snapshot\n(still loading)"

    n = await settle_page(_BlankMCP(), timeout=0.05, interval=0.0)
    assert n == 0
