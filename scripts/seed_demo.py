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
from input.models import Project, ProjectSkill, Suite, Version  # noqa: E402
from storage.db import Store  # noqa: E402

PROJECT_ID = "demo"
PROJECT_NAME = "演示项目 · saucedemo"
BASE_URL = "https://www.saucedemo.com"

# 项目级业务 Skill(标准 Skill 渐进披露:description 常驻供 LLM 判断,content 按需 load_skill 展开)。
# 内容由真实页面快照分析得出(登录页账号清单 / inventory 商品+排序 / cart / checkout 流程)。
SKILLS = [
    ProjectSkill(
        project_id=PROJECT_ID,
        name="saucedemo 商城业务",
        description="Swag Labs(saucedemo)演示电商的登录账号、商品目录、购物车与下单结算业务说明;"
        "登录/加购/下单/排序类用例相关时加载。",
        content=(
            "站点:Swag Labs 演示电商(https://www.saucedemo.com)。\n"
            "【登录】用户名见登录页清单,所有用户密码统一为 secret_sauce。可用账号及行为差异:\n"
            "- standard_user:正常用户;\n"
            "- locked_out_user:已被锁定,登录会报错「Epic sadface: Sorry, this user has been locked out.」;\n"
            "- problem_user:商品图片错乱、部分控件行为异常;\n"
            "- performance_glitch_user:页面加载明显变慢,需多等待/重试,勿误判为卡死;\n"
            "- error_user / visual_user:表单或视觉存在异常。\n"
            "【商品页 inventory.html】标题 Products,共 6 件商品。右上「Open Menu」含 All Items / About / "
            "Logout / Reset App State(重置购物车与登录态)。排序下拉(product-sort-container)可选:"
            "Name (A to Z) / Name (Z to A) / Price (low to high) / Price (high to low)。\n"
            "商品与价格:Sauce Labs Backpack $29.99、Sauce Labs Bike Light $9.99、Sauce Labs Bolt T-Shirt $15.99、"
            "Sauce Labs Fleece Jacket $49.99、Sauce Labs Onesie $7.99、Test.allTheThings() T-Shirt (Red) $15.99。\n"
            "每件商品有「Add to cart」按钮,加购后按钮变为「Remove」,右上购物车角标(shopping_cart_badge)数字 +1。\n"
            "【购物车 cart.html】列 QTY / Description;「Continue Shopping」返回商品页,「Remove」移除,「Checkout」进入结算。\n"
            "【结算】Step One(checkout-step-one.html)填 First Name / Last Name / Zip/Postal Code → Continue;"
            "Step Two(checkout-step-two.html)为订单概览(Payment / Shipping / Item total / Tax / Total)→ Finish;"
            "完成页 checkout-complete.html 显示「Thank you for your order!」。\n"
            "【判定信号】加购成功:购物车角标数字变化 + 按钮变 Remove;下单成功:URL 到 checkout-complete.html "
            "且出现「Thank you for your order!」。优先用这些确定性信号判断,而非仅凭页面跳转。"
        ),
    ),
]


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
                Project(
                    id=PROJECT_ID, name=PROJECT_NAME, description="saucedemo 联调种子", owner="demo"
                )
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

        # 项目级业务 Skill(upsert 幂等)
        existing_skills = {s.name for s in await store.list_skills(PROJECT_ID)}
        for sk in SKILLS:
            await store.save_skill(sk)
            verb = "更新" if sk.name in existing_skills else "建"
            print(f"[skill] {verb} {sk.name}")
    finally:
        await store.close()
    print("done. 前端用 ?project=demo 进入。")


if __name__ == "__main__":
    asyncio.run(main())
