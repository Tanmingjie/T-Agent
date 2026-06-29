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
from harness.prompt import BASE_PROMPT, PromptBuilder
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
    TestCase,
    TestSpec,
)
from intelligence.pre_analysis import SpecGenerator
from storage.artifacts import get_artifact_store

logger = logging.getLogger(__name__)

# playwright-mcp 工具使用提示(以 Context 形式注入 System Prompt)
PLAYWRIGHT_MCP_HINT = """\
【浏览器操作(playwright-mcp)】
浏览器操作通过 playwright-mcp 工具完成,关键用法:
- 先用 `browser_navigate` 打开目标页面;之后用 `browser_snapshot` 获取 A11y 快照,快照里每个可操作元素都带一个 ref(形如 e11)。
- 操作元素时(`browser_click` / `browser_type` / `browser_select_option` / `browser_hover`)必须传两个参数:
  · `element`:人类可读的元素描述(如 "用户名输入框");
  · `ref`:从最近一次快照里复制的那个 ref 值(如 "e11")。
  ⚠️ ref 是 playwright-mcp 的专用引用,**不是 CSS 选择器**,不要写成 ref=e11 这种形式塞进选择器字段,直接把 e11 作为 ref 参数传。
- 页面发生跳转/变化后,ref 会失效,需要重新 `browser_snapshot` 再操作。
- `browser_type` 填完输入框、要触发提交时可带 submit=true,或之后单独 click 提交按钮。
- 需要等待用 `browser_wait_for`:等**文本出现/消失**传 `text`/`textGone`;需**按时长等待/观察**(如"等待 3 分钟")传 `time`(秒,如 180),平台会真正等满该时长(封顶 5 分钟)后再返回最新页面。"""

# 控制工具的名字(执行器据此路由,permission/截图据此跳过):StepPlan 推进 + Skill 渐进加载
_CONTROL_TOOLS = {"mark_step_done", LOAD_SKILL_TOOL}

# 不该被截图的浏览器工具(快照/截图自身)
_NO_SHOT_TOOLS = {"browser_snapshot", "browser_take_screenshot"}
# 截图开关:默认开,env MCP_SCREENSHOT=0 关闭
_CAPTURE_SCREENSHOTS = os.getenv("MCP_SCREENSHOT", "1") != "0"

# 交互类动作:可能触发**无 load 事件**的异步内容(SPA 局部刷新/延迟跳转),Playwright 的
# 自动等待覆盖不到 → 执行后做一次 settle,避免随后抓到空白/半渲染快照(快照无 ref→卡住)。
# 〔2026-06-25〕**显式导航 browser_navigate/back/forward 不在此列**:Playwright 的 goto 默认
# 已自动等到 'load' 才返回,再 settle 纯属冗余(每次白烧一轮轮询)。settle 只留给"点击触发
# 异步内容"这类 Playwright 判不出加载完成的场景(如"点登录→SPA 异步渲染主页")。
_NAV_TOOLS = {
    "browser_click",
    "browser_press_key",
    "browser_file_upload",
}
# 页面稳定等待(settle)开关与参数。默认开;env MCP_SETTLE=0 关闭。
# 机制:交互动作后轮询 a11y 快照,ref 节点数 >0 且连续两次不变即认为稳定(或超时)。
_SETTLE_ENABLED = os.getenv("MCP_SETTLE", "1") != "0"
_SETTLE_TIMEOUT = float(os.getenv("MCP_SETTLE_TIMEOUT_MS", "8000")) / 1000.0
_SETTLE_INTERVAL = float(os.getenv("MCP_SETTLE_INTERVAL_MS", "400")) / 1000.0
# 阶段末尾 Validator 判前的 settle 超时:阶段末步是 mark_step_done(不改页面),页面通常在
# 前序动作后已稳——这里只防"点击触发的 SPA 异步内容还在渲染就被裁判抓到"→ **短超时即可**
# (默认 2s),不必沿用主 settle 的 8s(动态页/长轮询页会每阶段白烧满)。env 可调。
_PHASE_SETTLE_TIMEOUT = float(os.getenv("PHASE_SETTLE_TIMEOUT_MS", "2000")) / 1000.0

