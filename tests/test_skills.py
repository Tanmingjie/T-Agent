"""Skill 体系单测(标准 Skill 渐进披露,2026-06-15 重构)。

验证:preload 正文常驻 / 非 preload 仅列简述 / load 后正文进 prompt / load_skill 工具。
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
    assert "load_skill" in text  # 引导随清单一起出现(BASE 不再常驻死指令)


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


# ── E3:相关性匹配 + 三层加载 ────────────────────────────────


def test_default_skills_include_mechanical_baseline():
    """通用执行建议仍作为默认 Skill 提供,但不绑定旧浏览器工具。"""
    names = {s.name for s in DEFAULT_SKILLS}
    assert "页面变化后重新观察" in names
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
