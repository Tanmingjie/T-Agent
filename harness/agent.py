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
from harness.assertion import AssertionEngine, AssertionStatus
from harness.context import ContextCompactor
from harness.healing import HealingSubagent
from harness.hooks import (
    AFTER_CASE,
    BEFORE_CASE,
    ON_FAILURE,
    ON_HEAL,
    ExecutionContext,
    HookManager,
)
from harness.llm import LLMClient
from harness.page_probe import MCPPageProbe, parse_snapshot
from harness.permission import PermissionChecker
from harness.precondition import (
    ACTION_STEP,
    AMBIGUOUS,
    STATE_HOOK,
    PreconditionClassifier,
    needs_confirmation,
    to_given_steps,
)
from harness.prompt import PromptBuilder
from harness.react_loop import ReActLoop, ToolExecutor, ToolOutcome
from harness.recorder import Recorder
from harness.skills import SkillManager
from harness.step_plan import StepPlan
from harness.tools import ToolRegistry
from input.models import (
    Assertion,
    ExecutionRecord,
    PreconditionItem,
    SpecStep,
    TestCase,
    TestSpec,
)
from intelligence.pre_analysis import SpecGenerator
from intelligence.scanner import Scanner
from intelligence.vocabulary import enhance_targets

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

# 词汇表增量扫描(策略C)开关:默认开,env VOCAB_SCAN=0 关闭。
# 执行结束后复用执行期已捕获的 a11y 快照(无需额外开浏览器),独立 context 提炼并库。
_INCREMENTAL_SCAN = os.getenv("VOCAB_SCAN", "1") != "0"

# 生成代码落盘目录(与 api/routers/results.py 的 GENERATED_ROOT 一致)
_GENERATED_ROOT = "storage/generated"

# 预置条件「状态声明」关键词 → Hook 名 的默认映射(用户可在构造时覆盖)。
# 命中即把该状态声明标为对应 Hook 负责(如「已登录」→ LoginHook,由 before_case 保证)。
DEFAULT_HOOK_MAP = {
    "已登录": "LoginHook",
    "登录系统": "LoginHook",
    "登陆": "LoginHook",
    "登录": "LoginHook",
}


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


