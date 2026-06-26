"""Context Compact(规格 §5.4 Context Compact,T-12)。

ReAct 文本式循环里,每步都把完整 A11y 快照作为「[观察]」回灌,上下文会快速膨胀
(实测一条用例可冲到几十万 token)。本模块在每轮发给 LLM 前压缩消息历史:

- **L1**:较旧的「[观察]」(对应已完成步骤)折叠成一行归档摘要,移出活跃上下文。
- **L2**:保留的近期「[观察]」若是大段 A11y 快照,按**当前步骤关键词相关度**截断
  (借鉴 browser-use:留 Page 头部 + 命中关键词的节点行 + 数量上限)。
- **L3**:Skill 注入由 Skill 系统(标准 Skill 渐进披露)处理,这里只管消息历史。

原则(§5.4):
- 自愈过程中不压缩(需完整失败上下文);本模块只压「[观察]」,不动 system/task/assistant。
- 系统消息(messages[0])与首条任务消息(messages[1])永远保留。

就地压缩(compact_inplace):messages 仅用于喂 LLM,ActionStep 已另行完整录制,
因此可安全地把旧观察改写短,真正省 token。
"""

from __future__ import annotations

import os
import re

OBS_PREFIX = "[观察]"
ARCHIVED_PREFIX = "[观察·已归档]"
THINK_ARCHIVED_PREFIX = "[思考·已归档]"


def _is_observation(msg: dict) -> bool:
    return (
        msg.get("role") == "user"
        and isinstance(msg.get("content"), str)
        and (msg["content"].startswith(OBS_PREFIX) or msg["content"].startswith(ARCHIVED_PREFIX))
    )


def _is_assistant(msg: dict) -> bool:
    return msg.get("role") == "assistant" and isinstance(msg.get("content"), str)


def _first_line(text: str, limit: int = 100) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line[:limit]


# a11y 节点行的「角色」= 短横线后的第一个 token(如 "- button [ref=e1]: 提交" → button)。
_ROLE_RE = re.compile(r"^\s*-\s*([A-Za-z][\w-]*)")
# 可交互/可操作角色:这些行带 ref 时是 agent 真正要点/填的目标,截断时**最高优先保留**
# (治内网血泪:步骤是中文、元素 a11y 名是英文/图标 → 关键词命不中 → 旧逻辑只留开头噪声行、
# 把后面的目标按钮整段丢掉 → 模型没 ref → 转 JS 又穿不透 shadow → 卡死)。
_INTERACTIVE_ROLES = {
    "button",
    "link",
    "textbox",
    "searchbox",
    "combobox",
    "listbox",
    "checkbox",
    "radio",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "tab",
    "option",
    "switch",
    "slider",
    "spinbutton",
}


def _line_role(line: str) -> str:
    m = _ROLE_RE.match(line)
    return m.group(1).lower() if m else ""


def _is_interactive(line: str) -> bool:
    """带 ref 的可交互元素行:标准可交互角色,或 web component(角色名含连字符,如 sl-button)。"""
    if "[ref=" not in line:
        return False
    role = _line_role(line)
    return role in _INTERACTIVE_ROLES or "-" in role


def truncate_snapshot(text: str, keywords: list[str], *, max_lines: int = 40) -> str:
    """按相关度截断一段 A11y 快照文本(L2),**保留优先级**:

    1. Page 头部元信息(URL/Title);
    2. 命中当前步骤关键词的行;
    3. **可交互/可操作元素行**(button/textbox/自定义组件… 带 ref)——即便关键词没命中也要留,
       否则跨语言/图标场景下目标元素的 ref 会被丢掉,模型拿不到 ref 就找不到元素;
    4. 其余行按文档顺序补足结构,直到 max_lines。

    总行数不超过 max_lines。≤max_lines 原样返回。
    """
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text

    kws = [k.lower() for k in (keywords or []) if k]
    keep = [False] * len(lines)

    def _kept_count() -> int:
        return sum(keep)

    # 优先级 1:头部元信息
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "page url" in low or "page title" in low or ln.strip().startswith("###"):
            keep[i] = True
    # 优先级 2:命中关键词
    if kws:
        for i, ln in enumerate(lines):
            if not keep[i] and any(k in ln.lower() for k in kws):
                keep[i] = True
    # 优先级 3:可交互元素行(关键词没命中也保;治"目标按钮被丢")
    for i, ln in enumerate(lines):
        if _kept_count() >= max_lines:
            break
        if not keep[i] and _is_interactive(ln):
            keep[i] = True
    # 优先级 4:其余行按顺序补足结构感
    for i in range(len(lines)):
        if _kept_count() >= max_lines:
            break
        if not keep[i]:
            keep[i] = True

    out: list[str] = []
    omitted = 0
    for i, ln in enumerate(lines):
        if keep[i]:
            out.append(ln)
        else:
            omitted += 1
    if omitted > 0:
        out.append(f"... [已按相关度截断 {omitted} 行]")
    return "\n".join(out)


