"""StepPlan / TodoWrite 机制(规格 §5.4 StepPlan,T-04)。

把 TestSpec.steps 展开成带状态机的执行计划,防止多步骤用例跳步/遗漏/重复:

- 状态机:``pending → active → done`` / ``failed`` / ``skipped``。
- 初始第 1 步为 ``active``,其余 ``pending``。
- LLM 每完成一步调用 ``mark_step_done(step_no)`` 工具更新状态,自动推进下一步。
- 序列化为 System Prompt 片段(TodoWrite 清单),注入给 LLM 感知进度。

对本地 LLM 取**宽容**策略:允许标记非当前步(只告警),非法编号返回错误文本
而非抛栈,避免偶发误调用炸掉 ReAct 循环。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from input.models import SpecStep, TestSpec

logger = logging.getLogger(__name__)


class StepStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


# 清单前缀符号
_MARK = {
    StepStatus.PENDING: "[ ]",
    StepStatus.ACTIVE: "[→]",
    StepStatus.DONE: "[x]",
    StepStatus.FAILED: "[✗]",
    StepStatus.SKIPPED: "[-]",
}

MARK_STEP_DONE_TOOL = "mark_step_done"


@dataclass
class PlanStep:
    step_no: int  # 1-based
    action: str
    target: str
    data: str | None = None
    status: StepStatus = StepStatus.PENDING
    note: str = ""  # 失败原因等

    def describe(self) -> str:
        s = f"{self.action} → {self.target}"
        if self.data:
            s += f"(数据: {self.data})"
        return s


class StepPlan:
    """多步骤执行计划 + 状态机。"""

    def __init__(self, steps: list[SpecStep]) -> None:
        self.steps: list[PlanStep] = [
            PlanStep(step_no=i, action=s.action, target=s.target, data=s.data)
            for i, s in enumerate(steps, start=1)
        ]
        if self.steps:
            self.steps[0].status = StepStatus.ACTIVE

    @classmethod
    def from_spec(cls, spec: TestSpec) -> "StepPlan":
        """由 TestSpec 构建(given 步骤由 Hook/前置处理,这里只排业务步骤)。"""
        return cls(spec.steps)

    # ── 查询 ──────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self.steps)

    def get(self, step_no: int) -> PlanStep | None:
        if 1 <= step_no <= len(self.steps):
            return self.steps[step_no - 1]
        return None

    @property
    def current(self) -> PlanStep | None:
        """当前应执行的步骤:首个 active;没有 active 则首个 pending。"""
        for st in self.steps:
            if st.status == StepStatus.ACTIVE:
                return st
        for st in self.steps:
            if st.status == StepStatus.PENDING:
                return st
        return None

    def all_resolved(self) -> bool:
        """所有步骤都已落定(无 pending / active)。"""
        return all(st.status not in (StepStatus.PENDING, StepStatus.ACTIVE) for st in self.steps)

    def all_done(self) -> bool:
        """所有步骤都成功完成。"""
        return bool(self.steps) and all(st.status == StepStatus.DONE for st in self.steps)

    def has_failure(self) -> bool:
        return any(st.status == StepStatus.FAILED for st in self.steps)

    # ── 状态转移 ──────────────────────────────────────────────

    def _activate_next(self) -> None:
        """若当前无 active,把首个 pending 置为 active。"""
        if any(st.status == StepStatus.ACTIVE for st in self.steps):
            return
        for st in self.steps:
            if st.status == StepStatus.PENDING:
                st.status = StepStatus.ACTIVE
                return

    def mark_done(self, step_no: int) -> PlanStep:
        st = self._require(step_no)
        if st.status != StepStatus.ACTIVE:
            logger.warning("mark_done 第 %d 步当前状态为 %s(非 active)", step_no, st.status)
        st.status = StepStatus.DONE
        self._activate_next()
        return st

    def reactivate(self, step_no: int) -> PlanStep:
        """把一个已 DONE 的步骤退回 ACTIVE(完成门控未达成,需重做)。

        撤销 ``mark_done`` 时 ``_activate_next`` 误激活的后继步(置回 PENDING),避免出现
        两个 active(``current`` 取首个 active,正好回到本步)。供步骤级完成门控未通过时
        退回该步让 ReAct 重做。
        """
        st = self._require(step_no)
        st.status = StepStatus.ACTIVE
        st.note = ""
        for nxt in self.steps:
            if nxt.step_no > step_no and nxt.status == StepStatus.ACTIVE:
                nxt.status = StepStatus.PENDING
        return st

    def mark_failed(self, step_no: int, reason: str = "") -> PlanStep:
        st = self._require(step_no)
        st.status = StepStatus.FAILED
        st.note = reason
        self._activate_next()
        return st

    def mark_skipped(self, step_no: int, reason: str = "") -> PlanStep:
        st = self._require(step_no)
        st.status = StepStatus.SKIPPED
        st.note = reason
        self._activate_next()
        return st

    def _require(self, step_no: int) -> PlanStep:
        st = self.get(step_no)
        if st is None:
            raise ValueError(f"步骤编号 {step_no} 越界(共 {len(self.steps)} 步)")
        return st

    # ── LLM 工具:mark_step_done ──────────────────────────────

    @staticmethod
    def tool_schema() -> dict:
        """``mark_step_done`` 的 LiteLLM tool 定义。"""
        return {
            "type": "function",
            "function": {
                "name": MARK_STEP_DONE_TOOL,
                "description": (
                    "标记一个测试步骤已成功完成。完成当前步骤后必须调用此工具,"
                    "以推进到下一步。step_no 为步骤编号(从 1 开始)。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "step_no": {
                            "type": "integer",
                            "description": "已完成的步骤编号(从 1 开始)",
                        }
                    },
                    "required": ["step_no"],
                },
            },
        }

    def apply_tool_call(self, name: str, arguments: dict) -> str | None:
        """若该 tool_call 属于 StepPlan,处理并返回结果文本;否则返回 None。

        返回 None 表示「不是我管的工具」,ReAct 循环应转交 MCP/Custom Tool。
        """
        if name != MARK_STEP_DONE_TOOL:
            return None
        raw = arguments.get("step_no")
        try:
            step_no = int(raw)
        except (TypeError, ValueError):
            return f"参数错误:step_no 需为整数,收到 {raw!r}。"
        try:
            st = self.mark_done(step_no)
        except ValueError as e:
            return f"标记失败:{e}"
        nxt = self.current
        if nxt is None:
            return f"已完成第 {step_no} 步「{st.describe()}」。所有步骤完成。"
        return (
            f"已完成第 {step_no} 步「{st.describe()}」。"
            f"下一步:第 {nxt.step_no} 步「{nxt.describe()}」。"
        )

    # ── 序列化为 Prompt 片段 ─────────────────────────────────

    def to_prompt(self) -> str:
        """TodoWrite 风格清单,注入 System Prompt。"""
        if not self.steps:
            return "执行计划:无步骤。"
        lines = [f"## 执行计划(共 {len(self.steps)} 步)"]
        for st in self.steps:
            line = f"{_MARK[st.status]} {st.step_no}. {st.describe()}"
            if st.note:
                line += f" — {st.note}"
            lines.append(line)
        cur = self.current
        if cur is not None:
            lines.append("")
            lines.append(
                f"当前应执行第 {cur.step_no} 步。完成后调用 "
                f"{MARK_STEP_DONE_TOOL}(step_no={cur.step_no})。"
            )
        return "\n".join(lines)
