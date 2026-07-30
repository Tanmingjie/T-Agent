"""Suite 调度 / Orchestrator(规格 §5.4 Subagent 隔离,T-18)。

启动并调度一个 Suite 内的用例 Subagent:

- **串行执行**(规格 §0/§7/§8:用例间串行,不并发,避免多 Subagent 抢 LLM 资源)。
- **用例间隔离**:每条用例用独立 ``ExecutionContext`` + 独立一次 ``agent.run``;
  某条用例抛异常被捕获并记为 FAIL,**不影响**后续用例(用例 A 不污染 B)。
- **Suite 级 Hooks**:before_suite / after_suite 各跑一次;before_suite 失败 → 整个 Suite
  中止(不进任何用例)。
- 汇总成 ``SuiteResult``(records + 通过/失败计数)。

注:per-case 的 before_case/after_case Hooks 由具体 agent 内部负责;
本模块只管 Suite 层调度与隔离。agent 只需实现 ``async run(case, spec=None, ctx=None)``。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncContextManager, Callable, Coroutine

from harness.hooks import AFTER_SUITE, BEFORE_SUITE, ExecutionContext, HookManager
from input.models import ExecutionRecord, Suite, TestCase

logger = logging.getLogger(__name__)

SSECallback = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]] | None
# 每条用例产出独立 agent(自带独立 MCP)的工厂:`async with agent_factory() as agent`
AgentFactory = Callable[[], AsyncContextManager[Any]]


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

    def __init__(
        self,
        agent=None,
        hooks: HookManager | None = None,
        agent_factory: "AgentFactory | None" = None,
    ) -> None:
        # agent:共享单实例(串行,向后兼容)。agent_factory:每条用例产出**自带独立 MCP**
        # 的 agent(async context manager),并发执行的前提(各用例各自浏览器、各自收尾)。
        self.agent = agent
        self.agent_factory = agent_factory
        self.hooks = hooks

    async def run_suite(
        self,
        cases: list[TestCase],
        suite: Suite | None = None,
        sse_callback: SSECallback = None,
        run_id: str | None = None,
        on_record: Callable[[ExecutionRecord], Coroutine[Any, Any, None]] | None = None,
        parallelism: int = 1,
        should_abort: Callable[[], Coroutine[Any, Any, bool]] | None = None,
        login_setup_case: TestCase | None = None,
        storage_state_path: Path | None = None,
    ) -> SuiteResult:
        suite_id = suite.id if suite else None
        result = SuiteResult(suite_id=suite_id)
        suite_ctx = ExecutionContext(suite=suite)

        # 并发需 agent_factory(各用例独立 MCP);无工厂时只能串行,clamp 到 1 并告警。
        parallelism = max(1, int(parallelism))
        if parallelism > 1 and self.agent_factory is None:
            logger.warning("parallelism=%d 但未提供 agent_factory,降级为串行", parallelism)
            parallelism = 1

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

        sem = asyncio.Semaphore(parallelism)

        async def _finish_case(
            case: TestCase, case_idx: int, record: ExecutionRecord
        ) -> ExecutionRecord:
            record.suite_id = suite_id
            if on_record is not None:
                try:
                    await on_record(record)
                except Exception as e:  # noqa: BLE001
                    logger.warning("用例 %s 结果持久化失败:%s", case.id, e)

            if sse_callback:
                await sse_callback(
                    "case_result",
                    {
                        "case_id": case.id,
                        "verdict": "PASS" if record.passed else "FAIL",
                        "index": case_idx,
                    },
                )
            return record

        async def _run_case(
            case: TestCase,
            case_idx: int,
            *,
            case_storage_state_path: Path | None = None,
            capture_storage_state: bool = False,
        ) -> ExecutionRecord:
            async with sem:  # 并发上限:同时最多 parallelism 条用例在跑
                # 协作式停止:已请求停止则跳过尚未开跑的用例(给一条「已中止」占位记录,
                # 不进浏览器)。正在跑的用例由当前执行内核在检查点里优雅退出。
                if should_abort is not None and await should_abort():
                    record = ExecutionRecord(
                        exec_id=f"aborted-{case.id}",
                        case_id=case.id,
                        suite_id=suite_id,
                        passed=False,
                        final_result="执行已被用户中止,该用例未开始",
                        start_time=time.time(),
                        end_time=time.time(),
                    )
                    return await _finish_case(case, case_idx, record)
                if sse_callback:
                    await sse_callback(
                        "case_start",
                        {"case_id": case.id, "title": case.name, "index": case_idx},
                    )
                record = await self._run_one(
                    case,
                    suite,
                    sse_callback=sse_callback,
                    run_id=run_id,
                    should_abort=should_abort,
                    storage_state_path=case_storage_state_path,
                    capture_storage_state=capture_storage_state,
                )
                if capture_storage_state and record.passed:
                    state_ready = (
                        case_storage_state_path is not None
                        and case_storage_state_path.is_file()
                        and case_storage_state_path.stat().st_size > 0
                    )
                    if not state_ready:
                        record.passed = False
                        record.final_result = "[FAIL] 登录准备用例未生成有效的认证状态文件"
                        record.end_time = time.time()
                return await _finish_case(case, case_idx, record)

        business_cases = cases
        setup_records: list[ExecutionRecord] = []
        first_business_index = 1
        if login_setup_case is not None:
            business_cases = [case for case in cases if case.id != login_setup_case.id]
            setup_record = await _run_case(
                login_setup_case,
                1,
                case_storage_state_path=storage_state_path,
                capture_storage_state=True,
            )
            setup_records.append(setup_record)
            first_business_index = 2
            if not setup_record.passed:
                skipped_records = []
                for offset, case in enumerate(business_cases, start=first_business_index):
                    skipped = ExecutionRecord(
                        exec_id=f"login-setup-failed-{case.id}",
                        case_id=case.id,
                        suite_id=suite_id,
                        passed=False,
                        final_result="登录准备用例失败，该业务用例未执行",
                        start_time=time.time(),
                        end_time=time.time(),
                    )
                    skipped_records.append(await _finish_case(case, offset, skipped))
                result.records = setup_records + skipped_records
            else:
                result.records = setup_records + list(
                    await asyncio.gather(
                        *(
                            _run_case(
                                case,
                                index,
                                case_storage_state_path=storage_state_path,
                            )
                            for index, case in enumerate(business_cases, start=first_business_index)
                        )
                    )
                )
        else:
            # gather 保留输入顺序;用例间隔离由 _run_one 的 try/except 保证。
            result.records = list(
                await asyncio.gather(
                    *(_run_case(case, index) for index, case in enumerate(cases, start=1))
                )
            )

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
        should_abort: Callable[[], Coroutine[Any, Any, bool]] | None = None,
        storage_state_path: Path | None = None,
        capture_storage_state: bool = False,
    ) -> ExecutionRecord:
        """执行单条用例;异常被隔离为 FAIL 记录,不冒泡影响其它用例。"""
        ctx = ExecutionContext(case=case, suite=suite)

        async def _step_cb(event: str, data: dict) -> None:
            if sse_callback is not None:
                await sse_callback(event, data)

        cb = _step_cb if sse_callback else None
        # should_abort 只在确有信号时传(保持 agent.run 的最小契约:无停止需求的调用方/fake
        # agent 不必识别该 kwarg)。
        extra = {"should_abort": should_abort} if should_abort is not None else {}
        if storage_state_path is not None:
            extra.update(
                storage_state_path=storage_state_path,
                capture_storage_state=capture_storage_state,
            )
        try:
            # agent_factory:每条用例独立 agent + MCP(并发隔离);否则用共享 agent(串行)。
            if self.agent_factory is not None:
                async with self.agent_factory() as agent:
                    return await agent.run(case, ctx=ctx, step_callback=cb, run_id=run_id, **extra)
            return await self.agent.run(case, ctx=ctx, step_callback=cb, run_id=run_id, **extra)
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
