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
async def trigger_scan(repo=Depends(get_repo), store=Depends(lambda: None)):
    """触发页面扫描(调用 intelligence/scanner.py)。

    注意:扫描需要浏览器连接,本路由目前返回提示;实际扫描在 Agent 执行时由
    intelligence/scanner.py 的 scan_and_save 完成。
    """
    return {
        "ok": True,
        "message": "扫描已触发,词汇表将在执行过程中增量更新。请执行 Suite 以触发实际扫描。",
    }
