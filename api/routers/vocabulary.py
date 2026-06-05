"""词汇表 CRUD + 扫描路由(Spec §4.5, T-27 尾巴)。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.server import get_repo
from input.models import PageVocabulary

router = APIRouter(tags=["vocabulary"])


class VocabularyEntry(BaseModel):
    url_pattern: str
    page_title: str
    login_role: str
    vocabulary: dict = {}
    action_map: list = []


@router.get("/vocabulary")
async def list_vocabulary(
    page: int = Query(1, ge=1),
    q: str = Query("", alias="query"),
    repo=Depends(get_repo),
):
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
async def create_vocabulary(entry: VocabularyEntry, repo=Depends(get_repo)):
    v = PageVocabulary(**entry.model_dump())
    await repo.save(v)
    return v.model_dump()


@router.put("/vocabulary/{vocab_id}")
async def update_vocabulary(vocab_id: int, entry: VocabularyEntry, repo=Depends(get_repo)):
    v = PageVocabulary(**entry.model_dump())
    await repo.save(v)
    return v.model_dump()


@router.delete("/vocabulary/{vocab_id}")
async def delete_vocabulary(
    vocab_id: int,
    url_pattern: str = Query(""),
    page_title: str = Query(""),
    login_role: str = Query(""),
    repo=Depends(get_repo),
):
    if not await repo.delete_by_key(url_pattern, page_title, login_role):
        raise HTTPException(404, "Vocabulary entry not found")
    return {"ok": True}


@router.post("/vocabulary/scan")
async def trigger_scan():
    """说明扫描策略(策略C:执行期增量)。

    页面扫描需要一个**已登录、已导航到目标页的活浏览器**取 A11y 快照,孤立的本接口
    没有浏览器上下文,无法独立扫描。实际扫描在 **Suite 执行时自动进行**:
    ``harness/agent.py`` 执行结束后复用 ReAct 期间捕获的快照,调用
    ``intelligence/scanner.py`` 的 ``scan_and_save`` 增量并库(AI 来源,手动条目优先)。
    可用环境变量 ``VOCAB_SCAN=0`` 关闭。
    """
    return {
        "ok": True,
        "scanned": False,
        "message": (
            "页面扫描在执行 Suite 时自动进行:用例跑完后会复用执行期的页面快照,"
            "自动提炼业务词→元素映射并入词汇表。请执行一个 Suite,完成后回此页刷新查看新增词条。"
        ),
    }
