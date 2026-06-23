"""ReAct 执行循环(规格 §5.4 ReAct Loop,T-06)。

Reason → Act → Observe 循环:

- **Reason**:读 StepPlan 状态 +(工具结果带回的)A11y/URL 观察 → LLM 决策。记 reasoning。
- **Act**:执行 tool_call(MCP / Custom Tool / mark_step_done)。记 intent。
- **Observe**:工具结果文本(playwright-mcp 的结果自带 A11y 快照)作为「观察」回灌。

安全护栏:
- 循环检测:连续 3 轮相同 tool_call 签名 → 终止(防本地 LLM 卡死)。
- max_steps 上限。
- 解析 LLM 输出的 ``TEST_RESULT: PASS/FAIL``,**但最终 PASS/FAIL 以断言结果为准**
  (本循环只负责执行与记录,断言由 T-08 在循环外裁决)。

消息流采用「观察作为 user 消息回灌」的文本式 ReAct,而非严格的 tool_call_id 配对——
本地模型(Qwen3)对 id 配对支持不稳,文本式更鲁棒,且我方 LLM 封装已做 tool_call 容错。
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable

from harness.llm import LLMClient, LLMToolCallError, ToolCall
from harness.page_probe import build_ref_index, parse_snapshot
from harness.step_plan import MARK_STEP_DONE_TOOL, StepPlan, StepStatus
from input.models import ActionStep

logger = logging.getLogger(__name__)

_TEST_RESULT_RE = re.compile(r"TEST_RESULT\s*[:：]\s*(PASS|FAIL)", re.IGNORECASE)
_INTENT_RE = re.compile(r"(?:INTENT|意图)\s*[:：]\s*(.+)")
# playwright-mcp 的 ref 形如 e11 / e123;模型有时把它放在 ref 之外的别名参数里(实测 DeepSeek
# 放进 target)。据此从别名回收 ref,恢复「执行期捕获真实 role+name」。
_REF_RE = re.compile(r"^e\d+$")
# 从 tool_result 的「Ran Playwright code」块抓**实际执行的定位表达式**(ground truth):
# 形如 page.locator('[data-test="username"]') / page.getByRole('button', { name: 'Login' })。
_EXEC_LOCATOR_RE = re.compile(r"page\.(?:locator|getBy[A-Za-z]+)\([^()]*\)")


def _ref_alias(arguments: dict) -> str | None:
    """从 tool_call 参数里取 ref:优先 ``ref``,否则看 ``target``/``element_ref`` 等是否像 ref。"""
    direct = arguments.get("ref")
    if direct:
        return str(direct)
    for k in ("target", "element_ref", "ref_id"):
        v = arguments.get(k)
        if v and _REF_RE.match(str(v).strip()):
            return str(v).strip()
    return None


def _safe_int(v) -> int:
    """宽松取整(mark_step_done 的 step_no 可能是 str/float),失败返回 0。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def extract_executed_locator(text: str) -> str:
    """从工具结果文本里抽取首个实际执行的 Playwright 定位表达式(无则空串)。"""
    if not text:
        return ""
    m = _EXEC_LOCATOR_RE.search(text)
    return m.group(0) if m else ""


@dataclass
class ToolOutcome:
    """工具执行结果 + 观察。"""

    text: str
    url: str = ""
    screenshot: str | None = None
    is_custom_tool: bool = False
    is_hook_action: bool = False


# 执行器:把 (name, arguments) 执行掉,返回 ToolOutcome 或纯文本
ToolExecutor = Callable[[str, dict], Awaitable["ToolOutcome | str"]]
# 系统提示词构造器:依当前 StepPlan 状态生成 system prompt(T-07 提供实现)
SystemBuilder = Callable[[StepPlan], str]


