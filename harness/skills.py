"""Skill 体系(规格 §5.4 Skill 体系,T-16)。

三类 Skill,组装进 System Prompt:

- **DomainSkill**:Suite 级业务术语/知识,执行前**始终注入**。
- **PageSkill**:按当前 URL **动态加载/卸载**(URL 命中才注入;离开页面即卸载,配合
  Context Compact 的 L3)。url_pattern 支持 ``/order/{id}`` 路由占位与普通子串。
- **ToolSkill**:按当前步骤关键词**相关度过滤**注入(触发词命中才注入)。

``SkillManager.select(url, keywords)`` 返回当前生效的 Skill(顺序 Domain→Page→Tool),
并跟踪 PageSkill 的加载/卸载;``render(...)`` 输出可拼接进 Prompt 的文本片段。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DomainSkill:
    name: str
    content: str
    kind: str = "domain"


@dataclass
class PageSkill:
    name: str
    content: str
    url_pattern: str
    kind: str = "page"

    def matches(self, url: str) -> bool:
        if not url:
            return False
        # 路由占位 {x} → [^/]+,其余部分按字面转义;作为子串在 url 中搜索
        parts = re.split(r"\{[^}]+\}", self.url_pattern)
        regex = "[^/]+".join(re.escape(p) for p in parts)
        try:
            return re.search(regex, url) is not None
        except re.error:
            return self.url_pattern in url


@dataclass
class ToolSkill:
    name: str
    content: str
    triggers: list[str]
    kind: str = "tool"

    def relevant(self, keywords: list[str]) -> bool:
        if not keywords:
            return False
        hay = " ".join(keywords)
        return any(t and t in hay for t in self.triggers)


Skill = "DomainSkill | PageSkill | ToolSkill"


class SkillManager:
    """注册并按 URL/关键词动态选择 Skill。"""

    def __init__(self) -> None:
        self._domain: list[DomainSkill] = []
        self._page: list[PageSkill] = []
        self._tool: list[ToolSkill] = []
        self.loaded_pages: set[str] = set()

    def register(self, skill) -> None:
        if isinstance(skill, DomainSkill):
            self._domain.append(skill)
        elif isinstance(skill, PageSkill):
            self._page.append(skill)
        elif isinstance(skill, ToolSkill):
            self._tool.append(skill)
        else:
            raise TypeError(f"未知 Skill 类型:{type(skill).__name__}")

    def select(self, *, url: str = "", keywords: list[str] | None = None) -> list:
        """返回当前生效的 Skill(Domain→Page→Tool),并更新 PageSkill 加载状态。"""
        keywords = keywords or []
        active_pages = [s for s in self._page if s.matches(url)]
        self._update_loaded(active_pages)
        active_tools = [s for s in self._tool if s.relevant(keywords)]
        return [*self._domain, *active_pages, *active_tools]

    def _update_loaded(self, active_pages: list[PageSkill]) -> None:
        new_loaded = {s.name for s in active_pages}
        for name in self.loaded_pages - new_loaded:
            logger.info("卸载 PageSkill:%s", name)
        for name in new_loaded - self.loaded_pages:
            logger.info("加载 PageSkill:%s", name)
        self.loaded_pages = new_loaded

    def render(self, *, url: str = "", keywords: list[str] | None = None) -> str:
        """把生效 Skill 拼成 Prompt 片段;无生效则返回空串。"""
        skills = self.select(url=url, keywords=keywords)
        if not skills:
            return ""
        lines = ["## 技能(业务/页面/工具)"]
        for s in skills:
            lines.append(f"- [{s.name}] {s.content}")
        return "\n".join(lines)


# 内置基础 DomainSkill(始终注入):通用业务测试常识,精炼克制不喧宾夺主。
# 工具机制类提示见 agent.PLAYWRIGHT_MCP_HINT,这里只放「业务语义/判定」层面的常识。
DEFAULT_DOMAIN_SKILLS: list[DomainSkill] = [
    DomainSkill(
        name="表单操作",
        content="填写表单先把所有必填项填完再提交;提交后留意页面是否出现校验错误提示,有则说明未真正提交成功。",
    ),
    DomainSkill(
        name="结果定位",
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
    """组装一个 SkillManager(把基础 DomainSkill 与 Suite 自定义提示词接进执行链)。

    - ``custom_prompt``:Suite 维护的业务提示词(此前是孤儿字段,从不被使用)→ 作为
      始终注入的 DomainSkill 接通。
    - ``include_defaults``:是否注入内置基础 DomainSkill(``DEFAULT_DOMAIN_SKILLS``)。
    - ``extra``:额外 Skill(DomainSkill/PageSkill/ToolSkill)列表。
    """
    mgr = SkillManager()
    if include_defaults:
        for s in DEFAULT_DOMAIN_SKILLS:
            mgr.register(s)
    if custom_prompt and custom_prompt.strip():
        mgr.register(DomainSkill(name="套件提示", content=custom_prompt.strip()))
    for s in extra or []:
        mgr.register(s)
    return mgr
