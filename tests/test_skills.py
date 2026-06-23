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


# ── E3:相关性匹配 + 三层加载 ────────────────────────────────


def test_default_skills_include_mechanical_baseline():
    """E3 基线机械 skill 下沉:重新快照、找不到元素诊断 等通用套路进 DEFAULT_SKILLS。"""
    names = {s.name for s in DEFAULT_SKILLS}
    assert "重新快照拿新 ref" in names
    assert "找不到元素的常见原因" in names
    # 仍是 preload=True(短、通用,不值得让模型为它多花一次工具调用)
    for s in DEFAULT_SKILLS:
        assert s.preload is True
        # 渐进披露需要 description(供 LLM 在未加载时判断相关性)
        assert s.description, f"{s.name} 缺 description"


def test_relevant_matches_by_token_overlap():
    """SkillManager.relevant:按 step 文本与 skill name+description 的 token 重叠挑相关 skill。"""
    mgr = SkillManager()
    mgr.register(
        Skill(
            name="加购物车套路",
            description="如何把商品加入购物车,本系统按钮叫 Add to cart",
            content="...",
        )
    )
    mgr.register(Skill(name="审批流程", description="发起审批、流转、回退", content="..."))
    mgr.register(Skill(name="登录流程", description="用户名密码登录与异常处理", content="..."))
    # 当前步骤跟"购物车"强相关 → top1 应是购物车 skill
    rel = mgr.relevant("点击第一个商品的加入购物车按钮", top_k=2)
    assert rel[0] == "加购物车套路"
    # 与"登录"相关
    rel2 = mgr.relevant("输入用户名 standard_user")
    assert "登录流程" in rel2


def test_relevant_skips_preloaded_and_already_loaded():
    """相关性只挑「未加载」的 skill(preload/已 load 不再推荐)。"""
    mgr = SkillManager()
    mgr.register(Skill(name="加购物车", description="购物车", content="...", preload=True))
    mgr.register(Skill(name="审批", description="审批", content="..."))
    # 购物车 skill 是 preload → 不应被推荐
    assert "加购物车" not in mgr.relevant("加购物车")
    # 已 load 的也不推荐
    mgr.load("审批")
    assert mgr.relevant("审批") == []


def test_relevant_empty_when_no_overlap():
    mgr = SkillManager()
    mgr.register(Skill(name="审批", description="发起审批", content="..."))
    assert mgr.relevant("登录") == []  # 完全不相关


def test_auto_load_top_match():
    """auto_load:挑相关性 top1 直接 load,返回名(乙层兜底)。"""
    mgr = SkillManager()
    mgr.register(Skill(name="加购物车", description="购物车操作", content="BODY"))
    name = mgr.auto_load("点击加入购物车")
    assert name == "加购物车"
    assert "加购物车" in mgr.loaded  # 实际已加载
    # 第二次再调:已 load,不在 relevant 候选 → 返回 None
    assert mgr.auto_load("点击加入购物车") is None


def test_auto_load_no_match_returns_none():
    mgr = SkillManager()
    mgr.register(Skill(name="审批", description="发起", content="..."))
    assert mgr.auto_load("登录") is None


# ── E3 + ReActLoop:卡住时浮现/兜底加载 ─────────────────────────


