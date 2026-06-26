"""System Prompt 分层组装(规格 §5.4 System Prompt 分层,T-07)。

四层(顺序拼接):

1. **Base**:平台固定——驱动契约(步=要达成的目标 / 先验后进 / 主动加载 skill)
   + ReAct 格式 + 防循环 + 安全边界。
2. **Context**:用户自定义业务知识,``{{变量}}`` 插值,优先级 Case > Suite > 全局。
3. **Task**:TestSpec(intent / preconditions)+ StepPlan 阶段化进度清单(动态)。
4. **Tools**:可用工具清单(MCP + 自定义 + load_skill),相关度/数量截断。

驱动契约(E1 重写,2026-06-23)守住:
- 步骤是**要达成的目标**——达成标志=页面出现你预期的变化,达成才 ``mark_step_done``。
- **先验后进**:mark 前看 ``[观察]``,没出现预期变化就别 mark,诊断换法重试。
- **动手前主动加载 skill**:若常驻 skill 清单(``load_skill`` 工具的可加载名册)
  里有相关业务/操作知识,**先 ``load_skill`` 再动手**,不要等失败再加载。
- 失败不重复:找不到元素或操作没生效,先诊断(没加载?要滚动?在弹窗?名字不同?需要业务知识?)
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
- ⚠️【目标态已满足 = 已达成,别多疑】若进入这一步时,页面**当前就已经是**你要达成的状态
  (例:要"打开开关"但开关已开、要"勾选"但已勾选、要"进入某页"但已在该页),这**就是达成**——
  **直接发起 `mark_step_done` 调用**(是真的调用工具,不是只在文字里说"已完成"),不要为了
  "看到变化"反复点来点去 / 反复快照确认。已满足的态不会再"变化",硬等变化只会空转。
  确认**一次**当前态符合预期即可推进。
- **达成才能 mark_step_done**;没有达成不要 mark,继续诊断换法重试。

【先验后进:mark 前必看 [观察]】
- 调用 `mark_step_done` 前,先看最近一次 `[观察]`:页面是否已处于你这一步的目标态
  (你的操作带来了预期变化,**或**目标态本来就已满足)?
- 若目标态已满足(无论是你刚操作出来的、还是进入这步时就已是)→ 调用
  `mark_step_done(step_no=本步编号)` 推进。**别因为"没看到变化"就反复确认**——已满足的态不会变化。
- 若目标态未满足 → 这一步还没成,**不要 mark**;分析为什么(没加载完?要滚动?在弹窗里?名字不同?
  少了前置动作?需要业务知识?),换策略重试。

【ReAct 工作方式】
- 每一轮:先用一句话写本步意图 `INTENT: <你要做什么、为什么>`,然后**真正发起恰好一个工具调用**
  (`mark_step_done` 也是工具调用)。
- ⚠️【想好就发起,别只是"说"】下面三种都算"未推进",会被系统判定卡死打断、白烧一轮:
  ① 只输出文字、不调用任何工具;
  ② 在文字里说"我要调用 X / 现在点击 Y""目标态已达成、标记完成"之类,**却没真发起**那个调用;
  ③ 把调用写成正文文本或代码块(如打字打出 `mark_step_done(step_no=1)` / `browser_click({"ref":"e54"})`)
     ——那只是文字、不会被执行。
  工具调用要**走工具接口发起**,不是写在回复正文里;说要做就当轮把它真发出去。
- 工具执行后系统会回灌 `[观察]`(含页面 A11y 信息 / URL)。基于观察再决定下一步。
- 不要臆测元素选择器;找不到元素时,先依据观察分析,再行动。

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
