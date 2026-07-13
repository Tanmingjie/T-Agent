"""词汇表管理(规格 §5.5 Page Intelligence,T-22)。

业务词 → UI 元素映射。缓存键 = ``url_pattern + page_title + login_role``,URL 用路由
匹配(``/order/{id}``)。**手动条目优先级高于 AI 扫描**;自愈失败可标记 ``stale``。

``VocabularyManager`` 基于 Store(T-21)读写,供运行时探针/自愈按业务词查页面真实元素
(``VocabularyResolver``)。〔2026-06-22 翻译阶段化后 ``enhance_targets`` 已移除——翻译只产
意图、不接地,不再在翻译期改写 target。〕
"""

from __future__ import annotations

import re

from input.models import PageVocabulary

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


def _loose_eq(a: str, b: str) -> bool:
    """宽松相等:任一侧为空(未指定)即视为通配命中,否则按相等。"""
    return not a or not b or a == b


def _term_lookup(vocab: dict, term: str) -> dict | None:
    """在一页词汇表里查业务词:先精确,再子串(优先长 key)。"""
    if term in vocab:
        return vocab[term]
    # 按长度降序匹配,优先长 key(避免短 key 吞掉长 key)
    for key in sorted(vocab, key=len, reverse=True):
        if term and (term in key or key in term):
            return vocab[key]
    return None


class VocabularyManager:
    def __init__(self, store, *, project_id: str = "") -> None:
        self.store = store
        # 租户作用域(T-P04b):单机/CLI 留空=默认租户;平台路径按 suite 的项目注入。
        self.project_id = project_id

    async def find_page(self, url: str, page_title: str, login_role: str) -> PageVocabulary | None:
        """按 路由匹配 + 标题 + 角色 找页面词汇表(非 stale 优先)。

        page_title / login_role 采用**宽松匹配**:任一侧为空视为通配。这样运行时
        探针(login_role 常未知、传空)仍能命中手动维护的词条;而两侧都给值时按相等匹配。
        """
        candidates = [
            v
            for v in await self.store.list_vocabularies(project_id=self.project_id)
            if route_match(v.url_pattern, url)
            and _loose_eq(v.page_title, page_title)
            and _loose_eq(v.login_role, login_role)
            # base_url 作用域:词条带 base_url 时,仅当它是当前 url 前缀才命中(跨系统隔离);
            # 空 base_url(历史/手动未填)视为通配,向后兼容。
            and (not v.base_url or (url or "").startswith(v.base_url))
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

    async def mark_stale(
        self, url_pattern: str, page_title: str, login_role: str, base_url: str = ""
    ) -> bool:
        """标记某页词汇表过期(自愈失败 / 手动)。命中返回 True。"""
        v = await self.store.get_vocabulary(
            url_pattern, page_title, login_role, base_url, self.project_id
        )
        if v is None:
            return False
        v.stale = True
        await self.store.save_vocabulary(v)
        return True


class VocabularyResolver:
    """把 VocabularyManager 适配成 page_probe 的 VocabResolver(运行时按页面查词)。

    探针只知道 url/title,login_role 由构造时给定(默认空)。命中返回词条
    ``{role, name, ...}``,供历史断言探针或后续定位器解析按真实 role+name 匹配。
    """

    def __init__(self, manager: VocabularyManager, *, login_role: str = "") -> None:
        self.manager = manager
        self.login_role = login_role

    async def resolve(self, target: str, *, url: str = "", title: str = "") -> dict | None:
        entry = await self.manager.resolve(
            target, url=url, page_title=title, login_role=self.login_role
        )
        # selector / name / role 任一可用即返回(selector 型最稳健,见 page_probe)
        if isinstance(entry, dict) and (
            entry.get("selector") or entry.get("name") or entry.get("role")
        ):
            return entry
        return None
