"""StepPlan / TodoWrite 状态机(阶段化重设计后)。"""

from __future__ import annotations

from harness.step_plan import (
    MARK_STEP_DONE_TOOL,
    PlanStep,
    StepPlan,
    StepStatus,
)
from input.models import Phase, TestSpec


def _plan(n: int) -> StepPlan:
    """单阶段 n 步。"""
    return StepPlan([Phase(steps=[f"点击按钮{i}" for i in range(1, n + 1)])])


# ── 初始状态 ──────────────────────────────────────────────────


def test_initial_first_active_rest_pending():
    plan = _plan(3)
    assert plan.steps[0].status == StepStatus.ACTIVE
    assert plan.steps[1].status == StepStatus.PENDING
    assert plan.steps[2].status == StepStatus.PENDING
    assert plan.current.step_no == 1


def test_empty_plan():
    plan = StepPlan([])
    assert len(plan) == 0
    assert plan.current is None
    assert not plan.all_done()
    assert plan.to_prompt().startswith("执行计划:无步骤")


def test_from_spec_flattens_phases():
    spec = TestSpec(
        case_id="TC001",
        name="x",
        base_url="http://x",
        phases=[
            Phase(steps=["登录1", "登录2"], expected="已登录"),
            Phase(steps=["进入模块"], expected="进入"),
        ],
    )
    plan = StepPlan.from_spec(spec)
    assert len(plan) == 3
    assert plan.phase_count == 2
    assert plan.phase_of(1) == 0 and plan.phase_of(3) == 1


# ── 阶段边界 ──────────────────────────────────────────────────


def test_phase_boundary_detection():
    spec = TestSpec(
        case_id="TC",
        name="x",
        base_url="http://x",
        phases=[Phase(steps=["a", "b"]), Phase(steps=["c"])],
    )
    plan = StepPlan.from_spec(spec)
    assert plan.is_phase_last_step(2) is True  # 阶段1 最后一步
    assert plan.is_phase_last_step(1) is False
    assert plan.is_phase_last_step(3) is True  # 阶段2 最后一步
    assert plan.phase_last_step_no(0) == 2 and plan.phase_last_step_no(1) == 3


# ── 推进 ──────────────────────────────────────────────────────


def test_mark_done_advances():
    plan = _plan(3)
    plan.mark_done(1)
    assert plan.steps[0].status == StepStatus.DONE
    assert plan.steps[1].status == StepStatus.ACTIVE
    assert plan.current.step_no == 2


def test_full_completion():
    plan = _plan(2)
    plan.mark_done(1)
    plan.mark_done(2)
    assert plan.all_done()
    assert plan.all_resolved()
    assert plan.current is None


def test_reactivate_returns_to_step():
    plan = _plan(2)
    plan.mark_done(1)
    plan.reactivate(1)
    assert plan.steps[0].status == StepStatus.ACTIVE
    assert plan.current.step_no == 1


def test_skipped_advances_and_resolves():
    plan = _plan(2)
    plan.mark_skipped(1, reason="不适用")
    assert plan.steps[0].status == StepStatus.SKIPPED
    assert plan.steps[1].status == StepStatus.ACTIVE
    plan.mark_done(2)
    assert plan.all_resolved()
    assert not plan.all_done()


def test_failed_state():
    plan = _plan(2)
    plan.mark_failed(1, reason="元素未找到")
    assert plan.has_failure()
    assert plan.steps[0].note == "元素未找到"


def test_require_out_of_range_raises():
    plan = _plan(1)
    import pytest

    with pytest.raises(ValueError):
        plan.mark_done(5)


# ── mark_step_done 工具 ───────────────────────────────────────


def test_tool_schema_shape():
    schema = StepPlan.tool_schema()
    assert schema["function"]["name"] == MARK_STEP_DONE_TOOL
    assert "step_no" in schema["function"]["parameters"]["properties"]
    assert schema["function"]["parameters"]["required"] == ["step_no"]


def test_apply_tool_call_advances_and_reports_next():
    plan = _plan(2)
    msg = plan.apply_tool_call(MARK_STEP_DONE_TOOL, {"step_no": 1})
    assert "已完成第 1 步" in msg
    assert "下一步:第 2 步" in msg
    assert plan.current.step_no == 2


def test_apply_tool_call_last_step_reports_all_done():
    plan = _plan(1)
    msg = plan.apply_tool_call(MARK_STEP_DONE_TOOL, {"step_no": 1})
    assert "所有步骤完成" in msg


def test_apply_tool_call_not_my_tool_returns_none():
    plan = _plan(1)
    assert plan.apply_tool_call("browser_click", {"ref": "x"}) is None


def test_apply_tool_call_bad_arg_returns_error_text():
    plan = _plan(1)
    msg = plan.apply_tool_call(MARK_STEP_DONE_TOOL, {"step_no": "abc"})
    assert "参数错误" in msg


def test_apply_tool_call_out_of_range_returns_error_text():
    plan = _plan(1)
    msg = plan.apply_tool_call(MARK_STEP_DONE_TOOL, {"step_no": 9})
    assert "标记失败" in msg


def test_apply_tool_call_string_int_coerced():
    plan = _plan(2)
    msg = plan.apply_tool_call(MARK_STEP_DONE_TOOL, {"step_no": "1"})
    assert "已完成第 1 步" in msg


# ── 序列化 ────────────────────────────────────────────────────


def test_to_prompt_marks_and_pointer():
    plan = _plan(3)
    plan.mark_done(1)
    text = plan.to_prompt()
    assert "[x] 1." in text
    assert "[→] 2." in text
    assert "[ ] 3." in text
    assert "当前应执行第 2 步" in text
    assert f"{MARK_STEP_DONE_TOOL}(step_no=2)" in text


def test_describe_returns_text():
    st = PlanStep(step_no=1, text="在用户名框输入 admin", phase_index=0)
    assert st.describe() == "在用户名框输入 admin"
