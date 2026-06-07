"""System Prompt 分层组装(规格 §5.4 System Prompt 分层,T-07)。

四层(顺序拼接):

1. **Base**:平台固定——角色 / ReAct 格式 / Behavioral Nudges 防循环 /
   TEST_RESULT 规范 / 安全边界。
2. **Context**:用户自定义业务知识,``{{变量}}`` 插值,优先级 Case > Suite > 全局。
3. **Task**:TestSpec(base_url / given / 用例级断言)+ StepPlan 进度清单(动态)。
4. **Tools**:可用工具清单(MCP + 自定义),相关度/数量截断。

Behavioral Nudges 对本地 LLM(Qwen3)尤其重要,显式约束「点击同一按钮 2 次就停下重析」
之类的防循环行为。

``PromptBuilder.build(step_plan)`` 的签名即 react_loop 的 ``SystemBuilder``,每轮调用
都会重算 Task 层以反映最新步骤状态。
"""

from __future__ import annotations

import re

from harness.step_plan import StepPlan
from input.models import TestSpec

# ── Base 层(平台固定) ────────────────────────────────────────────

BASE_PROMPT = """\
你是一个内网 Web 自动化测试执行 Agent。你的任务是严格按「执行计划」操作浏览器,
完成一条业务测试用例,并在过程中如实记录每一步。

【ReAct 工作方式】
- 每一轮:先用一句话写出本步的「意图」,格式 `INTENT: <你要做什么、为什么>`,然后调用恰好一个工具。
- **每一轮回复都必须以「恰好一个工具调用」结束**(`mark_step_done` 也是工具调用)。
  只输出文字、不调用任何工具 = 未推进,会被系统判定为卡死并打断。
  唯一例外:所有步骤都已完成时,才不调用工具、直接输出 `TEST_RESULT`。
- 完成某一步的页面操作后,**立刻在同一轮或下一轮调用 `mark_step_done`**,不要只用文字说"已完成"。
- 工具执行后系统会回灌 `[观察]`(含页面 A11y 信息 / URL)。基于观察再决定下一步。
- 不要臆测元素选择器;找不到元素时,先依据观察到的页面信息分析,再行动。

【防循环(务必遵守)】
- 如果你对同一元素执行了 2 次相同操作仍无进展,**停下**,重新分析当前页面,换一种定位或思路,不要机械重复。
- 一次只做一个明确动作。
- 完成执行计划中的某一步后,**必须**调用 `mark_step_done(step_no=该步编号)` 推进进度。

【结束与判定】
- 当所有步骤完成、你认为用例已执行完毕时,**不要再调用任何工具**,直接输出一行:
  `TEST_RESULT: PASS` 或 `TEST_RESULT: FAIL`。
- 注意:最终 PASS/FAIL 由系统的结构化断言裁决,你输出的结果仅供参考,不要臆断业务结果。

【安全边界】
- 只在被测系统范围内操作,禁止访问无关站点。
- 涉及删除 / 提交 / 支付 / 确认等高危操作时,确认它确实是当前测试步骤所要求的才执行。"""


# ── 工具函数 ────────────────────────────────────────────────────

_VAR_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def interpolate(template: str, variables: dict | None) -> str:
    """``{{var}}`` 插值。未提供的变量原样保留(不报错,利于本地容错)。"""
    if not template:
        return ""
    variables = variables or {}

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        return str(variables[key]) if key in variables else m.group(0)

    return _VAR_RE.sub(_sub, template)


def merge_context(
    *,
    global_ctx: str = "",
    suite_ctx: str = "",
    case_ctx: str = "",
    global_vars: dict | None = None,
    suite_vars: dict | None = None,
    case_vars: dict | None = None,
) -> str:
    """合并三级业务上下文并插值。

    变量优先级 Case > Suite > 全局(同名变量 Case 覆盖)。文本按 全局→Suite→Case
    顺序拼接(越靠后越具体,模型更易记住最后出现的最具体约束)。
    """
    merged_vars = {**(global_vars or {}), **(suite_vars or {}), **(case_vars or {})}
    parts = [
        interpolate(t, merged_vars) for t in (global_ctx, suite_ctx, case_ctx) if t and t.strip()
    ]
    return "\n\n".join(parts)


def render_task(spec: TestSpec, step_plan: StepPlan) -> str:
    """Task 层:TestSpec 概要 + StepPlan 进度清单。"""
    lines = ["## 测试任务", f"用例:{spec.name}(ID: {spec.case_id})", f"系统地址:{spec.base_url}"]
    if spec.given:
        lines.append("")
        lines.append("前置操作(given,已由系统准备或需先完成):")
        for g in spec.given:
            d = f"(数据: {g.data})" if g.data else ""
            lines.append(f"  - {g.action} → {g.target}{d}")
    lines.append("")
    lines.append(step_plan.to_prompt())
    if spec.assertions:
        lines.append("")
        lines.append("用例级最终断言(由系统在结束后自动验证,你无需手动判断):")
        for a in spec.assertions:
            exp = f" == {a.expected}" if a.expected is not None else ""
            lines.append(f"  - [{a.type}] {a.target}{exp}")
    return "\n".join(lines)


def render_tools(tools: list[dict], *, max_tools: int | None = None) -> str:
    """Tools 层:工具清单的可读描述。

    tools 为 LiteLLM function 格式;max_tools 为相关度截断后保留的上限(阶段一可空)。
    """
    if not tools:
        return "## 可用工具\n(无)"
    selected = tools[:max_tools] if max_tools else tools
    lines = ["## 可用工具"]
    for t in selected:
        fn = t.get("function", t)
        name = fn.get("name", "?")
        desc = (fn.get("description") or "").strip().replace("\n", " ")
        lines.append(f"- {name}:{desc}" if desc else f"- {name}")
    if max_tools and len(tools) > max_tools:
        lines.append(f"(另有 {len(tools) - max_tools} 个工具按相关度暂未列出)")
    return "\n".join(lines)


# ── 组装器 ──────────────────────────────────────────────────────


class PromptBuilder:
    """分层组装 System Prompt。``build(step_plan)`` 即 react_loop 的 SystemBuilder。"""

    def __init__(
        self,
        spec: TestSpec,
        tools: list[dict] | None = None,
        *,
        context: str = "",
        base: str = BASE_PROMPT,
        max_tools: int | None = None,
    ) -> None:
        self.spec = spec
        self.tools = tools or []
        self.context = context
        self.base = base
        self.max_tools = max_tools

    def build(self, step_plan: StepPlan) -> str:
        sections = [self.base]
        if self.context and self.context.strip():
            sections.append("## 业务背景\n" + self.context.strip())
        sections.append(render_task(self.spec, step_plan))
        sections.append(render_tools(self.tools, max_tools=self.max_tools))
        return "\n\n".join(sections)
