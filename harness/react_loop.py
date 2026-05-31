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
from harness.step_plan import StepPlan
from input.models import ActionStep

logger = logging.getLogger(__name__)

_TEST_RESULT_RE = re.compile(r"TEST_RESULT\s*[:：]\s*(PASS|FAIL)", re.IGNORECASE)
_INTENT_RE = re.compile(r"(?:INTENT|意图)\s*[:：]\s*(.+)")


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


@dataclass
class ReActResult:
    action_steps: list[ActionStep] = field(default_factory=list)
    llm_result: str | None = None  # 模型自报的 PASS/FAIL(仅参考)
    stop_reason: StopReason = StopReason.LLM_FINISHED
    iterations: int = 0


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
        max_idle_nudges: int = 3,
        healer=None,
        get_snapshot=None,
        compactor=None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.max_idle_nudges = max_idle_nudges
        # 操作侧自愈:工具报错/找不到元素时重定位(可选)
        self.healer = healer
        self.get_snapshot = get_snapshot  # async () -> str,返回当前页面快照文本
        self.compactor = compactor  # Context Compact(可选),发 LLM 前压缩历史
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

        for iteration in range(1, self.max_steps + 1):
            result.iterations = iteration
            # 每轮刷新 system,反映最新 StepPlan 进度
            messages[0]["content"] = self.build_system(self.step_plan)

            # Context Compact:发 LLM 前压缩历史观察(按当前步骤关键词保相关度)
            if self.compactor is not None:
                self.compactor.compact_inplace(messages, self._current_keywords())

            try:
                resp = await self.llm.chat(messages, tools=self.tools)
            except LLMToolCallError as e:
                logger.warning("tool_call 容错后仍失败,终止循环:%s", e)
                result.stop_reason = StopReason.TOOL_CALL_ERROR
                break

            reasoning = resp.content or ""
            maybe_result = parse_test_result(reasoning)
            if maybe_result:
                result.llm_result = maybe_result

            # 模型不再调用工具
            if not resp.tool_calls:
                # 真完成:所有步骤已落定,或模型明确给出了 TEST_RESULT
                if self.step_plan.all_resolved() or maybe_result is not None:
                    result.stop_reason = StopReason.LLM_FINISHED
                    break
                # 否则模型"哑火"但还有步骤没做 → 推它继续(防呆上限内)
                idle_nudges += 1
                if idle_nudges > self.max_idle_nudges:
                    logger.warning("模型连续 %d 次未推进且无 TEST_RESULT,终止", idle_nudges)
                    result.stop_reason = StopReason.LLM_FINISHED
                    break
                cur = self.step_plan.current
                cur_desc = f"第 {cur.step_no} 步「{cur.describe()}」" if cur else "剩余步骤"
                messages.append({"role": "assistant", "content": reasoning})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"你还没有完成所有步骤,当前应执行{cur_desc}。"
                            "请继续调用工具执行,完成该步后调用 mark_step_done;"
                            "所有步骤都完成后再输出 TEST_RESULT。不要提前停止。"
                        ),
                    }
                )
                continue
            idle_nudges = 0  # 本轮有工具调用,重置哑火计数

            # 循环检测
            sig = _signature(resp.tool_calls)
            recent_sigs.append(sig)
            if len(recent_sigs) == self.loop_window and len(set(recent_sigs)) == 1:
                logger.warning("连续 %d 轮相同 tool_call,判定卡死,终止", self.loop_window)
                result.stop_reason = StopReason.LOOP_DETECTED
                break

            # 记录模型的「思考 + 决策」
            messages.append(
                {"role": "assistant", "content": reasoning or _render_calls(resp.tool_calls)}
            )

            # Act + Observe:逐个执行 tool_call
            intent = _parse_intent(reasoning)
            for tc in resp.tool_calls:
                step_no += 1
                started = time.monotonic()
                outcome = await self._execute_one(tc)
                duration_ms = int((time.monotonic() - started) * 1000)

                # 操作侧自愈:工具报错/找不到元素时重定位,回灌建议引导重试
                heal_attempts: list[dict] = []
                obs_suffix = ""
                if self.healer is not None and _is_tool_failure(outcome.text):
                    heal_attempts, obs_suffix = await self._heal_action(tc, intent)

                result.action_steps.append(
                    ActionStep(
                        step_no=step_no,
                        tool_name=tc.name,
                        tool_input=tc.arguments,
                        reasoning=reasoning,
                        intent=intent,
                        tool_result=outcome.text,
                        screenshot=outcome.screenshot,
                        url=outcome.url,
                        is_custom_tool=outcome.is_custom_tool,
                        is_hook_action=outcome.is_hook_action,
                        duration_ms=duration_ms,
                        heal_attempts=heal_attempts,
                    )
                )
                # 观察回灌(含自愈建议)
                messages.append({"role": "user", "content": f"[观察] {outcome.text}{obs_suffix}"})

            # 所有步骤已落定 → 完成(交由外层跑断言裁决)
            if self.step_plan.all_resolved():
                result.stop_reason = StopReason.COMPLETED
                break
        else:
            result.stop_reason = StopReason.MAX_STEPS

        return result

    def _current_keywords(self) -> list[str]:
        """当前步骤的关键词,供 L2 相关度截断。"""
        cur = self.step_plan.current
        if cur is None:
            return []
        kws = [cur.target, cur.action]
        if cur.data:
            kws.append(cur.data)
        # target 里的分词也加入(中文按整体,英文/空格切分)
        kws += [w for w in cur.target.replace("(", " ").replace(")", " ").split() if w]
        return [k for k in kws if k]

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

        heal = await self.healer.relocate(
            intent=intent or tc.name, target=str(target), snapshot_text=snapshot_text
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