def ensure_navigation_step(spec: TestSpec) -> TestSpec:
    """codegen 前置:若 spec 无导航步但有 base_url,**注入隐式首步导航**。

    很多用例把"打开页面"写在预置条件(被分类为 state_hook,不进 steps),或干脆默认浏览器
    已在目标页。这样生成的 pytest-bdd 代码会缺 `page.goto`,**回放时根本不打开页面**而失败。
    这里在生成前补一个 navigate 步,使产物可独立回放。仅用于 codegen 输入,不影响执行。
    """
    if not spec.base_url:
        return spec
    if any(s.action == "navigate" for s in spec.steps):
        return spec
    nav = SpecStep(action="navigate", target=spec.base_url)
    return spec.model_copy(update={"steps": [nav, *spec.steps]})


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
        precondition_classifier: PreconditionClassifier | None = None,
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
        # 预置条件三分类器:默认自带一个(始终接通,不靠调用方手动注入)。
        # 传 False 可显式关闭(纯翻译,不分类);传实例可自定义 hook_map/阈值。
        if precondition_classifier is None:
            self.precondition_classifier = PreconditionClassifier(llm, hook_map=DEFAULT_HOOK_MAP)
        elif precondition_classifier is False:
            self.precondition_classifier = None
        else:
            self.precondition_classifier = precondition_classifier
        self.hooks = hooks
        self.skills = skills
        self.permission = permission
        self.tools_registry = tools_registry
        self.step_callback = step_callback
        self.vocab_resolver = vocab_resolver
        # 最近一次分类结果(供 run() 写入 ctx / 日志;generate_spec 与 run 解耦时复用)
        self._last_precondition_items: list[PreconditionItem] = []

    async def generate_spec(self, case: TestCase) -> TestSpec:
        """生成 TestSpec(供 CLI 先打印给用户审查)。

        含预置条件三分类(规格 §5.1/§5.2):先分类 → 把分类结果下发给翻译器
        (引导 given 只收 action_step)→ 再**确定性**地把 action_step 合入 given,
        避免 LLM 漏放。state_hook / ambiguous 记到 ``_last_precondition_items``。
        """
        items = await self._classify_preconditions(case)
        spec = await self.spec_generator.generate(case, precondition_items=items or None)
        if items:
            spec = self._merge_given_from_preconditions(spec, items)
        return spec

    async def _classify_preconditions(self, case: TestCase) -> list[PreconditionItem]:
        """对 case.preconditions 做三分类;无分类器/无预置条件时返回空列表。"""
        self._last_precondition_items = []
        if self.precondition_classifier is None or not case.preconditions:
            return []
        # 复用已确认的历史分类(规格 §3.2「首次确认后记忆,下次跳过」):把用例里
        # confirmed_by_user 的条目灌进分类器 memory,classify 命中即跳过 LLM、且用户选择优先。
        for it in case.precondition_items:
            if it.confirmed_by_user and it.text:
                self.precondition_classifier.memory[it.text] = it
        try:
            items = await self.precondition_classifier.classify(case.preconditions)
        except Exception as e:  # noqa: BLE001 — 分类失败不阻断翻译,降级为不分类
            logger.warning("预置条件分类失败(%s):%s,降级为不分类", case.id, e)
            return []
        self._last_precondition_items = items
        # 回写到用例对象:执行链/ API 层据此落库,前端可标黄展示并确认(闭环)。
        case.precondition_items = items
        for it in items:
            if it.type == STATE_HOOK:
                logger.info("预置条件[状态声明]→ %s 负责:%s", it.hook_ref, it.text)
        pending = needs_confirmation(items)
        if pending:
            logger.warning(
                "用例 %s 有 %d 条模糊预置条件需用户确认:%s",
                case.id,
                len(pending),
                "; ".join(p.text for p in pending),
            )
        return items

    @staticmethod
    def _merge_given_from_preconditions(spec: TestSpec, items: list[PreconditionItem]) -> TestSpec:
        """把 action_step 类预置条件确定性合入 spec.given(按 target 去重,放在最前)。

        LLM 已被引导把 action_step 放进 given,这里兜底补齐 LLM 漏放的,避免前置操作丢失。
        """
        derived = to_given_steps(items)
        if not derived:
            return spec
        existing_targets = {g.target for g in spec.given}
        missing = [g for g in derived if g.target not in existing_targets]
        if not missing:
            return spec
        return spec.model_copy(update={"given": [*missing, *spec.given]})

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
                        "prompt": step.prompt,  # 本轮请求,供执行中「查看 prompt」
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
            # 词汇表增强(规格 §5.2):用页面真实文案改写模糊业务词("提交"→"保存并提交")。
            # 仅当本次自动生成 spec 时增强;调用方显式传入(如 CLI 审查后)的 spec 不动。
            spec = await self._enhance_spec_with_vocab(spec, case)

        # 预置条件分类结果入 ctx:state_hook 要求的 Hook 名供 before_case 侧参考(P2)。
        if self._last_precondition_items:
            required_hooks = sorted(
                {
                    it.hook_ref
                    for it in self._last_precondition_items
                    if it.type == STATE_HOOK and it.hook_ref
                }
            )
            ctx.set("required_hooks", required_hooks)
            ctx.set(
                "ambiguous_preconditions",
                [it.text for it in needs_confirmation(self._last_precondition_items)],
            )

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

        async def get_screenshot() -> str | None:
            """取当前页面截图(base64 PNG),供操作侧视觉自愈双通道。失败返回 None。"""
            import base64

            try:
                result = await self.mcp.call_tool("browser_take_screenshot", {})
                img = self.mcp.result_to_image_bytes(result)
            except Exception as e:  # noqa: BLE001 — 截图失败不影响自愈兜底
                logger.warning("操作侧视觉自愈取截图失败:%s", e)
                return None
            return base64.b64encode(img).decode("ascii") if img else None

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
            get_screenshot=get_screenshot,
            compactor=ContextCompactor(),
            capture_screenshot=capture_screenshot,
            on_step=emit_step,  # 每步落定即时推送(实时进度)
            vocab_resolver=self.vocab_resolver,  # 操作侧自愈词汇表优先
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
        # 数据断言(custom_tool)经 ToolRegistry 取业务真值;未注入则该类断言 skipped
        engine = AssertionEngine(probe, healer=healer, tool_registry=self.tools_registry)
        # 聚合用例级 + 步骤级 expect 断言,避免 LLM 把断言放在 step.expect 时被漏验
        a_results = await engine.verify_all(collect_assertions(spec))
        recorder.set_case_assertions([r.to_dict() for r in a_results])
        recorder.record.heal_count += sum(1 for r in a_results if r.healed)
        passed = AssertionEngine.verdict(a_results)

        # 断言逐条记账(便于失败定位):全部走 DEBUG,失败/跳过额外 WARNING 抬到默认可见。
        for r in a_results:
            logger.debug(
                "断言 [%s] %s target=%r expected=%r actual=%r %s",
                r.status.value.upper(),
                r.assertion.type,
                r.assertion.target,
                r.assertion.expected,
                r.actual,
                f"reason={r.reason}" if r.reason else "",
            )
            if r.status != AssertionStatus.PASS:
                logger.warning(
                    "断言未通过 [%s] %s target=%r expected=%r actual=%r reason=%s",
                    r.status.value.upper(),
                    r.assertion.type,
                    r.assertion.target,
                    r.assertion.expected,
                    r.actual,
                    r.reason or "(无)",
                )

        # 收尾 Hooks:发生自愈触发 on_heal;失败触发 on_failure;after_case 无论成败都跑。
        if self.hooks is not None:
            ctx.set("passed", passed)
            # 自愈可观测(规格 §7.7):聚合断言侧(重定位后复验通过)+ 操作侧(工具报错
            # 后重定位重试)两路自愈;任一发生即触发 on_heal,详情入 ctx 供 hook 消费。
            healed_assertions = [r for r in a_results if r.healed]
            action_heals = [h for s in result.action_steps for h in s.heal_attempts]
            if healed_assertions or action_heals:
                ctx.set("heal_count", len(healed_assertions) + len(action_heals))
                ctx.set("healed_assertions", [r.to_dict() for r in healed_assertions])
                ctx.set("action_heals", action_heals)
                logger.info(
                    "用例 %s 发生自愈:断言侧 %d 次,操作侧 %d 次",
                    case.id,
                    len(healed_assertions),
                    len(action_heals),
                )
                await self.hooks.run(ON_HEAL, ctx)
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
                # 无导航步则注入隐式首步导航,使生成的测试可独立回放(否则缺 page.goto)
                gen_spec = ensure_navigation_step(gen_spec)
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

        # —— 词汇表增量扫描(策略C):执行结束后复用已见快照提炼业务词→元素映射并库 ——
        await self._incremental_scan(result, emit_phase)

        logger.info(
            "用例 %s 执行完毕:%s(LLM 自报=%s,停因=%s)",
            case.id,
            "PASS" if passed else "FAIL",
            result.llm_result,
            result.stop_reason.value,
        )
        return record

    async def _enhance_spec_with_vocab(self, spec: TestSpec, case: TestCase) -> TestSpec:
        """翻译期词汇表增强(规格 §5.2):按 base_url 命中的页面词汇表,把 spec 里精确等于
        某业务词的 target 改写成页面真实文案。保守(仅精确键匹配)、best-effort。"""
        if self.vocab_resolver is None:
            return spec
        manager = getattr(self.vocab_resolver, "manager", None)
        if manager is None:
            return spec
        url = case.base_url or spec.base_url
        if not url:
            return spec
        login_role = getattr(self.vocab_resolver, "login_role", "") or ""
        try:
            page = await manager.find_page(url, "", login_role)
        except Exception as e:  # noqa: BLE001
            logger.warning("翻译期查词汇表失败:%s", e)
            return spec
        if page is None or page.stale:
            return spec
        mapping = {
            term: str(e["name"])
            for term, e in page.vocabulary.items()
            if isinstance(e, dict) and e.get("name")
        }
        if not mapping:
            return spec
        return enhance_targets(spec, mapping)

    async def _incremental_scan(self, result, emit_phase) -> None:
        """执行期增量扫描(策略C):复用 ReAct 期间已捕获的 a11y 快照,独立 context 提炼
        「业务词 → UI 元素」映射并入词汇表(手动条目优先,不被覆盖)。

        不额外开浏览器、不污染主 Agent;best-effort,失败只告警不影响用例结果。
        仅当注入了带 VocabularyManager 的 resolver(即真正接了词汇表持久化)时才扫描。
        """
        if not _INCREMENTAL_SCAN or self.vocab_resolver is None:
            return
        manager = getattr(self.vocab_resolver, "manager", None)
        if manager is None:
            return

        # 收集执行期见过的页面快照(tool_result 里含 a11y ref 即为快照),按 URL 去重取最丰富一份
        by_url: dict[str, str] = {}
        for s in result.action_steps:
            txt = s.tool_result or ""
            if "[ref=" not in txt:
                continue
            url = s.url or ""
            if len(txt) > len(by_url.get(url, "")):
                by_url[url] = txt
        if not by_url:
            return

        login_role = getattr(self.vocab_resolver, "login_role", "") or ""
        scanner = Scanner(self.llm)
        phase_emitted = False
        for snapshot_text in by_url.values():
            snap = parse_snapshot(snapshot_text)
            # 去重:该页已有「非 stale」词汇表(含手动维护)就跳过,免同界面多用例重复提炼。
            # 页面变更时自愈失败会 mark_stale → 下次重扫;新页面照常扫(自我纠正)。
            existing = await manager.find_page(snap.url, snap.title, login_role)
            if existing is not None and not existing.stale:
                continue
            if not phase_emitted:  # 真要扫了才发阶段事件(无新页面则不显示 scanning)
                await emit_phase("scanning", "提炼页面词汇表")
                phase_emitted = True
            try:
                await scanner.scan_and_save(snapshot_text, login_role=login_role, manager=manager)
            except Exception as e:  # noqa: BLE001 — 扫描失败不影响用例结果
                logger.warning("词汇表增量扫描失败:%s", e)

    def _token_usage(self) -> int:
        fn = getattr(self.llm, "usage_summary", None)
        if callable(fn):
            try:
                return fn().total_tokens
            except Exception:  # noqa: BLE001
                return 0
        return 0
