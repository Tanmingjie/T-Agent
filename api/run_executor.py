"""可复用的「执行一个 run」核心(平台化 T-P08)。

从 ``api/routers/execution.py::_worker_main`` 抽出,**与进程无关**:在自己的 loop 里建
独立 Store、按 suite 所属项目作用域构造 LLM 和 Midscene Agent,跑 Orchestrator,
落 ExecutionRecord + 更新 RunRecord。

两处复用:
- API 单机路径(`execution.py`):线程内调用。
- 独立 worker 进程(`scripts/worker.py`):领队列任务后直接调用。

**进度持久化(2026-06-26 统一)**:execute_run 是**唯一的事件落库点**——内部 `_emit`
把每个生命周期事件写 `run_event` 表(供 `/stream` 从 seq 0 重放,实现「退出执行页再进来
看全程」),再可选转发给调用方传入的 `sse_cb`(live 低延迟通道,可为 None)。embedded /
queue 两模式都走这条路 → 行为一致、都可重连重放。调用方**不再各自往 run_event 表写**。

权限审批曾用于 ReAct tool-call 执行;Midscene 全量替换后不再在执行核里消费。
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

SSECallback = Callable[[str, dict], Awaitable[None]]


async def execute_run(
    *,
    db_url: str,
    run_id: str,
    suite_id: str,
    case_id: str | None = None,
    sse_cb: SSECallback | None = None,
    perm_approver_factory: Callable[[SSECallback], object] | None = None,
    force_skill_names: list[str] | None = None,
) -> None:
    """执行一个 run 到完成(自带独立 Store/loop 资源)。失败不抛,落 failed 状态。

    ``force_skill_names``:本次执行显式选择的项目 skill 名。Midscene 路径下把命中
    skill 正文合并进翻译/执行上下文,作为业务知识输入。
    """
    from api.repository import SQLModelRepository, get_suite_settings
    from harness.llm import build_llm_client
    from harness.midscene_agent import MidsceneCaseAgent
    from harness.orchestrator import Orchestrator
    from input.models import ExecutionRecord
    from storage.db import Store

    store = Store(url=db_url)
    await store.init()
    repo = SQLModelRepository(store)
    cases: list = []  # 提到 try 外:异常兜底时给未落记录的用例补占位记录要用
    saved_ids: set[str] = set()  # 本 run 已落库的 case_id(_save_record 累加)
    fail_reason = ""
    completed = False

    async def _emit(event: str, data: dict) -> None:
        """唯一事件落库点:写 run_event 表(供 /stream 从 seq 0 重放)+ 转发 live 通道。

        落表 best-effort(失败不阻断执行);转发同理。两模式共用,保证退出再进可重放全程。
        """
        try:
            await store.append_run_event(run_id, event, data)
        except Exception:  # noqa: BLE001
            logger.warning("写 run_event 失败 run=%s event=%s", run_id, event, exc_info=True)
        if sse_cb is not None:
            try:
                await sse_cb(event, data)
            except Exception:  # noqa: BLE001
                pass

    try:
        suite = await store.get_suite(suite_id)
        if suite is None:
            fail_reason = "套件不存在"
            return
        cases = await repo.list_by_suite(suite_id)
        if case_id is not None:
            cases = [c for c in cases if c.id == case_id]

        llm_config = await store.get_llm_config(suite.project_id) if suite.project_id else None
        settings_row = await get_suite_settings(store, suite_id)
        parallelism = int(settings_row.get("parallelism", 1))

        # 项目级翻译知识/操作指南:注入翻译 prompt(助补全流程/对齐术语/写对 expected)
        translation_knowledge = ""
        if suite.project_id:
            project = await store.get_project(suite.project_id)
            if project is not None:
                translation_knowledge = project.translation_knowledge or ""

        # 用户执行前勾选的 skill → 作为 Midscene 执行上下文补充。未勾选的 skill 不进入
        # 本次执行,避免再保留 ReAct 时代的渐进披露分支。
        force_set = {n for n in (force_skill_names or []) if n}
        if suite.project_id:
            for sk in await store.list_skills(suite.project_id):
                if sk.name in force_set and sk.content.strip():
                    translation_knowledge += (
                        f"\n\n[执行 Skill:{sk.name}]\n"
                        f"{(sk.description or '').strip()}\n"
                        f"{sk.content.strip()}"
                    )

        @asynccontextmanager
        async def make_agent():
            agent = MidsceneCaseAgent(
                llm=build_llm_client(llm_config),
                hooks=None,
                translation_knowledge=translation_knowledge,
            )
            yield agent

        _case_by_id = {c.id: c for c in cases}

        async def _save_record(record) -> None:
            record.run_id = run_id
            await repo.save_record(record)
            saved_ids.add(record.case_id)
            case = _case_by_id.get(record.case_id)
            if case is not None and case.precondition_items:
                await store.save_case(case)

        async def _should_abort() -> bool:
            """协作式停止信号:用户「停止」请求落 run_record.cancel_requested,执行链轮询。"""
            try:
                return await repo.is_cancel_requested(run_id)
            except Exception:  # noqa: BLE001 — 查标志失败按未取消处理(不误停)
                return False

        orch = Orchestrator(agent_factory=make_agent)
        result = await orch.run_suite(
            cases,
            suite=suite,
            sse_callback=_emit,
            run_id=run_id,
            on_record=_save_record,
            parallelism=parallelism,
            should_abort=_should_abort,
        )
        # 用户中止 → 终态记 aborted(区别于正常 completed);否则 completed。
        aborted = await _should_abort()
        await repo.update_run(
            run_id,
            status="aborted" if aborted else "completed",
            passed_cases=result.passed_count,
            failed_cases=result.failed_count,
            finished_at=time.time(),
        )
        completed = True
    except Exception as e:  # noqa: BLE001 — 任何阶段(setup/orchestrator)异常都在此兜底
        fail_reason = str(e) or e.__class__.__name__
        logger.exception("Run %s 异常中断", run_id)
        await _emit("error", {"message": fail_reason})
    finally:
        # 兜底:run 未正常完成(setup 阶段异常 / 进程被中断 / orchestrator 抛错)→ 标 failed
        # (幂等)+ 给「本 run 未落任何记录」的用例补一条「执行中断」占位记录。根治旧代码的
        # 两个坑:① 外层 setup 异常漏标状态 → 僵尸 running;② 中途被杀 → /result 全空、抽屉无详情。
        if not completed:
            try:
                await repo.update_run(run_id, status="failed", finished_at=time.time())
            except Exception:  # noqa: BLE001
                logger.warning("标记 run %s failed 失败", run_id, exc_info=True)
            for c in cases:
                if c.id in saved_ids:
                    continue
                try:
                    await repo.save_record(
                        ExecutionRecord(
                            exec_id=f"{run_id}-{c.id}-interrupted",
                            case_id=c.id,
                            suite_id=suite_id,
                            run_id=run_id,
                            passed=False,
                            final_result=(
                                "执行中断,未产生完整记录" f"(原因:{fail_reason or '进程被中断'})"
                            ),
                        )
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("补占位记录失败 case=%s", c.id, exc_info=True)
        # 无论成功/失败/中断,确保 suite_done 收尾事件落表(否则 /stream 尾随永不终止)。
        await _emit("suite_done", {"run_id": run_id, "sentinel": True})
        await store.close()
