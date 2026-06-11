"""执行录制(规格 §4 数据结构 + §7 借鉴 browser-use,T-09)。

把 ReAct 循环产出的 ActionStep 汇总成 ExecutionRecord,并负责:

- 生成 exec_id、记录起止时间、token 用量、自愈次数。
- 维护每个 exec 的截图目录(storage/screenshots/<exec_id>/)。
- 承载用例级最终断言结果(ExecutionRecord 模型无此字段,故由 Recorder 持有,
  并写入可读 final_result + to_history 序列化输出;落库留到 T-21)。
- 序列化:借鉴 browser-use AgentHistory,**model_output(思考/决策)与
  action_result(工具结果/观察)分离**,便于回放与代码生成。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from input.models import ActionStep, ExecutionRecord
from storage.artifacts import get_artifact_store

# 产物存储抽象(T-P10)单一真相:截图根目录从 ArtifactStore 取(env ARTIFACT_ROOT 可改),
# 与 results 路由读取端一致;M3 换对象存储只换实现。
DEFAULT_SCREENSHOT_ROOT = str(get_artifact_store().screenshots_root)


class Recorder:
    """单个用例执行的录制器。"""

    def __init__(
        self,
        case_id: str,
        *,
        suite_id: str | None = None,
        run_id: str | None = None,
        exec_id: str | None = None,
        screenshot_root: str | Path = DEFAULT_SCREENSHOT_ROOT,
    ) -> None:
        self.exec_id = exec_id or uuid.uuid4().hex[:12]
        self.run_id = run_id or "norun"
        self.case_id = case_id
        self.screenshot_root = Path(screenshot_root)
        self.record = ExecutionRecord(
            exec_id=self.exec_id,
            case_id=case_id,
            suite_id=suite_id,
            run_id=run_id,
            start_time=time.time(),
        )
        # 用例级最终断言结果(dict 形态,见 AssertionResult.to_dict)
        self.case_assertions: list[dict] = []
        # ReAct 循环停因 + 迭代数(诊断"为什么停在某步":早停/卡死/步数上限…)
        self.stop_reason: str = ""

    # ── 录制 ──────────────────────────────────────────────────

    def add_step(self, step: ActionStep) -> None:
        self.record.steps.append(step)
        self.record.heal_count += len(step.heal_attempts)

    def extend_steps(self, steps: list[ActionStep]) -> None:
        for s in steps:
            self.add_step(s)

    def attach_step_assertions(self, step_no: int, results: list[dict]) -> None:
        """把某步的即时断言结果挂到对应 ActionStep。"""
        for s in self.record.steps:
            if s.step_no == step_no:
                s.assertion_results.extend(results)

    def set_case_assertions(self, results: list[dict]) -> None:
        """设置用例级最终断言结果(来自 AssertionEngine,已 to_dict)。"""
        self.case_assertions = list(results)
        self.record.case_assertions = self.case_assertions

    def set_token_usage(self, total_tokens: int) -> None:
        self.record.token_usage = total_tokens

    def set_spec(self, spec) -> None:
        """存档本次执行使用的 TestSpec(供前端可视化翻译结果)。"""
        self.record.spec = spec

    def set_stop_reason(self, reason: str) -> None:
        """记录 ReAct 循环停因(写入 final_result 摘要,便于诊断早停)。"""
        self.stop_reason = reason

    # ── 截图目录 ──────────────────────────────────────────────

    @property
    def screenshot_dir(self) -> Path:
        return self.screenshot_root / self.run_id / self.case_id

    def screenshot_path(self, step_no: int, ext: str = "png") -> str:
        """返回该步截图应保存的路径(并确保目录存在)。"""
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        return str(self.screenshot_dir / f"step_{step_no:03d}.{ext}")

    # ── 收尾 ──────────────────────────────────────────────────

    def finalize(self, passed: bool, final_result: str = "") -> ExecutionRecord:
        """落定结果。final_result 为空时按断言结果自动生成摘要。"""
        self.record.passed = passed
        self.record.end_time = time.time()
        self.record.final_result = final_result or self._summarize()
        self.record.updated_at = time.time()
        return self.record

    def _summarize(self) -> str:
        verdict = "PASS" if self.record.passed else "FAIL"
        n_steps = len(self.record.steps)
        head = f"[{verdict}] 用例 {self.record.case_id},共 {n_steps} 步,自愈 {self.record.heal_count} 次。"
        if self.stop_reason:
            head += f"(停因={self.stop_reason})"
        lines = [head]
        if self.case_assertions:
            lines.append("用例级断言:")
            for a in self.case_assertions:
                mark = {"pass": "✓", "fail": "✗", "skipped": "—"}.get(a.get("status"), "?")
                detail = a.get("reason") or a.get("actual") or ""
                lines.append(f"  {mark} [{a.get('type')}] {a.get('target')} {detail}".rstrip())
        return "\n".join(lines)

    # ── 序列化(model_output / action_result 分离) ──────────

    def to_history(self) -> list[dict]:
        """逐步序列化,思考决策与执行结果分离(借鉴 browser-use AgentHistory)。"""
        history = []
        for s in self.record.steps:
            history.append(
                {
                    "step_no": s.step_no,
                    "model_output": {
                        "reasoning": s.reasoning,
                        "intent": s.intent,
                        "prompt": s.prompt,  # 本轮请求(System+最近输入),供「查看 prompt」
                        "tool_name": s.tool_name,
                        "tool_input": s.tool_input,
                    },
                    "action_result": {
                        "tool_result": s.tool_result,
                        "url": s.url,
                        "screenshot": s.screenshot,
                        "assertion_results": s.assertion_results,
                        "heal_attempts": s.heal_attempts,  # 操作侧自愈(过程时间线展示)
                        "is_custom_tool": s.is_custom_tool,
                        "is_hook_action": s.is_hook_action,
                        "duration_ms": s.duration_ms,
                    },
                }
            )
        return history

    def to_dict(self) -> dict:
        """完整序列化:执行记录 + 用例级断言 + 分离式历史。"""
        d = self.record.model_dump()
        d["case_assertions"] = self.case_assertions
        d["history"] = self.to_history()
        return d
