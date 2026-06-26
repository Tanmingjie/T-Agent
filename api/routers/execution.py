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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.auth import require_suite_access
from api.execution_worker import spawn_run
from api.repository import get_suite_settings, set_suite_settings

router = APIRouter(tags=["execution"])


# get_repo/get_store 用惰性包装(不在模块加载期 import api.server),打断
# execution→server→(router 块)→execution 的循环导入(execution 被直接 import 时尤甚)。
def get_repo():
    from api.server import get_repo as _g

    return _g()


def get_store():
    from api.server import get_store as _g

    return _g()


# suite 维度鉴权(单机/无 project_id 放行)。SSE /stream 因 EventSource 无法带 header,
# 单机隐式 admin 放行;平台 SSE 鉴权随 T-P09 双进程改造引入 token。
_suite_guard = [Depends(require_suite_access)]

# embedded 模式下「本进程内有活 worker 线程」的 run 集合,纯作**僵尸检测**用
# (DB 为 running 但不在此集合 = 上次进程崩溃遗留 → 自动收尾)。SSE 投递不再走内存,
# 统一经 run_event 表(execute_run 落表,/stream 从 seq 0 重放+尾随),故退出再进可看全程。
_live_runs: set[str] = set()
# 权限审批结果回传:执行在 worker 线程/loop,审批在 API loop,跨线程用 threading.Event set。
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


