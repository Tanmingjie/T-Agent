"""T-22 单元测试:Page Intelligence(词汇表 + Scanner)。

TDD:路由匹配、词汇表查询、手动>AI 优先、stale 标记、Scanner 提炼、注入增强。
"""

from __future__ import annotations

import json

import pytest

from harness.llm import LLMClient, LLMResponse
from input.models import Assertion, PageVocabulary, SpecStep, TestSpec
from intelligence.scanner import Scanner
from intelligence.vocabulary import VocabularyManager, enhance_targets, route_match
from storage.db import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(f"sqlite+aiosqlite:///{tmp_path}/v.db")
    await s.init()
    yield s
    await s.close()


class _FakeLLM(LLMClient):
    def __init__(self, content=""):
        self._content = content

    async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
        return LLMResponse(content=self._content)


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


# ── 手动条目优先于 AI 扫描 ──────────────────────────────────


async def test_manual_entry_wins_over_ai(store):
    # 已有手动条目
    await store.save_vocabulary(
        PageVocabulary(
            url_pattern="/p",
            page_title="t",
            login_role="r",
            vocabulary={"提交": {"name": "人工指定提交", "source": "manual"}},
        )
    )
    mgr = VocabularyManager(store)
    # AI 扫描得到不同结果
    scanned = PageVocabulary(
        url_pattern="/p",
        page_title="t",
        login_role="r",
        vocabulary={
            "提交": {"name": "AI猜的提交", "source": "ai"},
            "取消": {"name": "Cancel", "source": "ai"},
        },
    )
    merged = await mgr.merge_scanned(scanned)
    assert merged.vocabulary["提交"]["name"] == "人工指定提交"  # 手动保留
    assert merged.vocabulary["取消"]["name"] == "Cancel"  # 新 AI 条目补入


# ── stale 标记(自愈失败触发)─────────────────────────────


async def test_mark_stale(store):
    await store.save_vocabulary(PageVocabulary(url_pattern="/p", page_title="t", login_role="r"))
    mgr = VocabularyManager(store)
    ok = await mgr.mark_stale("/p", "t", "r")
    assert ok
    got = await store.get_vocabulary("/p", "t", "r")
    assert got.stale is True


# ── Scanner:LLM 提炼词汇表 ──────────────────────────────────

SNAPSHOT = """\
### Page
- Page URL: https://intranet/order/9
- Page Title: 订单详情
### Snapshot
```yaml
- button "保存并提交" [ref=e3]
- textbox "用户名" [ref=e5]
```
"""


async def test_scanner_extract_builds_vocabulary():
    llm = _FakeLLM(
        json.dumps(
            {
                "提交": {"role": "button", "name": "保存并提交", "confidence": 0.9},
                "用户名": {"role": "textbox", "name": "用户名", "confidence": 0.95},
            },
            ensure_ascii=False,
        )
    )
    scanner = Scanner(llm)
    vocab = await scanner.extract(SNAPSHOT, login_role="admin")
    assert vocab.page_title == "订单详情"
    assert vocab.login_role == "admin"
    assert vocab.vocabulary["提交"]["name"] == "保存并提交"
    # AI 提炼的条目应标记来源
    assert vocab.vocabulary["提交"].get("source") == "ai"


async def test_scanner_scan_and_save_persists(store):
    llm = _FakeLLM(
        json.dumps({"提交": {"role": "button", "name": "保存并提交"}}, ensure_ascii=False)
    )
    scanner = Scanner(llm)
    mgr = VocabularyManager(store)
    await scanner.scan_and_save(
        SNAPSHOT, login_role="admin", manager=mgr, url_pattern="/order/{id}"
    )
    got = await mgr.resolve(
        "提交", url="https://intranet/order/1", page_title="订单详情", login_role="admin"
    )
    assert got["name"] == "保存并提交"


async def test_scanner_bad_json_empty_vocab():
    vocab = await Scanner(_FakeLLM("不是json")).extract(SNAPSHOT, login_role="admin")
    assert vocab.vocabulary == {}


# ── 注入增强:用词汇表改写 TestSpec 目标 ────────────────────


def test_enhance_targets_rewrites_vague_terms():
    spec = TestSpec(
        case_id="TC1",
        name="x",
        base_url="https://x",
        steps=[SpecStep(action="click", target="提交")],
        assertions=[Assertion(type="element_visible", target="提交")],
    )
    enhanced = enhance_targets(spec, {"提交": "保存并提交"})
    assert enhanced.steps[0].target == "保存并提交"
    assert enhanced.assertions[0].target == "保存并提交"


def test_enhance_targets_leaves_unknown_unchanged():
    spec = TestSpec(
        case_id="TC1",
        name="x",
        base_url="https://x",
        steps=[SpecStep(action="click", target="登录")],
    )
    enhanced = enhance_targets(spec, {"提交": "保存并提交"})
    assert enhanced.steps[0].target == "登录"
