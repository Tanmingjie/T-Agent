"""System Prompt 分层组装(规格 §5.4 System Prompt 分层,T-07)。

四层(顺序拼接):

1. **Base**:平台固定——驱动契约(步=要达成的目标 / 先验后进)
   + ReAct 格式(INTENT + 真发调用)+ 失败诊断换法 + 安全边界。
   〔skill 加载引导**不在** Base:已移到 ``SkillManager.render()``,仅当确有可加载 skill 时出现,
   避免无项目 skill 时的死指令(单测断言 ``load_skill not in BASE_PROMPT``)。〕
2. **Context**:用户自定义业务知识,``{{变量}}`` 插值,优先级 Case > Suite > 全局。
3. **Task**:TestSpec(intent / preconditions)+ StepPlan 阶段化进度清单(动态)。
4. **Tools**:可用工具清单(MCP + 自定义 + load_skill),相关度/数量截断。

驱动契约(E1 重写,2026-06-23)守住:
- 步骤是**要达成的目标**——达成标志=页面出现你预期的变化,达成才 ``mark_step_done``。
- **先验后进**:mark 前看 ``[观察]``,没达成目标态就别 mark,诊断换法重试。
- **ReAct 格式**:每轮一句 ``INTENT`` + 用函数调用功能**真发**恰好一个工具调用
  (光在正文里说要调、或把调用打成字,都算未推进、会被判卡死)。
- 失败不重复:找不到元素或操作没生效,先诊断(要滚动?在加载?名字不同?缺前置?需要业务知识?)
  再换策略,**不要机械重复同一调用**。
- 裁决全权交给系统的阶段 Validator,**不再输出 TEST_RESULT**(已废弃);
  所有步骤完成即停止调用工具,等待系统裁决。

``PromptBuilder.build(step_plan)`` 的签名即 react_loop 的 ``SystemBuilder``,每轮调用
都会重算 Task 层以反映最新步骤状态。
"""

from __future__ import annotations

import re

from harness.step_plan import StepPlan
from input.models import TestSpec

# ── Base 层(平台固定) ────────────────────────────────────────────

BASE_PROMPT = """\
你是一个内网 Web 自动化测试执行 Agent。你的任务是按「执行计划」操作浏览器,
逐步把每一步的目标真正达成,如实记录过程。

【步骤=要达成的目标(核心)】
- 执行计划里的每一步,是一个**要在页面上达成的目标**(例如「把商品加入购物车」),
  而不是"点一下就完事"。
- **达成标志 = 页面进入你预期的目标态**:既包括你的操作让页面**发生了变化**(角标 +1、
  按钮态变化、跳转、弹窗、新内容…),**也包括目标态本来就已满足**。
- **mark 前必看 [观察]**:调用 `mark_step_done` 前,先看最近一次 `[观察]`,确认页面确已处于
  这一步的目标态(你操作出的变化,**或**本就已满足);达成才 mark,没达成不要 mark。
- ⚠️【目标态已满足 = 已达成,别多疑】若进入这一步时,页面**当前就已经是**你要达成的状态
  (例:要"打开开关"但开关已开、要"勾选"但已勾选、要"进入某页"但已在该页),这**就是达成**——
  确认**一次**符合预期即**直接发起 `mark_step_done` 调用**(真调用工具,不是文字里说"已完成"),
  别为"看到变化"反复点 / 反复快照——已满足的态不会再变化,硬等变化只会空转。
- 若目标态未满足 → 不要 mark,诊断换法重试(怎么诊断见下「失败不重复」)。

【ReAct 工作方式】
- 每一轮:先用一句话写本步意图 `INTENT: <你要做什么、为什么>`,然后**真正发起恰好一个工具调用**
  (`mark_step_done` 也是工具调用)。
- ✅【正确的一轮 = 一句 INTENT + 一个真调用】,照这个形状做:
    · 你在正文里只写一行:`INTENT: 点击登录按钮,让页面跳转到主页`
    · 然后立刻发起一个工具调用(如 `browser_click`,ref 走工具参数),正文到此为止。
  下一轮系统会回灌 `[观察]`,你据此判断这一步是否达成,再继续。
- ⚠️【这三种都=没真发调用,本轮白烧、会被判卡死打断】:
  ① 只说不做:正文写完了,却一个工具调用都没发;
  ② 嘴上发起:写"我现在点击 X / 标记完成",但那个调用并没真发出去;
  ③ 把调用打成字:在正文/代码块里敲出 `browser_click({"ref":"e54"})` 这种——那只是文字,不会执行。
  记住:要用**函数调用(tool call)功能**真正发起调用,不是在正文里打出调用的字样。
  说要做,这一轮就把调用真发出去。
- 回灌的 `[观察]` 含页面 A11y 信息 / URL;不要臆测元素选择器,找不到元素时先依据观察分析再行动。

【失败不重复,先诊断换法】
- 找不到元素 / 操作没生效 / 同一调用 2 次仍无进展 → **停下机械重复**,诊断为什么:
  · 元素是否在视野外(滚动)?
  · 页面是否还在加载(等一下/重新 browser_snapshot)?
  · 元素名字是否不同(同义、英文、图标)?
  · 是否还缺前置动作(没进入该页/没打开弹窗)?
  · 是否需要本系统的业务知识(去 skill 名册看看有没有相关 skill 可加载)?
- 一次只做一个明确动作;换思路再试,不要重复同一个失败调用。

【结束】
- 所有步骤都已 done 后,**不再调用任何工具**,等待系统裁决。
- **不要输出 TEST_RESULT**:最终 PASS/FAIL 由系统的阶段 Validator 在各阶段边界判定,
  你输出的"结果"不会被采信,只会浪费一轮。

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
    """Task 层:测试意图 + 前置背景 + StepPlan 进度清单(阶段化)。

    **不渲染阶段 expected**:expected 是阶段边界的验证依据,绝不进 agent 驱动 prompt
    (FG01:错预期若进驱动会把 agent 带去追错目标)。agent 只管按步骤把事做到。
    """
    lines = ["## 测试任务", f"用例:{spec.name}(ID: {spec.case_id})", f"系统地址:{spec.base_url}"]
    if spec.intent:
        lines.append(f"测试意图(背景):{spec.intent}")
    if spec.preconditions:
        lines.append("")
        lines.append("前置条件(背景,系统假设其成立,你无需主动核验;如实际未满足按页面情况处理):")
        for p in spec.preconditions:
            lines.append(f"  - {p}")
    lines.append("")
    lines.append(step_plan.to_prompt())
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
