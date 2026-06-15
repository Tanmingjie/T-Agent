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
import time
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
from harness.skills import LOAD_SKILL_TOOL, SkillManager
from harness.step_plan import StepPlan, StepStatus
from harness.tools import ToolRegistry
from input.models import (
    Assertion,
    ExecutionRecord,
    PageVocabulary,
    PreconditionItem,
    SpecStep,
    TestCase,
    TestSpec,
)
from intelligence.pre_analysis import SpecGenerator
from intelligence.scanner import Scanner, url_scope
from intelligence.vocabulary import enhance_targets
from storage.artifacts import get_artifact_store

logger = logging.getLogger(__name__)

# playwright-mcp 工具使用提示(以 Context 形式注入 System Prompt)
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

# 控制工具的名字(执行器据此路由,permission/截图据此跳过):StepPlan 推进 + Skill 渐进加载
_CONTROL_TOOLS = {"mark_step_done", LOAD_SKILL_TOOL}

# 不该被截图的浏览器工具(快照/截图自身)
_NO_SHOT_TOOLS = {"browser_snapshot", "browser_take_screenshot"}
# 截图开关:默认开,env MCP_SCREENSHOT=0 关闭
_CAPTURE_SCREENSHOTS = os.getenv("MCP_SCREENSHOT", "1") != "0"

# 导航/加载类动作:执行后页面可能在跳转或异步加载,需等稳定再抓快照/截图,
# 否则会抓到空白 loading 态(如"点登录→页面加载中→快照无 ref→后续步骤卡住")。
_NAV_TOOLS = {
    "browser_click",
    "browser_navigate",
    "browser_navigate_back",
    "browser_navigate_forward",
    "browser_press_key",
    "browser_file_upload",
}
# 页面稳定等待(settle)开关与参数。默认开;env MCP_SETTLE=0 关闭。
# 机制:导航类动作后轮询 a11y 快照,ref 节点数 >0 且连续两次不变即认为稳定(或超时)。
_SETTLE_ENABLED = os.getenv("MCP_SETTLE", "1") != "0"
_SETTLE_TIMEOUT = float(os.getenv("MCP_SETTLE_TIMEOUT_MS", "8000")) / 1000.0
_SETTLE_INTERVAL = float(os.getenv("MCP_SETTLE_INTERVAL_MS", "400")) / 1000.0

# 词汇表增量扫描(策略C)开关:**默认关**(2026-06-10),env VOCAB_SCAN=1 开启。
# 主动扫描(/vocabulary/scan,会导航的探索式扫描)是词汇表主入口;执行期增量扫描降级为
# 可选补充——避免它延后用例收尾(交互执行末尾多一次 LLM 往返)、避免与主动扫描两写入源。
_INCREMENTAL_SCAN = os.getenv("VOCAB_SCAN", "0") != "0"

# 生成代码落盘目录:从 ArtifactStore 抽象取(T-P10),与 results 路由读取端单一真相
_GENERATED_ROOT = str(get_artifact_store().generated_dir())


def _evidence_rank(cand: dict) -> int:
    """执行后补充候选的「元素证据强度」:有实际定位器最强,其次有真实可及名。"""
    if cand.get("selector"):
        return 2
    if cand.get("name"):
        return 1
    return 0


def _already_covered(existing_entry, cand: dict) -> bool:
    """该业务词是否已被词汇表覆盖且一致(覆盖即不再补;保守:不一致/未命中均判未覆盖)。

    一致判据:既有词条的真实名与本次跑通的一致,或既有词条带 selector(已是可靠定位)。
    """
    if not isinstance(existing_entry, dict):
        return False
    if existing_entry.get("selector"):
        return True
    name = (existing_entry.get("name") or "").strip()
    return bool(name) and name == (cand.get("name") or "").strip()


# 预置条件「状态声明」关键词 → Hook 名 的默认映射(用户可在构造时覆盖)。
# 命中即把该状态声明标为对应 Hook 负责(如「已登录」→ LoginHook,由 before_case 保证)。
DEFAULT_HOOK_MAP = {
    "已登录": "LoginHook",
    "登录系统": "LoginHook",
    "登陆": "LoginHook",
    "登录": "LoginHook",
}


