"""T-04 单元测试:StepPlan / TodoWrite 状态机。"""

from __future__ import annotations

from harness.step_plan import (
    MARK_STEP_DONE_TOOL,
    PlanStep,
    StepPlan,
    StepStatus,
)
from input.models import SpecStep, TestSpec


def _spec_steps(n: int) -> list[SpecStep]:
    return [SpecStep(action="click", target=f"按钮{i}") for i in range(1, n + 1)]


# ── 初始状态 ──────────────────────────────────────────────────


def test_initial_first_active_rest_pending():
    plan = StepPlan(_spec_steps(3))
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


def test_from_spec_only_business_steps():
    spec = TestSpec(
        case_id="TC001",
        name="x",
        base_url="http://x",
        given=[SpecStep(action="navigate", target="登录")],
        steps=_spec_steps(2),
    )
    plan = StepPlan.from_spec(spec)
    assert len(plan) == 2  # given 不计入


# ── 推进 ──────────────────────────────────────────────────────


def test_mark_done_advances():
    plan = StepPlan(_spec_steps(3))
    plan.mark_done(1)
    assert plan.steps[0].status == StepStatus.DONE
    assert plan.steps[1].status == StepStatus.ACTIVE
    assert plan.current.step_no == 2


def test_full_completion():
    plan = StepPlan(_spec_steps(2))
    plan.mark_done(1)
    plan.mark_done(2)
    assert plan.all_done()
    assert plan.all_resolved()
    assert plan.current is None


def test_skipped_advances_and_resolves():
    plan = StepPlan(_spec_steps(2))
    plan.mark_skipped(1, reason="不适用")
    assert plan.steps[0].status == StepStatus.SKIPPED
    assert plan.steps[1].status == StepStatus.ACTIVE
    plan.mark_done(2)
    assert plan.all_resolved()
    assert not plan.all_done()  # 有 skipped,不算全 done


def test_failed_state():
    plan = StepPlan(_spec_steps(2))
    plan.mark_failed(1, reason="元素未找到")
    assert plan.has_failure()
    assert plan.steps[0].note == "元素未找到"


def test_require_out_of_range_raises():
    plan = StepPlan(_spec_steps(1))
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
    plan = StepPlan(_spec_steps(2))
    msg = plan.apply_tool_call(MARK_STEP_DONE_TOOL, {"step_no": 1})
    assert "已完成第 1 步" in msg
    assert "下一步:第 2 步" in msg
    assert plan.current.step_no == 2


def test_apply_tool_call_last_step_reports_all_done():
    plan = StepPlan(_spec_steps(1))
    msg = plan.apply_tool_call(MARK_STEP_DONE_TOOL, {"step_no": 1})
    assert "所有步骤完成" in msg


def test_apply_tool_call_not_my_tool_returns_none():
    plan = StepPlan(_spec_steps(1))
    assert plan.apply_tool_call("browser_click", {"ref": "x"}) is None


def test_apply_tool_call_bad_arg_returns_error_text():
    plan = StepPlan(_spec_steps(1))
    msg = plan.apply_tool_call(MARK_STEP_DONE_TOOL, {"step_no": "abc"})
    assert "参数错误" in msg


def test_apply_tool_call_out_of_range_returns_error_text():
    plan = StepPlan(_spec_steps(1))
    msg = plan.apply_tool_call(MARK_STEP_DONE_TOOL, {"step_no": 9})
    assert "标记失败" in msg


def test_apply_tool_call_string_int_coerced():
    plan = StepPlan(_spec_steps(2))
    msg = plan.apply_tool_call(MARK_STEP_DONE_TOOL, {"step_no": "1"})
    assert "已完成第 1 步" in msg


# ── 序列化 ────────────────────────────────────────────────────


def test_to_prompt_marks_and_pointer():
    plan = StepPlan(_spec_steps(3))
    plan.mark_done(1)
    text = plan.to_prompt()
    assert "[x] 1." in text
    assert "[→] 2." in text
    assert "[ ] 3." in text
    assert "当前应执行第 2 步" in text
    assert f"{MARK_STEP_DONE_TOOL}(step_no=2)" in text


def test_describe_includes_data():
    st = PlanStep(step_no=1, action="fill", target="用户名", data="admin")
    assert "fill → 用户名" in st.describe()
    assert "admin" in st.describe()
