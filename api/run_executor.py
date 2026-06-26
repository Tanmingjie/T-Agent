"""可复用的「执行一个 run」核心(平台化 T-P08)。

从 ``api/routers/execution.py::_worker_main`` 抽出,**与进程无关**:在自己的 loop 里建
独立 Store、按 suite 所属项目作用域构造 LLM/词汇表/Hooks/Skills/Tools,跑 Orchestrator,
落 ExecutionRecord + 更新 RunRecord。

两处复用:
- API 单机路径(`execution.py`):线程内调用。
- 独立 worker 进程(`scripts/worker.py`):领队列任务后直接调用。

**进度持久化(2026-06-26 统一)**:execute_run 是**唯一的事件落库点**——内部 `_emit`
把每个生命周期事件写 `run_event` 表(供 `/stream` 从 seq 0 重放,实现「退出执行页再进来
看全程」),再可选转发给调用方传入的 `sse_cb`(live 低延迟通道,可为 None)。embedded /
queue 两模式都走这条路 → 行为一致、都可重连重放。调用方**不再各自往 run_event 表写**。

权限审批:`perm_approver_factory(emit)` 由调用方注入(API 用 threading.Event 跨线程;
worker 进程用审批工单表)。工厂收到 execute_run 的 `_emit`,使权限事件也落表可重放。
不注入则按 suite 的 permission_mode;approve 模式无 approver 时权限层默认拒绝(保守)。
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
    sse_cb: SSECallback | None = None,
    perm_approver_factory: Callable[[SSECallback], object] | None = None,
) -> None:
    """执行一个 run 到完成(自带独立 Store/loop 资源)。失败不抛,落 failed 状态。"""
    from api.repository import SQLModelRepository, get_suite_settings
    from harness.agent import TestCaseAgent
    from harness.llm import build_llm_client
    from harness.orchestrator import Orchestrator
    from harness.skills import build_skill_manager
    from input.models import ExecutionRecord
    from intelligence.vocabulary import VocabularyManager, VocabularyResolver
    from mcp_client.client import MCPClient
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

        settings_row = await get_suite_settings(store, suite_id)
        parallelism = int(settings_row.get("parallelism", 1))
        approve_mode = settings_row.get("permission_mode") == "approve"

        vocab_resolver = VocabularyResolver(VocabularyManager(store, project_id=suite.project_id))
        llm_config = await store.get_llm_config(suite.project_id) if suite.project_id else None
        mcp_args = _mcp_args()

        tools_registry = None
        # 平台:项目级 HTTP 型 Custom Tool(M2)优先;无则回退 env YAML(单机/命令型)。
        if suite.project_id:
            http_tools = await store.list_http_tools(suite.project_id)
            if http_tools:
                from harness.tools import build_http_tool_registry

                tools_registry = build_http_tool_registry(http_tools)
        if tools_registry is None:
            tools_yaml = os.getenv("CUSTOM_TOOLS_YAML")
            if tools_yaml:
                try:
                    from harness.tools import load_tool_registry_from_yaml

                    tools_registry = load_tool_registry_from_yaml(tools_yaml)
                except Exception as e:  # noqa: BLE001
                    logger.warning("加载 Custom Tool 配置失败(%s):%s", tools_yaml, e)

        # 项目级 Skill:**渐进披露**(preload=False)——name+description 常驻名册,
        # 由 LLM/ReAct 主动 load_skill 展开正文。E3(2026-06-23)解掉旧 force-preload TODO:
        # 主路靠 prompt 让模型动手前主动加载(BASE_PROMPT 已写明);弱模型不主动 → ReAct
        # 卡住时由 SkillManager.relevant 浮现催加载(甲)、再不加载则 auto_load 兜底注入(乙)。
        # 这条三层路径在 ReActLoop 里实现,run_executor 只负责"按渐进披露注册"。
        extra_skills = []
        if suite.project_id:
            from harness.skills import Skill

            for sk in await store.list_skills(suite.project_id):
                if sk.content.strip():
                    extra_skills.append(
                        Skill(
                            name=sk.name,
                            content=sk.content.strip(),
                            description=(sk.description or "").strip(),
                            preload=False,
                        )
                    )

        # 审批器:工厂注入 _emit,使权限事件也落 run_event 表(可重放)。
        approver = (
            perm_approver_factory(_emit)
            if (approve_mode and perm_approver_factory is not None)
            else None
        )

        @asynccontextmanager
        async def make_agent():
            skills = build_skill_manager(
                custom_prompt=suite.custom_prompt, extra=extra_skills or None
            )
            async with MCPClient(args=mcp_args) as mcp:
                # Hook 是通用扩展点(harness/hooks.py);默认不预填登录,登录态复用交由
                # 后续「环境管理」主线维护。需要时由调用方装配 HookManager 传入。
                agent = TestCaseAgent(
                    llm=build_llm_client(llm_config),
                    mcp=mcp,
                    vocab_resolver=vocab_resolver,
                    hooks=None,
                    skills=skills,
                    tools_registry=tools_registry,
                    max_steps=int(os.getenv("AGENT_MAX_STEPS", "40")),
                )
                if approver is not None:
                    agent.permission_approver = approver
                yield agent

        _case_by_id = {c.id: c for c in cases}

        async def _save_record(record) -> None:
            record.run_id = run_id
            await repo.save_record(record)
            saved_ids.add(record.case_id)
            case = _case_by_id.get(record.case_id)
            if case is not None and case.precondition_items:
                await store.save_case(case)

        orch = Orchestrator(agent_factory=make_agent)
        result = await orch.run_suite(
            cases,
            suite=suite,
            sse_callback=_emit,
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
