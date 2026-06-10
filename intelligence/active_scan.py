"""主动扫描(策略A 升级版,2026-06-10):会导航的**只读**探索式词汇表扫描。

与执行期增量扫描(策略C,复用用例轨迹)互补:主动扫描**自己开浏览器、自己导航**,
按用户给的入口清单逐页抓 A11y 快照提炼词汇,可选**浅爬**(点击导航类元素进入点击触发
的内页)。覆盖 base_url 主页 **+ 点击触发的子页**(规格 §5.5;用户 2026-06-10 决策)。

只读护栏:浅爬仅点击「导航类角色」(link/tab/menuitem),且跳过可及名命中高危词
(删除/提交/支付…)的元素,避免误触发删除/下单等变更操作;深度、页数、单页点击数受限。

本模块只依赖一个最小 MCP 接口(``call_tool`` + ``result_to_text``),便于 mock 单测;
登录由调用方以 ``login`` 回调注入(Cookie 注入 / 跑 login_aw),与扫描逻辑解耦。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from harness.page_probe import Snapshot, parse_snapshot
from harness.permission import DEFAULT_DANGEROUS_WORDS
from intelligence.scanner import Scanner
from intelligence.vocabulary import VocabularyManager

logger = logging.getLogger(__name__)

# 浅爬只点这些「导航类」角色(读多写少);按钮(button)默认不点,避免提交/变更副作用。
_NAV_ROLES = {"link", "tab", "menuitem"}


def _join(base_url: str, path: str) -> str:
    """拼 base_url + path。path 已是绝对 URL 则原样返回。"""
    p = (path or "").strip()
    if not p:
        return base_url
    if p.startswith("http://") or p.startswith("https://"):
        return p
    return base_url.rstrip("/") + "/" + p.lstrip("/")


@dataclass
class ScanReport:
    """一次主动扫描的产出汇总。"""

    pages: list[dict] = field(default_factory=list)  # [{url, title, terms}]
    errors: list[str] = field(default_factory=list)

    @property
    def total_terms(self) -> int:
        return sum(p["terms"] for p in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def to_dict(self) -> dict:
        return {
            "pages": self.pages,
            "errors": self.errors,
            "page_count": self.page_count,
            "total_terms": self.total_terms,
        }


class ActiveScanner:
    def __init__(
        self,
        mcp,
        scanner: Scanner,
        manager: VocabularyManager,
        *,
        login_role: str = "",
        settle=None,
        dangerous_words: list[str] | None = None,
        max_pages: int = 20,
        crawl_depth: int = 1,
        max_clicks_per_page: int = 8,
    ) -> None:
        self.mcp = mcp
        self.scanner = scanner
        self.manager = manager
        self.login_role = login_role
        self.settle = settle  # 可选 async callable(mcp):导航后等页面稳定
        self.dangerous = [w.lower() for w in (dangerous_words or DEFAULT_DANGEROUS_WORDS)]
        self.max_pages = max_pages
        self.crawl_depth = crawl_depth
        self.max_clicks_per_page = max_clicks_per_page

    async def scan(
        self,
        base_url: str,
        entry_paths: list[str],
        *,
        login=None,
        shallow_crawl: bool = False,
    ) -> ScanReport:
        """主动扫描入口清单(+ 可选浅爬)。返回 ScanReport。"""
        report = ScanReport()
        visited: set[str] = set()
        if login is not None:
            try:
                await login()
            except Exception as e:  # noqa: BLE001 — 登录失败仍尝试扫公开页
                report.errors.append(f"登录失败:{e}")
                logger.warning("主动扫描登录失败:%s", e)
        depth = self.crawl_depth if shallow_crawl else 0
        seeds = entry_paths or ["/"]
        for p in seeds:
            if len(report.pages) >= self.max_pages:
                break
            url = _join(base_url, p)
            try:
                await self._navigate(url)
            except Exception as e:  # noqa: BLE001
                report.errors.append(f"导航 {url} 失败:{e}")
                continue
            await self._scan_page(base_url, report, visited, depth, shallow_crawl)
        return report

    async def _scan_page(
        self, base_url: str, report: ScanReport, visited: set[str], depth: int, shallow: bool
    ) -> None:
        """抓当前页快照 → 提炼并库;若允许浅爬则逐个点击导航类元素递归。"""
        try:
            snap_text = await self._snapshot()
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"抓快照失败:{e}")
            return
        snap = parse_snapshot(snap_text)
        key = snap.url or f"page#{len(visited)}"
        if key in visited:
            return
        visited.add(key)
        try:
            vocab = await self.scanner.scan_and_save(
                snap_text, login_role=self.login_role, manager=self.manager, base_url=base_url
            )
            terms = len(vocab.vocabulary)
        except Exception as e:  # noqa: BLE001 — 提炼失败记一页 0 词,不中断整轮
            report.errors.append(f"提炼 {key} 失败:{e}")
            terms = 0
        report.pages.append({"url": key, "title": snap.title, "terms": terms})

        if not shallow or depth <= 0 or len(report.pages) >= self.max_pages:
            return
        for ref, name in self._safe_nav_refs(snap)[: self.max_clicks_per_page]:
            if len(report.pages) >= self.max_pages:
                break
            try:
                await self._click(ref, name)
            except Exception as e:  # noqa: BLE001
                report.errors.append(f"点击 {name!r} 失败:{e}")
                continue
            await self._scan_page(base_url, report, visited, depth - 1, shallow)
            # 回到本页继续点下一个(点击可能已跳走)
            try:
                await self._navigate(key)
            except Exception:  # noqa: BLE001
                break

    def _safe_nav_refs(self, snap: Snapshot) -> list[tuple[str, str]]:
        """从快照里挑可安全点击的导航类元素 (ref, name):角色白名单 + 跳过高危词。"""
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for n in snap.nodes:
            if not n.ref or n.role not in _NAV_ROLES or not n.name:
                continue
            low = n.name.lower()
            if any(w in low for w in self.dangerous):
                continue  # 只读护栏:可及名含高危词的导航元素不点
            if n.ref in seen:
                continue
            seen.add(n.ref)
            out.append((n.ref, n.name))
        return out

    # ── 最小 MCP 交互(可 mock)──────────────────────────────

    async def _navigate(self, url: str) -> None:
        await self.mcp.call_tool("browser_navigate", {"url": url})
        if self.settle is not None:
            await self.settle(self.mcp)

    async def _click(self, ref: str, name: str) -> None:
        await self.mcp.call_tool("browser_click", {"ref": ref, "element": name})
        if self.settle is not None:
            await self.settle(self.mcp)

    async def _snapshot(self) -> str:
        res = await self.mcp.call_tool("browser_snapshot", {})
        return self.mcp.result_to_text(res)