# 单步定位失败预算(#1 快速失败):同一业务步累计定位失败(自愈也没救回)达此数 →
# 快速判 STEP_FAILED 终止(疑似点错前序元素致后续找不到目标)。env STEP_FAIL_BUDGET 可调。
# E2(2026-06-23):3→5,给「诊断换法」自适应留空间(像 Claude 一样多试几招),仍兜底真卡死。
# 2026-06-29:5→10——内网脏 live SPA(持续重渲染→nav 链接 ref 秒级失效→click-by-ref 5s 超时)上,
# 模型靠「改用 URL 直达 / 重抓快照」恢复需要更多尝试,默认 5 会在合法恢复前掐断。先试 8 仍偶尔
# 在 nav-click 上被掐(thingsboard.cloud 替身实测),10 才稳(复杂用例 TB04/设备详情 TB05 均需)。
# 10 为脏 SPA 默认;干净站点几乎不触达此预算(成本只在真卡步上),很干净的场景可经 env 调小。
_STEP_FAIL_BUDGET = int(os.getenv("STEP_FAIL_BUDGET", "10"))

# 步级卡住主动提醒预算(E2):同一业务步连续 N 轮**页面指纹未变化且未推进** → 主动注入
# 诊断引导(滚动/换思路/查 skill 名册)。默认 2;env STUCK_ROUND_BUDGET 可调。
_STUCK_ROUND_BUDGET = int(os.getenv("STUCK_ROUND_BUDGET", "2"))

# 循环检测窗口:连续 N 轮**完全相同**的 tool_call 才判卡死终止(LOOP_DETECTED)。默认 4
# (放宽,长流程如下单结算偶发重复一两次快照/点击属正常,3 太敏感会误杀);env LOOP_WINDOW 可调。
_LOOP_WINDOW = int(os.getenv("LOOP_WINDOW", "4"))

# 「按时长等待」补齐:playwright-mcp 的 browser_wait_for 单次调用内部上限约 30s——请求
# time=180 实测 ~30s 就返回(未等满),使「等待观察 N 分钟」类步骤拿不到真实经过的时间。
# 解法:执行器把长时长等待**分段**(每段 < 内部上限)循环调用,真正累积到请求时长,封顶
# WAIT_MAX_SECONDS(防误填"等 1 小时"长期占住 worker)。仅对纯时长等待(有 time、无 text/
# textGone)生效;等"文本出现/消失"仍直通(本就有语义终止条件)。
_WAIT_TOOL = "browser_wait_for"
_WAIT_MAX_SECONDS = float(os.getenv("WAIT_MAX_SECONDS", "300"))  # 上限默认 5min
_WAIT_CHUNK_SECONDS = float(
    os.getenv("WAIT_CHUNK_SECONDS", "20")
)  # 单段 < playwright-mcp ~30s 上限

# 生成代码落盘目录:从 ArtifactStore 抽象取(T-P10),与 results 路由读取端单一真相
_GENERATED_ROOT = str(get_artifact_store().generated_dir())


def _wait_seconds(arguments: dict) -> float | None:
    """从 browser_wait_for 参数取**纯时长等待**的秒数;非纯时长(带 text/textGone)或无效返回 None。"""
    if not isinstance(arguments, dict):
        return None
    if arguments.get("text") or arguments.get("textGone"):
        return None  # 等文本出现/消失:有语义终止条件,直通,不分段
    raw = arguments.get("time")
    if raw is None:
        return None
    try:
        secs = float(raw)
    except (TypeError, ValueError):
        return None
    return secs if secs > 0 else None


