"""执行路由: /run, /stream SSE(Spec §4.2)。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.repository import get_suite_settings, set_suite_settings
from api.server import get_repo, get_store

router = APIRouter(tags=["execution"])

# In-memory registry of active SSE queues + permission events
_sse_queues: dict[str, asyncio.Queue] = {}
_permission_events: dict[str, asyncio.Event] = {}
_permission_results: dict[str, dict] = {}

logger = logging.getLogger(__name__)


async def _sse_event(event: str, data: dict, queue: asyncio.Queue) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    await queue.put(f"event: {event}\ndata: {payload}\n\n")


@router.post("/suites/{suite_id}/run")
async def trigger_run(suite_id: str, repo=Depends(get_repo), store=Depends(get_store)):
    suite = await repo.get_suite(suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")

    cases = await repo.list_by_suite(suite_id)
    if not cases:
        raise HTTPException(400, "Suite 没有用例，请先上传 Excel")

    # Check if already running
    runs = await repo.list_runs_by_suite(suite_id)
    active_run = next((r for r in runs if r["status"] == "running"), None)
    if active_run is not None:
        raise HTTPException(409, "已有执行在进行中")

    run_id = uuid.uuid4().hex[:12]
    await repo.create_run(run_id, suite_id, len(cases))

    queue: asyncio.Queue = asyncio.Queue()
    _sse_queues[run_id] = queue

    async def _run():
        try:
            from harness.agent import TestCaseAgent
            from harness.orchestrator import Orchestrator

            async def sse_cb(event: str, data: dict) -> None:
                await _sse_event(event, data, queue)

            # Push suite_start
            await _sse_event("suite_start", {"run_id": run_id, "total_cases": len(cases)}, queue)

            # Create agent with LLM from env
            from harness.llm import LLM

            llm = LLM()
            agent = TestCaseAgent(llm=llm)

            orch = Orchestrator(agent=agent)

            # Check permission mode
            settings = await get_suite_settings(store, suite_id)
            if settings["permission_mode"] == "approve":
                import uuid as _uuid

                from harness.permission import async_event_approver

                async def _perm_approver(req):
                    event_id = _uuid.uuid4().hex[:8]
                    ev = asyncio.Event()
                    _permission_events[event_id] = ev
                    _permission_results[event_id] = {"approved": False}
                    await _sse_event(
                        "permission",
                        {
                            "event_id": event_id,
                            "case_id": "current",
                            "action": req.tool_name,
                            "reason": req.reason,
                        },
                        queue,
                    )
                    return await async_event_approver(ev, _permission_results[event_id])(req)

                agent.permission_approver = _perm_approver

            result = await orch.run_suite(cases, suite=suite, sse_callback=sse_cb)

            # Save ExecutionRecords with run_id
            for record in result.records:
                record.run_id = run_id
                await repo.save_record(record)

            await repo.update_run(
                run_id,
                status="completed",
                passed_cases=result.passed_count,
                failed_cases=result.failed_count,
                finished_at=time.time(),
            )
        except Exception as e:
            logger.exception("Run %s failed", run_id)
            await _sse_event("error", {"message": str(e)}, queue)
            await repo.update_run(run_id, status="failed", finished_at=time.time())
        finally:
            _sse_queues.pop(run_id, None)

    asyncio.create_task(_run())
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


@router.put("/suites/{suite_id}/settings")
async def update_settings(suite_id: str, body: SettingsUpdate, store=Depends(get_store)):
    await set_suite_settings(store, suite_id, body.permission_mode)
    return {"ok": True}
