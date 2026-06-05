"""执行 Worker:把整条 suite 执行搬出 API 事件循环。

根因背景:uvicorn 默认单进程 / 单事件循环 / 单线程,协作式并发(只在 ``await`` 让出)。
若把用例执行(`asyncio.create_task`)跑在这条 API 共用循环上,执行链里残留的同步活儿
(快照解析 / ref 索引 / 上下文压缩 / 库的同步开销)就会周期性占住循环 → 执行期间所有
HTTP 请求 pending。

本模块提供通用工具:**每次 run 一个守护线程 + 自己的事件循环**承载执行;SSE 事件经
``call_soon_threadsafe`` 线程安全地桥回 API loop 的队列。这样 API 循环只管 HTTP/SSE,
结构上永不被执行阻塞,也是并发执行(各用例独立浏览器)的地基。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


def format_sse(event: str, data: dict) -> str:
    """事件 → SSE 帧文本(与原 _sse_event 一致:换行转义,避免破坏 data 行)。"""
    payload = json.dumps(data, ensure_ascii=False).replace("\n", "\\n")
    return f"event: {event}\ndata: {payload}\n\n"


def make_sse_bridge(
    api_loop: asyncio.AbstractEventLoop, queue: asyncio.Queue
) -> Callable[[str, dict], Awaitable[None]]:
    """返回 async ``sse_cb(event, data)``:从 **worker 线程**线程安全地把事件投递到
    API loop 的 SSE 队列(``put_nowait`` 不阻塞;队列无界)。"""

    async def sse_cb(event: str, data: dict) -> None:
        msg = format_sse(event, data)
        try:
            api_loop.call_soon_threadsafe(queue.put_nowait, msg)
        except RuntimeError:  # API loop 已关闭(进程收尾)——丢弃即可
            pass

    return sse_cb


def spawn_run(run_id: str, coro_factory: Callable[[], Awaitable[None]]) -> threading.Thread:
    """每次 run 起一个守护线程,在**自己的事件循环**里跑整条 suite 执行。

    ``coro_factory`` 是无参 async 工厂,在 worker loop 内构造执行主体(用 worker 自己的
    Store/repo + 独立 MCP),异常只记录不外抛(线程内自管)。
    """

    def _thread() -> None:
        try:
            asyncio.run(coro_factory())
        except Exception:  # noqa: BLE001 — 执行线程兜底,异常仅记录不影响 API 进程
            logger.exception("执行线程 run=%s 异常", run_id)

    t = threading.Thread(target=_thread, name=f"run-{run_id}", daemon=True)
    t.start()
    return t


def schedule_queue_cleanup(
    api_loop: asyncio.AbstractEventLoop, queue_registry: dict, run_id: str
) -> None:
    """在 API loop 上安全移除该 run 的 SSE 队列(worker 收尾后调用)。"""
    try:
        api_loop.call_soon_threadsafe(queue_registry.pop, run_id, None)
    except RuntimeError:
        pass
