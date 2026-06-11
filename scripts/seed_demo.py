"""把 saucedemo 作为 demo 项目/版本/套件种入后端 DB —— 内网项目接口未对接前的联调数据。

替代此前的前端 mock 渠道:数据真实落库,前端 `?project=demo` 走真实后端、端到端可点通
(版本→套件→执行→报告)。幂等:重复跑只补缺失部分,已存在则跳过。

用法(项目根,Windows PowerShell):
    python scripts/seed_demo.py
环境变量 DATABASE_URL 缺省时落 sqlite+aiosqlite:///storage/ai_test.db(与 API 一致)。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# embeddable python:sys.path 锁定,补当前目录以便 import 本地包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from input.excel_parser import parse_excel  # noqa: E402
from input.models import Project, Suite, Version  # noqa: E402
from storage.db import Store  # noqa: E402

PROJECT_ID = "demo"
PROJECT_NAME = "演示项目 · saucedemo"
BASE_URL = "https://www.saucedemo.com"


def namespaced_case_id(suite_id: str, case_id: str) -> str:
    """与 api/routers/suites.py 一致:用例 id 加套件前缀消歧(避免 TC101 跨套件覆盖)。"""
    prefix = f"{suite_id}--"
    return case_id if case_id.startswith(prefix) else f"{prefix}{case_id}"

# 版本 → 套件(xlsx)。v1.3 含两套件,旧版本只占位展示多版本切换。
VERSIONS = [
    ("demo-v1_3", "v1.3"),
    ("demo-v1_2", "v1.2"),
    ("demo-v1_1", "v1.1"),
]
# (suite_id, suite 名, version_id, xlsx 相对路径)
SUITES = [
    ("demo-suite-basic", "登录加购", "demo-v1_3", "examples/saucedemo_cases.xlsx"),
    ("demo-suite-checkout", "完整结算", "demo-v1_3", "examples/saucedemo_checkout.xlsx"),
]


async def main() -> None:
    root = Path(__file__).resolve().parent.parent
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///storage/ai_test.db")
    store = Store(url=db_url)
    await store.init()
    try:
        # 项目
        if await store.get_project(PROJECT_ID) is None:
            await store.save_project(
                Project(id=PROJECT_ID, name=PROJECT_NAME, description="saucedemo 联调种子", owner="demo")
            )
            print(f"[project] 建 {PROJECT_ID} ({PROJECT_NAME})")
        else:
            print(f"[project] {PROJECT_ID} 已存在,跳过")

        # 版本
        existing_vers = {v.id for v in await store.list_versions(PROJECT_ID)}
        for vid, vname in VERSIONS:
            if vid in existing_vers:
                print(f"[version] {vid} 已存在,跳过")
                continue
            await store.save_version(Version(id=vid, project_id=PROJECT_ID, name=vname))
            print(f"[version] 建 {vid} ({vname})")

        # 套件 + 用例
        existing_suites = {s.id for s in await store.list_suites(project_id=PROJECT_ID)}
        for sid, sname, vid, rel in SUITES:
            if sid in existing_suites:
                print(f"[suite] {sid} 已存在,跳过")
                continue
            await store.save_suite(
                Suite(
                    id=sid,
                    name=sname,
                    base_url=BASE_URL,
                    project_id=PROJECT_ID,
                    version_id=vid,
                    owner="demo",
                )
            )
            xlsx = root / rel
            cases = parse_excel(xlsx, base_url=BASE_URL, suite_id=sid)
            for c in cases:
                c.base_url = BASE_URL
                c.id = namespaced_case_id(sid, c.id)
            for c in cases:
                await store.save_case(c)
            print(f"[suite] 建 {sid} ({sname}) ← {rel},{len(cases)} 条用例")
    finally:
        await store.close()
    print("done. 前端用 ?project=demo 进入。")


if __name__ == "__main__":
    asyncio.run(main())
