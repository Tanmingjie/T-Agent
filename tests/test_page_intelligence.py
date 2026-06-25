"""Page Intelligence 单测:词汇表查询 / 路由匹配 / VocabularyResolver / stale。

〔2026-06-24 扫描子系统收缩〕:Scanner(LLM 提炼)+ 执行期增量扫描(agent._incremental_scan)
+ merge_scanned 已整体退役,相关测试随之删除。本文件只覆盖**手动维护 + 运行时解析**:
VocabularyManager.resolve、VocabularyResolver(给 page_probe / 操作侧自愈用)、route_match、
base_url 作用域、mark_stale。
"""

from __future__ import annotations

import pytest

from input.models import PageVocabulary
from intelligence.vocabulary import VocabularyManager, route_match
from storage.db import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(f"sqlite+aiosqlite:///{tmp_path}/v.db")
    await s.init()
    yield s
    await s.close()


# ── 路由匹配 ─────────────────────────────────────────────────


def test_route_match_with_param():
    assert route_match("/order/{id}", "https://x/order/9")
    assert not route_match("/order/{id}", "https://x/home")


def test_route_match_plain():
    assert route_match("/inventory", "https://x/inventory.html")


# ── VocabularyManager:查询 + 路由 ───────────────────────────


async def test_resolve_term_by_route(store):
    await store.save_vocabulary(
        PageVocabulary(
            url_pattern="/order/{id}",
            page_title="订单详情",
            login_role="admin",
            vocabulary={"提交": {"role": "button", "name": "保存并提交", "confidence": 0.9}},
        )
    )
    mgr = VocabularyManager(store)
    hit = await mgr.resolve(
        "提交", url="https://x/order/88", page_title="订单详情", login_role="admin"
    )
    assert hit is not None
    assert hit["name"] == "保存并提交"


async def test_resolve_miss_returns_none(store):
    mgr = VocabularyManager(store)
    assert (
        await mgr.resolve("提交", url="https://x/order/1", page_title="订单", login_role="admin")
        is None
    )


# ── 宽松匹配 + VocabularyResolver(运行时接入) ────────────────


async def test_loose_match_empty_login_role_hits(store):
    """运行时 login_role 常未知(传空)→ 应仍能命中按 admin 存的词条(空=通配)。"""
    await store.save_vocabulary(
        PageVocabulary(
            url_pattern="/inventory",
            page_title="Swag Labs",
            login_role="admin",
            vocabulary={"购物车图标": {"selector": ".shopping_cart_badge"}},
        )
    )
    mgr = VocabularyManager(store)
    hit = await mgr.resolve(
        "购物车图标", url="https://x/inventory.html", page_title="Swag Labs", login_role=""
    )
    assert hit == {"selector": ".shopping_cart_badge"}


async def test_vocabulary_resolver_returns_selector_entry(store):
    """VocabularyResolver(给 page_probe 用)能解析 selector-only 词条。"""
    from intelligence.vocabulary import VocabularyResolver

    await store.save_vocabulary(
        PageVocabulary(
            url_pattern="/inventory",
            page_title="Swag Labs",
            login_role="",
            vocabulary={"购物车图标": {"selector": ".shopping_cart_badge"}},
        )
    )
    resolver = VocabularyResolver(VocabularyManager(store))
    entry = await resolver.resolve(
        "购物车图标数量", url="https://x/inventory.html", title="Swag Labs"
    )
    assert entry == {"selector": ".shopping_cart_badge"}
    # 未命中页面 → None
    assert await resolver.resolve("购物车图标", url="https://x/other", title="X") is None


# ── base_url 作用域:跨系统隔离 / 同系统共享 ──────────────────


async def test_base_url_scopes_cross_system(store):
    """两个系统同 url_pattern(/login)不同 base_url → 各自命中,不互相污染。"""
    from intelligence.vocabulary import VocabularyResolver

    await store.save_vocabulary(
        PageVocabulary(
            base_url="https://sys-a.intra",
            url_pattern="/login",
            page_title="",
            login_role="",
            vocabulary={"提交": {"name": "A系统提交"}},
        )
    )
    await store.save_vocabulary(
        PageVocabulary(
            base_url="https://sys-b.intra",
            url_pattern="/login",
            page_title="",
            login_role="",
            vocabulary={"提交": {"name": "B系统提交"}},
        )
    )
    # 两条独立存在(键含 base_url,不被 upsert 合并)
    assert len(await store.list_vocabularies()) == 2
    resolver = VocabularyResolver(VocabularyManager(store))
    a = await resolver.resolve("提交", url="https://sys-a.intra/login")
    b = await resolver.resolve("提交", url="https://sys-b.intra/login")
    assert a == {"name": "A系统提交"}
    assert b == {"name": "B系统提交"}


async def test_empty_base_url_is_wildcard(store):
    """空 base_url(历史/手动未填)对任意 url 通配命中(向后兼容)。"""
    from intelligence.vocabulary import VocabularyResolver

    await store.save_vocabulary(
        PageVocabulary(
            url_pattern="/login",
            page_title="",
            login_role="",
            vocabulary={"提交": {"name": "通配提交"}},
        )
    )
    resolver = VocabularyResolver(VocabularyManager(store))
    got = await resolver.resolve("提交", url="https://anything.com/login")
    assert got == {"name": "通配提交"}


# ── stale 标记(自愈失败触发)─────────────────────────────


async def test_mark_stale(store):
    await store.save_vocabulary(PageVocabulary(url_pattern="/p", page_title="t", login_role="r"))
    mgr = VocabularyManager(store)
    ok = await mgr.mark_stale("/p", "t", "r")
    assert ok
    got = await store.get_vocabulary("/p", "t", "r")
    assert got.stale is True
