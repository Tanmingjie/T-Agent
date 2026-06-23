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
from codegen.locators import locators_from_steps
from harness.assertion import AssertionEngine, AssertionResult, AssertionStatus
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
from harness.prompt import PromptBuilder
from harness.react_loop import ReActLoop
from harness.react_loop import StopReason as ReActStopReason
from harness.react_loop import ToolExecutor, ToolOutcome
from harness.recorder import Recorder
from harness.skills import LOAD_SKILL_TOOL, SkillManager
from harness.step_plan import StepPlan, StepStatus
from harness.tools import ToolRegistry
from input.models import (
    Assertion,
    ExecutionRecord,
    PageVocabulary,
    TestCase,
    TestSpec,
)
from intelligence.pre_analysis import SpecGenerator
from intelligence.scanner import Scanner, url_scope
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

# 单步定位失败预算(#1 快速失败):同一业务步累计定位失败(自愈也没救回)达此数 →
# 快速判 STEP_FAILED 终止(疑似点错前序元素致后续找不到目标)。env STEP_FAIL_BUDGET 可调。
# E2(2026-06-23):3→5,给「诊断换法」自适应留空间(像 Claude 一样多试几招),仍兜底真卡死。
_STEP_FAIL_BUDGET = int(os.getenv("STEP_FAIL_BUDGET", "5"))

# 步级卡住主动提醒预算(E2):同一业务步连续 N 轮**页面指纹未变化且未推进** → 主动注入
# 诊断引导(滚动/换思路/查 skill 名册)。默认 2;env STUCK_ROUND_BUDGET 可调。
_STUCK_ROUND_BUDGET = int(os.getenv("STUCK_ROUND_BUDGET", "2"))

# 循环检测窗口:连续 N 轮**完全相同**的 tool_call 才判卡死终止(LOOP_DETECTED)。默认 4
# (放宽,长流程如下单结算偶发重复一两次快照/点击属正常,3 太敏感会误杀);env LOOP_WINDOW 可调。
_LOOP_WINDOW = int(os.getenv("LOOP_WINDOW", "4"))

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


