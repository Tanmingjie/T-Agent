"""T-07 单元测试:Prompt 分层组装。"""

from __future__ import annotations

from harness.prompt import (
    BASE_PROMPT,
    PromptBuilder,
    interpolate,
    merge_context,
    render_task,
    render_tools,
)
from harness.step_plan import StepPlan
from input.models import Phase, TestSpec


def _spec() -> TestSpec:
    return TestSpec(
        case_id="TC001",
        name="提交订单",
        base_url="http://intranet.example",
        intent="验证能提交订单进入待审批",
        preconditions=["已新建一条草稿订单"],
        phases=[Phase(steps=["点击提交按钮"], expected="订单状态变为待审批")],
    )


# ── 插值 ──────────────────────────────────────────────────────


def test_interpolate_basic():
    assert interpolate("环境是 {{env}}", {"env": "测试"}) == "环境是 测试"


def test_interpolate_missing_var_kept():
    # 缺失变量原样保留,不报错
    assert interpolate("{{a}}-{{b}}", {"a": "1"}) == "1-{{b}}"


def test_interpolate_empty():
    assert interpolate("", {"a": 1}) == ""


# ── 上下文合并 / 优先级 ───────────────────────────────────────


def test_merge_context_priority_case_wins():
    out = merge_context(
        global_ctx="全局:超时 {{t}}",
        suite_ctx="套件:超时 {{t}}",
        case_ctx="用例:超时 {{t}}",
        global_vars={"t": "10s"},
        suite_vars={"t": "20s"},
        case_vars={"t": "30s"},
    )
    # 同名变量 Case 覆盖,所有出现处都用 30s
    assert out.count("30s") == 3
    assert "10s" not in out and "20s" not in out


def test_merge_context_skips_empty_levels():
    out = merge_context(global_ctx="", suite_ctx="  ", case_ctx="只有用例级")
    assert out == "只有用例级"


# ── Task 层 ───────────────────────────────────────────────────


def test_render_task_contains_intent_preconditions_and_plan():
    plan = StepPlan.from_spec(_spec())
    text = render_task(_spec(), plan)
    assert "提交订单" in text
    assert "http://intranet.example" in text
    assert "验证能提交订单" in text  # intent(背景)
    assert "已新建一条草稿订单" in text  # preconditions(背景)
    assert "执行计划" in text  # StepPlan 清单
    assert "点击提交按钮" in text  # 步骤
    # expected 绝不进驱动 prompt(FG01)
    assert "订单状态变为待审批" not in text


# ── Tools 层 ──────────────────────────────────────────────────


def _tools():
    return [
        {"type": "function", "function": {"name": "browser_click", "description": "点击元素"}},
        {"type": "function", "function": {"name": "browser_navigate", "description": "导航"}},
        {"type": "function", "function": {"name": "browser_fill", "description": "填表"}},
    ]


def test_render_tools_lists_all():
    text = render_tools(_tools())
    assert "browser_click:点击元素" in text
    assert "browser_navigate" in text


def test_render_tools_truncates():
    text = render_tools(_tools(), max_tools=2)
    assert "browser_click" in text
    assert "browser_fill" not in text
    assert "另有 1 个工具" in text


def test_render_tools_empty():
    assert "(无)" in render_tools([])


# ── 组装器 ────────────────────────────────────────────────────


def test_builder_assembles_all_layers():
    plan = StepPlan.from_spec(_spec())
    builder = PromptBuilder(_spec(), _tools(), context="业务背景:订单系统 {{x}}")
    out = builder.build(plan)
    # Base 层关键约束在
    assert "TEST_RESULT" in out
    assert "mark_step_done" in out
    assert "防循环" in out
    # 各层都在
    assert "业务背景" in out
    assert "测试任务" in out
    assert "可用工具" in out


def test_builder_refreshes_task_each_build():
    plan = StepPlan.from_spec(_spec())
    builder = PromptBuilder(_spec(), _tools())
    before = builder.build(plan)
    assert "[→] 1." in before
    plan.mark_done(1)
    after = builder.build(plan)
    assert "[x] 1." in after  # 进度反映到新 prompt


def test_base_prompt_has_safety_and_react():
    assert "安全边界" in BASE_PROMPT
    assert "INTENT" in BASE_PROMPT
