"""词汇表 CRUD 路由(Spec §4.5)。

〔2026-06-24 扫描子系统收缩〕:词汇表自动扫描(主动扫描 ActiveScanner + 执行后增量扫描)
整体退役——阶段化重设计后翻译不接地、裁决主走 llm_judge,自动扫描产出几乎不被消费。本路由
只保留**手动维护**(CRUD);运行时 `VocabularyResolver` 仍服务断言探针解析与操作侧自愈。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.auth import Principal, get_principal, role_in_project
from api.server import get_repo, get_store
from input.models import PageVocabulary


async def _ensure_project_access(store, principal: Principal, project_id: str) -> None:
    """词汇表按项目作用域:指定 project_id 时要求成员资格(单机/空 project 放行)。"""
    if project_id and await role_in_project(store, principal.user_id, project_id) is None:
        from fastapi import HTTPException

        raise HTTPException(403, "无权访问该项目词汇表")


router = APIRouter(tags=["vocabulary"])
logger = logging.getLogger(__name__)


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