def _build_metrics(
    *,
    phase_tokens: dict,
    total_tokens: int,
    result,
    max_steps: int,
    done_steps: int,
    total_steps: int,
    execution_complete: bool,
    a_results: list,
) -> dict:
    """汇总分阶段成本/质量指标(#6),供运营观测。结构稳定,字段可增不减。

    - tokens:各阶段 token 成本(自愈落入 executing、llm_judge 落入 asserting)+ 总计。
    - execution:ReAct 健康度——停因 / 轮数 / 哑火续推次数 / 完整性闸门(是否全步 DONE)。
    - healing:操作侧(工具报错重定位)与断言侧(目标重定位复验)自愈分路计数。
    - assertions:裁决分布 + ``ai_judged``(llm_judge 兜底占比 = false-green 风险面)。
    """
    action_heals = sum(len(s.heal_attempts) for s in result.action_steps)
    assertion_heals = sum(1 for r in a_results if getattr(r, "healed", False))
    a_pass = sum(1 for r in a_results if r.status == AssertionStatus.PASS)
    a_fail = sum(1 for r in a_results if r.status == AssertionStatus.FAIL)
    a_skip = sum(1 for r in a_results if r.status == AssertionStatus.SKIPPED)
    a_ai = sum(1 for r in a_results if getattr(r, "ai_judged", False))
    tokens = {k: int(v) for k, v in phase_tokens.items()}
    tokens["total"] = int(total_tokens)
    return {
        "tokens": tokens,
        "execution": {
            "stop_reason": result.stop_reason.value,
            "iterations": result.iterations,
            "max_steps": max_steps,
            "idle_nudges": getattr(result, "idle_nudges", 0),
            "complete": bool(execution_complete),
            "done_steps": done_steps,
            "total_steps": total_steps,
            "action_steps": len(result.action_steps),
        },
        "healing": {"action": action_heals, "assertion": assertion_heals},
        "assertions": {
            "pass": a_pass,
            "fail": a_fail,
            "skipped": a_skip,
            "ai_judged": a_ai,
            "total": len(a_results),
        },
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

    async def generate_spec(self, case: TestCase, *, on_delta=None) -> TestSpec:
        """生成阶段化 TestSpec(纯 LLM 翻译,单次调用)。供 CLI 先打印给用户审查。

        预置条件不再分类(纯背景 list[str],原样进 spec.preconditions);翻译只产意图,
        阶段化分组 + 组级预期,不接地。``on_delta`` 给定走流式(长生成不被网关空闲超时切断)。
        """
        return await self.spec_generator.generate(case, on_delta=on_delta)

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

        # 分阶段 token 计量(#6):LLM 封装累计 total_usage,在阶段边界快照取差值,
        # 把 token 成本归到「翻译/执行/断言/codegen/扫描」各阶段(自愈/llm_judge 自然
        # 落入其所在阶段)。每用例一个独立 LLM client,故差值即本用例本阶段消耗。
        phase_tokens: dict[str, int] = {}
        _tok_mark = self._token_usage()

        def _mark_phase(name: str) -> None:
            nonlocal _tok_mark
            cur = self._token_usage()
            phase_tokens[name] = phase_tokens.get(name, 0) + max(0, cur - _tok_mark)
            _tok_mark = cur

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
        _mark_phase("spec")

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

        # —— 阶段边界 Validator(逐阶段裁决,取代终态裁决):某阶段【最后一步】mark_step_done
        # 落定后,在当时所处页面用**偏-FAIL + 证据接地**的 LLM 裁判核验该阶段 ``expected``。
        #   · 通过 → 记为该阶段裁决证据,继续下一阶段;
        #   · 未达成 → 返回原因串 → react_loop 判 PHASE_FAILED,用例直接失败(阶段失败即失败)。
        # expected **只在此核验,绝不进 agent 驱动**(FG01)。复用 AssertionEngine._check_llm_judge
        # (内部以 llm_judge Assertion 承载阶段预期),与旧终态裁判同一套证据接地、fail-closed 逻辑。
        phase_results: list = []  # [(phase_index, AssertionResult)] —— 逐阶段裁决证据

        validated_phases: set[int] = set()  # E4 去重:同一 phase 只裁决一次

        async def on_phase_end(phase_index: int) -> str | None:
            if not (0 <= phase_index < len(spec.phases)):
                return None
            # E4 同 phase 只裁决一次:即便模型重复 mark 同一末步,Validator 不再重复跑。
            # 已通过的 phase 跳过(返回 None 放行);已 FAIL 的 phase 已经停了不会到这。
            if phase_index in validated_phases:
                return None
            expected = (spec.phases[phase_index].expected or "").strip()
            if not expected:
                # 阶段没有 expected(翻译退化:本该产出组级预期却空)→ 无可核验依据 = 主裁决
                # 缺失,FAIL(G1:不再放过;暴露翻译质量问题)→ 返回原因触发 PHASE_FAILED 停 ReAct。
                phase_results.append(
                    (
                        phase_index,
                        AssertionResult(
                            assertion=Assertion(type="llm_judge", target="", expected=""),
                            status=AssertionStatus.FAIL,
                            reason="该阶段无 expected,无法裁决 → FAIL(疑似翻译退化,请检查 spec)",
                            ai_judged=True,
                            phase_index=phase_index,
                        ),
                    )
                )
                validated_phases.add(phase_index)
                return f"阶段 {phase_index + 1} 无 expected,无法裁决"
            # E4 判前 settle:mark_step_done 不触发 settle,Validator 在 mark 当场抓快照
            # 可能抓到「上一动作还在加载」的页面(裁判要么白等要么把过渡态误判)。先等
            # 页面稳定再 refresh,确保 judge 看的是**稳定终态页**。
            if _SETTLE_ENABLED:
                await settle_page(self.mcp, timeout=_SETTLE_TIMEOUT, interval=_SETTLE_INTERVAL)
            probe_p = MCPPageProbe(self.mcp, resolver=self.vocab_resolver)
            await probe_p.refresh()  # 抓当时所处页面快照(阶段边界,页面还在那一刻)
            engine_p = AssertionEngine(
                probe_p, healer=healer, tool_registry=self.tools_registry, llm=self.llm
            )
            r = await engine_p._check_llm_judge(
                Assertion(type="llm_judge", target=expected, expected=expected, confidence="low")
            )
            r.phase_index = phase_index  # F2:一等字段,前端按阶段分组
            phase_results.append((phase_index, r))
            validated_phases.add(phase_index)
            # PASS / SKIPPED(裁判调用失败等)→ 不阻断继续;FAIL → 返回原因 → 阶段失败即失败。
            if r.status == AssertionStatus.FAIL:
                return r.reason or f"阶段 {phase_index + 1} 的预期未在当前页面达成"
            return None

        loop = ReActLoop(
            self.llm,
            tools=tools,
            execute=executor,
            step_plan=plan,
            build_system=build_system,
            max_steps=self.max_steps,
            loop_window=_LOOP_WINDOW,
            healer=healer,
            get_snapshot=get_snapshot,
            get_screenshot=get_screenshot,
            compactor=ContextCompactor(),
            capture_screenshot=capture_screenshot,
            on_step=emit_step,  # 每步落定即时推送(实时进度)
            on_llm_delta=emit_think_delta,  # 思考过程流式 + ReAct 期网关保活
            vocab_resolver=self.vocab_resolver,  # 操作侧自愈词汇表优先
            on_phase_end=on_phase_end,  # 阶段边界 Validator(偏-FAIL 证据接地;未达成→PHASE_FAILED)
            step_fail_budget=_STEP_FAIL_BUDGET,  # #1 单步定位失败预算 → 快速失败
            stuck_round_budget=_STUCK_ROUND_BUDGET,  # E2 步级卡住主动提醒
            skill_manager=self.skills,  # E3 卡住兜底:甲(浮现催加载)/乙(自动注入)
        )
        await emit_phase("executing", "驱动浏览器逐步执行")
        result = await loop.run()
        _mark_phase("executing")
        recorder.extend_steps(result.action_steps)
        recorder.set_token_usage(self._token_usage())
        recorder.set_stop_reason(f"{result.stop_reason.value}/iter={result.iterations}")

        # —— 裁决汇总(逐阶段 Validator 已在执行中、各阶段边界即时验过;此处仅记账)——
        # 阶段化重设计后无独立「asserting 阶段」:Validator 的 token/时长在 ③ executing
        # 内分摊(每条 _check_llm_judge 调用即时计入);汇总/落库/verdict 计算属 ⑤ 闸门职责。
        #
        # G2 缺席阶段 FAIL 占位:早停(STEP_FAILED/max_steps/卡死)时 phase_results 短于
        # n_phases,落库 case_assertions 长度不对齐 spec.phases,前端时间线断层("阶段 1 ✅/
        # 阶段 2 ✅/阶段 3-5 不见/整体 ❌"用户困惑)。给未触达阶段补 FAIL 占位补齐长度。
        # 占位**不进 validated_phases**(那只记真实裁决过的阶段,verdict 与归因据此区分)。
        for pi in range(len(spec.phases)):
            if pi not in validated_phases:
                phase_results.append(
                    (
                        pi,
                        AssertionResult(
                            assertion=Assertion(
                                type="llm_judge", target="", expected=spec.phases[pi].expected
                            ),
                            status=AssertionStatus.FAIL,
                            reason="该阶段未触达,执行已早停",
                            ai_judged=True,
                            phase_index=pi,
                        ),
                    )
                )
        phase_results.sort(key=lambda x: x[0])  # 按 phase_index 升序,前端展示连续
        all_results = [r for _, r in phase_results]
        # 落库:AssertionResult.phase_index 一等字段(F2),to_dict 自然带出;expected 不再
        # 外塞覆盖(它本就来自 Assertion.expected = phase.expected,二者等价由 on_phase_end 保证)。
        recorder.set_case_assertions([r.to_dict() for r in all_results])
        recorder.record.heal_count += sum(1 for r in all_results if r.healed)

        # —— 裁决:全阶段通过 + 执行完整 ——
        # 阶段失败即失败(PHASE_FAILED):某阶段 Validator 判 FAIL → react_loop 已停,该阶段
        # 记 FAIL。其余早停(max_steps/卡死/tool 错)→ 部分阶段未被验证 → 执行未完成 → FAIL。
        done_steps = sum(1 for st in plan.steps if st.status == StepStatus.DONE)
        total_steps = len(plan.steps)
        execution_complete = plan.all_done()
        n_phases = len(spec.phases)
        validated = len(validated_phases)  # 真实裁决过的阶段数(G2 占位不计)
        # phase_fail 只看**真实裁决过**的阶段(占位 FAIL 的 pi 不在 validated_phases),
        # 否则早停(max_steps/卡死)的缺席占位会污染它 → 失败归因误报"阶段预期未达成"。
        phase_fail = any(
            r.status == AssertionStatus.FAIL for pi, r in phase_results if pi in validated_phases
        )
        # 可信通过:执行完整 + 有阶段 + 无真实阶段 FAIL。G1:llm_judge 的 SKIPPED 三态已收成
        # FAIL,阶段裁决只剩 PASS/FAIL → 不再需要 validated==n_phases 守门(执行完整即蕴含
        # 每阶段末步都触发过 on_phase_end,故全被裁决过)。
        passed = execution_complete and n_phases > 0 and not phase_fail

        incomplete_reason = ""
        if not passed:
            if n_phases == 0:
                # 翻译退化为空 phases(LLM 翻译失败或契约破坏)→ 无可执行内容,本就该 FAIL。
                incomplete_reason = (
                    "[FAIL] 翻译退化为空 phases,无可执行内容(LLM 翻译失败或契约破坏)。"
                )
            elif phase_fail:
                fi = result.failed_phase_index
                incomplete_reason = (
                    f"[FAIL] 阶段 {fi + 1 if fi >= 0 else '?'} 的预期未达成:"
                    f"{result.failed_phase_reason or '(见阶段裁决)'};"
                    f"仅完成 {done_steps}/{total_steps} 步。"
                )
            elif result.stop_reason == ReActStopReason.STEP_FAILED and result.failed_step_no:
                incomplete_reason = (
                    f"[FAIL] 执行未完成:第 {result.failed_step_no} 步"
                    f"「{result.failed_step_target}」反复定位失败(快速失败),"
                    f"疑似前序步骤点错元素致此步找不到目标;仅完成 {done_steps}/{total_steps} 步。"
                )
            elif not execution_complete:
                incomplete_reason = (
                    f"[FAIL] 执行未完成:仅完成 {done_steps}/{total_steps} 步"
                    f"(停因={result.stop_reason.value}),后续步骤未执行;"
                    f"{validated}/{n_phases} 个阶段被验证。"
                )
            else:
                incomplete_reason = f"[FAIL] {validated}/{n_phases} 个阶段被验证,未全部通过。"
            logger.warning("用例 %s %s", case.id, incomplete_reason)

        # 阶段裁决逐条记账(便于失败定位)。
        for pi, r in phase_results:
            level = logging.WARNING if r.status != AssertionStatus.PASS else logging.DEBUG
            logger.log(
                level,
                "阶段 %d 裁决 [%s] expected=%r reason=%s",
                pi + 1,
                r.status.value.upper(),
                r.assertion.expected,
                r.reason or "(无)",
            )

        # 收尾 Hooks:发生自愈触发 on_heal;失败触发 on_failure;after_case 无论成败都跑。
        if self.hooks is not None:
            ctx.set("passed", passed)
            # 自愈可观测(规格 §7.7):聚合断言侧(重定位后复验通过)+ 操作侧(工具报错
            # 后重定位重试)两路自愈;任一发生即触发 on_heal,详情入 ctx 供 hook 消费。
            healed_assertions = [r for r in all_results if r.healed]
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
        # 阶段化重设计后 codegen 走最小适配:步骤=phases 摊平的自然语言串,Then=各阶段 expected
        # (作 BDD 文本/注释,NL 无法确定性断言)。定位优先用执行轨迹捕获的真实 role+name。
        # 质量打磨(轨迹驱动 codegen)留后续任务,这里只保证不崩、产出可读的回放骨架。
        if passed:
            await emit_phase("codegen", "生成测试代码")
            try:
                # 执行期捕获的真实定位器(按步骤文本归类),给 codegen 渲染稳健选择器
                locators = locators_from_steps(record.steps)
                gen = await asyncio.to_thread(
                    BDDGenerator().generate, spec, record, locators=locators
                )
                record.generated_code = f"{gen.feature}\n\n{gen.step_defs}"
                gen.write(_GENERATED_ROOT)  # storage/generated/(供下载)
            except Exception as e:  # noqa: BLE001 — 代码生成失败不影响用例结果
                logger.warning("用例 %s 代码生成失败:%s", case.id, e)
            _mark_phase("codegen")

        # —— 词汇表增量扫描(策略C):执行结束后复用已见快照提炼业务词→元素映射并库 ——
        await self._incremental_scan(result, emit_phase)
        _mark_phase("scanning")

        # —— 分阶段成本/质量指标(#6):随 record 落库 + 透出前端 ——
        # 可观测埋点,best-effort:任何异常都不得影响用例结果(与 codegen/scan 同口径)。
        try:
            # headline token 刷成最终累计值(原在 executing 后定格,漏断言阶段 llm_judge token)
            recorder.set_token_usage(self._token_usage())
            recorder.set_metrics(
                _build_metrics(
                    phase_tokens=phase_tokens,
                    total_tokens=recorder.record.token_usage,
                    result=result,
                    max_steps=self.max_steps,
                    done_steps=done_steps,
                    total_steps=total_steps,
                    execution_complete=execution_complete,
                    a_results=all_results,
                )
            )
        except Exception as e:  # noqa: BLE001 — 指标埋点失败不影响用例结果
            logger.warning("用例 %s 指标汇总失败:%s", case.id, e)

        logger.info(
            "用例 %s 执行完毕:%s(LLM 自报=%s,停因=%s)",
            case.id,
            "PASS" if passed else "FAIL",
            result.llm_result,
            result.stop_reason.value,
        )
        return record

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