# 驱动侧快照截断旋钮的默认值(env 可调,作内网长页面/藏元素的安全阀)。
#   OBS_MAX_CHARS:近观察超过此字符数才触发 L2 截断(默认 2000)。
#   SNAPSHOT_MAX_LINES:L2 截断后保留的最大行数(默认 40)。
# 内网页面元素特别多、目标常被截掉时调大;但越大每轮 token 越多。
_DEFAULT_MAX_OBS_CHARS = int(os.getenv("OBS_MAX_CHARS", "2000"))
_DEFAULT_SNAPSHOT_MAX_LINES = int(os.getenv("SNAPSHOT_MAX_LINES", "40"))


class ContextCompactor:
    """消息历史压缩器。"""

    def __init__(
        self,
        *,
        keep_recent_observations: int = 2,
        max_obs_chars: int | None = None,
        snapshot_max_lines: int | None = None,
        protect_head: int = 2,
        keep_recent_assistant: int = 3,
    ) -> None:
        # 保留最近 N 条观察的(相对)完整内容,更早的折叠为一行
        self.keep_recent_observations = keep_recent_observations
        # None → 取 env 默认(显式传值优先,供测试/调用方覆盖)
        self.max_obs_chars = _DEFAULT_MAX_OBS_CHARS if max_obs_chars is None else max_obs_chars
        self.snapshot_max_lines = (
            _DEFAULT_SNAPSHOT_MAX_LINES if snapshot_max_lines is None else snapshot_max_lines
        )
        self.protect_head = protect_head  # 永远保护的前缀消息数(system + task)
        # 保留最近 N 条 assistant 叙述的完整内容,更早的折叠为一行(B:治叙述通道无限累积——
        # narration churn 时每轮都 append 一大段思考且从不压缩,是 token 爆炸的主因之一)。
        self.keep_recent_assistant = keep_recent_assistant

    def compact_inplace(self, messages: list[dict], keywords: list[str] | None = None) -> int:
        """就地压缩 messages 里的观察 + 旧 assistant 叙述。返回大致省下的字符数。

        - 早于「最近 keep_recent_observations 条」的观察 → 折叠为一行归档(L1)。
        - 最近保留的观察 → 若过长按关键词截断(L2)。
        - 早于「最近 keep_recent_assistant 条」的 assistant 叙述 → 折叠为一行归档(B)。
        """
        keywords = keywords or []
        saved = 0
        obs_idx = [
            i for i, m in enumerate(messages) if i >= self.protect_head and _is_observation(m)
        ]
        recent = (
            set(obs_idx[-self.keep_recent_observations :])
            if self.keep_recent_observations
            else set()
        )
        for i in obs_idx:
            content = messages[i]["content"]
            if i in recent:
                # L2:保留但截断过长快照
                if len(content) > self.max_obs_chars:
                    body = (
                        content[len(OBS_PREFIX) :].lstrip()
                        if content.startswith(OBS_PREFIX)
                        else content
                    )
                    new_body = truncate_snapshot(body, keywords, max_lines=self.snapshot_max_lines)
                    new = f"{OBS_PREFIX} {new_body}"
                    saved += len(content) - len(new)
                    messages[i]["content"] = new
            else:
                # L1:折叠为一行归档
                if not content.startswith(ARCHIVED_PREFIX):
                    summary = _first_line(
                        content[len(OBS_PREFIX) :] if content.startswith(OBS_PREFIX) else content
                    )
                    new = f"{ARCHIVED_PREFIX} {summary}"
                    saved += max(0, len(content) - len(new))
                    messages[i]["content"] = new

        # B:折叠旧 assistant 叙述(保护头之后、最近 keep_recent_assistant 条之外的)。
        asst_idx = [
            i for i, m in enumerate(messages) if i >= self.protect_head and _is_assistant(m)
        ]
        keep_asst = (
            set(asst_idx[-self.keep_recent_assistant :]) if self.keep_recent_assistant else set()
        )
        for i in asst_idx:
            if i in keep_asst:
                continue
            content = messages[i]["content"]
            if content.startswith(THINK_ARCHIVED_PREFIX):
                continue
            summary = _first_line(content)
            new = f"{THINK_ARCHIVED_PREFIX} {summary}"
            if len(new) < len(content):
                saved += len(content) - len(new)
                messages[i]["content"] = new
        return saved
