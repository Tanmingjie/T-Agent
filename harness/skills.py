"""Skill 体系(对齐 Anthropic/Claude Code 标准 Skill,2026-06-15 重构)。

每条 Skill = ``name`` + ``description`` + ``content``(正文)。**渐进披露**:
System Prompt 常驻一份便宜的「可按需加载技能」清单(``name — description``);LLM 判断
与当前任务相关时,**主动调用 ``load_skill(name)`` 工具**把正文拉进上下文(之后轮次保留)。
未加载的 skill 正文**永不进 prompt**,省 context——加载与否完全由 **LLM 决策**。

内置基线常识(``DEFAULT_SKILLS``)标 ``preload=True``:正文始终在场(短、通用,不值得
让模型为它多花一次工具调用)。用户/项目 skill 默认 ``preload=False``,走渐进加载。

〔此前按 Domain/Page/Tool 三类、用 URL/关键词做平台侧匹配注入;2026-06-15 改为标准 Skill
渐进披露后**不再区分类型**,删 PageSkill/ToolSkill 的 url_pattern/triggers 匹配逻辑。〕
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# load_skill 控制工具名(LLM 渐进加载入口;执行器据此路由,permission/截图据此跳过)
LOAD_SKILL_TOOL = "load_skill"


@dataclass
class Skill:
    """标准 Skill:name + description(常驻清单) + content(按需展开的正文)。"""

    name: str
    content: str  # 正文:加载后注入 prompt 的完整业务知识
    description: str = ""  # 简述:常驻「可按需加载」清单,供 LLM 判断是否加载
    preload: bool = False  # True=正文始终在场(内置基线);False=按需 load_skill 展开


class SkillManager:
    """注册 Skill + 渐进披露:常驻 name/description 清单,LLM 调 ``load_skill`` 展开正文。"""

    def __init__(self) -> None:
        self._skills: list[Skill] = []
        self.loaded: set[str] = set()  # 本次执行已展开的 skill 名(正文进 prompt)

    def register(self, skill: Skill) -> None:
        self._skills.append(skill)

    def names(self) -> list[str]:
        return [s.name for s in self._skills]

    def load(self, name: str) -> str | None:
        """LLM 调 ``load_skill`` 时展开某 skill 正文。命中返回正文并标记已加载;否则 None。"""
        key = (name or "").strip()
        for s in self._skills:
            if s.name == key:
                if s.name not in self.loaded:
                    logger.info("加载 Skill:%s", s.name)
                self.loaded.add(s.name)
                return s.content
        return None

    def render(self) -> str:
        """拼成 Prompt 片段:已展开(preload / 已 load)技能正文 + 可按需加载清单。

        每轮由 ``build_system`` 重算并放进 System Prompt——已加载正文常驻于此(不走观察、
        不被 Context Compact 折叠),清单则始终便宜地列出未加载技能供 LLM 选择展开。
        """
        if not self._skills:
            return ""
        shown = [s for s in self._skills if s.preload or s.name in self.loaded]
        pending = [s for s in self._skills if not s.preload and s.name not in self.loaded]
        lines: list[str] = []
        if shown:
            lines.append("## 已加载技能(业务知识)")
            for s in shown:
                lines.append(f"- [{s.name}] {s.content}")
        if pending:
            lines.append("## 可按需加载的技能")
            lines.append(
                "下列技能只给出简述。判断与当前步骤相关时,调用 "
                'load_skill(name="技能名") 展开其完整内容,再据此操作:'
            )
            for s in pending:
                lines.append(f"- {s.name}:{s.description or '(无简述)'}")
        return "\n".join(lines)

    @staticmethod
    def tool_schema() -> dict:
        """``load_skill`` 的 LiteLLM tool 定义(渐进加载入口)。"""
        return {
            "type": "function",
            "function": {
                "name": LOAD_SKILL_TOOL,
                "description": (
                    "展开一条「可按需加载的技能」的完整内容并据此操作。"
                    "当某技能简述与当前步骤相关、需要其业务知识时调用;参数 name 取自清单。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "要加载的技能名(取自「可按需加载的技能」清单)",
                        }
                    },
                    "required": ["name"],
                },
            },
        }


# 内置基线常识(始终展开):通用业务测试常识,精炼克制不喧宾夺主。
# 工具机制类提示见 agent.PLAYWRIGHT_MCP_HINT,这里只放「业务语义/判定」层面的常识。
DEFAULT_SKILLS: list[Skill] = [
    Skill(
        name="表单操作",
        preload=True,
        content="填写表单先把所有必填项填完再提交;提交后留意页面是否出现校验错误提示,有则说明未真正提交成功。",
    ),
    Skill(
        name="结果定位",
        preload=True,
        content="业务操作的结果通常体现为:状态文字变化、列表新增一行、或出现成功/失败提示(toast/alert);"
        "优先依据这些确定性信号判断,而非仅凭页面跳转。",
    ),
]


def build_skill_manager(
    custom_prompt: str = "",
    *,
    include_defaults: bool = True,
    extra: "list | None" = None,
) -> SkillManager:
    """组装 SkillManager(基础常识 + Suite 提示词 + 项目 Skill 接进执行链)。

    - ``custom_prompt``:Suite 维护的业务提示词 → 作为始终展开的 Skill(``preload=True``)。
    - ``include_defaults``:是否注入内置基线常识(``DEFAULT_SKILLS``)。
    - ``extra``:额外 ``Skill`` 列表(项目级 Skill;默认 ``preload=False`` 走渐进加载)。
    """
    mgr = SkillManager()
    if include_defaults:
        for s in DEFAULT_SKILLS:
            mgr.register(s)
    if custom_prompt and custom_prompt.strip():
        mgr.register(Skill(name="套件提示", content=custom_prompt.strip(), preload=True))
    for s in extra or []:
        mgr.register(s)
    return mgr
