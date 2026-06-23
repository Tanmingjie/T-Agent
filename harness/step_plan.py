"""StepPlan / TodoWrite 机制(阶段化重设计,2026-06-22)。

把阶段化 TestSpec 的 ``phases`` 摊平成带状态机的**扁平步骤清单**驱动 ReAct,同时记住每步
所属阶段,以便在**阶段边界**(某阶段最后一步落定)触发该阶段的 Validator。

- 状态机:``pending → active → done`` / ``failed`` / ``skipped``。
- 初始第 1 步 ``active``,其余 ``pending``。
- LLM 每完成一步调 ``mark_step_done(step_no)`` 推进。
- 序列化为 System Prompt 片段:按阶段分组展示步骤,**只展示步骤、不展示阶段 expected**
  (expected 是验证依据,绝不进驱动 prompt——FG01 血泪)。

对本地 LLM 取**宽容**策略:标记非当前步只告警,非法编号返回错误文本而非抛栈。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from input.models import TestSpec

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
    step_no: int  # 全局 1-based
    text: str  # 自然语言步骤
    phase_index: int  # 所属阶段(0-based)
    status: StepStatus = StepStatus.PENDING
    note: str = ""  # 失败原因等

    def describe(self) -> str:
        return self.text


class StepPlan:
    """多步骤执行计划 + 状态机(扁平步骤,记阶段归属)。"""

    def __init__(self, phases: list) -> None:
        """``phases``:list[Phase](每个 Phase 有 ``steps: list[str]``)。"""
        self.phases = list(phases)
        self.steps: list[PlanStep] = []
        no = 0
        for pi, ph in enumerate(self.phases):
            for text in getattr(ph, "steps", []):
                no += 1
                self.steps.append(PlanStep(step_no=no, text=text, phase_index=pi))
        if self.steps:
            self.steps[0].status = StepStatus.ACTIVE

    @classmethod
    def from_spec(cls, spec: TestSpec) -> "StepPlan":
        """由阶段化 TestSpec 构建(摊平 phases.steps,记每步所属阶段)。"""
        return cls(spec.phases)

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

    # ── 阶段边界 ──────────────────────────────────────────────

    @property
    def phase_count(self) -> int:
        return len(self.phases)

    def phase_of(self, step_no: int) -> int:
        st = self.get(step_no)
        return st.phase_index if st is not None else -1

    def phase_last_step_no(self, phase_index: int) -> int | None:
        """某阶段最后一步的全局 step_no(空阶段返回 None)。"""
        nos = [s.step_no for s in self.steps if s.phase_index == phase_index]
        return nos[-1] if nos else None

    def is_phase_last_step(self, step_no: int) -> bool:
        """该步是否是其所属阶段的最后一步(→ 触发该阶段 Validator)。"""
        st = self.get(step_no)
        if st is None:
            return False
        return self.phase_last_step_no(st.phase_index) == step_no

    def phase_steps_done(self, phase_index: int) -> bool:
        """某阶段的全部步骤是否都已 DONE。"""
        steps = [s for s in self.steps if s.phase_index == phase_index]
        return bool(steps) and all(s.status == StepStatus.DONE for s in steps)

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
        """把一个已 DONE 的步骤退回 ACTIVE(撤销误激活的后继步)。"""
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
        """若该 tool_call 属于 StepPlan,处理并返回结果文本;否则返回 None。"""
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
        """TodoWrite 风格清单,按阶段分组注入 System Prompt(**不含阶段 expected**)。"""
        if not self.steps:
            return "执行计划:无步骤。"
        lines = [f"## 执行计划(共 {len(self.steps)} 步,{self.phase_count} 个阶段)"]
        for pi in range(self.phase_count):
            steps = [s for s in self.steps if s.phase_index == pi]
            if not steps:
                continue
            lines.append(f"— 阶段 {pi + 1} —")
            for st in steps:
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
