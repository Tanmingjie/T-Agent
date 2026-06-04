"""Suite 调度 / Orchestrator(规格 §5.4 Subagent 隔离,T-18)。

启动并调度一个 Suite 内的用例 Subagent:

- **串行执行**(规格 §0/§7/§8:用例间串行,不并发,避免多 Subagent 抢 LLM 资源)。
- **用例间隔离**:每条用例用独立 ``ExecutionContext`` + 独立一次 ``agent.run``;
  某条用例抛异常被捕获并记为 FAIL,**不影响**后续用例(用例 A 不污染 B)。
- **Suite 级 Hooks**:before_suite / after_suite 各跑一次;before_suite 失败 → 整个 Suite
  中止(不进任何用例)。
- 汇总成 ``SuiteResult``(records + 通过/失败计数)。

注:per-case 的 before_case/after_case Hooks 由 ``TestCaseAgent`` 内部负责;
本模块只管 Suite 层调度与隔离。agent 只需实现 ``async run(case, spec=None, ctx=None)``。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from harness.hooks import AFTER_SUITE, BEFORE_SUITE, ExecutionContext, HookManager
from input.models import ExecutionRecord, Suite, TestCase

logger = logging.getLogger(__name__)

SSECallback = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]] | None


@dataclass
class SuiteResult:
    suite_id: str | None = None
    records: list[ExecutionRecord] = field(default_factory=list)
    aborted: bool = False
    error: str = ""

    @property
    def total(self) -> int:
        return len(self.records)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.records if r.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.records if not r.passed)


class Orchestrator:
    """Suite 调度器。"""

    def __init__(self, agent, hooks: HookManager | None = None) -> None:
        self.agent = agent
        self.hooks = hooks

    async def run_suite(
        self,
        cases: list[TestCase],
        suite: Suite | None = None,
        sse_callback: SSECallback = None,
        run_id: str | None = None,
    ) -> SuiteResult:
        suite_id = suite.id if suite else None
        result = SuiteResult(suite_id=suite_id)
        suite_ctx = ExecutionContext(suite=suite)

        # before_suite:失败则中止整个 Suite
        if self.hooks is not None:
            bs = await self.hooks.run(BEFORE_SUITE, suite_ctx)
            if not bs.ok:
                result.aborted = True
                result.error = f"before_suite 失败:{bs.error}(hook={bs.failed_hook})"
                logger.warning(result.error)
                if sse_callback:
                    await sse_callback("error", {"message": result.error})
                return result

        # Push suite_start
        if sse_callback:
            await sse_callback(
                "suite_start", {"run_id": run_id or "pending", "total_cases": len(cases)}
            )

        # 串行执行用例,逐个隔离
        case_idx = 0
        for case in cases:
            case_idx += 1
            if sse_callback:
                await sse_callback(
                    "case_start",
                    {"case_id": case.id, "title": case.name, "index": case_idx},
                )

            record = await self._run_one(case, suite, sse_callback=sse_callback, run_id=run_id)

            if sse_callback:
                await sse_callback(
                    "case_result",
                    {
                        "case_id": case.id,
                        "verdict": "PASS" if record.passed else "FAIL",
                        "index": case_idx,
                    },
                )

            record.suite_id = suite_id
            result.records.append(record)

        if self.hooks is not None:
            await self.hooks.run(AFTER_SUITE, suite_ctx)

        passed = result.passed_count
        failed = result.failed_count
        if sse_callback:
            await sse_callback(
                "suite_done",
                {
                    "run_id": run_id or "pending",
                    "passed": passed,
                    "failed": failed,
                    "total": result.total,
                },
            )

        logger.info(
            "Suite %s 完成:%d 通过 / %d 失败 / 共 %d",
            suite_id,
            passed,
            failed,
            result.total,
        )
        return result

    async def _run_one(
        self,
        case: TestCase,
        suite: Suite | None,
        sse_callback: SSECallback = None,
        run_id: str | None = None,
    ) -> ExecutionRecord:
        """执行单条用例;异常被隔离为 FAIL 记录,不冒泡影响其它用例。"""
        ctx = ExecutionContext(case=case, suite=suite)

        async def _step_cb(event: str, data: dict) -> None:
            if sse_callback is not None:
                await sse_callback(event, data)

        try:
            return await self.agent.run(
                case,
                ctx=ctx,
                step_callback=_step_cb if sse_callback else None,
                run_id=run_id,
            )
        except Exception as e:  # noqa: BLE001 — 用例间隔离:A 的异常不拖垮 B
            logger.warning("用例 %s 执行异常,记为 FAIL:%s", case.id, e)
            return ExecutionRecord(
                exec_id=f"err-{case.id}",
                case_id=case.id,
                suite_id=suite.id if suite else None,
                passed=False,
                final_result=f"[FAIL] 用例执行异常:{type(e).__name__}: {e}",
                start_time=time.time(),
                end_time=time.time(),
            )