async def test_react_stuck_surfaces_relevant_skill_name():
    """E3 甲:卡住时把命中的 skill 名点出来催加载(prompt 提到 load_skill(name="..."))。"""
    import json

    from harness.llm import LLMClient, LLMResponse, ToolCall
    from harness.react_loop import ReActLoop, ToolOutcome
    from harness.step_plan import StepPlan
    from input.models import Phase

    plan = StepPlan([Phase(steps=["点击加入购物车按钮"])])
    mgr = SkillManager()
    mgr.register(Skill(name="加购物车套路", description="本系统的加购按钮叫购物车", content="..."))

    fixed = "Page URL: http://x/p\n- button [ref=e1]"
    captured: list[list[dict]] = []

    async def execute(name, arguments):
        handled = plan.apply_tool_call(name, arguments)
        if handled is not None:
            return ToolOutcome(text=handled)
        return ToolOutcome(text=fixed)

    class _LLM(LLMClient):
        def __init__(self, responses):
            self._r = responses
            self._i = 0

        async def chat(self, messages, tools=None, **kwargs):
            captured.append([dict(m) for m in messages])
            idx = min(self._i, len(self._r) - 1)
            self._i += 1
            return self._r[idx]

    llm = _LLM(
        [
            LLMResponse(content="", tool_calls=[ToolCall(name="browser_snapshot", arguments={})]),
            LLMResponse(
                content="", tool_calls=[ToolCall(name="browser_hover", arguments={"ref": "e1"})]
            ),
            LLMResponse(
                content="", tool_calls=[ToolCall(name="browser_hover", arguments={"ref": "e2"})]
            ),
            LLMResponse(
                content="", tool_calls=[ToolCall(name="browser_click", arguments={"ref": "e1"})]
            ),
            LLMResponse(
                content="", tool_calls=[ToolCall(name="mark_step_done", arguments={"step_no": 1})]
            ),
        ]
    )
    loop = ReActLoop(
        llm,
        tools=[],
        execute=execute,
        step_plan=plan,
        build_system=lambda p: p.to_prompt(),
        stuck_round_budget=2,
        loop_window=5,
        skill_manager=mgr,
        max_steps=10,
    )
    await loop.run()
    # r4 时 messages 应已含「相关 skill」提示 + skill 名
    msg_texts = [m.get("content", "") for m in captured[3] if isinstance(m.get("content"), str)]
    joined = "\n".join(msg_texts)
    assert "[卡住提醒]" in joined
    assert "加购物车套路" in joined
    assert "load_skill" in joined


async def test_react_stuck_auto_loads_when_still_stuck():
    """E3 乙:甲层已发但仍多轮卡住 → 平台 auto_load top1 兜底注入。"""
    from harness.llm import LLMClient, LLMResponse, ToolCall
    from harness.react_loop import ReActLoop, ToolOutcome
    from harness.step_plan import StepPlan
    from input.models import Phase

    plan = StepPlan([Phase(steps=["点击加入购物车按钮"])])
    mgr = SkillManager()
    mgr.register(Skill(name="加购物车套路", description="加购按钮的常见位置", content="BODY_X"))

    fixed = "Page URL: http://x/p\n- button [ref=e1]"
    captured: list[list[dict]] = []

    async def execute(name, arguments):
        handled = plan.apply_tool_call(name, arguments)
        if handled is not None:
            return ToolOutcome(text=handled)
        return ToolOutcome(text=fixed)

    class _LLM(LLMClient):
        def __init__(self, responses):
            self._r = responses
            self._i = 0

        async def chat(self, messages, tools=None, **kwargs):
            captured.append([dict(m) for m in messages])
            idx = min(self._i, len(self._r) - 1)
            self._i += 1
            return self._r[idx]

    # 持续卡住:r1 snapshot,r2..r5 都 hover 不同 ref(签名各异避开循环检测)
    # budget=2,甲层在 stuck=2 触发(r3 末);乙层在 stuck=4 触发(r5 末)
    llm = _LLM(
        [
            LLMResponse(content="", tool_calls=[ToolCall(name="browser_snapshot", arguments={})]),
            LLMResponse(
                content="", tool_calls=[ToolCall(name="browser_hover", arguments={"ref": "e1"})]
            ),
            LLMResponse(
                content="", tool_calls=[ToolCall(name="browser_hover", arguments={"ref": "e2"})]
            ),  # stuck=2
            LLMResponse(
                content="", tool_calls=[ToolCall(name="browser_hover", arguments={"ref": "e3"})]
            ),
            LLMResponse(
                content="", tool_calls=[ToolCall(name="browser_hover", arguments={"ref": "e4"})]
            ),  # stuck=4
            LLMResponse(
                content="", tool_calls=[ToolCall(name="browser_click", arguments={"ref": "e1"})]
            ),
            LLMResponse(
                content="", tool_calls=[ToolCall(name="mark_step_done", arguments={"step_no": 1})]
            ),
        ]
    )
    loop = ReActLoop(
        llm,
        tools=[],
        execute=execute,
        step_plan=plan,
        build_system=lambda p: p.to_prompt(),
        stuck_round_budget=2,
        loop_window=10,
        skill_manager=mgr,
        max_steps=12,
    )
    await loop.run()
    # 验证 skill 已被 auto_load(乙层真正生效)
    assert "加购物车套路" in mgr.loaded
    # messages 里应出现「平台自动加载技能」提示
    all_msgs = []
    for snap in captured:
        all_msgs.extend(m.get("content", "") for m in snap if isinstance(m.get("content"), str))
    joined = "\n".join(all_msgs)
    assert "[平台自动加载技能]" in joined
