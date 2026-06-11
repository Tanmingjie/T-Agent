"""词汇表 CRUD + 主动扫描路由(Spec §4.5)。

主动扫描(2026-06-10):会导航的只读探索式扫描——起浏览器、可选登录、按入口清单逐页
抓快照提炼词汇,可选浅爬点击触发的内页。扫描在**独立线程 + 独立事件循环 + 独立 Store**
里跑(同 execution_worker,避免阻塞 API loop),状态经内存表轮询。
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.auth import Principal, get_principal, role_in_project
from api.execution_worker import spawn_run
from api.server import get_repo, get_store
from input.models import PageVocabulary


async def _ensure_project_access(store, principal: Principal, project_id: str) -> None:
    """词汇表按项目作用域:指定 project_id 时要求成员资格(单机/空 project 放行)。"""
    if project_id and await role_in_project(store, principal.user_id, project_id) is None:
        from fastapi import HTTPException

        raise HTTPException(403, "无权访问该项目词汇表")


router = APIRouter(tags=["vocabulary"])
logger = logging.getLogger(__name__)

# 主动扫描状态(内存,进程级):scan_id → {status, report?, error?}
_scan_status: dict[str, dict] = {}


class VocabularyEntry(BaseModel):
    project_id: str = ""  # 多租户作用域(T-P04b);项目上下文路由化留 T-P07,此处先留字段不丢
    base_url: str = ""  # 被测系统根地址(作用域键),跨系统隔离
    url_pattern: str
    page_title: str
    login_role: str
    vocabulary: dict = {}
    action_map: list = []


@router.get("/vocabulary")
async def list_vocabulary(
    page: int = Query(1, ge=1),
    q: str = Query("", alias="query"),
    project_id: str = Query(""),
    principal: Principal = Depends(get_principal),
    store=Depends(get_store),
    repo=Depends(get_repo),
):
    # 指定项目 → 成员可见、作用域过滤;未指定 → 仅平台管理员(含单机隐式 admin)看全部。
    if project_id:
        await _ensure_project_access(store, principal, project_id)
        all_items = await repo.list_vocabularies(project_id=project_id)
    else:
        if not principal.is_platform_admin:
            raise HTTPException(400, "请指定 project_id")
        all_items = await repo.list_vocabularies()
    if q:
        all_items = [
            v
            for v in all_items
            if q.lower() in v.page_title.lower() or q.lower() in v.url_pattern.lower()
        ]
    # Simple page-based slice (50 per page)
    start = (page - 1) * 50
    items = all_items[start : start + 50]
    return {"items": [v.model_dump() for v in items], "total": len(all_items), "page": page}


@router.post("/vocabulary")
async def create_vocabulary(
    entry: VocabularyEntry,
    principal: Principal = Depends(get_principal),
    store=Depends(get_store),
    repo=Depends(get_repo),
):
    await _ensure_project_access(store, principal, entry.project_id)
    v = PageVocabulary(**entry.model_dump())
    await repo.save(v)
    return v.model_dump()


@router.put("/vocabulary/{vocab_id}")
async def update_vocabulary(
    vocab_id: int,
    entry: VocabularyEntry,
    principal: Principal = Depends(get_principal),
    store=Depends(get_store),
    repo=Depends(get_repo),
):
    await _ensure_project_access(store, principal, entry.project_id)
    v = PageVocabulary(**entry.model_dump())
    await repo.save(v)
    return v.model_dump()


@router.delete("/vocabulary/{vocab_id}")
async def delete_vocabulary(
    vocab_id: int,
    url_pattern: str = Query(""),
    page_title: str = Query(""),
    login_role: str = Query(""),
    base_url: str = Query(""),
    project_id: str = Query(""),
    principal: Principal = Depends(get_principal),
    store=Depends(get_store),
    repo=Depends(get_repo),
):
    await _ensure_project_access(store, principal, project_id)
    if not await repo.delete_by_key(url_pattern, page_title, login_role, base_url, project_id):
        raise HTTPException(404, "Vocabulary entry not found")
    return {"ok": True}


class ScanRequest(BaseModel):
    base_url: str  # 被测系统根地址(作用域键)
    entry_paths: list[str] = []  # 入口页面/路径清单(空=只扫 "/")
    session_profile: str | None = None  # 可选:用其落盘 Cookie 登录后再扫(内网需登录的页)
    login_role: str = ""  # 词条归属角色(可空)
    shallow_crawl: bool = False  # 可选浅爬:点击导航类元素进入点击触发的内页(只读护栏)


def _mcp_args() -> list[str]:
    args = ["@playwright/mcp@latest"]
    if os.getenv("MCP_ISOLATED", "1") != "0":
        args.append("--isolated")
    if os.getenv("MCP_HEADLESS", "1") != "0":
        args.append("--headless")
    return args


@router.post("/vocabulary/scan")
async def trigger_scan(body: ScanRequest):
    """启动一次主动扫描(会导航的只读探索式扫描)。后台线程跑,返回 scan_id 供轮询。

    覆盖 base_url 主页 + 入口清单 + (可选)点击触发的内页。需登录的页面给 ``session_profile``
    (用其落盘 Cookie 注入);公开页面留空即可。
    """
    scan_id = uuid.uuid4().hex[:12]
    _scan_status[scan_id] = {"status": "running", "report": None, "error": None}
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///storage/ai_test.db")

    async def _worker() -> None:
        from harness.agent import settle_page
        from harness.llm import LiteLLMClient
        from harness.session import SessionManager, make_mcp_cookie_injector
        from intelligence.active_scan import ActiveScanner
        from intelligence.scanner import Scanner
        from intelligence.vocabulary import VocabularyManager
        from mcp_client.client import MCPClient
        from storage.db import Store

        store = Store(url=db_url)
        await store.init()
        try:
            async with MCPClient(args=_mcp_args()) as mcp:
                manager = VocabularyManager(store)
                scanner = Scanner(LiteLLMClient())

                # 登录回调:有 session_profile 且其 Cookie 有效则注入(否则扫公开页)
                login = None
                if body.session_profile:
                    profile = await store.get_session_profile(body.session_profile)
                    if profile is not None:
                        cookies = SessionManager().load_cookies(profile)
                        if cookies:
                            injector = make_mcp_cookie_injector(mcp, body.base_url)

                            async def login():  # noqa: E306
                                await injector(None, cookies)

                async def _settle(_mcp):
                    await settle_page(_mcp)

                active = ActiveScanner(
                    mcp,
                    scanner,
                    manager,
                    login_role=body.login_role,
                    settle=_settle,
                    crawl_depth=int(os.getenv("SCAN_CRAWL_DEPTH", "1")),
                    max_pages=int(os.getenv("SCAN_MAX_PAGES", "20")),
                )
                report = await active.scan(
                    body.base_url,
                    body.entry_paths,
                    login=login,
                    shallow_crawl=body.shallow_crawl,
                )
                _scan_status[scan_id] = {
                    "status": "completed",
                    "report": report.to_dict(),
                    "error": None,
                }
        except Exception as e:  # noqa: BLE001
            logger.exception("主动扫描 %s 失败", scan_id)
            _scan_status[scan_id] = {"status": "failed", "report": None, "error": str(e)}
        finally:
            await store.close()

    spawn_run(scan_id, _worker)
    return {"scan_id": scan_id, "status": "started"}


@router.get("/vocabulary/scan/{scan_id}")
async def scan_status(scan_id: str):
    st = _scan_status.get(scan_id)
    if st is None:
        raise HTTPException(404, "scan not found")
    return {"scan_id": scan_id, **st}
