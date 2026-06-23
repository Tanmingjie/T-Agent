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
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 相关性匹配用的极简分词:连续 ASCII 字母数字 或 连续 CJK 字段。
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]+")


def _tokens(text: str) -> set[str]:
    """把一段文本切成稳定 token 集合(确定性、无 LLM)。

    - 英文/数字:整段 lowercase 当 token;
    - 中文:整段作为整体 token + 长度≥3 时再加 2 字符 bigram(让「加购物车」能命中
      「购物」)——避免 1 字符过短噪声。
    """
    if not text:
        return set()
    out: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        tok = raw.lower()
        if not tok:
            continue
        out.add(tok)
        # 中文段补 2 字符 bigram(只有>=3 才补,避免「点」这种单字噪声)
        if "一" <= raw[0] <= "鿿" and len(raw) >= 3:
            for i in range(len(raw) - 1):
                out.add(raw[i : i + 2])
    return out


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

    def relevant(self, step_text: str, *, top_k: int = 3, min_score: int = 1) -> list[str]:
        """按 step 文本与 skill name+description 的 token 重叠度,挑相关 skill 名(降序)。

        E3 三层加载里**甲(浮现催加载)**用此方法:卡住时把命中的 skill 名点出来,
        催模型 `load_skill`。**只选未加载的 skill**(preload 已在场、已 load 不再推荐)。
        极简确定性匹配——避免再叫一次 LLM 决策"哪条相关"。
        """
        if not step_text:
            return []
        step_toks = _tokens(step_text)
        if not step_toks:
            return []
        candidates: list[tuple[int, str]] = []
        for s in self._skills:
            if s.preload or s.name in self.loaded:
                continue
            corpus = _tokens(f"{s.name} {s.description or ''}")
            score = len(step_toks & corpus)
            if score >= min_score:
                candidates.append((score, s.name))
        candidates.sort(key=lambda x: (-x[0], x[1]))  # 高分在前;同分按名稳定排序
        return [n for _, n in candidates[:top_k]]

    def auto_load(self, step_text: str, *, min_score: int = 1) -> str | None:
        """E3 三层加载里**乙(自动注入兜底)**:按相关性挑 top1 直接 load,返回名;无命中则 None。

        用在「甲已浮现但模型仍没加载且仍卡住」时,平台直接替它加载,保弱模型也生效。
        """
        names = self.relevant(step_text, top_k=1, min_score=min_score)
        if not names:
            return None
        loaded = self.load(names[0])
        return names[0] if loaded is not None else None

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


# 内置基线常识(始终展开):通用业务测试常识 + 通用机械操作套路,精炼克制不喧宾夺主。
# 工具机制类基础见 agent.PLAYWRIGHT_MCP_HINT(每个 case 都注入);此处补的是「操作/判定的
# 常见诀窍」——E3 把通用机械动作下沉到这里,业务侧 skill 只填业务知识,不再每个项目重写。
DEFAULT_SKILLS: list[Skill] = [
    Skill(
        name="表单操作",
        description="填表与提交的通用套路(必填、校验错误识别)",
        preload=True,
        content="填写表单先把所有必填项填完再提交;提交后留意页面是否出现校验错误提示,有则说明未真正提交成功。",
    ),
    Skill(
        name="结果定位",
        description="操作生效与否的常见信号(状态文字/列表/toast)",
        preload=True,
        content="业务操作的结果通常体现为:状态文字变化、列表新增一行、或出现成功/失败提示(toast/alert);"
        "优先依据这些确定性信号判断,而非仅凭页面跳转。",
    ),
    Skill(
        name="重新快照拿新 ref",
        description="跳转/弹窗/异步加载后旧 ref 失效,需要重新 browser_snapshot",
        preload=True,
        content="ref 是 browser_snapshot 那一刻分配的临时引用,页面发生跳转、出现弹窗、"
        "或异步内容加载后,旧 ref 会失效或指向错位元素。任何让页面发生变化的动作之后,"
        "若要继续操作,应重新调用 browser_snapshot 获取最新 ref,而不是复用上一份快照的 ref。",
    ),
    Skill(
        name="找不到元素的常见原因",
        description="目标在快照里看不见时的几种典型情形与诊断方向",
        preload=True,
        content="若 a11y 快照里找不到目标元素,不要盲点。常见原因:"
        '(a) 在视野外——尝试 browser_press_key(key="PageDown") 滚动后再 browser_snapshot;'
        "(b) 页面仍在加载——稍等再 browser_snapshot,或显式 browser_wait_for 等待文本出现;"
        "(c) 名字不同——同义词/英文/图标按钮(如「加购物车」实际叫 'Add to cart' 或纯图标);"
        "(d) 还在错的页面——先做前置(进入正确模块/打开弹窗)再找。",
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