class StopReason(str, Enum):
    LLM_FINISHED = "llm_finished"  # 模型不再调用工具(自认完成)
    COMPLETED = "completed"  # 所有步骤已落定
    MAX_STEPS = "max_steps"  # 触达步数上限
    LOOP_DETECTED = "loop_detected"  # 连续重复同一调用
    TOOL_CALL_ERROR = "tool_call_error"  # tool_call 容错+重试仍失败
    STEP_FAILED = "step_failed"  # 单步连续定位失败超预算(快速失败,疑似点错前序元素)
    PHASE_FAILED = "phase_failed"  # 阶段边界 Validator 判该阶段 expected 未达成(阶段失败即失败)


@dataclass
class ReActResult:
    action_steps: list[ActionStep] = field(default_factory=list)
    llm_result: str | None = None  # 模型自报的 PASS/FAIL(仅参考)
    stop_reason: StopReason = StopReason.LLM_FINISHED
    iterations: int = 0
    idle_nudges: int = 0  # 模型"哑火"(只回文字不调工具)被续推的累计次数(健康度指标)
    failed_step_no: int = 0  # STEP_FAILED 时:卡死的业务步编号(0=无)
    failed_step_target: str = ""  # STEP_FAILED 时:该步的目标语义(诊断"点错哪个")
    failed_phase_index: int = -1  # PHASE_FAILED 时:未达成的阶段(0-based;-1=无)
    failed_phase_reason: str = ""  # PHASE_FAILED 时:Validator 给的未达成原因


def _signature(tool_calls: list[ToolCall]) -> str:
    """一轮 tool_calls 的签名,用于循环检测。"""
    return json.dumps(
        [[tc.name, tc.arguments] for tc in tool_calls],
        sort_keys=True,
        ensure_ascii=False,
    )


def _render_calls(tool_calls: list[ToolCall]) -> str:
    return "; ".join(
        f"{tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})" for tc in tool_calls
    )


def _arg_brief(arguments: dict, limit: int = 120) -> str:
    """工具参数的单行摘要(进度日志用),过长截断避免刷屏。"""
    try:
        s = json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(arguments)
    return s if len(s) <= limit else s[:limit] + "…"


def parse_test_result(content: str | None) -> str | None:
    """从内容里解析 TEST_RESULT: PASS/FAIL(大小写不敏感),无则 None。"""
    if not content:
        return None
    m = _TEST_RESULT_RE.search(content)
    return m.group(1).upper() if m else None


def _parse_intent(content: str | None) -> str:
    if not content:
        return ""
    m = _INTENT_RE.search(content)
    return m.group(1).strip() if m else ""


# playwright-mcp 失败/找不到元素的标志(用于触发操作侧自愈)
_FAILURE_MARKERS = (
    "### Error",
    "[工具执行异常]",
    "Unknown engine",
    "Timeout",
    "resolved to 0 element",
    "no element",
    "not found",
    "strict mode violation",
)


def _is_tool_failure(text: str | None) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(m.lower() in low for m in _FAILURE_MARKERS)


