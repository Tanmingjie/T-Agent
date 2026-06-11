"""T-P08 临时联调:入队 → 经 run_executor(worker 路径)执行一个真实 run → 验结果。

跑法(项目根,.env 提供 LLM):
    python examples/_tp08_queue_smoke.py
验证:saucedemo TC101 经队列+共享执行核跑通,ExecutionRecord.passed=True。
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cli.run_case import _load_dotenv

_load_dotenv(Path(__file__).resolve().parent.parent / ".env")

os.environ.setdefault("MCP_ISOLATED", "1")
os.environ.setdefault("MCP_HEADLESS", "1")


async def main() -> int:
    from api.run_executor import execute_run
    from input.excel_parser import parse_excel
    from input.models import Suite
    from storage.db import Store

    db_url = f"sqlite+aiosqlite:///{Path('storage/_tp08_smoke.db').resolve().as_posix()}"
    Path("storage/_tp08_smoke.db").unlink(missing_ok=True)
    store = Store(url=db_url)
    await store.init()

    # 1) 建 suite + 用例(saucedemo TC101)
    suite_id = "tp08"
    base_url = "https://www.saucedemo.com"
    await store.save_suite(Suite(id=suite_id, name="tp08", base_url=base_url))
    cases = parse_excel("examples/saucedemo_cases.xlsx", base_url=base_url, suite_id=suite_id)
    cases = [c for c in cases if c.id.endswith("TC101")]
    for c in cases:
        c.base_url = base_url
        c.suite_id = suite_id
        await store.save_case(c)
    case_id = cases[0].id

    # 2) 建 run 记录 + 入队
    from api.repository import SQLModelRepository

    repo = SQLModelRepository(store)
    run_id = uuid.uuid4().hex[:12]
    await repo.create_run(run_id, suite_id, 1)
    await store.enqueue_run(run_id, suite_id, "", case_id)

    # 3) 模拟 worker:领取 → 执行 → 完成
    claimed = await store.claim_next_run("smoke-worker")
    assert claimed is not None and claimed.run_id == run_id
    print(f"领到任务 run={claimed.run_id} case={claimed.case_id}")

    async def _noop(_e, _d):
        return None

    await execute_run(
        db_url=db_url, run_id=run_id, suite_id=suite_id, case_id=case_id, sse_cb=_noop
    )
    await store.complete_queued_run(run_id, "done")

    # 4) 验结果
    records = await repo.list_records_by_run(run_id)
    run = await repo.get_run(run_id)
    await store.close()
    if not records:
        print("❌ 无执行记录")
        return 1
    rec = records[0]
    print(f"run.status={run['status']} record.passed={rec.passed} 断言={rec.case_assertions}")
    ok = run["status"] == "completed" and rec.passed
    print("✅ T-P08 队列+worker 路径跑通 PASS" if ok else "❌ 未通过")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
