"""执行 Worker 工具单测:SSE 帧格式化 / 跨线程桥接 / 线程内跑协程。"""

from __future__ import annotations

import asyncio
import threading

from api.execution_worker import format_sse, make_sse_bridge, spawn_run


def test_format_sse_frame():
    msg = format_sse("case_start", {"case_id": "A", "i": 1})
    assert msg.startswith("event: case_start\ndata: ")
    assert msg.endswith("\n\n")  # SSE 帧以空行结束
    assert '"case_id": "A"' in msg


def test_format_sse_escapes_newlines_in_payload():
    # data 行内不得含裸换行(否则破坏 SSE 帧);换行被转义
    msg = format_sse("x", {"k": "line1\nline2"})
    data_line = msg.split("data: ", 1)[1]
    assert "\n\n" == data_line[-2:]  # 仅结尾的帧分隔
    assert "line1" in msg and "line2" in msg
    assert "line1\nline2" not in msg  # 真换行已转义


async def test_sse_bridge_delivers_to_queue():
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    cb = make_sse_bridge(loop, q)
    await cb("phase", {"phase": "executing"})
    await asyncio.sleep(0)  # 让 call_soon_threadsafe 排的回调执行
    msg = q.get_nowait()
    assert msg.startswith("event: phase\ndata: ")
    assert '"phase": "executing"' in msg


def test_spawn_run_executes_coro_in_thread():
    done = threading.Event()
    seen: dict = {}

    async def main():
        seen["thread"] = threading.current_thread().name
        done.set()

    spawn_run("rX", main)
    assert done.wait(2.0)  # 线程内协程确实跑了
    assert seen["thread"] == "run-rX"
