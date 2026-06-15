"""Skill 体系单测(标准 Skill 渐进披露,2026-06-15 重构)。

验证:preload 正文常驻 / 非 preload 仅列简述 / load 后正文进 prompt / load_skill 工具
路由 + 渐进加载与 Agent 集成。
"""

from __future__ import annotations

from harness.skills import (
    DEFAULT_SKILLS,
    LOAD_SKILL_TOOL,
    Skill,
    SkillManager,
    build_skill_manager,
)

# ── render:preload 常驻正文 / 非 preload 仅列简述 ──────────────


def test_preload_skill_content_always_rendered():
    mgr = SkillManager()
    mgr.register(Skill(name="基线", content="BASE_业务常识", preload=True))
    text = mgr.render()
    assert "BASE_业务常识" in text
    assert "已加载技能" in text


def test_lazy_skill_lists_description_not_content():
    mgr = SkillManager()
    mgr.register(Skill(name="订单规则", description="DESC_订单状态流转", content="BODY_完整规则"))
    text = mgr.render()
    assert "DESC_订单状态流转" in text  # 简述常驻清单
    assert "BODY_完整规则" not in text  # 正文未加载,不进 prompt
    assert "可按需加载的技能" in text


def test_load_expands_content_and_marks_loaded():
    mgr = SkillManager()
    mgr.register(Skill(name="订单规则", description="DESC", content="BODY_完整规则"))
    assert mgr.load("订单规则") == "BODY_完整规则"
    assert "订单规则" in mgr.loaded
    text = mgr.render()
    assert "BODY_完整规则" in text  # 加载后正文进 prompt
    assert "已加载技能" in text


def test_load_unknown_returns_none():
    mgr = SkillManager()
    mgr.register(Skill(name="A", content="a"))
    assert mgr.load("不存在") is None
    assert mgr.loaded == set()


def test_render_empty_when_no_skills():
    assert SkillManager().render() == ""


def test_tool_schema_is_load_skill():
    schema = SkillManager.tool_schema()
    assert schema["function"]["name"] == LOAD_SKILL_TOOL
    assert "name" in schema["function"]["parameters"]["properties"]


# ── build_skill_manager ────────────────────────────────────────


def test_build_injects_default_baseline_preloaded():
    mgr = build_skill_manager()
    text = mgr.render()
    for s in DEFAULT_SKILLS:
        assert s.content in text  # 内置基线 preload → 正文常驻


def test_build_wires_custom_prompt_preloaded():
    mgr = build_skill_manager(custom_prompt="订单状态: 待审批/已审批")
    assert "订单状态: 待审批/已审批" in mgr.render()  # 套件提示 preload


def test_build_can_skip_defaults():
    assert build_skill_manager(include_defaults=False).render() == ""


def test_build_extra_project_skill_is_lazy():
    mgr = build_skill_manager(
        include_defaults=False,
        extra=[Skill(name="项目规则", description="DESC_X", content="BODY_X")],
    )
    text = mgr.render()
    assert "DESC_X" in text  # 项目 skill 默认 preload=False → 仅列简述
    assert "BODY_X" not in text
    assert mgr.load("项目规则") == "BODY_X"
    assert "BODY_X" in mgr.render()


# ── 与 Agent 集成:渐进加载 ──────────────────────────────────


class _RecordingLLM:
    """记录每次收到的 system prompt + tools;按序返回预设响应。"""

    def __init__(self, responses):
        self._r = responses
        self._i = 0
        self.systems: list[str] = []
        self.tool_names: list[str] = []

    async def chat(self, messages, tools=None, **kwargs):
        self.systems.append(messages[0]["content"])
        if tools:
            self.tool_names = [t["function"]["name"] for t in tools]
        idx = min(self._i, len(self._r) - 1)
        self._i += 1
        return self._r[idx]


async def test_agent_preloaded_skill_in_system_prompt():
    from harness.agent import TestCaseAgent
    from tests.test_agent import SNAPSHOT_OK, _case, _FakeMCP, _resp, _spec

    mgr = SkillManager()
    mgr.register(Skill(name="基线", content="PRELOAD_MARK_常识", preload=True))
    llm = _RecordingLLM(
        [
            _resp(content="点", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK), skills=mgr)
    await agent.run(_case(), spec=_spec())
    assert any("PRELOAD_MARK_常识" in s for s in llm.systems)
    assert LOAD_SKILL_TOOL in llm.tool_names  # load_skill 工具已暴露给 LLM


async def test_agent_lazy_skill_loaded_via_tool():
    from harness.agent import TestCaseAgent
    from tests.test_agent import SNAPSHOT_OK, _case, _FakeMCP, _resp, _spec

    mgr = SkillManager()
    mgr.register(Skill(name="订单规则", description="DESC_简述", content="LAZY_MARK_正文"))
    llm = _RecordingLLM(
        [
            # 第 1 轮:LLM 判断相关,调 load_skill 展开正文
            _resp(content="加载", calls=[(LOAD_SKILL_TOOL, {"name": "订单规则"})]),
            _resp(content="点", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK), skills=mgr)
    await agent.run(_case(), spec=_spec())
    # 首轮:只有简述、无正文;load_skill 之后的轮次:正文进 system prompt
    assert "DESC_简述" in llm.systems[0]
    assert "LAZY_MARK_正文" not in llm.systems[0]
    assert any("LAZY_MARK_正文" in s for s in llm.systems[1:])
