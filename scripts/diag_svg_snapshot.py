"""诊断:playwright-mcp 的 browser_snapshot(a11y 树)能否看到 SVG 交互元素。

复现"内网工艺图(SVG)找不到元素"的根因——SVG 图形默认不进无障碍树 → 快照里没有
→ 模型拿不到 ref → 点不动。对照:browser_evaluate 直接遍历 SVG DOM,证明元素其实都在。

用法:
    python scripts/diag_svg_snapshot.py [URL]
默认打开本地样例 storage/diag_svg_hmi.html。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_client.client import MCPClient, viewport_args  # noqa: E402

_SVG_ENUM = """() => {
  const out = [];
  document.querySelectorAll('svg').forEach(svg => {
    svg.querySelectorAll('*').forEach(el => {
      const tag = el.tagName.toLowerCase();
      const onclick = el.getAttribute('onclick');
      const titleEl = el.querySelector ? el.querySelector(':scope > title') : null;
      const info = {
        tag,
        id: el.id || null,
        role: el.getAttribute('role'),
        ariaLabel: el.getAttribute('aria-label'),
        title: titleEl ? titleEl.textContent : null,
        text: tag === 'text' ? el.textContent.trim() : null,
        clickable: !!onclick || el.style.cursor === 'pointer',
      };
      if (info.clickable || info.text || info.ariaLabel || info.title) out.push(info);
    });
  });
  return out;
}"""


def _serve(directory: Path) -> tuple[str, object]:
    """起一个后台 HTTP server 服务 directory,返回 (base_url, httpd)。避开 file:// 在 headless 被挡。"""
    import functools
    import threading
    from http.server import HTTPServer, SimpleHTTPRequestHandler

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}", httpd


async def main() -> None:
    storage = Path(__file__).resolve().parent.parent / "storage"
    httpd = None
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        base, httpd = _serve(storage)
        url = f"{base}/diag_svg_hmi.html"
    args = ["@playwright/mcp@latest", "--isolated", "--headless"] + viewport_args()
    async with MCPClient(args=args) as mcp:
        nav = mcp.result_to_text(await mcp.call_tool("browser_navigate", {"url": url}))
        print("[navigate]", nav[:200], "\n")
        snap = mcp.result_to_text(await mcp.call_tool("browser_snapshot", {}))
        print("=" * 70)
        print("【browser_snapshot(a11y 树,= 喂给模型的观察)】")
        print("=" * 70)
        print(snap[:4000])

        ev = mcp.result_to_text(
            await mcp.call_tool("browser_evaluate", {"function": _SVG_ENUM})
        )
        print()
        print("=" * 70)
        print("【browser_evaluate 直接遍历 SVG DOM(元素其实都在这里)】")
        print("=" * 70)
        print(ev[:4000])


if __name__ == "__main__":
    asyncio.run(main())
