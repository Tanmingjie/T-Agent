"""增量抓取**新站点**公开页 a11y 快照,**合并**进 eval_fg/snapshots.json(保留旧条目)。

扩样用(2026-06-24):原 snapshots.json 仅 automationexercise 单站点 5 页,n=26 偏小、单站点。
本脚本新增 the-internet(QA 靶场,强确定性)+ demoblaze(电商 demo)若干公开页,**只写新 key**、
不覆盖已标注的 automationexercise 快照(重抓会因广告/动态内容漂移使既有 EVAL 标签失效)。

抓完读快照人工补 EVAL 真/假预期(见 judge_eval.EVAL),再跑 ab_grounding 收紧置信区间。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())

from mcp_client.client import MCPClient  # noqa: E402

# 新增页面(key → url)。key 不与现有 snapshots.json 冲突。
PAGES_MORE = {
    # the-internet.herokuapp.com —— QA 靶场,公开、强确定性、英文
    "ti_login": "https://the-internet.herokuapp.com/login",
    "ti_dropdown": "https://the-internet.herokuapp.com/dropdown",
    "ti_checkboxes": "https://the-internet.herokuapp.com/checkboxes",
    "ti_tables": "https://the-internet.herokuapp.com/tables",
    "ti_add_remove": "https://the-internet.herokuapp.com/add_remove_elements/",
    "ti_status": "https://the-internet.herokuapp.com/status_codes",
    # demoblaze.com —— 电商 demo,公开商品网格
    "db_home": "https://www.demoblaze.com/",
    "db_cart": "https://www.demoblaze.com/cart.html",
}

OUT = Path("eval_fg/snapshots.json")


async def main() -> None:
    mcp_args = ["@playwright/mcp@latest", "--isolated", "--headless"]
    snaps: dict[str, str] = {}
    if OUT.exists():
        snaps = json.loads(OUT.read_text(encoding="utf-8"))
        print(f"已载入现有 {len(snaps)} 条快照,增量合并新站点…\n")
    async with MCPClient(args=mcp_args) as mcp:
        for key, url in PAGES_MORE.items():
            try:
                await mcp.call_tool("browser_navigate", {"url": url})
                await asyncio.sleep(2.5)
                result = await mcp.call_tool("browser_snapshot", {})
                text = mcp.result_to_text(result)
                snaps[key] = text
                print(f"[{key}] {url}  快照长度={len(text)}  ref节点={text.count('[ref=')}")
            except Exception as e:  # noqa: BLE001
                print(f"[{key}] {url}  抓取失败:{e}")
    OUT.write_text(json.dumps(snaps, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}(共 {len(snaps)} 条)")


if __name__ == "__main__":
    asyncio.run(main())
