"""词汇表管理(规格 §5.5 Page Intelligence,T-22)。

业务词 → UI 元素映射。缓存键 = ``url_pattern + page_title + login_role``,URL 用路由
匹配(``/order/{id}``)。**手动条目优先级高于 AI 扫描**;自愈失败可标记 ``stale``。

``VocabularyManager`` 基于 Store(T-21)读写;``enhance_targets`` 用解析出的映射把
TestSpec 里的模糊业务词改写成页面真实词("提交" → "保存并提交"),供 §5.2 预解析增强。
"""

from __future__ import annotations

import re

from input.models import Assertion, PageVocabulary, SpecStep, TestSpec

MANUAL = "manual"
AI = "ai"


def route_match(url_pattern: str, url: str) -> bool:
    """URL 是否匹配路由模式(``{x}`` → 任意非 / 段;否则按字面子串)。"""
    if not url_pattern or not url:
        return False
    parts = re.split(r"\{[^}]+\}", url_pattern)
    regex = "[^/]+".join(re.escape(p) for p in parts)
    try:
        return re.search(regex, url) is not None
    except re.error:
        return url_pattern in url


def _term_lookup(vocab: dict, term: str) -> dict | None:
    """在一页词汇表里查业务词:先精确,再双向包含。"""
    if term in vocab:
        return vocab[term]
    for key, val in vocab.items():
        if term and (term in key or key in term):
            return val
    return None


class VocabularyManager:
    def __init__(self, store) -> None:
        self.store = store

    async def find_page(self, url: str, page_title: str, login_role: str) -> PageVocabulary | None:
        """按 路由匹配 + 标题 + 角色 找页面词汇表(非 stale 优先)。"""
        candidates = [
            v
            for v in await self.store.list_vocabularies()
            if route_match(v.url_pattern, url)
            and v.page_title == page_title
            and v.login_role == login_role
        ]
        if not candidates:
            return None
        fresh = [v for v in candidates if not v.stale]
        return (fresh or candidates)[0]

    async def resolve(
        self, term: str, *, url: str, page_title: str, login_role: str
    ) -> dict | None:
        """业务词 → UI 元素映射(命中页面后查词)。"""
        page = await self.find_page(url, page_title, login_role)
        if page is None:
            return None
        return _term_lookup(page.vocabulary, term)

    async def merge_scanned(self, scanned: PageVocabulary) -> PageVocabulary:
        """把 AI 扫描结果并入既有词汇表;手动条目不被覆盖。"""
        existing = await self.store.get_vocabulary(
            scanned.url_pattern, scanned.page_title, scanned.login_role
        )
        if existing is None:
            await self.store.save_vocabulary(scanned)
            return scanned
        merged = dict(existing.vocabulary)
        for term, entry in scanned.vocabulary.items():
            old = merged.get(term)
            if isinstance(old, dict) and old.get("source") == MANUAL:
                continue  # 手动条目优先,保留
            merged[term] = entry
        existing.vocabulary = merged
        existing.stale = False
        await self.store.save_vocabulary(existing)
        return existing

    async def mark_stale(self, url_pattern: str, page_title: str, login_role: str) -> bool:
        """标记某页词汇表过期(自愈失败 / 手动)。命中返回 True。"""
        v = await self.store.get_vocabulary(url_pattern, page_title, login_role)
        if v is None:
            return False
        v.stale = True
        await self.store.save_vocabulary(v)
        return True


def enhance_targets(spec: TestSpec, mapping: dict[str, str]) -> TestSpec:
    """用 {业务词: 页面真实词} 改写 TestSpec 的 step/assertion 目标(纯函数)。

    未命中的目标保持不变。返回新的 TestSpec(不改原对象)。
    """

    def _rewrite(target: str) -> str:
        return mapping.get(target, target)

    new = spec.model_copy(deep=True)
    new.given = [SpecStep(**{**g.model_dump(), "target": _rewrite(g.target)}) for g in new.given]
    new.steps = [SpecStep(**{**s.model_dump(), "target": _rewrite(s.target)}) for s in new.steps]
    new.assertions = [
        Assertion(**{**a.model_dump(), "target": _rewrite(a.target)}) for a in new.assertions
    ]
    return new