@router.post("/suites/{suite_id}/run", dependencies=_suite_guard)
async def trigger_run(
    suite_id: str,
    case_id: str | None = None,
    repo=Depends(get_repo),
    store=Depends(get_store),
):
    """触发执行。``case_id`` 给定时只跑该单条用例(抽屉里的「执行」按钮),否则跑整套件。"""
    suite = await repo.get_suite(suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")

    cases = await repo.list_by_suite(suite_id)
    if not cases:
        raise HTTPException(400, "Suite 没有用例，请先上传 Excel")
    if case_id is not None:
        cases = [c for c in cases if c.id == case_id]
        if not cases:
            raise HTTPException(404, f"用例 {case_id} 不存在于该套件")

    # Check if already running。注意:_sse_queues 是内存态,进程重启后必为空,
    # 故 DB 里仍为 running 但不在队列中的 run 是上次崩溃/重启遗留的僵尸 → 自动收尾,
    # 不再 409 卡住用户(否则每次崩溃都要手动改库)。
    runs = await repo.list_runs_by_suite(suite_id)
    active_run = next((r for r in runs if r["status"] == "running"), None)
    if active_run is not None:
        if active_run["id"] in _live_runs:
            raise HTTPException(409, "已有执行在进行中")
        await repo.update_run(active_run["id"], status="failed", finished_at=time.time())

    run_id = uuid.uuid4().hex[:12]
    await repo.create_run(run_id, suite_id, len(cases), suite.project_id, suite.version_id)
    await store.append_audit(
        "system", "run.trigger", project_id=suite.project_id, target=suite_id, detail=run_id
    )

    # 双进程模式(RUN_MODE=queue):API 只入队,独立 worker(scripts/worker.py)领取执行。
    # 默认 embedded:进程内守护线程执行(单机)。两模式进度都落 run_event 表,/stream 统一
    # 从表重放+尾随 → 退出执行页再进来可看全程。
    if os.getenv("RUN_MODE") == "queue":
        await store.enqueue_run(run_id, suite_id, suite.project_id, case_id)
        return {"run_id": run_id, "status": "queued"}

    api_loop = asyncio.get_running_loop()
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///storage/ai_test.db")

    def _perm_approver_factory(emit):
        """API 进程内审批:Reason 后 Act 前经 emit 推审批请求(落表可重放),
        threading.Event 跨线程等结果(执行在 worker 线程,审批 POST 在 API loop)。"""
        from harness.permission import threading_event_approver

        async def _perm_approver(req):
            event_id = uuid.uuid4().hex[:8]
            ev = threading.Event()
            _permission_events[event_id] = ev
            _permission_results[event_id] = {"approved": False}
            await emit(
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

        return _perm_approver

    _live_runs.add(run_id)  # 僵尸检测标记:本进程有活 worker 线程

    async def _worker_main() -> None:
        # 共享执行核(api/run_executor.py):自带独立 Store/loop;事件由 execute_run 落 run_event
        # 表(sse_cb=None,不再走内存队列)。/stream 从表重放+尾随。
        from api.run_executor import execute_run

        try:
            await execute_run(
                db_url=db_url,
                run_id=run_id,
                suite_id=suite_id,
                case_id=case_id,
                sse_cb=None,
                perm_approver_factory=_perm_approver_factory,
            )
        finally:
            api_loop.call_soon_threadsafe(_live_runs.discard, run_id)

    spawn_run(run_id, _worker_main)
    return {"run_id": run_id, "status": "started"}


@router.post("/suites/{suite_id}/runs/{run_id}/stop", dependencies=_suite_guard)
async def stop_run(suite_id: str, run_id: str, repo=Depends(get_repo), store=Depends(get_store)):
    """请求停止一个正在执行的 run(协作式优雅停)。

    置 run_record.cancel_requested 标志;执行链(orchestrator 每用例前 / ReAct 每轮)轮询到
    后,正在飞的那步 MCP/LLM 调用跑完即优雅退出,未开跑的用例补「已中止」占位,run 终态记
    aborted。embedded / queue 两模式统一(都各自有 Store 读同一标志)。幂等:已结束/不存在
    返回 ok=false。"""
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    if run["status"] != "running":
        return {"ok": False, "status": run["status"], "detail": "run 已结束,无需停止"}
    flagged = await repo.request_cancel(run_id)
    if flagged:
        # 落 run_event 表,让在场/重连的 /stream 订阅者即时看到「停止中」。
        await store.append_run_event(run_id, "aborting", {"run_id": run_id})
    return {"ok": flagged, "status": "running" if flagged else run["status"]}


@router.get("/suites/{suite_id}/stream")
async def stream_events(
    suite_id: str, run_id: str, store=Depends(get_store), repo=Depends(get_repo)
):
    # 统一:从 run_event 表**重放(seq 0 起)+ 尾随**——embedded/queue 同一逻辑。在场或晚到
    # (退出执行页再进来)订阅者都能拿到完整进度,suite_done/error 收尾。run 不存在则 404。
    from api.execution_worker import format_sse

    run = await repo.get_run(run_id)
    if run is None and await store.get_queued_run(run_id) is None:
        raise HTTPException(404, "Run not found")

    async def _generate():
        yield ": keepalive\n\n"
        last_seq = 0
        idle = 0
        while True:
            events = await store.list_run_events(run_id, after_seq=last_seq)
            if events:
                idle = 0
                for ev in events:
                    last_seq = ev.seq
                    yield format_sse(ev.event_type, ev.data)
                    if ev.event_type in ("suite_done", "error"):
                        return
            else:
                idle += 1
                yield ": keepalive\n\n"
                # 兜底:run 已落终态且无新事件 → 收尾(防 worker 没发 suite_done)
                if idle >= 4:
                    cur = await repo.get_run(run_id)
                    if cur and cur["status"] in ("completed", "failed", "aborted"):
                        yield format_sse("suite_done", {"run_id": run_id, "sentinel": True})
                        return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/suites/{suite_id}/settings", dependencies=_suite_guard)
async def get_settings(suite_id: str, store=Depends(get_store)):
    return await get_suite_settings(store, suite_id)


class SettingsUpdate(BaseModel):
    permission_mode: str  # "trust" | "approve"
    parallelism: int = 1  # 并发执行用例数(1=串行)


@router.put("/suites/{suite_id}/settings", dependencies=_suite_guard)
async def update_settings(suite_id: str, body: SettingsUpdate, store=Depends(get_store)):
    await set_suite_settings(store, suite_id, body.permission_mode, body.parallelism)
    return {"ok": True}