class ReActLoop:
    """ReAct 主循环。执行与记录;不裁决最终 PASS/FAIL。"""

    def __init__(
        self,
        llm: LLMClient,
        tools: list[dict],
        execute: ToolExecutor,
        step_plan: StepPlan,
        build_system: SystemBuilder,
        *,
        task_message: str = "开始执行测试。请按执行计划逐步操作,每完成一步调用 mark_step_done。",
        max_steps: int = 30,
        loop_window: int = 3,
        max_idle_nudges: int = 5,
        healer=None,
        get_snapshot=None,
        get_screenshot=None,
        compactor=None,
        capture_screenshot=None,
        on_step=None,
        on_llm_delta=None,
        vocab_resolver=None,
        on_phase_end=None,
        step_fail_budget: int = 3,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.max_idle_nudges = max_idle_nudges
        # 操作侧自愈:工具报错/找不到元素时重定位(可选)
        self.healer = healer
        self.get_snapshot = get_snapshot  # async () -> str,返回当前页面快照文本
        self.get_screenshot = get_screenshot  # async () -> base64|None,视觉自愈双通道
        self.compactor = compactor  # Context Compact(可选),发 LLM 前压缩历史
        # 截图回调:async (step_no, tool_name) -> filename|None;每步执行后落盘截图
        self.capture_screenshot = capture_screenshot
        # 实时步骤回调:async (ActionStep) -> None;每步落定后立即回调(供 SSE 实时推送进度)
        self.on_step = on_step
        # reasoning 流式回调:async (text) -> None;每轮 LLM 思考逐 token 推送(执行期
        # 「思考过程」+ 慢模型下对网关保活,防 ReAct 期 LLM 调用空闲超时切 SSE)。
        self.on_llm_delta = on_llm_delta
        # 词汇表解析器(可选):操作侧自愈时按业务词查真实页面名,作为 P1 候选(规格 §5.4
        # "词汇表第一优先查询")。无则自愈退回纯快照启发式。
        self.vocab_resolver = vocab_resolver
        # 阶段边界 Validator 回调(可选):async (phase_index) -> str | None。某阶段**最后一步**
        # mark_step_done 落定后触发,在【当时所处页面】用偏-FAIL 证据接地裁判核验该阶段 expected。
        # 返回 None/空 = 该阶段通过、继续;返回**非空原因串** = 未达成 → 用例直接失败(阶段失败
        # 即失败,本轮不做 replan/重试),停因 PHASE_FAILED。expected 只在此核验,不进驱动(FG01)。
        self.on_phase_end = on_phase_end
        # 单步定位失败预算(#1 快速失败):同一业务步**累计**定位失败(自愈也没救回)达此数 →
        # 快速判 STEP_FAILED 终止(疑似点错前序元素致后续找不到目标),不再磨到 max_steps。
        self.step_fail_budget = step_fail_budget
        self.execute = execute
        self.step_plan = step_plan
        self.build_system = build_system
        self.task_message = task_message
        self.max_steps = max_steps
        self.loop_window = loop_window

    async def run(self) -> ReActResult:
        result = ReActResult()
        messages: list[dict] = [
            {"role": "system", "content": self.build_system(self.step_plan)},
            {"role": "user", "content": self.task_message},
        ]
        recent_sigs: deque[str] = deque(maxlen=self.loop_window)
        step_no = 0
        idle_nudges = 0  # 模型"哑火"(无 tool_call 且未完成)时的连续推动次数
        # 最近一次观察(工具结果)文本,含 playwright-mcp 快照。LLM 本轮回传的 ref 即
        # 对应它最近观察到的这份快照 → 据此回查被操作元素的真实 (role, name)。
        last_snapshot_text = ""
        # B-软最小护栏(只接「过早 mark_done」):记录每个 StepPlan 步骤是否真的做过操作,
        # 以及已对哪些步骤软提示过(每步至多拦一次,避免误判空转)。
        acted_steps: set[int] = set()  # 该 step_no 下执行过「操作类」工具(非 snapshot/非 mark)
        nudged_mark: set[int] = set()  # 已就「过早 mark_done」提示过的 step_no
        step_fail_count: dict[int, int] = {}  # 业务步 → 累计定位失败次数(#1 单步失败预算)

        for iteration in range(1, self.max_steps + 1):
            result.iterations = iteration
            # 每轮刷新 system,反映最新 StepPlan 进度
            messages[0]["content"] = self.build_system(self.step_plan)

            # Context Compact:发 LLM 前压缩历史观察(按当前步骤关键词保相关度)
            if self.compactor is not None:
                self.compactor.compact_inplace(messages, self._current_keywords())

            # 捕获本轮请求(供「查看 prompt」调试):System Prompt + 触发本轮的最近输入。
            # 不存完整历史(多份快照过重),只取改 prompt 最需要看的两段。
            current_prompt = self._snapshot_prompt(messages)

            try:
                resp = await self.llm.chat(messages, tools=self.tools, on_delta=self.on_llm_delta)
            except LLMToolCallError as e:
                # 铁律3:偶发 tool_call 格式错误不得搞崩 ReAct 循环。还有未完成步骤时,
                # 不直接终止,而是哑火续推(纠偏后让模型重出正确调用),仅在预算耗尽/
                # 步骤已全部落定时才真正终止 → 治"输入密码后模型吐了个坏调用就停在中途"。
                logger.warning("tool_call 容错后仍失败:%s", e)
                idle_nudges += 1
                if self.step_plan.all_resolved() or idle_nudges > self.max_idle_nudges:
                    result.stop_reason = StopReason.TOOL_CALL_ERROR
                    break
                cur = self.step_plan.current
                cur_desc = f"第 {cur.step_no} 步「{cur.describe()}」" if cur else "剩余步骤"
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"上一次工具调用格式有误,已忽略。请重新输出一个**格式正确**的"
                            f"工具调用以继续{cur_desc};不要重复刚才的错误格式,也不要提前结束。"
                        ),
                    }
                )
                continue

            # 流式偶发丢 tool_call(stream_chunk_builder 重建有概率漏采)→ 无调用且仍有
            # 未完成步骤时,**非流式复核一次**把漏采的调用捞回来,避免把「其实模型调了工具」
            # 误判为哑火空转(治流式下 ReAct 期偶发的「连续未推进→终止」)。
            if (
                self.on_llm_delta is not None
                and not resp.tool_calls
                and not self.step_plan.all_resolved()
            ):
                try:
                    resp = await self.llm.chat(messages, tools=self.tools)
                except LLMToolCallError:
                    pass  # 复核也失败 → 维持原结果,走下面哑火逻辑

            reasoning = resp.content or ""
            maybe_result = parse_test_result(reasoning)
            if maybe_result:
                result.llm_result = maybe_result

            # 模型不再调用工具
            if not resp.tool_calls:
                # 真完成:所有步骤已落定(空 plan 亦为真)→ 结束。
                # 注意:**有未完成步骤时,绝不因模型自报 TEST_RESULT 而终止**(铁律4:
                # 最终 PASS/FAIL 以断言裁决为准)。否则 DeepSeek 等会在登录后提前吐一句
                # TEST_RESULT 就让循环在中途停下,后续步骤(如加购)永远不执行。
                if self.step_plan.all_resolved():
                    result.stop_reason = StopReason.LLM_FINISHED
                    break
                # 模型"哑火"或提前收尾,但还有步骤没做 → 推它继续(防呆上限内)
                idle_nudges += 1
                if idle_nudges > self.max_idle_nudges:
                    logger.warning("模型连续 %d 次未推进(步骤未完成),终止", idle_nudges)
                    result.stop_reason = StopReason.LLM_FINISHED
                    break
                cur = self.step_plan.current
                cur_desc = f"第 {cur.step_no} 步「{cur.describe()}」" if cur else "剩余步骤"
                premature = (
                    "(你输出了 TEST_RESULT,但步骤尚未全部完成,系统不会采信。)"
                    if maybe_result is not None
                    else ""
                )
                # 主动抓一份**最新完整快照**塞回去再催促。模型"只回文字不调工具"多因手里
                # 没有可用 ref——上下文压缩把旧快照折叠/按中文关键词截断了(页面文案常是英文,
                # 关键词命不中 → 目标行连同 ref 被丢)。直接喂当前页面 + 强制只发一个工具调用,
                # 比单纯文字催促更能逼出动作(实测 DeepSeek 抓完快照后退化成叙述、卡死至终止)。
                fresh = await self._safe_snapshot()
                messages.append({"role": "assistant", "content": reasoning})
                step_no_hint = cur.step_no if cur else "该步"
                nudge = (
                    f"你只输出了文字、没有调用任何工具,系统判定为未推进。现在必须执行{cur_desc}。"
                    f"{premature}请**立即只调用一个工具**:"
                    f"若该步骤的页面操作其实已经完成,直接调用 mark_step_done(step_no={step_no_hint}) 推进;"
                    "否则用快照里对应元素的 ref 调用 browser_click / browser_type / "
                    "browser_select_option 等操作目标元素。"
                    "所有步骤完成后才输出 TEST_RESULT。禁止只回复文字而不调用工具。"
                )
                if fresh:
                    # 作为**普通** user 消息附快照(不加 [观察] 前缀 → 不被 Context Compact
                    # 折叠/截断),确保模型下一轮能看到完整 ref 去操作。
                    nudge += f"\n\n[当前页面快照]\n{fresh}"
                    if "[ref=" in fresh:
                        last_snapshot_text = fresh
                else:
                    nudge += "若不确定页面元素,先调用 browser_snapshot 获取带 ref 的快照,再操作。"
                messages.append({"role": "user", "content": nudge})
                continue
            idle_nudges = 0  # 本轮有工具调用,重置哑火计数

            # 循环检测
            sig = _signature(resp.tool_calls)
            recent_sigs.append(sig)
            if len(recent_sigs) == self.loop_window and len(set(recent_sigs)) == 1:
                logger.warning("连续 %d 轮相同 tool_call,判定卡死,终止", self.loop_window)
                result.stop_reason = StopReason.LOOP_DETECTED
                break

            # B-软最小护栏:仅当本轮**只调用** mark_step_done、且该步从未做过任何操作类工具、
            # 且尚未就此步提示过 → 软提示先实操,不采信本次标记(铁律2(a):软、可恢复、不判失败)。
            # 限「只调 mark」+「每步至多一次」双重保守,避免误判把正常流程拖进空转。
            guard_nudge = self._guard_premature_mark(resp.tool_calls, acted_steps, nudged_mark)
            if guard_nudge is not None:
                messages.append(
                    {"role": "assistant", "content": reasoning or _render_calls(resp.tool_calls)}
                )
                messages.append({"role": "user", "content": guard_nudge})
                continue

            # 记录模型的「思考 + 决策」
            messages.append(
                {"role": "assistant", "content": reasoning or _render_calls(resp.tool_calls)}
            )

            # Act + Observe:逐个执行 tool_call
            intent = _parse_intent(reasoning)
            # 本轮所有 ref 都基于「上一次观察到的快照」分配,先建一份 ref→节点 索引
            ref_index = build_ref_index(last_snapshot_text)
            cur_step = self.step_plan.current
            cur_target = cur_step.text if cur_step is not None else ""
            step_failed_stop = False  # 本轮内是否触发单步失败预算 / 阶段失败(均终止)
            for tc in resp.tool_calls:
                step_no += 1
                started = time.monotonic()
                outcome = await self._execute_one(tc)
                duration_ms = int((time.monotonic() - started) * 1000)

                # 每步进度(便于失败时回看执行轨迹):工具 + 参数摘要 + 成功/失败 + 耗时。
                failed = _is_tool_failure(outcome.text)
                logger.log(
                    logging.WARNING if failed else logging.INFO,
                    "步骤 %d: %s(%s)%s %dms%s",
                    step_no,
                    tc.name,
                    _arg_brief(tc.arguments),
                    " 失败" if failed else " ok",
                    duration_ms,
                    f" | {outcome.text[:160]}" if failed else "",
                )

                # 操作侧自愈:工具报错/找不到元素时重定位,回灌建议引导重试
                heal_attempts: list[dict] = []
                obs_suffix = ""
                if self.healer is not None and _is_tool_failure(outcome.text):
                    heal_attempts, obs_suffix = await self._heal_action(tc, intent)

                # 截图落盘(可选):每步执行后抓当前页面,回调决定是否截(非浏览器工具跳过)
                shot = outcome.screenshot
                if self.capture_screenshot is not None and not _is_tool_failure(outcome.text):
                    try:
                        shot = await self.capture_screenshot(step_no, tc.name) or shot
                    except Exception as e:  # noqa: BLE001 — 截图失败不影响执行
                        logger.warning("步骤 %d 截图失败:%s", step_no, e)

                # 执行期捕获:从操作回传的 ref(含 target 等别名)回查上一份快照,拿真实 role+name;
                # 同时抓**实际执行的定位表达式**(ground truth,优先于快照重建,见 codegen 对齐)。
                el_role, el_name = "", ""
                ref = _ref_alias(tc.arguments)
                if ref and (node := ref_index.get(str(ref))) is not None:
                    el_role, el_name = node.role, node.name
                el_selector = extract_executed_locator(outcome.text)

                result.action_steps.append(
                    ActionStep(
                        step_no=step_no,
                        tool_name=tc.name,
                        tool_input=tc.arguments,
                        reasoning=reasoning,
                        intent=intent,
                        prompt=current_prompt,
                        tool_result=outcome.text,
                        screenshot=shot,
                        url=outcome.url,
                        element_role=el_role,
                        element_name=el_name,
                        element_selector=el_selector,
                        step_target=cur_target,
                        is_custom_tool=outcome.is_custom_tool,
                        is_hook_action=outcome.is_hook_action,
                        duration_ms=duration_ms,
                        heal_attempts=heal_attempts,
                    )
                )
                # 实时回调:本步已落定,立即推送(SSE 实时进度,不等整轮/整条用例结束)
                if self.on_step is not None:
                    try:
                        await self.on_step(result.action_steps[-1])
                    except Exception as e:  # noqa: BLE001 — 推送失败不影响执行
                        logger.warning("on_step 回调失败:%s", e)

                # 「操作类」工具(非 snapshot/非 mark)记账:成功 → 标记该步已实操(过早 mark
                # 护栏据此);失败 → 累计该步定位失败次数(#1 单步失败预算据此)。
                is_op_tool = cur_step is not None and tc.name not in (
                    MARK_STEP_DONE_TOOL,
                    "browser_snapshot",
                )
                if is_op_tool and not failed:
                    acted_steps.add(cur_step.step_no)
                elif is_op_tool and failed:
                    step_fail_count[cur_step.step_no] = step_fail_count.get(cur_step.step_no, 0) + 1

                # 观察回灌(含自愈建议)
                messages.append({"role": "user", "content": f"[观察] {outcome.text}{obs_suffix}"})
                # 记下本次观察快照,供下一轮 ref 回查。仅当观察里**真的带 ref**(浏览器工具
                # 的 a11y 快照)才更新——否则「操作→mark_step_done」这种常见序列会用
                # mark_step_done 的非快照输出覆盖掉快照,令下一轮 ref 索引为空、捕获漏采。
                if outcome.text and "[ref=" in outcome.text:
                    last_snapshot_text = outcome.text

                # #1 快速失败:同一业务步累计定位失败达预算(自愈也没救回)→ 终止,
                # 标明卡死步(疑似点错前序元素致后续找不到目标)。不再磨到 max_steps。
                if is_op_tool and step_fail_count.get(cur_step.step_no, 0) >= self.step_fail_budget:
                    logger.warning(
                        "第 %d 步「%s」累计定位失败 %d 次(预算 %d),快速失败终止",
                        cur_step.step_no,
                        cur_step.text,
                        step_fail_count[cur_step.step_no],
                        self.step_fail_budget,
                    )
                    result.stop_reason = StopReason.STEP_FAILED
                    result.failed_step_no = cur_step.step_no
                    result.failed_step_target = cur_step.text
                    step_failed_stop = True
                    break

                # 阶段边界 Validator:mark_step_done 让某【阶段最后一步】落定 DONE → 在当时所处
                # 页面用偏-FAIL 证据接地裁判核验该阶段 expected。通过 → 继续;未达成 → 用例直接
                # 失败(阶段失败即失败,不做 replan/重试),停因 PHASE_FAILED。回调由外层提供。
                if (
                    tc.name == MARK_STEP_DONE_TOOL
                    and self.on_phase_end is not None
                    and self.step_plan.is_phase_last_step(_safe_int(tc.arguments.get("step_no")))
                ):
                    done_no = _safe_int(tc.arguments.get("step_no"))
                    ps = self.step_plan.get(done_no)
                    if ps is not None and ps.status == StepStatus.DONE:
                        reason = None
                        try:
                            reason = await self.on_phase_end(ps.phase_index)
                        except Exception as e:  # noqa: BLE001 — 回调异常按"未拦"处理,继续
                            logger.warning(
                                "阶段 Validator 回调失败(phase %s):%s", ps.phase_index, e
                            )
                            reason = None
                        if reason:  # 非空 → 该阶段 expected 未达成 → 阶段失败即失败
                            logger.warning(
                                "阶段 %d 的 expected 未达成(在第 %d 步边界核验):%s",
                                ps.phase_index + 1,
                                done_no,
                                reason,
                            )
                            result.stop_reason = StopReason.PHASE_FAILED
                            result.failed_phase_index = ps.phase_index
                            result.failed_phase_reason = reason
                            result.failed_step_no = done_no
                            result.failed_step_target = ps.text
                            step_failed_stop = True
                            break

            if step_failed_stop:
                break
            # 所有步骤已落定 → 完成(交由外层跑阶段裁决汇总)
            if self.step_plan.all_resolved():
                result.stop_reason = StopReason.COMPLETED
                break
        else:
            result.stop_reason = StopReason.MAX_STEPS

        result.idle_nudges = idle_nudges
        return result

    @staticmethod
    def _snapshot_prompt(messages: list[dict]) -> str:
        """把本轮发给 LLM 的请求拼成可读文本:System Prompt + 最近一条输入。"""
        system = messages[0]["content"] if messages else ""
        last_user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        return f"### System Prompt\n{system}\n\n### 最近输入(本轮触发)\n{last_user}"

    @staticmethod
    def _guard_premature_mark(
        tool_calls: list[ToolCall], acted_steps: set[int], nudged_mark: set[int]
    ) -> str | None:
        """过早 mark_done 软护栏(B-软最小版,只接此一种)。

        触发条件(三重保守,避免误判正常流程):本轮**仅**一个工具调用且为 mark_step_done、
        其 step_no **从未执行过操作类工具**、且**尚未就此步提示过**。触发则返回软提示串
        (调用方回灌并跳过本次标记);否则返回 None 放行。已提示过的步骤再次标记即放行
        (覆盖「纯校验/状态已满足、确实无需操作」的合法步骤,代价至多一次多余往返)。
        """
        if len(tool_calls) != 1:
            return None
        tc = tool_calls[0]
        if tc.name != MARK_STEP_DONE_TOOL:
            return None
        raw = tc.arguments.get("step_no") if isinstance(tc.arguments, dict) else None
        try:
            step_no = int(raw)
        except (TypeError, ValueError):
            return None
        if step_no in acted_steps or step_no in nudged_mark:
            return None
        nudged_mark.add(step_no)
        return (
            f"你要把第 {step_no} 步标记完成,但本步还没执行任何页面操作(点击/输入/选择)。"
            f"请先用快照里对应元素的 ref 实际执行该步骤的操作,确认页面已响应,再调用 "
            f"mark_step_done(step_no={step_no})。"
            f"若该步骤确实无需任何页面操作(纯校验 / 状态已满足),可直接再次调用 "
            f"mark_step_done(step_no={step_no}) 推进。"
        )

    def _current_keywords(self) -> list[str]:
        """当前步骤的关键词,供 L2 相关度截断。"""
        cur = self.step_plan.current
        if cur is None:
            return []
        text = cur.text or ""
        # 步骤文本整体 + 分词(中文按整体,英文/空格切分)
        kws = [text]
        kws += [w for w in text.replace("(", " ").replace(")", " ").split() if w]
        return [k for k in kws if k]

    async def _safe_snapshot(self) -> str:
        """安全地取一份当前页面快照(无 get_snapshot 或失败时返回空串)。"""
        if self.get_snapshot is None:
            return ""
        try:
            return await self.get_snapshot() or ""
        except Exception as e:  # noqa: BLE001 — 取快照失败不应炸循环
            logger.warning("idle 续推取快照失败:%s", e)
            return ""

    async def _heal_action(self, tc: ToolCall, intent: str) -> tuple[list[dict], str]:
        """工具失败时调自愈重定位,返回 (heal_attempts, 回灌给 LLM 的建议后缀)。"""
        target = tc.arguments.get("element") or tc.arguments.get("target") or intent or tc.name
        snapshot_text = ""
        if self.get_snapshot is not None:
            try:
                snapshot_text = await self.get_snapshot()
            except Exception as e:  # noqa: BLE001
                logger.warning("自愈取快照失败:%s", e)
        if not snapshot_text:
            return [], ""

        # 词汇表优先(规格 §5.4):按业务词查到真实页面名 → 作为 P1 命中候选喂给 healer
        vocabulary: dict | None = None
        if self.vocab_resolver is not None:
            try:
                snap = parse_snapshot(snapshot_text)
                entry = await self.vocab_resolver.resolve(
                    str(target), url=snap.url, title=snap.title
                )
                if isinstance(entry, dict) and entry.get("name"):
                    vocabulary = {str(target): str(entry["name"])}
            except Exception as e:  # noqa: BLE001 — 查词失败不影响自愈兜底
                logger.warning("自愈查词汇表失败:%s", e)

        # 视觉双通道(规格 §5.4 P5):取一张截图一并喂给自愈,治图标/角标类文本对不上的误判
        screenshot: str | None = None
        if self.get_screenshot is not None:
            try:
                screenshot = await self.get_screenshot()
            except Exception as e:  # noqa: BLE001 — 截图失败退回纯文本通道
                logger.warning("操作侧自愈取截图失败:%s", e)
        heal = await self.healer.relocate(
            intent=intent or tc.name,
            target=str(target),
            snapshot_text=snapshot_text,
            vocabulary=vocabulary,
            screenshot=screenshot,
        )
        attempt = {
            "tool": tc.name,
            "target": str(target),
            "healed": heal.healed,
            "summary": heal.summary,
            "chosen": heal.chosen.target if heal.chosen else None,
            "strategy": heal.chosen.strategy if heal.chosen else None,
        }
        if heal.healed and heal.chosen is not None:
            suffix = (
                f"\n[自愈建议] 目标「{target}」定位失败;页面上更可能对应的是"
                f"「{heal.chosen.target}」({heal.chosen.strategy})。请改用它重试,不要重复同一个失败调用。"
            )
        else:
            suffix = (
                f"\n[自愈] 未能为「{target}」找到可靠替代。"
                "请重新 browser_snapshot 观察页面,换一种定位方式,不要重复同一调用。"
            )
        return [attempt], suffix

    async def _execute_one(self, tc: ToolCall) -> ToolOutcome:
        try:
            raw = await self.execute(tc.name, tc.arguments)
        except Exception as e:  # noqa: BLE001 — 单个工具失败不应炸掉循环
            logger.warning("工具 %s 执行异常:%s", tc.name, e)
            return ToolOutcome(text=f"[工具执行异常] {e}")
        if isinstance(raw, ToolOutcome):
            return raw
        return ToolOutcome(text=str(raw))
