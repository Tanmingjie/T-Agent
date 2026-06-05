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


# ── 策略C:执行期增量扫描接入 agent ─────────────────────────


async def test_agent_incremental_scan_persists_from_run(store):
    """agent._incremental_scan 复用执行期快照(action_steps.tool_result)增量并库。"""
    from harness.agent import TestCaseAgent
    from harness.react_loop import ReActResult
    from input.models import ActionStep
    from intelligence.vocabulary import VocabularyManager, VocabularyResolver

    llm = _FakeLLM(
        json.dumps({"提交": {"role": "button", "name": "保存并提交"}}, ensure_ascii=False)
    )
    resolver = VocabularyResolver(VocabularyManager(store), login_role="admin")
    agent = TestCaseAgent(llm=llm, mcp=None, vocab_resolver=resolver)

    result = ReActResult(
        action_steps=[
            # 非快照步(无 ref)→ 忽略
            ActionStep(step_no=1, tool_name="mark_step_done", tool_result="已完成第 1 步"),
            # 快照步(含 ref)→ 提炼并库
            ActionStep(
                step_no=2,
                tool_name="browser_snapshot",
                tool_result=SNAPSHOT,
                url="https://intranet/order/9",
            ),
        ]
    )

    async def _noop_phase(phase, label):
        pass

    await agent._incremental_scan(result, _noop_phase)

    got = await resolver.manager.resolve(
        "提交", url="https://intranet/order/9", page_title="订单详情", login_role="admin"
    )
    assert got is not None and got["name"] == "保存并提交"
    assert got.get("source") == "ai"


async def test_agent_incremental_scan_dedups_fresh_page(store):
    """同页面已有非 stale 词汇表 → 第二次扫描跳过(不再调 LLM 提炼)。"""
    from harness.agent import TestCaseAgent
    from harness.react_loop import ReActResult
    from input.models import ActionStep
    from intelligence.vocabulary import VocabularyManager, VocabularyResolver

    class _CountingLLM(LLMClient):
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            return LLMResponse(
                content=json.dumps(
                    {"提交": {"role": "button", "name": "保存并提交"}}, ensure_ascii=False
                )
            )

    llm = _CountingLLM()
    resolver = VocabularyResolver(VocabularyManager(store), login_role="admin")
    agent = TestCaseAgent(llm=llm, mcp=None, vocab_resolver=resolver)
    result = ReActResult(
        action_steps=[
            ActionStep(
                step_no=1,
                tool_name="browser_snapshot",
                tool_result=SNAPSHOT,
                url="https://intranet/order/9",
            )
        ]
    )

    async def _noop_phase(phase, label):
        pass

    await agent._incremental_scan(result, _noop_phase)  # 首次:扫
    await agent._incremental_scan(result, _noop_phase)  # 二次:已存非 stale → 跳过
    assert llm.calls == 1  # 只提炼了一次


async def test_agent_enhance_spec_with_vocab_rewrites_target(store):
    """翻译期增强:base_url 命中的词汇表把精确业务词 target 改写成页面真实文案。"""
    from harness.agent import TestCaseAgent
    from input.models import SpecStep, TestCase, TestSpec
    from intelligence.vocabulary import VocabularyManager, VocabularyResolver

    await store.save_vocabulary(
        PageVocabulary(
            url_pattern="/order",
            page_title="",
            login_role="",
            vocabulary={"提交": {"role": "button", "name": "保存并提交"}},
        )
    )
    resolver = VocabularyResolver(VocabularyManager(store))
    agent = TestCaseAgent(llm=_FakeLLM(), mcp=None, vocab_resolver=resolver)

    spec = TestSpec(
        case_id="T1",
        name="下单",
        base_url="https://intranet/order/9",
        steps=[SpecStep(action="click", target="提交"), SpecStep(action="click", target="取消")],
    )
    case = TestCase(id="T1", name="下单", base_url="https://intranet/order/9", steps=[])
    out = await agent._enhance_spec_with_vocab(spec, case)
    assert out.steps[0].target == "保存并提交"  # 命中改写
    assert out.steps[1].target == "取消"  # 未命中保持


async def test_agent_incremental_scan_skips_without_snapshots(store):
    """无任何带 ref 的快照时,不调用 LLM、不写库(best-effort 早退)。"""
    from harness.agent import TestCaseAgent
    from harness.react_loop import ReActResult
    from input.models import ActionStep
    from intelligence.vocabulary import VocabularyManager, VocabularyResolver

    class _BoomLLM(LLMClient):
        async def chat(self, messages, tools=None, **kwargs):
            raise AssertionError("无快照不应触发 LLM 提炼")

    resolver = VocabularyResolver(VocabularyManager(store))
    agent = TestCaseAgent(llm=_BoomLLM(), mcp=None, vocab_resolver=resolver)
    result = ReActResult(
        action_steps=[ActionStep(step_no=1, tool_name="mark_step_done", tool_result="x")]
    )

    async def _noop_phase(phase, label):
        pass

    await agent._incremental_scan(result, _noop_phase)  # 不应抛
    assert await store.list_vocabularies() == []


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
