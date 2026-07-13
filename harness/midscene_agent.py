"""Midscene 执行后端适配器。

该 Agent 只替换浏览器执行内核:TestSpec 生成、Run/SSE、ExecutionRecord 归一仍走
T-Agent 既有契约。真实视觉执行由 ``VisualExecutor`` 调 Node sidecar 完成。
"""

from __future__ import annotations

import time
from typing import Callable, Coroutine

from harness.hooks import AFTER_CASE, BEFORE_CASE, ON_FAILURE, ExecutionContext, HookManager
from harness.llm import LLMClient
from harness.recorder import Recorder
from harness.visual_executor import VisualExecutionResult, VisualExecutor
from input.models import ActionStep, Assertion, ExecutionRecord, TestCase, TestSpec
from intelligence.pre_analysis import SpecGenerator


class MidsceneCaseAgent:
    """可选 Midscene 视觉执行后端。"""

    def __init__(
        self,
        *,
        llm: LLMClient,
        visual_executor: VisualExecutor | None = None,
        translation_knowledge: str = "",
        spec_generator: SpecGenerator | None = None,
        hooks: HookManager | None = None,
        step_callback: Callable[[str, dict], Coroutine] | None = None,
    ) -> None:
        self.llm = llm
        self.visual_executor = visual_executor or VisualExecutor()
        self.translation_knowledge = translation_knowledge
        self.spec_generator = spec_generator or SpecGenerator(llm)
        self.hooks = hooks
        self.step_callback = step_callback

    async def generate_spec(self, case: TestCase, *, on_delta=None) -> TestSpec:
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
        ctx = ctx or ExecutionContext(case=case)
        recorder = Recorder(case.id, suite_id=case.suite_id, run_id=run_id)
        cb = step_callback or self.step_callback

        async def emit(event: str, data: dict) -> None:
            if cb is None:
                return
            try:
                await cb(event, data)
            except Exception:  # noqa: BLE001
                pass

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

        await emit(
            "phase", {"case_id": case.id, "phase": "spec", "label": "翻译用例为执行规格 (TestSpec)"}
        )
        if spec is None:
            spec = await self.generate_spec(case)
        recorder.set_spec(spec)
        await emit("spec_ready", {"case_id": case.id, "spec": spec.model_dump(mode="json")})

        if should_abort is not None and await should_abort():
            record = recorder.finalize(
                passed=False,
                final_result="[FAIL] 执行已被用户中止:Midscene 尚未启动。",
            )
            return record

        await emit(
            "phase", {"case_id": case.id, "phase": "executing", "label": "Midscene 视觉执行"}
        )
        result = await self.visual_executor.run_case(
            run_id=run_id or "norun",
            case=case,
            spec=spec,
            execution_context=self.translation_knowledge,
        )

        for step in self._action_steps(result):
            recorder.add_step(step)
            await emit(
                "step_change",
                {
                    "case_id": case.id,
                    "step_index": step.step_no,
                    "status": "done",
                    "description": step.tool_result or step.intent or step.tool_name,
                    "screenshot": step.screenshot,
                    "prompt": step.prompt,
                    "reasoning": step.reasoning,
                    "tool_result": step.tool_result,
                    "url": step.url,
                    "heal_count": 0,
                },
            )

        assertions = self._case_assertions(spec, result)
        recorder.set_case_assertions(assertions)
        passed = bool(result.passed) and all(a["status"] == "pass" for a in assertions)
        recorder.set_stop_reason(result.stop_reason)
        recorder.set_metrics(
            {
                "execution_kernel": "midscene",
                "midscene": {
                    "stop_reason": result.stop_reason,
                    "artifacts": result.artifacts,
                    "phase_count": len(spec.phases),
                    "error": result.error,
                },
            }
        )

        final_result = "" if passed else self._failure_summary(result, assertions)
        record = recorder.finalize(passed=passed, final_result=final_result)

        if self.hooks is not None:
            ctx.set("passed", passed)
            if not passed:
                await self.hooks.run(ON_FAILURE, ctx)
            await self.hooks.run(AFTER_CASE, ctx)
        return record

    @staticmethod
    def _action_steps(result: VisualExecutionResult) -> list[ActionStep]:
        if result.actions:
            steps: list[ActionStep] = []
            for i, action in enumerate(result.actions, start=1):
                steps.append(
                    ActionStep(
                        step_no=i,
                        tool_name=str(action.get("tool_name") or "midscene_aiAct"),
                        tool_input=dict(action.get("tool_input") or {}),
                        reasoning=str(action.get("reasoning") or ""),
                        intent=str(action.get("intent") or action.get("text") or ""),
                        prompt=str(action.get("prompt") or ""),
                        tool_result=str(action.get("result") or action.get("summary") or ""),
                        screenshot=action.get("screenshot"),
                        url=str(action.get("url") or ""),
                        duration_ms=int(action.get("duration_ms") or 0),
                    )
                )
            return steps

        return [
            ActionStep(
                step_no=max(1, r.phase_index + 1),
                tool_name="midscene_aiAct",
                tool_input={"phase_index": r.phase_index},
                intent=f"执行阶段 {r.phase_index + 1}",
                tool_result=r.reason or r.evidence or r.status,
                duration_ms=0,
            )
            for r in result.phase_results
        ]

    @staticmethod
    def _case_assertions(spec: TestSpec, result: VisualExecutionResult) -> list[dict]:
        by_phase = {r.phase_index: r for r in result.phase_results}
        out: list[dict] = []
        for pi, phase in enumerate(spec.phases):
            r = by_phase.get(pi)
            if r is None:
                out.append(
                    _assertion_dict(
                        phase_index=pi,
                        expected=phase.expected,
                        status="fail",
                        reason="该阶段未触达,Midscene 执行已早停",
                    )
                )
                continue
            status = "pass" if r.status == "pass" else "fail"
            out.append(
                _assertion_dict(
                    phase_index=pi,
                    expected=r.expected or phase.expected,
                    status=status,
                    reason=r.reason,
                    actual=r.evidence or str(r.query or ""),
                )
            )
        return out

    @staticmethod
    def _failure_summary(result: VisualExecutionResult, assertions: list[dict]) -> str:
        failed = next((a for a in assertions if a.get("status") != "pass"), None)
        if failed:
            return (
                f"[FAIL] Midscene 执行未通过:阶段 {failed.get('phase_index', -1) + 1} "
                f"{failed.get('reason') or result.error or result.stop_reason}"
            )
        return f"[FAIL] Midscene 执行未通过:{result.error or result.stop_reason or '未知错误'}"


def _assertion_dict(
    *,
    phase_index: int,
    expected: str,
    status: str,
    reason: str = "",
    actual: str = "",
) -> dict:
    assertion = Assertion(type="llm_judge", target=expected, expected=expected, confidence="low")
    return {
        "type": assertion.type,
        "target": assertion.target,
        "expected": assertion.expected,
        "status": status,
        "actual": actual,
        "reason": reason,
        "healable": False,
        "healed": False,
        "heal_note": "",
        "ai_judged": True,
        "phase_index": phase_index,
    }
