"""可复用的「执行一个 run」核心(平台化 T-P08)。

从 ``api/routers/execution.py::_worker_main`` 抽出,**与进程无关**:在自己的 loop 里建
独立 Store、按 suite 所属项目作用域构造 LLM/词汇表/Hooks/Skills/Tools,跑 Orchestrator,
落 ExecutionRecord + 更新 RunRecord。

两处复用:
- API 单机路径(`execution.py`):线程内调用,SSE 经 `make_sse_bridge` 桥回 API loop。
- 独立 worker 进程(`scripts/worker.py`):领队列任务后直接调用;SSE 由 T-P09 的
  LISTEN/NOTIFY 接(此前 sse_cb 可为 no-op,run 仍完整执行并落库)。

权限审批:`perm_approver` 由调用方注入(API 用 threading.Event 跨线程;worker 进程用
审批工单表,T-P09)。不注入则按 suite 的 permission_mode;approve 模式无 approver 时
权限层默认拒绝(保守)。
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

SSECallback = Callable[[str, dict], Awaitable[None]]


def _mcp_args() -> list[str]:
    args = ["@playwright/mcp@latest"]
    if os.getenv("MCP_ISOLATED", "1") != "0":
        args.append("--isolated")
    if os.getenv("MCP_HEADLESS", "1") != "0":
        args.append("--headless")
    return args


async def execute_run(
    *,
    db_url: str,
    run_id: str,
    suite_id: str,
    case_id: str | None = None,
    sse_cb: SSECallback,
    perm_approver=None,
) -> None:
    """执行一个 run 到完成(自带独立 Store/loop 资源)。失败不抛,落 failed 状态。"""
    from api.repository import SQLModelRepository, get_suite_settings
    from harness.agent import TestCaseAgent
    from harness.hook_builder import build_session_hooks
    from harness.llm import build_llm_client
    from harness.orchestrator import Orchestrator
    from harness.skills import build_skill_manager
    from intelligence.vocabulary import VocabularyManager, VocabularyResolver
    from mcp_client.client import MCPClient
    from storage.db import Store

    store = Store(url=db_url)
    await store.init()
    repo = SQLModelRepository(store)
    try:
        suite = await store.get_suite(suite_id)
        if suite is None:
            await repo.update_run(run_id, status="failed", finished_at=time.time())
            return
        cases = await repo.list_by_suite(suite_id)
        if case_id is not None:
            cases = [c for c in cases if c.id == case_id]

        settings_row = await get_suite_settings(store, suite_id)
        parallelism = int(settings_row.get("parallelism", 1))
        approve_mode = settings_row.get("permission_mode") == "approve"

        vocab_resolver = VocabularyResolver(VocabularyManager(store, project_id=suite.project_id))
        llm_config = await store.get_llm_config(suite.project_id) if suite.project_id else None
        mcp_args = _mcp_args()

        tools_registry = None
        tools_yaml = os.getenv("CUSTOM_TOOLS_YAML")
        if tools_yaml:
            try:
                from harness.tools import load_tool_registry_from_yaml

                tools_registry = load_tool_registry_from_yaml(tools_yaml)
            except Exception as e:  # noqa: BLE001
                logger.warning("加载 Custom Tool 配置失败(%s):%s", tools_yaml, e)

        session_profile = None
        if suite.session_profile:
            session_profile = await store.get_session_profile(suite.session_profile)
            if session_profile is None:
                logger.warning(
                    "Suite %s 绑定的 SessionProfile %r 不存在,跳过 Session 复用",
                    suite_id,
                    suite.session_profile,
                )

        @asynccontextmanager
        async def make_agent():
            skills = build_skill_manager(custom_prompt=suite.custom_prompt)
            async with MCPClient(args=mcp_args) as mcp:
                hooks = (
                    build_session_hooks(session_profile, mcp)
                    if session_profile is not None
                    else None
                )
                agent = TestCaseAgent(
                    llm=build_llm_client(llm_config),
                    mcp=mcp,
                    vocab_resolver=vocab_resolver,
                    hooks=hooks,
                    skills=skills,
                    tools_registry=tools_registry,
                    max_steps=int(os.getenv("AGENT_MAX_STEPS", "40")),
                )
                if approve_mode and perm_approver is not None:
                    agent.permission_approver = perm_approver
                yield agent

        _case_by_id = {c.id: c for c in cases}

        async def _save_record(record) -> None:
            record.run_id = run_id
            await repo.save_record(record)
            case = _case_by_id.get(record.case_id)
            if case is not None and case.precondition_items:
                await store.save_case(case)

        try:
            orch = Orchestrator(agent_factory=make_agent)
            result = await orch.run_suite(
                cases,
                suite=suite,
                sse_callback=sse_cb,
                run_id=run_id,
                on_record=_save_record,
                parallelism=parallelism,
            )
            await repo.update_run(
                run_id,
                status="completed",
                passed_cases=result.passed_count,
                failed_cases=result.failed_count,
                finished_at=time.time(),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Run %s failed", run_id)
            await sse_cb("error", {"message": str(e)})
            await repo.update_run(run_id, status="failed", finished_at=time.time())
        finally:
            await sse_cb("suite_done", {"run_id": run_id, "sentinel": True})
    finally:
        await store.close()