async def settle_page(mcp, *, timeout: float, interval: float) -> int:
    """等页面加载稳定:轮询 a11y 快照,ref 节点数 >0 且连续两次不变即返回(或超时)。

    治"点登录→页面跳转/异步加载中→紧接着的快照/截图空白→后续步骤无 ref 可用而卡住"。
    纯只读快照,任何异常都安静返回、不阻断执行。返回最终探到的 ref 节点数(便于观测/测试)。
    """
    deadline = time.monotonic() + timeout
    prev = -1
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        try:
            result = await mcp.call_tool("browser_snapshot", {})
            text = mcp.result_to_text(result)
        except Exception as e:  # noqa: BLE001 — 等待失败不阻断执行
            logger.warning("settle 取快照失败:%s", e)
            return prev if prev > 0 else 0
        n = text.count("[ref=")
        if n > 0 and n == prev:
            return n  # 连续两次节点数一致且非空 → 稳定
        prev = n
    return prev if prev > 0 else 0


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

    async def generate_spec(self, case: TestCase, *, on_delta=None) -> TestSpec:
        """生成 TestSpec(供 CLI 先打印给用户审查)。

        含预置条件三分类(规格 §5.1/§5.2)。**默认合并**:当有待分类的预置条件时,用
        **一次** LLM 调用同时完成「分类 + 翻译」(省掉单独的分类往返,与模型快慢无关的结构
        优化);分类结果确定性建项(置信阈值 / Hook 映射 / 用户确认优先)并把 action_step
        合入 given。无待分类(无预置 / 全命中 memory / 无分类器)时退回单次翻译(分类 0 调用)。
        """
        classifier = self.precondition_classifier
        # 合并需分类器支持(memory + classify_from_raw);自定义/精简分类器不支持时退回两次调用。
        supports_merge = classifier is not None and hasattr(classifier, "classify_from_raw")
        valid_pre = (
            [p for p in case.preconditions if p and p.strip()]
            if (classifier is not None and case.preconditions)
            else []
        )
        pending: list[str] = []
        if valid_pre and supports_merge:
            self._seed_confirmed_preconditions(case)
            pending = [p for p in dict.fromkeys(valid_pre) if p not in classifier.memory]

        if valid_pre and supports_merge and pending:
            # —— 合并:一次调用同时分类 + 翻译 ——
            # 把**实际配置**的 Hook 列表告知 LLM:有则引导只对可用 Hook 归 state_hook,
            # 无则引导状态前提归 action_step/ambiguous(防「分类成 Hook 却没人执行」)。
            available_hooks = self.hooks.hook_names() if self.hooks is not None else []
            spec, raw = await self.spec_generator.generate_with_classification(
                case, on_delta=on_delta, available_hooks=available_hooks
            )
            try:
                items = classifier.classify_from_raw(case.preconditions, raw)
            except Exception as e:  # noqa: BLE001 — 分类不阻断翻译,降级为不分类
                logger.warning("预置条件分类(合并)失败(%s):%s,降级为不分类", case.id, e)
                items = []
            if items:
                self._record_classification(case, items)
                spec = self._merge_given_from_preconditions(spec, items)
            else:
                self._last_precondition_items = []
            return spec

        # —— 无待分类:分类不耗 LLM(空 / 全命中 memory),单独翻译 ——
        items = await self._classify_preconditions(case)
        spec = await self.spec_generator.generate(
            case, precondition_items=items or None, on_delta=on_delta
        )
        if items:
            spec = self._merge_given_from_preconditions(spec, items)
        return spec

    def _seed_confirmed_preconditions(self, case: TestCase) -> None:
        """把用例里 confirmed_by_user 的条目灌进分类器 memory(§3.2:确认后记忆、跳过 LLM、
        用户选择优先)。"""
        if self.precondition_classifier is None:
            return
        for it in case.precondition_items:
            if it.confirmed_by_user and it.text:
                self.precondition_classifier.memory[it.text] = it

    def _record_classification(self, case: TestCase, items: list[PreconditionItem]) -> None:
        """分类结果记账:写回用例(落库 + 前端标黄确认闭环)、记 _last_、state_hook 日志、
        模糊项告警。"""
        self._last_precondition_items = items
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

    async def _classify_preconditions(self, case: TestCase) -> list[PreconditionItem]:
        """对 case.preconditions 做三分类;无分类器/无预置条件时返回空列表。

        注:有待分类条目时,``generate_spec`` 走**合并调用**(分类随翻译一次出),本方法
        仅在「无待分类(空 / 全命中 memory)」时被调用——此时 ``classify`` 不触发 LLM。
        """
        self._last_precondition_items = []
        if self.precondition_classifier is None or not case.preconditions:
            return []
        self._seed_confirmed_preconditions(case)
        try:
            items = await self.precondition_classifier.classify(case.preconditions)
        except Exception as e:  # noqa: BLE001 — 分类失败不阻断翻译,降级为不分类
            logger.warning("预置条件分类失败(%s):%s,降级为不分类", case.id, e)
            return []
        self._record_classification(case, items)
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

        # 执行期「思考过程」流式(reasoning 逐 token):合批 ~50 字符转发,削减 SSE 事件数。
        _think_buf: list[str] = []

        async def _flush_think() -> None:
            if cb is None or not _think_buf:
                return
            text = "".join(_think_buf)
            _think_buf.clear()
            try:
                await cb("think_delta", {"case_id": case.id, "delta": text})
            except Exception:  # noqa: BLE001
                pass

        async def emit_think_delta(text: str) -> None:
            _think_buf.append(text)
            # 合批 ~60 字符:前端已用 rAF 把渲染合到每帧≤1 次(与 token 速率解耦),
            # 故这里取小批量让思考文本**更细粒度、顺滑地**流出(不再为省前端重渲染而攒大块,
            # 那会让流看起来一跳一跳)。queue 模式下 SSE/run_event 行数略增,可接受。
            if sum(len(s) for s in _think_buf) >= 60:
                await _flush_think()

        async def emit_step(step) -> None:
            await _flush_think()  # 步骤落定前冲刷该步思考尾巴,保证顺序:思考→步骤
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
                        "reasoning": step.reasoning,  # 该步思考(后端权威,前端 thinkStream 兜底)
                        "tool_result": step.tool_result,  # 工具观察(过程时间线展示)
                        "url": step.url,
                        "heal_count": len(step.heal_attempts),  # 操作侧自愈次数(自愈可见)
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

        # 流式增量合批:LLM↔网关保活靠 acompletion 的 stream=True(自动),浏览器侧增量
        # 仅供 UX,故按 ~50 字符合批转发,削减 SSE 事件数 / queue 模式 run_event 行数。
        _delta_buf: list[str] = []

        async def _flush_delta() -> None:
            if cb is None or not _delta_buf:
                return
            text = "".join(_delta_buf)
            _delta_buf.clear()
            try:
                await cb("spec_delta", {"case_id": case.id, "delta": text})
            except Exception:  # noqa: BLE001
                pass

        async def emit_spec_delta(text: str) -> None:
            _delta_buf.append(text)
            if sum(len(s) for s in _delta_buf) >= 50:
                await _flush_delta()

        await emit_phase("spec", "翻译用例为执行规格 (TestSpec)")
        if spec is None:
            spec = await self.generate_spec(case, on_delta=emit_spec_delta)
            await _flush_delta()  # 冲刷尾部不足 50 字符的增量
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
        if self.skills is not None:
            tools.append(SkillManager.tool_schema())  # load_skill:LLM 渐进加载技能正文
        if self.tools_registry is not None:
            tools += self.tools_registry.to_litellm_tools()

        prompt_ctx = "\n\n".join(p for p in (self.context, PLAYWRIGHT_MCP_HINT) if p)
        builder = PromptBuilder(spec, tools, context=prompt_ctx)
        healer = HealingSubagent(self.llm)

        # 当前 URL 状态(供 Permission 按环境/URL 判定):初始为 base_url,执行器随观察更新
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
            # load_skill(LLM 渐进加载技能正文)→ 标记已加载,正文由 build_system 注入系统提示
            if name == LOAD_SKILL_TOOL:
                if self.skills is None:
                    return ToolOutcome(text="当前没有可加载的技能。")
                wanted = (arguments or {}).get("name", "")
                content = self.skills.load(wanted)
                if content is None:
                    return ToolOutcome(
                        text=f"未找到技能「{wanted}」,请从「可按需加载的技能」清单里选准确的技能名。"
                    )
                return ToolOutcome(
                    text=f"已加载技能「{wanted}」,其完整内容已在系统提示的「已加载技能」区,请据此继续。"
                )
            # 自定义工具(LLM 按需调用)→ 注册表;不走 MCP
            if self.tools_registry is not None and self.tools_registry.has(name):
                text = await self.tools_registry.call(name, arguments)
                return ToolOutcome(text=text, is_custom_tool=True)
            outcome = await base_executor(name, arguments)
            if outcome.url:
                state["url"] = outcome.url
            # 导航/加载类动作后等页面稳定,避免随后抓到空白 loading 态(快照无 ref → 卡住)
            if _SETTLE_ENABLED and name in _NAV_TOOLS:
                await settle_page(self.mcp, timeout=_SETTLE_TIMEOUT, interval=_SETTLE_INTERVAL)
            return outcome

        def build_system(step_plan: StepPlan) -> str:
            text = builder.build(step_plan)
            if self.skills is not None:
                skill_text = self.skills.render()  # 已加载正文 + 可按需加载清单(LLM 自行展开)
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
            on_llm_delta=emit_think_delta,  # 思考过程流式 + ReAct 期网关保活
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
        engine = AssertionEngine(
            probe, healer=healer, tool_registry=self.tools_registry, llm=self.llm
        )
        # 聚合用例级 + 步骤级 expect 断言,避免 LLM 把断言放在 step.expect 时被漏验
        a_results = await engine.verify_all(collect_assertions(spec))
        recorder.set_case_assertions([r.to_dict() for r in a_results])
        recorder.record.heal_count += sum(1 for r in a_results if r.healed)
        assert_passed = AssertionEngine.verdict(a_results)

        # —— 执行完整性闸门(原则:步骤未全部完成 → 用例直接 FAIL)——
        # 任一步骤非 DONE(pending 未执行 / failed / skipped)即视为执行未完成:此时
        # 终态断言是在**半路页面**上跑的,既可能误绿(碰巧通过)也可能误红(报成断言失败、
        # 掩盖真因)。原则:不靠半路断言裁决、不静默跳过剩余步骤就收尾——直接判 FAIL 并
        # 标明真因(停在第几步 / 停因)。only when 全步 DONE 才以断言裁决。
        done_steps = sum(1 for st in plan.steps if st.status == StepStatus.DONE)
        total_steps = len(plan.steps)
        execution_complete = plan.all_done()
        incomplete_reason = ""
        if not execution_complete:
            incomplete_reason = (
                f"[FAIL] 执行未完成:仅完成 {done_steps}/{total_steps} 步"
                f"(停因={result.stop_reason.value}),后续步骤未执行;不以半路断言裁决。"
            )
            logger.warning("用例 %s %s", case.id, incomplete_reason)
        passed = assert_passed and execution_complete

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

        # 执行未完成时 final_result 显式标真因(优先于断言摘要),避免被半路断言红误导
        record = recorder.finalize(passed=passed, final_result=incomplete_reason)

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
        """执行后增量补充(策略C,**辅助**):用执行轨迹里**跑通的真实元素**(ground truth)
        总结这条用例触达、但词汇表还缺的业务词,LLM 挑选规范化后增量并库(手动条目优先)。

        与主动扫描分工:**主动扫描**(/vocabulary/scan)负责全量铺页面词汇;**本模块**只用
        执行真值补**这条用例的增量**——无新词则 **0 次 LLM 调用**。复用执行期已登录会话,
        不另开浏览器、不重走全流程。best-effort,失败只告警不影响用例结果。仅当注入了带
        VocabularyManager 的 resolver(即真正接了词汇表持久化)时才执行。
        """
        if not _INCREMENTAL_SCAN or self.vocab_resolver is None:
            return
        manager = getattr(self.vocab_resolver, "manager", None)
        if manager is None:
            return
        login_role = getattr(self.vocab_resolver, "login_role", "") or ""

        # 1) 从执行轨迹收集「业务词 → 真实元素」候选(只取真正操作过、有元素证据的步),
        #    按 URL 分组;同 URL 内同业务词留证据最强的一条(有 selector > 仅有 name)。
        #    步骤按 step_no 有序:**空 url 的步(如 browser_type 不导航、outcome.url 为空)
        #    继承最近一个非空 url**——它其实就在那个页面上,否则空 url 自成一组会落出
        #    base_url 为空的孤儿词汇表行(同一逻辑页被拆成两条;live 实证过)。
        by_url: dict[str, dict[str, dict]] = {}
        cur_url = ""
        for s in sorted(result.action_steps, key=lambda x: x.step_no):
            if (s.url or "").strip():
                cur_url = s.url.strip()
            term = (s.step_target or s.tool_input.get("element") or "").strip()
            name = (s.element_name or "").strip()
            selector = (s.element_selector or "").strip()
            if not term or (not name and not selector):
                continue  # 无业务词或无真实元素证据(纯 mark_done/snapshot 等)→ 跳过
            cand = {
                "term": term,
                "role": (s.element_role or "").strip(),
                "name": name,
                "selector": selector,
            }
            page = by_url.setdefault(cur_url, {})
            prev = page.get(term)
            if prev is None or _evidence_rank(cand) > _evidence_rank(prev):
                page[term] = cand
        by_url.pop("", None)  # 整轮都没拿到 url(异常)→ 丢弃,不落 base_url 为空的孤儿行
        if not by_url:
            return

        scanner = Scanner(self.llm)
        phase_emitted = False
        for url, cands in by_url.items():
            # 2) 确定性过滤:已被词汇表覆盖且一致的业务词不再补;复用既有页身份(对齐键,免重复行)
            existing_page = await self._safe_find_page(manager, url, login_role)
            existing_vocab = existing_page.vocabulary if existing_page else {}
            supplements = [
                c for term, c in cands.items() if not _already_covered(existing_vocab.get(term), c)
            ]
            if not supplements:
                continue
            # 3) 仅在有待补充候选时,叫 LLM 做一次「总结挑词」(无候选则 0 调用)
            if not phase_emitted:
                await emit_phase("scanning", "总结补充词汇表")
                phase_emitted = True
            delta = await scanner.summarize_supplements(
                supplements, existing_terms=list(existing_vocab.keys())
            )
            if not delta:
                continue
            # 4) 落库:用既有页身份(无则按 URL 推断)构造 delta 词汇表,merge_scanned(手动优先)
            if existing_page is not None:
                base_url, url_pattern, page_title = (
                    existing_page.base_url,
                    existing_page.url_pattern,
                    existing_page.page_title,
                )
            else:
                base_url, url_pattern = url_scope(url)
                page_title = ""
            pv = PageVocabulary(
                base_url=base_url,
                url_pattern=url_pattern,
                page_title=page_title,
                login_role=login_role,
                vocabulary=delta,
            )
            try:
                await manager.merge_scanned(pv)
            except Exception as e:  # noqa: BLE001 — 并库失败不影响用例结果
                logger.warning("执行后词汇并库失败:%s", e)

    async def _safe_find_page(self, manager, url: str, login_role: str):
        """find_page 包错:执行后补充是 best-effort,查词汇表异常不该打断用例收尾。

        执行轨迹未存 page_title,按 url + 宽松匹配(空 title/role 通配)查既有页。
        """
        try:
            return await manager.find_page(url, "", login_role)
        except Exception as e:  # noqa: BLE001
            logger.warning("执行后查词汇表失败:%s", e)
            return None

    def _token_usage(self) -> int:
        fn = getattr(self.llm, "usage_summary", None)
        if callable(fn):
            try:
                return fn().total_tokens
            except Exception:  # noqa: BLE001
                return 0
        return 0
