"""TestCase Subagent(规格 §5.4 Subagent 隔离,T-10)。

用例级隔离:每条用例 = 独立 async 协程 + 独立 messages list(由 ReActLoop 内部维护),
用例 A 的失败上下文不污染用例 B。Suite 批量调度(orchestrator)留到阶段二 T-18,
本模块只负责「执行一条用例」这一隔离单元。

串联:TestSpec 生成(T-05)→ StepPlan(T-04)→ Prompt 分层(T-07)→ MCP 工具(T-02)
→ ReAct 循环(T-06)→ A11y PageProbe + 断言引擎(T-08/T-10)→ 录制(T-09)。

**最终 PASS/FAIL 由断言引擎裁决,不取 LLM 自报结果**(规格 §0 原则)。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from codegen.bdd import BDDGenerator
from codegen.locators import locators_from_steps, resolve_locators
from harness.assertion import AssertionEngine
from harness.context import ContextCompactor
from harness.healing import HealingSubagent
from harness.hooks import AFTER_CASE, BEFORE_CASE, ON_FAILURE, ExecutionContext, HookManager
from harness.llm import LLMClient
from harness.page_probe import MCPPageProbe, parse_snapshot
from harness.permission import PermissionChecker
from harness.prompt import PromptBuilder
from harness.react_loop import ReActLoop, ToolExecutor, ToolOutcome
from harness.recorder import Recorder
from harness.skills import SkillManager
from harness.step_plan import StepPlan
from harness.tools import ToolRegistry
from input.models import Assertion, ExecutionRecord, TestCase, TestSpec
from intelligence.pre_analysis import SpecGenerator

logger = logging.getLogger(__name__)

# playwright-mcp 工具使用提示(阶段一以 Context 形式注入;阶段二归入 ToolSkill)
PLAYWRIGHT_MCP_HINT = """\
浏览器操作通过 playwright-mcp 工具完成,关键用法:
- 先用 `browser_navigate` 打开目标页面;之后用 `browser_snapshot` 获取 A11y 快照,快照里每个可操作元素都带一个 ref(形如 e11)。
- 操作元素时(`browser_click` / `browser_type` / `browser_select_option` / `browser_hover`)必须传两个参数:
  · `element`:人类可读的元素描述(如 "用户名输入框");
  · `ref`:从最近一次快照里复制的那个 ref 值(如 "e11")。
  ⚠️ ref 是 playwright-mcp 的专用引用,**不是 CSS 选择器**,不要写成 ref=e11 这种形式塞进选择器字段,直接把 e11 作为 ref 参数传。
- 页面发生跳转/变化后,ref 会失效,需要重新 `browser_snapshot` 再操作。
- `browser_type` 填完输入框、要触发提交时可带 submit=true,或之后单独 click 提交按钮。
- 需要等待文本出现/消失用 `browser_wait_for`。

