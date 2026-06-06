"""执行路由: /run, /stream SSE(Spec §4.2)。

执行不在 API 事件循环上跑:每次 run 起一个守护线程 + 独立事件循环 + 独立 Store
(见 ``api/execution_worker.py``),API 循环只管 HTTP/SSE,结构上永不被执行阻塞。
并发由 Orchestrator 的 ``parallelism`` 控制(各用例独立 MCP/浏览器)。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.execution_worker import make_sse_bridge, schedule_queue_cleanup, spawn_run
from api.repository import get_suite_settings, set_suite_settings
from api.server import get_repo, get_store

router = APIRouter(tags=["execution"])

# In-memory registry of active SSE queues + permission events。
# 权限事件用 threading.Event(执行在 worker 线程/loop,审批在 API loop,跨线程 set)。
_sse_queues: dict[str, asyncio.Queue] = {}
_permission_events: dict[str, threading.Event] = {}
_permission_results: dict[str, dict] = {}

logger = logging.getLogger(__name__)


def _mcp_args() -> list[str]:
    """playwright-mcp 启动参数:默认 --isolated(规避 Chrome 密码泄露弹框)+ --headless。"""
    args = ["@playwright/mcp@latest"]
    if os.getenv("MCP_ISOLATED", "1") != "0":
        args.append("--isolated")
    if os.getenv("MCP_HEADLESS", "1") != "0":
        args.append("--headless")
    return args


@router.post("/suites/{suite_id}/run")
async def trigger_run(suite_id: str, repo=Depends(get_repo), store=Depends(get_store)):
    suite = await repo.get_suite(suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")

    cases = await repo.list_by_suite(suite_id)
    if not cases:
        raise HTTPException(400, "Suite 没有用例，请先上传 Excel")

    # Check if already running。注意:_sse_queues 是内存态,进程重启后必为空,
    # 故 DB 里仍为 running 但不在队列中的 run 是上次崩溃/重启遗留的僵尸 → 自动收尾,
    # 不再 409 卡住用户(否则每次崩溃都要手动改库)。
    runs = await repo.list_runs_by_suite(suite_id)
    active_run = next((r for r in runs if r["status"] == "running"), None)
    if active_run is not None:
        if active_run["id"] in _sse_queues:
            raise HTTPException(409, "已有执行在进行中")
        await repo.update_run(active_run["id"], status="failed", finished_at=time.time())

    run_id = uuid.uuid4().hex[:12]
    await repo.create_run(run_id, suite_id, len(cases))

    settings = await get_suite_settings(store, suite_id)
    parallelism = int(settings.get("parallelism", 1))
    approve_mode = settings.get("permission_mode") == "approve"

    queue: asyncio.Queue = asyncio.Queue()
    _sse_queues[run_id] = queue
    api_loop = asyncio.get_running_loop()  # worker 经它 call_soon_threadsafe 桥回 SSE
    sse_cb = make_sse_bridge(api_loop, queue)

    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///storage/ai_test.db")

    async def _worker_main() -> None:
        # —— worker 线程/事件循环内:独立 Store(engine 绑定本 loop,不能共享 API 的) ——
        from api.repository import SQLModelRepository
        from harness.agent import TestCaseAgent
        from harness.llm import LiteLLMClient
        from harness.orchestrator import Orchestrator
        from harness.permission import threading_event_approver
        from intelligence.vocabulary import VocabularyManager, VocabularyResolver
        from mcp_client.client import MCPClient
        from storage.db import Store

        worker_store = Store(url=db_url)
        await worker_store.init()
        worker_repo = SQLModelRepository(worker_store)
        vocab_resolver = VocabularyResolver(VocabularyManager(worker_store))
        mcp_args = _mcp_args()

        # Custom Tool 注册表:env CUSTOM_TOOLS_YAML 指向 YAML 配置时加载(LLM 按需调用
        # + custom_tool 数据断言)。无配置则为 None,该类工具/断言不生效(skipped)。
        tools_registry = None
        tools_yaml = os.getenv("CUSTOM_TOOLS_YAML")
        if tools_yaml:
            try:
                from harness.tools import load_tool_registry_from_yaml

                tools_registry = load_tool_registry_from_yaml(tools_yaml)
            except Exception as e:  # noqa: BLE001 — 配置坏不应阻断整个 run
                logger.warning("加载 Custom Tool 配置失败(%s):%s", tools_yaml, e)

        # Session/Login Hooks(P2):Suite 绑定了 SessionProfile 才接通,实现跨用例 Cookie 复用。
        session_profile = None
        if suite.session_profile:
            session_profile = await worker_store.get_session_profile(suite.session_profile)
            if session_profile is None:
                logger.warning(
                    "Suite %s 绑定的 SessionProfile %r 不存在,跳过 Session 复用",
                    suite_id,
                    suite.session_profile,
                )

        async def _perm_approver(req):
            event_id = uuid.uuid4().hex[:8]
            ev = threading.Event()
            _permission_events[event_id] = ev
            _permission_results[event_id] = {"approved": False}
            await sse_cb(
                "permission",
                {
                    "event_id": event_id,
                    "case_id": "current",
                    "action": req.tool_name,
                    "reason": req.reason,
                },
            )
            try:
                return await threading_event_approver(ev, _permission_results[event_id])(req)
            finally:
                _permission_events.pop(event_id, None)
                _permission_results.pop(event_id, None)

        # 每条用例独立 agent + MCP(各自 Chrome 子进程):并发隔离 + 各自收尾,无全局卡顿
        @asynccontextmanager
        async def make_agent():
            from harness.hook_builder import build_session_hooks
            from harness.skills import build_skill_manager

            # Skill 体系(P3):基础 DomainSkill + Suite 自定义提示词(custom_prompt)接通
            skills = build_skill_manager(custom_prompt=suite.custom_prompt)

            async with MCPClient(args=mcp_args) as mcp:
                # Session Hooks 需绑定本用例的 mcp(注入/抓取 Cookie 都走它)
                hooks = (
                    build_session_hooks(session_profile, mcp)
                    if session_profile is not None
                    else None
                )
                agent = TestCaseAgent(
                    llm=LiteLLMClient(),
                    mcp=mcp,
                    vocab_resolver=vocab_resolver,
                    hooks=hooks,
                    skills=skills,
                    tools_registry=tools_registry,
                )
                if approve_mode:
                    agent.permission_approver = _perm_approver
                yield agent

        async def _save_record(record) -> None:
            record.run_id = run_id
            await worker_repo.save_record(record)

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
            await worker_repo.update_run(
                run_id,
                status="completed",
                passed_cases=result.passed_count,
                failed_cases=result.failed_count,
                finished_at=time.time(),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Run %s failed", run_id)
            await sse_cb("error", {"message": str(e)})
            await worker_repo.update_run(run_id, status="failed", finished_at=time.time())
        finally:
            # 收尾哨兵 + 在 API loop 上安全移除队列(让 /stream 终止)
            await sse_cb("suite_done", {"run_id": run_id, "sentinel": True})
            await asyncio.sleep(0.5)
            schedule_queue_cleanup(api_loop, _sse_queues, run_id)
            await worker_store.close()

    spawn_run(run_id, _worker_main)
    return {"run_id": run_id, "status": "started"}


@router.get("/suites/{suite_id}/stream")
async def stream_events(suite_id: str, run_id: str):
    queue = _sse_queues.get(run_id)
    if queue is None:
        raise HTTPException(404, "Run not found or already finished")

    async def _generate():
        yield ": keepalive\n\n"
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield msg
                # Terminate stream on suite_done or error events
                if msg.startswith("event: suite_done") or msg.startswith("event: error"):
                    break
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/suites/{suite_id}/settings")
async def get_settings(suite_id: str, store=Depends(get_store)):
    return await get_suite_settings(store, suite_id)


class SettingsUpdate(BaseModel):
    permission_mode: str  # "trust" | "approve"
    parallelism: int = 1  # 并发执行用例数(1=串行)


@router.put("/suites/{suite_id}/settings")
async def update_settings(suite_id: str, body: SettingsUpdate, store=Depends(get_store)):
    await set_suite_settings(store, suite_id, body.permission_mode, body.parallelism)
    return {"ok": True}
