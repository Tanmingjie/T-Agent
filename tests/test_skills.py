"""T-16 单元测试:Skill 体系(DomainSkill / PageSkill / ToolSkill)。

TDD:先写这些行为期望,再实现 harness/skills.py。
"""

from __future__ import annotations

from harness.skills import DomainSkill, PageSkill, SkillManager, ToolSkill

# ── DomainSkill:Suite 级,始终注入 ──────────────────────────


def test_domain_skill_always_selected():
    mgr = SkillManager()
    mgr.register(DomainSkill(name="订单术语", content="订单状态: 待审批/已审批"))
    # 无论 url / keywords 如何,都在
    assert [s.name for s in mgr.select(url="", keywords=[])] == ["订单术语"]
    assert [s.name for s in mgr.select(url="http://x/any", keywords=["xyz"])] == ["订单术语"]


# ── PageSkill:按 URL 动态加载/卸载 ─────────────────────────


def test_page_skill_selected_only_when_url_matches():
    mgr = SkillManager()
    mgr.register(PageSkill(name="订单页", content="本页有提交按钮", url_pattern="/order/{id}"))
    assert [s.name for s in mgr.select(url="https://x/order/9")] == ["订单页"]
    assert mgr.select(url="https://x/home") == []


def test_page_skill_plain_substring_pattern():
    mgr = SkillManager()
    mgr.register(PageSkill(name="清单页", content="x", url_pattern="/inventory"))
    assert [s.name for s in mgr.select(url="https://x/inventory.html")] == ["清单页"]


def test_page_skill_load_unload_tracking():
    mgr = SkillManager()
    mgr.register(PageSkill(name="A", content="a", url_pattern="/order/{id}"))
    mgr.register(PageSkill(name="B", content="b", url_pattern="/inventory"))

    mgr.select(url="https://x/order/1")
    assert mgr.loaded_pages == {"A"}
    # 离开订单页进清单页 → A 卸载,B 加载
    mgr.select(url="https://x/inventory")
    assert mgr.loaded_pages == {"B"}


# ── ToolSkill:相关度过滤 ───────────────────────────────────


def test_tool_skill_selected_when_keyword_relevant():
    mgr = SkillManager()
    mgr.register(
        ToolSkill(name="上传技能", content="用 browser_file_upload", triggers=["上传", "附件"])
    )
    assert [s.name for s in mgr.select(keywords=["上传发票"])] == ["上传技能"]
    assert mgr.select(keywords=["点击登录"]) == []


# ── render:组装成 prompt 片段 ──────────────────────────────


def test_render_includes_active_skill_content():
    mgr = SkillManager()
    mgr.register(DomainSkill(name="域", content="业务术语X"))
    mgr.register(PageSkill(name="页", content="页面提示Y", url_pattern="/order"))
    mgr.register(ToolSkill(name="具", content="工具说明Z", triggers=["上传"]))

    text = mgr.render(url="https://x/order/9", keywords=["上传文件"])
    assert "业务术语X" in text  # domain
    assert "页面提示Y" in text  # page(url 命中)
    assert "工具说明Z" in text  # tool(keyword 命中)


def test_render_excludes_inactive():
    mgr = SkillManager()
    mgr.register(PageSkill(name="页", content="不该出现", url_pattern="/order"))
    text = mgr.render(url="https://x/home", keywords=[])
    assert "不该出现" not in text


def test_render_empty_when_nothing_active():
    assert SkillManager().render(url="http://x", keywords=[]) == ""


# ── 选择顺序:Domain → Page → Tool ─────────────────────────


def test_select_order_domain_page_tool():
    mgr = SkillManager()
    mgr.register(ToolSkill(name="T", content="t", triggers=["上传"]))
    mgr.register(PageSkill(name="P", content="p", url_pattern="/order"))
    mgr.register(DomainSkill(name="D", content="d"))
    names = [s.name for s in mgr.select(url="https://x/order", keywords=["上传"])]
    assert names == ["D", "P", "T"]


# ── 与 Agent 集成:注入 Prompt + 随 URL 动态生效 ──────────────


class _RecordingLLM:
    """记录每次收到的 system prompt;按序返回预设响应。"""

    def __init__(self, responses):
        self._r = responses
        self._i = 0
        self.systems: list[str] = []

    async def chat(self, messages, tools=None, **kwargs):
        self.systems.append(messages[0]["content"])
        idx = min(self._i, len(self._r) - 1)
        self._i += 1
        return self._r[idx]


async def test_agent_injects_domain_skill_into_system_prompt():
    from harness.agent import TestCaseAgent
    from tests.test_agent import SNAPSHOT_OK, _case, _FakeMCP, _resp, _spec

    mgr = SkillManager()
    mgr.register(DomainSkill(name="域", content="DOMAIN_MARK_业务术语"))

    llm = _RecordingLLM(
        [
            _resp(content="点", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK), skills=mgr)
    await agent.run(_case(), spec=_spec())
    assert any("DOMAIN_MARK_业务术语" in s for s in llm.systems)


async def test_agent_pageskill_activates_after_navigation():
    from harness.agent import TestCaseAgent
    from tests.test_agent import SNAPSHOT_OK, _case, _FakeMCP, _resp, _spec

    # SNAPSHOT_OK 的 Page URL 是 https://intranet/order/list
    mgr = SkillManager()
    mgr.register(
        PageSkill(name="订单页", content="PAGE_MARK_订单页提示", url_pattern="/order/list")
    )

    llm = _RecordingLLM(
        [
            _resp(content="点", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK), skills=mgr)
    await agent.run(_case(), spec=_spec())
    # 首轮 url=base_url(intranet,不含 /order/list)→ 未生效;
    # 工具执行后 url 更新为 /order/list → 之后的 system prompt 才出现
    assert "PAGE_MARK_订单页提示" not in llm.systems[0]
    assert any("PAGE_MARK_订单页提示" in s for s in llm.systems[1:])