【务必完成所有步骤】执行计划里的每一步都要真正做完并各自调用 mark_step_done,
**不要在中途停止**;只有当所有步骤都完成后,才输出最终的 TEST_RESULT。"""

# StepPlan 控制工具的名字(执行器据此路由)
_CONTROL_TOOLS = {"mark_step_done"}

# 不该被截图的浏览器工具(快照/截图自身)
_NO_SHOT_TOOLS = {"browser_snapshot", "browser_take_screenshot"}
# 截图开关:默认开,env MCP_SCREENSHOT=0 关闭
_CAPTURE_SCREENSHOTS = os.getenv("MCP_SCREENSHOT", "1") != "0"

# 生成代码落盘目录(与 api/routers/results.py 的 GENERATED_ROOT 一致)
_GENERATED_ROOT = "storage/generated"


def _step_keywords(step_plan: StepPlan) -> list[str]:
    """当前步骤的关键词,供 ToolSkill 相关度过滤。"""
    cur = step_plan.current
    if cur is None:
        return []
    kws = [cur.target, cur.action]
    if cur.data:
        kws.append(cur.data)
    kws += [w for w in cur.target.replace("(", " ").replace(")", " ").split() if w]
    return [k for k in kws if k]


def collect_assertions(spec: TestSpec) -> list[Assertion]:
    """聚合一个 TestSpec 里的全部断言:用例级 + given/steps 的步骤级 expect。

    LLM 生成 TestSpec 时把断言放在用例级 assertions 还是步骤级 expect 并不稳定,
    聚合后统一验证,确保断言不被静默忽略(否则会出现"无断言→判 FAIL 且无明细")。
    """
    out: list[Assertion] = list(spec.assertions)
    for step in list(spec.given) + list(spec.steps):
        out.extend(step.expect)
    # 去重:LLM 常把同一断言既放用例级又放某步 expect,聚合后会重复计入,
    # 导致裁决里出现两条一模一样的结果(见真实跑 TC101)。按语义键去重,保序。
    seen: set[tuple] = set()
    deduped: list[Assertion] = []
    for a in out:
        key = (a.type, a.target, a.expected, a.selector)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(a)
    return deduped


def make_executor(step_plan: StepPlan, mcp) -> ToolExecutor:
    """构造 ReAct 执行器:控制工具走 StepPlan,其余走 MCP。"""

    async def execute(name: str, arguments: dict) -> ToolOutcome:
        # 1) StepPlan 控制工具(mark_step_done 等)
        handled = step_plan.apply_tool_call(name, arguments)
        if handled is not None:
            return ToolOutcome(text=handled)
        # 2) 其余 → playwright-mcp
        result = await mcp.call_tool(name, arguments)
        text = mcp.result_to_text(result)
        url = parse_snapshot(text).url  # 结果里若含 Page URL 则提取
        return ToolOutcome(text=text, url=url)

    return execute


class TestCaseAgent:
    """执行单条用例的隔离 Agent。"""

    __test__ = False  # 名字以 Test 开头,告知 pytest 这不是测试类

    def __init__(
        self,
        llm: LLMClient,
        mcp,
        *,
        context: str = "",
        max_steps: int = 30,
        spec_generator: SpecGenerator | None = None,
        hooks: HookManager | None = None,
        skills: SkillManager | None = None,
        permission: PermissionChecker | None = None,
        tools_registry: ToolRegistry | None = None,
        step_callback: Callable[[str, dict], Coroutine] | None = None,
        vocab_resolver=None,
    ) -> None:
        self.llm = llm
        self.mcp = mcp
        self.context = context
        self.max_steps = max_steps
        self.spec_generator = spec_generator or SpecGenerator(llm)
        self.hooks = hooks
        self.skills = skills
        self.permission = permission
        self.tools_registry = tools_registry
        self.step_callback = step_callback
        self.vocab_resolver = vocab_resolver

    async def generate_spec(self, case: TestCase) -> TestSpec:
        """仅生成 TestSpec(供 CLI 先打印给用户审查)。"""
        return await self.spec_generator.generate(case)

    async def run(
        self,
        case: TestCase,
        spec: TestSpec | None = None,
        ctx: ExecutionContext | None = None,
        step_callback=None,
        run_id: str | None = None,
    ) -> ExecutionRecord:
        """执行一条用例,返回 ExecutionRecord(PASS/FAIL 由断言裁决)。"""
        ctx = ctx or ExecutionContext(case=case)
        recorder = Recorder(case.id, suite_id=case.suite_id, run_id=run_id)

        # 实时进度回调(SSE):阶段 + 逐步,执行期即时推送,而非整条用例跑完才补发
        cb = step_callback or self.step_callback

        async def emit_phase(phase: str, label: str) -> None:
            if cb is None:
                return
            try:
                await cb("phase", {"case_id": case.id, "phase": phase, "label": label})
            except Exception:  # noqa: BLE001 — 推送失败不影响执行
                pass

        async def emit_spec(spec: TestSpec) -> None:
            # 翻译完成即把 TestSpec 推给前端,执行中点「用例信息」也能看到执行规格
            # (此前 spec 只随结果落库,执行期抽屉不拉结果故为空)。
            if cb is None:
                return
            try:
                await cb("spec_ready", {"case_id": case.id, "spec": spec.model_dump(mode="json")})
            except Exception:  # noqa: BLE001
                pass

        async def emit_step(step) -> None:
            if cb is None:
                return
            desc = (
                f"{step.tool_name}({', '.join(f'{k}={v}' for k, v in step.tool_input.items())})"
                if step.tool_input
                else step.tool_name
            )
            try:
                await cb(
                    "step_change",
                    {
                        "case_id": case.id,
                        "step_index": step.step_no,
                        "status": "done",
                        "description": desc,
                        # 真实截图文件名(None=该步未截图,如快照/失败步)→ 前端据此判断有无图,
                        # 不再一律假设有图(否则失败/重试步会去取不存在的 step_NNN.png 报 404)
                        "screenshot": step.screenshot,
                    },
                )
            except Exception:  # noqa: BLE001
                pass

        # before_case Hooks:失败则用例直接 FAIL,不进 Agent(规格 §5.4)
        if self.hooks is not None:
            bc = await self.hooks.run(BEFORE_CASE, ctx)
            if not bc.ok:
                record = recorder.finalize(
                    passed=False,
                    final_result=f"[FAIL] before_case 失败:{bc.error}(hook={bc.failed_hook}),未进入执行。",
                )
                await self.hooks.run(ON_FAILURE, ctx)
                await self.hooks.run(AFTER_CASE, ctx)
                return record

        await emit_phase("spec", "翻译用例为执行规格 (TestSpec)")
        if spec is None:
            spec = await self.generate_spec(case)

        recorder.set_spec(spec)  # 存档翻译产物,供前端可视化
        await emit_spec(spec)  # 实时推送给抽屉(执行中也能看执行规格)
        plan = StepPlan.from_spec(spec)

        # 工具集 = MCP 工具 + mark_step_done 控制工具 + 自定义工具(LLM 按需调用)
        tools = list(self.mcp.to_litellm_tools()) + [StepPlan.tool_schema()]
        if self.tools_registry is not None:
            tools += self.tools_registry.to_litellm_tools()

        prompt_ctx = "\n\n".join(p for p in (self.context, PLAYWRIGHT_MCP_HINT) if p)
        builder = PromptBuilder(spec, tools, context=prompt_ctx)
        healer = HealingSubagent(self.llm)

        # 当前 URL 状态(供 PageSkill 动态加载):初始为 base_url,执行器随观察更新
        state = {"url": case.base_url or spec.base_url}
        base_executor = make_executor(plan, self.mcp)

        async def executor(name: str, arguments: dict) -> ToolOutcome:
            # Permission(Reason 后 Act 前):非控制工具的高危操作需放行
            if self.permission is not None and name not in _CONTROL_TOOLS:
                allowed = await self.permission.check(name, arguments, url=state["url"])
                if not allowed:
                    return ToolOutcome(
                        text=f"[权限被拒] 工具 {name} 命中安全策略,未执行。如确需执行请人工确认。"
                    )
            # 自定义工具(LLM 按需调用)→ 注册表;不走 MCP
            if self.tools_registry is not None and self.tools_registry.has(name):
                text = await self.tools_registry.call(name, arguments)
                return ToolOutcome(text=text, is_custom_tool=True)
            outcome = await base_executor(name, arguments)
            if outcome.url:
                state["url"] = outcome.url
            return outcome

        def build_system(step_plan: StepPlan) -> str:
            text = builder.build(step_plan)
            if self.skills is not None:
                skill_text = self.skills.render(
                    url=state["url"], keywords=_step_keywords(step_plan)
                )
                if skill_text:
                    text = f"{text}\n\n{skill_text}"
            return text

        async def get_snapshot() -> str:
            """取当前页面快照文本,供操作侧自愈重定位。"""
            result = await self.mcp.call_tool("browser_snapshot", {})
            return self.mcp.result_to_text(result)

        async def capture_screenshot(step_no: int, tool_name: str) -> str | None:
            """浏览器动作后抓当前页面落盘成 step_NNN.png(控制/自定义/非浏览器工具跳过)。"""
            if not _CAPTURE_SCREENSHOTS:
                return None
            if tool_name in _CONTROL_TOOLS or tool_name in _NO_SHOT_TOOLS:
                return None
            if self.tools_registry is not None and self.tools_registry.has(tool_name):
                return None
            if not tool_name.startswith("browser_"):
                return None
            result = await self.mcp.call_tool("browser_take_screenshot", {})
            img = self.mcp.result_to_image_bytes(result)
            if not img:
                return None
            path = recorder.screenshot_path(step_no)  # 确保目录存在
            Path(path).write_bytes(img)
            return f"step_{step_no:03d}.png"

        loop = ReActLoop(
            self.llm,
            tools=tools,
            execute=executor,
            step_plan=plan,
            build_system=build_system,
            max_steps=self.max_steps,
            healer=healer,
            get_snapshot=get_snapshot,
            compactor=ContextCompactor(),
            capture_screenshot=capture_screenshot,
            on_step=emit_step,  # 每步落定即时推送(实时进度)
        )
        await emit_phase("executing", "驱动浏览器逐步执行")
        result = await loop.run()
        recorder.extend_steps(result.action_steps)
        recorder.set_token_usage(self._token_usage())
        recorder.set_stop_reason(f"{result.stop_reason.value}/iter={result.iterations}")

        # —— 断言裁决(确定性,非 LLM 眼判;目标找不到时自愈重定位) ——
        await emit_phase("asserting", "结构化断言裁决")
        probe = MCPPageProbe(self.mcp, resolver=self.vocab_resolver)
        await probe.refresh()  # 用例结束后抓一次终态快照
        engine = AssertionEngine(probe, healer=healer)
        # 聚合用例级 + 步骤级 expect 断言,避免 LLM 把断言放在 step.expect 时被漏验
        a_results = await engine.verify_all(collect_assertions(spec))
        recorder.set_case_assertions([r.to_dict() for r in a_results])
        recorder.record.heal_count += sum(1 for r in a_results if r.healed)
        passed = AssertionEngine.verdict(a_results)

        # 收尾 Hooks:失败触发 on_failure;after_case 无论成败都跑(清理/登出)
        if self.hooks is not None:
            ctx.set("passed", passed)
            if not passed:
                await self.hooks.run(ON_FAILURE, ctx)
            await self.hooks.run(AFTER_CASE, ctx)

        record = recorder.finalize(passed=passed)

        # —— 代码生成(规格 §5.6):仅执行通过后产出 pytest-bdd 代码 ——
        if passed:
            await emit_phase("codegen", "生成测试代码")
            try:
                # 把断言聚合进 spec(LLM 常放 step.expect),否则生成的 Then 段为空
                gen_spec = spec.model_copy(update={"assertions": collect_assertions(spec)})
                # 解析层(框架无关):语义 target → 稳健 Locator(词汇表 role+name 优先)
                targets = [s.target for s in gen_spec.steps] + [
                    a.target for a in gen_spec.assertions
                ]
                locators = await resolve_locators(targets, self.vocab_resolver, url=state["url"])
                # 执行期捕获的真实 role+name 优先级最高,覆盖词汇表解析结果
                locators = {**locators, **locators_from_steps(record.steps)}
                # black.format_str + ast.parse 是同步 CPU,挪线程避免占用事件循环(收尾不卡)
                gen = await asyncio.to_thread(
                    BDDGenerator().generate, gen_spec, record, locators=locators
                )
                record.generated_code = f"{gen.feature}\n\n{gen.step_defs}"
                gen.write(_GENERATED_ROOT)  # storage/generated/(供下载)
            except Exception as e:  # noqa: BLE001 — 代码生成失败不影响用例结果
                logger.warning("用例 %s 代码生成失败:%s", case.id, e)

        logger.info(
            "用例 %s 执行完毕:%s(LLM 自报=%s,停因=%s)",
            case.id,
            "PASS" if passed else "FAIL",
            result.llm_result,
            result.stop_reason.value,
        )
        return record

    def _token_usage(self) -> int:
        fn = getattr(self.llm, "usage_summary", None)
        if callable(fn):
            try:
                return fn().total_tokens
            except Exception:  # noqa: BLE001
                return 0
        return 0
