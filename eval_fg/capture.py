"""抓取 automationexercise 若干公开页面的真实 a11y 快照,存 snapshots.json。

用于「裁判 false-green 评测」:把裁判从执行里剥离,直接用真实快照压测
``AssertionEngine._check_llm_judge``(偏-FAIL 通道)在真/假预期上的判定。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())  # 本机 embeddable python:sys.path 锁定,手动注入

from mcp_client.client import MCPClient  # noqa: E402

PAGES = {
    "home": "https://automationexercise.com/",
    "products": "https://automationexercise.com/products",
    "search_dress": "https://automationexercise.com/products?search=dress",
    "login": "https://automationexercise.com/login",
    "cart_empty": "https://automationexercise.com/view_cart",
}

OUT = Path("eval_fg/snapshots.json")


async def main() -> None:
    mcp_args = ["@playwright/mcp@latest", "--isolated", "--headless"]
    snaps: dict[str, str] = {}
    async with MCPClient(args=mcp_args) as mcp:
        for key, url in PAGES.items():
            await mcp.call_tool("browser_navigate", {"url": url})
            await asyncio.sleep(2.5)  # 让页面/广告稳定
            result = await mcp.call_tool("browser_snapshot", {})
            text = mcp.result_to_text(result)
            snaps[key] = text
            print(f"[{key}] {url}  快照长度={len(text)}  ref节点={text.count('[ref=')}")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(snaps, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
