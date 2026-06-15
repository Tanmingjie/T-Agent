"""T-10 单元测试:TestCaseAgent 集成 + 执行器路由。

用 fake LLM(脚本化)+ fake MCP 驱动,验证:断言驱动的最终判定(非 LLM 眼判)、
执行器把 mark_step_done 路由到 StepPlan、其余路由到 MCP。
"""

from __future__ import annotations

import json

from harness.agent import (
    TestCaseAgent,
    collect_assertions,
    ensure_navigation_step,
    make_executor,
)
from harness.hooks import ExecutionContext
from harness.llm import LLMClient, LLMResponse, ToolCall
from harness.step_plan import StepPlan
from input.models import Assertion, PreconditionItem, SpecStep, TestCase, TestSpec

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


async def test_run_fails_when_execution_incomplete_even_if_assertions_pass():
    """原则:步骤没全做完 → 用例直接 FAIL,不靠半路断言裁决(即便断言碰巧能过)。"""
    spec = TestSpec(
        case_id="TC001",
        name="两步流程",
        base_url="https://intranet",
        steps=[
            SpecStep(action="click", target="提交按钮"),
            SpecStep(action="click", target="确认按钮"),
        ],
        assertions=[Assertion(type="url_contains", target="URL", expected="/order/list")],
    )
    llm = _ScriptedLLM(
        [
            _resp(content="点提交", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成第一步", calls=[("mark_step_done", {"step_no": 1})]),
            # 第二步从不执行,一直哑火(无 tool_call)→ 哑火上限终止,step2 仍 pending
            _resp(content="我觉得做完了 TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=spec)
    # 断言(url_contains /order/list)在 SNAPSHOT_OK 上本可过,但执行未完成 → 强制 FAIL
    assert record.passed is False
    assert "执行未完成" in record.final_result


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
    # 执行通过 → 生成 pytest-bdd 代码并持久化到 record
    assert record.generated_code
    assert "Feature:" in record.generated_code
    assert "import" in record.generated_code


async def test_step_level_expect_verified_on_current_page():
    """#2:步骤级 expect 在该步落定时于【当前子页面】验证,计入裁决并带 step_no/phase 标注。"""
    spec = TestSpec(
        case_id="TC001",
        name="x",
        base_url="https://intranet",
        steps=[
            SpecStep(
                action="click",
                target="提交按钮",
                expect=[Assertion(type="url_contains", target="URL", expected="/order/list")],
            )
        ],
        assertions=[],  # 无用例级断言,只有步骤级
    )
    llm = _ScriptedLLM(
        [
            _resp(content="点", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="结束 TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=spec)
    assert record.passed is True
    step_a = [a for a in record.case_assertions if a.get("phase") == "step"]
    assert step_a and step_a[0]["step_no"] == 1
    assert step_a[0]["status"] == "pass"


class _GatingLLM(LLMClient):
    """区分 ReAct 调用(按脚本序列)与 llm_judge 完成门控调用(按 verdict 队列)。

    门控调用经 ``AssertionEngine._check_llm_judge``,其 system prompt 含「测试断言裁判」。
    """

    def __init__(self, react_responses, gate_verdicts):
        self._r = react_responses
        self._i = 0
        self._verdicts = list(gate_verdicts)

    async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
        sys = messages[0]["content"] if messages else ""
        if "测试断言裁判" in sys:  # 完成门控(llm_judge)
            v = self._verdicts.pop(0) if self._verdicts else "PASS"
            return LLMResponse(content=json.dumps({"verdict": v, "reason": "门控测试"}))
        idx = min(self._i, len(self._r) - 1)
        self._i += 1
        return self._r[idx]


async def test_step_gate_llm_unmet_reverts_then_passes():
    """完成门控(LLM 判 expect_text):首次判未达成 → 退回该步重做;重做后判达成 → 用例 PASS。

    并验「重做覆盖」:同一步只保留最后一次门控证据(ai_judged PASS),中途 FAIL 不残留拖垮裁决。
    """
    spec = TestSpec(
        case_id="TC001",
        name="一步带完成判据",
        base_url="https://intranet",
        steps=[SpecStep(action="click", target="提交按钮", expect_text="页面跳转到订单列表")],
        assertions=[],  # 仅靠步骤级门控(LLM 判)裁决
    )
    react = [
        _resp(content="点提交", calls=[("browser_click", {"ref": "e3"})]),
        _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),  # 门控判 FAIL → 退回
        _resp(content="再点", calls=[("browser_click", {"ref": "e3"})]),  # 重做
        _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),  # 门控判 PASS
        _resp(content="结束 TEST_RESULT: PASS"),
    ]
    agent = TestCaseAgent(_GatingLLM(react, ["FAIL", "PASS"]), _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=spec)
    assert record.passed is True
    step_a = [a for a in record.case_assertions if a.get("phase") == "step"]
    # 重做覆盖:第 1 步只剩最后一次门控证据(PASS),无残留 FAIL
    assert len(step_a) == 1
    assert step_a[0]["step_no"] == 1
    assert step_a[0]["status"] == "pass"
    assert step_a[0]["ai_judged"] is True  # 门控判定标低置信可见


async def test_step_gate_llm_unmet_exhausts_budget_fails():
    """完成门控反复判未达成超预算 → EXPECT_UNMET,执行未完成 FAIL,标明卡死步。"""
    spec = TestSpec(
        case_id="TC001",
        name="判据始终不达成",
        base_url="https://intranet",
        steps=[SpecStep(action="click", target="提交按钮", expect_text="跳转到不存在的页")],
        assertions=[],
    )
    react = [
        _resp(content="点", calls=[("browser_click", {"ref": "e3"})]),
        _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),  # FAIL 1
        _resp(content="再点", calls=[("browser_click", {"ref": "e3"})]),
        _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),  # FAIL 2 → 预算耗尽
        _resp(content="不该到这"),
    ]
    agent = TestCaseAgent(_GatingLLM(react, ["FAIL", "FAIL"]), _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=spec)
    assert record.passed is False
    assert "执行未完成" in record.final_result
    assert "完成判据反复未达成" in record.final_result


async def test_metrics_populated_on_pass():
    """#6:执行后 record.metrics 带分阶段成本/质量结构,完整性闸门与断言分布正确。"""
    llm = _ScriptedLLM(
        [
            _resp(content="点提交", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="结束 TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=_spec())
    m = record.metrics
    assert set(m) == {"tokens", "execution", "healing", "assertions"}
    # 完整性闸门:全步 DONE
    assert m["execution"]["complete"] is True
    assert m["execution"]["done_steps"] == m["execution"]["total_steps"] == 1
    assert m["execution"]["stop_reason"]  # 非空停因
    # 断言分布:_spec() 两条结构化断言均 pass,无 AI 兜底
    assert m["assertions"]["total"] == 2
    assert m["assertions"]["pass"] == 2
    assert m["assertions"]["ai_judged"] == 0
    # token 结构存在(fake LLM 无 usage → 全 0,但键齐全)
    assert "total" in m["tokens"]


async def test_metrics_marks_incomplete_execution():
    """#6:早停留 pending 步 → metrics.execution.complete=False 且 done<total。"""
    spec = TestSpec(
        case_id="TC001",
        name="两步",
        base_url="https://intranet",
        steps=[
            SpecStep(action="click", target="提交按钮"),
            SpecStep(action="click", target="确认按钮"),
        ],
        assertions=[Assertion(type="url_contains", target="URL", expected="/order/list")],
    )
    llm = _ScriptedLLM(
        [
            _resp(content="点提交", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成第一步", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="我觉得做完了 TEST_RESULT: PASS"),  # 第二步从不执行 → 哑火终止
        ]
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    record = await agent.run(_case(), spec=spec)
    assert record.metrics["execution"]["complete"] is False
    assert record.metrics["execution"]["done_steps"] == 1
    assert record.metrics["execution"]["total_steps"] == 2
    assert record.metrics["execution"]["idle_nudges"] >= 1  # 哑火续推被计量


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


def test_collect_assertions_dedups_case_and_step_duplicates():
    """LLM 常把同一断言既放用例级又放某步 expect,聚合后必须去重(真实跑 TC101)。"""
    dup = Assertion(type="text_equals", target="购物车图标数量", expected="1")
    spec = TestSpec(
        case_id="TC",
        name="x",
        base_url="http://x",
        steps=[SpecStep(action="click", target="加购", expect=[dup.model_copy()])],
        assertions=[
            Assertion(type="url_contains", target="URL", expected="inventory.html"),
            dup.model_copy(),
        ],
    )
    out = collect_assertions(spec)
    # url_contains + text_equals 各一条,step 里重复的 text_equals 被去重
    assert len(out) == 2
    keys = {(a.type, a.target, a.expected) for a in out}
    assert ("text_equals", "购物车图标数量", "1") in keys
    assert ("url_contains", "URL", "inventory.html") in keys


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


# ── codegen 导航注入(生成的测试可独立回放) ──────────────────


def test_ensure_navigation_step_injects_when_absent():
    spec = TestSpec(
        case_id="TC",
        name="x",
        base_url="https://www.saucedemo.com",
        steps=[SpecStep(action="fill", target="用户名", data="u")],
    )
    out = ensure_navigation_step(spec)
    assert out.steps[0].action == "navigate"
    assert out.steps[0].target == "https://www.saucedemo.com"
    assert len(out.steps) == 2  # 原步骤保留


def test_ensure_navigation_step_noop_when_present_or_no_base_url():
    has_nav = TestSpec(
        case_id="TC",
        name="x",
        base_url="http://x",
        steps=[SpecStep(action="navigate", target="http://x/login")],
    )
    assert ensure_navigation_step(has_nav).steps[0].target == "http://x/login"
    no_url = TestSpec(
        case_id="TC", name="x", base_url="", steps=[SpecStep(action="click", target="a")]
    )
    assert len(ensure_navigation_step(no_url).steps) == 1


# ── 预置条件分类接通(P1) ──────────────────────────────────────


class _FakeClassifier:
    def __init__(self, items):
        self._items = items
        self.calls: list[list[str]] = []

    async def classify(self, preconditions):
        self.calls.append(list(preconditions))
        return list(self._items)


_SPEC_JSON_EMPTY_GIVEN = (
    '{"given": [], '
    '"steps": [{"action": "click", "target": "提交按钮", "expect": []}], '
    '"assertions": [{"type": "url_contains", "target": "URL", "expected": "/order/list"}]}'
)


def test_precondition_classifier_default_on_and_disablable():
    # 默认自带分类器(始终接通);传 False 显式关闭
    agent = TestCaseAgent(_ScriptedLLM([]), _FakeMCP(SNAPSHOT_OK))
    assert agent.precondition_classifier is not None
    off = TestCaseAgent(_ScriptedLLM([]), _FakeMCP(SNAPSHOT_OK), precondition_classifier=False)
    assert off.precondition_classifier is None


async def test_generate_spec_merges_action_step_precondition_into_given():
    # 翻译器产出的 given 为空;分类器把预置条件判为 action_step → 确定性合入 given
    llm = _ScriptedLLM([_resp(content=_SPEC_JSON_EMPTY_GIVEN)])
    clf = _FakeClassifier(
        [PreconditionItem(text="新建一条草稿订单", type="action_step", confidence=0.9)]
    )
    case = TestCase(
        id="TC",
        name="x",
        preconditions=["新建一条草稿订单"],
        steps=["点击提交"],
        base_url="http://x",
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK), precondition_classifier=clf)
    spec = await agent.generate_spec(case)
    assert clf.calls == [["新建一条草稿订单"]]
    assert any(g.target == "新建一条草稿订单" for g in spec.given)


async def test_generate_spec_merged_single_call_with_real_classifier():
    # 真分类器 + 有预置条件 → 合并路径:一次 LLM 调用同时分类 + 翻译;action_step 合入 given
    from harness.precondition import PreconditionClassifier

    merged = (
        '{"given": [], '
        '"steps": [{"action": "click", "target": "提交按钮", "expect": []}], '
        '"assertions": [{"type": "url_contains", "target": "URL", "expected": "/list"}], '
        '"preconditions": [{"text": "新建一条草稿订单", "type": "action_step", "confidence": 0.9}]}'
    )
    llm = _ScriptedLLM([_resp(content=merged)])
    clf = PreconditionClassifier(llm)
    case = TestCase(
        id="TC",
        name="x",
        preconditions=["新建一条草稿订单"],
        steps=["点击提交"],
        base_url="http://x",
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK), precondition_classifier=clf)
    spec = await agent.generate_spec(case)
    assert llm._i == 1  # 只一次 LLM 往返(分类随翻译一次出)
    assert any(g.target == "新建一条草稿订单" for g in spec.given)  # action_step 合入 given
    assert case.precondition_items[0].type == "action_step"  # 分类回写(确认闭环)


async def test_generate_spec_skips_classifier_without_preconditions():
    llm = _ScriptedLLM([_resp(content=_SPEC_JSON_EMPTY_GIVEN)])
    clf = _FakeClassifier([])
    case = TestCase(id="TC", name="x", steps=["点"], base_url="http://x")  # 无预置条件
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK), precondition_classifier=clf)
    await agent.generate_spec(case)
    assert clf.calls == []  # 无预置条件不调分类器


async def test_run_records_state_hook_into_ctx():
    # state_hook 预置条件 → ctx.required_hooks(供 before_case 侧 P2 参考)
    llm = _ScriptedLLM(
        [
            _resp(content=_SPEC_JSON_EMPTY_GIVEN),  # generate_spec
            _resp(content="点", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="TEST_RESULT: PASS"),
        ]
    )
    clf = _FakeClassifier(
        [
            PreconditionItem(
                text="已登录系统", type="state_hook", hook_ref="LoginHook", confidence=0.95
            )
        ]
    )
    case = TestCase(
        id="TC",
        name="x",
        preconditions=["已登录系统"],
        steps=["点击提交"],
        base_url="https://intranet",
    )
    ctx = ExecutionContext(case=case)
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK), precondition_classifier=clf)
    await agent.run(case, ctx=ctx)
    assert ctx.get("required_hooks") == ["LoginHook"]


async def test_live_progress_streams_phases_and_steps_in_order():
    """执行期实时推送:阶段事件 + 逐步 step_change 在执行过程中按序到达,
    而非整条用例跑完才补发(修复抽屉执行中无数据)。"""
    llm = _ScriptedLLM(
        [
            _resp(content="点提交", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="TEST_RESULT: PASS"),
        ]
    )
    events: list[tuple[str, dict]] = []

    async def cb(event, data):
        events.append((event, data))

    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK))
    await agent.run(_case(), spec=_spec(), step_callback=cb)

    # 生命周期阶段按序出现
    phases = [d["phase"] for e, d in events if e == "phase"]
    assert phases == ["spec", "executing", "asserting", "codegen"]
    # executing 阶段先于 step_change(步骤在执行阶段内逐步推送)
    exec_idx = next(
        i for i, (e, d) in enumerate(events) if e == "phase" and d["phase"] == "executing"
    )
    first_step = next(i for i, (e, _) in enumerate(events) if e == "step_change")
    assert exec_idx < first_step
    # asserting 阶段在所有 step_change 之后(步骤实时发完才进断言)
    assert_idx = next(
        i for i, (e, d) in enumerate(events) if e == "phase" and d["phase"] == "asserting"
    )
    last_step = max(i for i, (e, _) in enumerate(events) if e == "step_change")
    assert last_step < assert_idx
    # step_change 内容可被前端解析(tool(args) 形式)
    sc = [d for e, d in events if e == "step_change"]
    assert any("browser_click" in d["description"] for d in sc)


# ── 页面稳定等待 settle(动作触发加载,治空白快照)──────────────


async def test_settle_page_waits_until_stable():
    """模拟"点登录→页面加载中(空白)→渐稳定":settle 轮询到 ref 节点数稳定才返回。"""
    from harness.agent import settle_page

    # 前两次空白(加载中,无 ref),之后稳定在 3 个 ref 节点
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
            return self.i  # 索引占位

        def result_to_text(self, result):
            text = seq[min(self.i, len(seq) - 1)]
            self.i += 1
            return text

    n = await settle_page(_SeqMCP(), timeout=5.0, interval=0.0)
    assert n == 3  # 稳定在 3 个 ref 节点(空白态被跨过)


async def test_settle_page_times_out_on_persistent_blank():
    """页面始终空白(无 ref)→ settle 超时返回 0,不死等。"""
    from harness.agent import settle_page

    class _BlankMCP:
        async def call_tool(self, name, arguments=None):
            return None

        def result_to_text(self, result):
            return "### Snapshot\n(still loading)"

    n = await settle_page(_BlankMCP(), timeout=0.05, interval=0.0)
    assert n == 0
