"""执行 worker 进程(平台化 T-P08)。

独立进程,轮询 run_queue 领取任务(PG: FOR UPDATE SKIP LOCKED),用 api.run_executor
执行到完成、落库。多开几个进程即横向扩并发(进程边界=未来 Pod 边界)。

用法:
    python scripts/worker.py
环境变量:
    DATABASE_URL                连接串(与 API 同库;平台用 postgresql+asyncpg://...)
    WORKER_ID                   worker 标识(默认 主机名-pid)
    WORKER_POLL_INTERVAL        无任务时轮询间隔秒(默认 2)
    WORKER_STALE_SECONDS        心跳超时回收阈值(默认 120)
    WORKER_MAX_PROJECT_CONC     单项目最大并发 run(0=不限,默认 0)
    + 执行相关:MCP_ISOLATED/MCP_HEADLESS/AGENT_MAX_STEPS/CUSTOM_TOOLS_YAML 等(同 API)

注:SSE 实时进度由 T-P09(LISTEN/NOTIFY)接;本进程执行期 sse_cb 为 no-op,run 仍完整
落库(ExecutionRecord/RunRecord),前端可轮询结果接口。审批暂走 trust(approve 模式的
跨进程审批工单留 T-P09)。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from pathlib import Path

# 允许直接 `python scripts/worker.py`(embeddable Python 的 sys.path 不含 cwd)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 加载 .env(LLM_MODEL 等),与 API 一致
from cli.run_case import _load_dotenv  # noqa: E402

_load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [worker] %(message)s")
logger = logging.getLogger("worker")


async def _run_one(db_url: str, claimed) -> None:
    """执行一条领到的任务,期间定时心跳防被回收。

    进度经 run_event 表落库(API /stream 尾随转 SSE,T-P09);审批走 permission_request
    工单表(写 pending 后轮询,超时默认拒绝,保守)。
    """
    import uuid

    from api.run_executor import execute_run
    from storage.db import Store

    hb_store = Store(url=db_url)
    await hb_store.init()
    stop = asyncio.Event()
    approve_timeout = float(os.getenv("WORKER_APPROVE_TIMEOUT", "300"))

    async def _sse_cb(event: str, data: dict) -> None:
        try:
            await hb_store.append_run_event(claimed.run_id, event, data)
        except Exception:  # noqa: BLE001
            logger.exception("写进度事件失败 run=%s event=%s", claimed.run_id, event)

    async def _approver(req) -> bool:
        req_id = uuid.uuid4().hex[:12]
        await hb_store.create_permission_request(
            req_id, claimed.run_id, getattr(req, "tool_name", ""), getattr(req, "reason", "")
        )
        await _sse_cb(
            "permission",
            {"event_id": req_id, "action": getattr(req, "tool_name", ""),
             "reason": getattr(req, "reason", "")},
        )
        deadline = asyncio.get_event_loop().time() + approve_timeout
        while asyncio.get_event_loop().time() < deadline:
            row = await hb_store.get_permission_request(req_id)
            if row is not None and row.status != "pending":
                return row.status == "approved"
            await asyncio.sleep(1.0)
        return False  # 超时默认拒绝(保守)

    async def _heartbeat() -> None:
        while not stop.is_set():
            try:
                await hb_store.heartbeat_run(claimed.run_id)
            except Exception:  # noqa: BLE001
                logger.exception("心跳失败 run=%s", claimed.run_id)
            try:
                await asyncio.wait_for(stop.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    hb_task = asyncio.create_task(_heartbeat())
    try:
        await execute_run(
            db_url=db_url,
            run_id=claimed.run_id,
            suite_id=claimed.suite_id,
            case_id=claimed.case_id,
            sse_cb=_sse_cb,
            perm_approver=_approver,
        )
        status = "done"
    except Exception:  # noqa: BLE001
        logger.exception("执行任务失败 run=%s", claimed.run_id)
        status = "failed"
    finally:
        stop.set()
        await hb_task
        await hb_store.complete_queued_run(claimed.run_id, status)
        await hb_store.close()
    logger.info("任务完成 run=%s status=%s", claimed.run_id, status)


async def main() -> None:
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///storage/ai_test.db")
    worker_id = os.getenv("WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
    poll = float(os.getenv("WORKER_POLL_INTERVAL", "2"))
    stale = float(os.getenv("WORKER_STALE_SECONDS", "120"))
    max_conc = int(os.getenv("WORKER_MAX_PROJECT_CONC", "0"))

    from storage.db import Store

    store = Store(url=db_url)
    await store.init()
    logger.info("worker 启动 id=%s db=%s", worker_id, db_url.split("@")[-1])
    try:
        while True:
            claimed = await store.claim_next_run(
                worker_id, stale_seconds=stale, max_project_concurrency=max_conc
            )
            if claimed is None:
                await asyncio.sleep(poll)
                continue
            logger.info("领到任务 run=%s suite=%s", claimed.run_id, claimed.suite_id)
            await _run_one(db_url, claimed)
    finally:
        await store.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("worker 退出")