async def _chunked_wait(mcp, requested: float) -> ToolOutcome:
    """把长时长等待分段累积到真实请求时长(封顶 _WAIT_MAX_SECONDS),返回最后一段的观察。

    每段时长 < playwright-mcp 单次内部上限(~30s),故每段都能等满;循环累积逼近请求时长。
    单段 call_tool 显式给足超时(段长 + 余量),不受默认 120s 影响(段长本就远小于它)。
    """
    capped = min(requested, _WAIT_MAX_SECONDS)
    remaining = capped
    last_text = ""
    last_err = False
    while remaining > 0.5:
        seg = min(_WAIT_CHUNK_SECONDS, remaining)
        result = await mcp.call_tool(_WAIT_TOOL, {"time": seg}, timeout=seg + 30.0)
        last_text = mcp.result_to_text(result)
        last_err = bool(getattr(result, "isError", False))
        remaining -= seg
    note = f"\n### 平台说明\n已实际等待约 {capped:.0f}s(分段累积)"
    if requested > capped:
        note += f";请求 {requested:.0f}s 超过上限 {_WAIT_MAX_SECONDS:.0f}s,已截断"
    url = parse_snapshot(last_text).url
    return ToolOutcome(text=last_text + note, url=url, is_error=last_err)


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

    - tokens:各阶段 token 成本(自愈落入 executing、llm_judge 落入 executing)+ 总计。
    - execution:ReAct 健康度——停因 / 轮数 / 哑火续推次数 / 完整性闸门(是否全步 DONE)。
    - healing:操作侧自愈(工具报错重定位重试)计数。〔阶段化下阶段裁决走 _check_llm_judge
      直连、不过 healable 装饰 → 断言侧自愈不存在,故只计操作侧。〕
    - assertions:裁决分布 + ``ai_judged``(llm_judge 占比 = false-green 风险面)。
    """
    action_heals = sum(len(s.heal_attempts) for s in result.action_steps)
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
            # #2 哑火可观测:哑火轮模型原文 + 性质分类,落库供"卡死"事后定性(放弃/坏调用/提前收尾)。
            "idle_outputs": getattr(result, "idle_outputs", []),
            "complete": bool(execution_complete),
            "done_steps": done_steps,
            "total_steps": total_steps,
            "action_steps": len(result.action_steps),
        },
        "healing": {"action": action_heals},
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
        # 2) 纯时长等待:分段累积到真实请求时长(治 playwright-mcp 单次 ~30s 内部上限,
        #    封顶 _WAIT_MAX_SECONDS),否则「等待观察 N 分钟」实际只等 ~30s。
        if name == _WAIT_TOOL and (secs := _wait_seconds(arguments)) is not None:
            return await _chunked_wait(mcp, secs)
        # 3) 其余 → playwright-mcp
        result = await mcp.call_tool(name, arguments)
        text = mcp.result_to_text(result)
        url = parse_snapshot(text).url  # 结果里若含 Page URL 则提取
        # isError = MCP 结构化失败标志(权威);用于失败判定,避免页面快照内容里的
        # error/timeout 等词被字符串 marker 误伤(见 react_loop._outcome_failed)。
        return ToolOutcome(text=text, url=url, is_error=bool(getattr(result, "isError", False)))

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
        translation_knowledge: str = "",
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
        # 项目级「翻译知识/操作指南」:注入翻译 prompt 助补全流程/对齐术语/写对 expected
        # (受 pre_analysis 两条护栏约束,不接地、不脑补)。区别于 self.context(执行期业务背景)。
        self.translation_knowledge = translation_knowledge
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
        return await self.spec_generator.generate(
            case, knowledge=self.translation_knowledge, on_delta=on_delta
        )

    async def run(
        self,
        case: TestCase,
        spec: TestSpec | None = None,
        ctx: ExecutionContext | None = None,
        step_callback=None,
        run_id: str | None = None,
        should_abort=None,
    ) -> ExecutionRecord:
        """执行一条用例,返回 ExecutionRecord(PASS/FAIL 由断言裁决)。

        ``should_abort``:可选 async () -> bool,协作式停止信号,透传给 ReActLoop 每轮检查。
        """
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

        # playwright-mcp 工具机制是平台固定说明(非业务背景),并入 base 层紧跟 BASE_PROMPT;
        # context 只承载真正的用户业务背景(self.context),无则不渲染「## 业务背景」。
        base = f"{BASE_PROMPT}\n\n{PLAYWRIGHT_MCP_HINT}"
        builder = PromptBuilder(spec, tools, context=self.context, base=base)
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
        # 运行时锚点(2026-06-24):阶段起始 URL 基线。初始 = 起始页(通常登录页);每阶段裁完
        # 更新为当时 URL,供下一阶段判跳转。裁判据「起始→当前」URL 变化确定性地认"导航/登录达成",
        # 治陌生内网落地页被偏-FAIL 误判(裁判认不出 /about 是不是主页)。
        prev_phase_url = {"url": (state.get("url") or "").strip()}

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
            # 页面稳定再 refresh,确保 judge 看的是**稳定终态页**。用短超时(_PHASE_SETTLE_TIMEOUT,
            # 默认 2s):页面多半已稳,这里只兜"SPA 异步内容还在渲染",不必磨满主 settle 的 8s。
            if _SETTLE_ENABLED:
                await settle_page(
                    self.mcp, timeout=_PHASE_SETTLE_TIMEOUT, interval=_SETTLE_INTERVAL
                )
            # resolver 不传:阶段裁决只走 _check_llm_judge(吃 raw_snapshot/current_url,
            # 绝不调 probe.query()),resolver 在这条路上是死参数。词汇表运行时解析当前唯一
            # 真实消费点是 react_loop 操作侧自愈(直连 vocab_resolver,不经本 probe)。
            probe_p = MCPPageProbe(self.mcp)
            await probe_p.refresh()  # 抓当时所处页面快照(阶段边界,页面还在那一刻)
            engine_p = AssertionEngine(
                probe_p, healer=healer, tool_registry=self.tools_registry, llm=self.llm
            )
            r = await engine_p._check_llm_judge(
                Assertion(type="llm_judge", target=expected, expected=expected, confidence="low"),
                prev_url=prev_phase_url["url"],  # 运行时锚点:本阶段起始 URL → 判跳转
            )
            r.phase_index = phase_index  # F2:一等字段,前端按阶段分组
            phase_results.append((phase_index, r))
            validated_phases.add(phase_index)
            # 更新基线:下一阶段的起始 URL = 本阶段裁决时所处页面 URL。
            cur = (state.get("url") or "").strip()
            if cur:
                prev_phase_url["url"] = cur
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
            should_abort=should_abort,  # 协作式停止:用户请求时优雅退出(停因 ABORTED)
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
            elif result.stop_reason == ReActStopReason.ABORTED:
                incomplete_reason = (
                    f"[FAIL] 执行已被用户中止:仅完成 {done_steps}/{total_steps} 步,"
                    f"后续步骤未执行。"
                )
            elif result.stop_reason == ReActStopReason.STEP_FAILED and result.failed_step_no:
                incomplete_reason = (
                    f"[FAIL] 执行未完成:第 {result.failed_step_no} 步"
                    f"「{result.failed_step_target}」反复定位失败(快速失败),"
                    f"疑似前序步骤点错元素致此步找不到目标;仅完成 {done_steps}/{total_steps} 步。"
                )
            elif result.stop_reason == ReActStopReason.LLM_ERROR:
                incomplete_reason = (
                    f"[FAIL] 执行中断:LLM 调用异常(超时/连接/服务端错)——"
                    f"{result.error_message or '(无详情)'};仅完成 {done_steps}/{total_steps} 步,"
                    f"后续步骤未执行。"
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

        # —— 收尾 Hooks(**休眠的通用扩展点**,参考 Claude Code hooks)——
        # 平台只提供机制、默认无 hook(run_executor 传 hooks=None;CLI 亦不预填)——真实执行
        # 中本段不触发,仅当调用方显式装配 HookManager 注入时才跑。三事件:
        #   on_heal —— 执行期发生**操作侧自愈**(react_loop 工具报错后重定位重试)时触发;
        #   on_failure —— 用例 FAIL 时触发;after_case —— 无论成败都跑(通常用于清理)。
        # 〔阶段化重设计后阶段裁决走 _check_llm_judge 直连、**不过 verify() 的 healable 装饰**
        #   → 断言侧自愈不存在,on_heal 只由操作侧自愈触发。〕
        if self.hooks is not None:
            ctx.set("passed", passed)
            # 操作侧自愈明细:react_loop 每步落定时已把 heal_attempts 累加进 record.heal_count
            # (recorder.add_step),此处只透传明细 + 复用那个**单一来源**计数给 hook,不再重算。
            action_heals = [h for s in result.action_steps for h in s.heal_attempts]
            if action_heals:
                ctx.set("heal_count", recorder.record.heal_count)
                ctx.set("action_heals", action_heals)
                logger.info("用例 %s 发生操作侧自愈 %d 次", case.id, len(action_heals))
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

    def _token_usage(self) -> int:
        fn = getattr(self.llm, "usage_summary", None)
        if callable(fn):
            try:
                return fn().total_tokens
            except Exception:  # noqa: BLE001
                return 0
        return 0
