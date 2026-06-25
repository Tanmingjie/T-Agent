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


def truncate_snapshot(text: str, keywords: list[str], *, max_lines: int = 40) -> str:
    """按关键词相关度截断一段 A11y 快照文本(L2)。

    保留:Page 头部元信息(URL/Title)+ 命中任一关键词的节点行;再补充少量其它行
    凑足结构感;总行数不超过 max_lines。无快照结构时按行数硬截断。
    """
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text

    kws = [k for k in (keywords or []) if k]
    head: list[str] = []
    matched: list[str] = []
    others: list[str] = []
    for ln in lines:
        low = ln.lower()
        if "page url" in low or "page title" in low or ln.strip().startswith("###"):
            head.append(ln)
        elif kws and any(k.lower() in low for k in kws):
            matched.append(ln)
        else:
            others.append(ln)

    kept = head + matched
    budget = max_lines - len(kept)
    if budget > 0:
        kept += others[:budget]
    omitted = len(lines) - len(kept)
    if omitted > 0:
        kept.append(f"... [已按相关度截断 {omitted} 行]")
    return "\n".join(kept)


class ContextCompactor:
    """消息历史压缩器。"""

    def __init__(
        self,
        *,
        keep_recent_observations: int = 2,
        max_obs_chars: int = 2000,
        snapshot_max_lines: int = 40,
        protect_head: int = 2,
        keep_recent_assistant: int = 3,
    ) -> None:
        # 保留最近 N 条观察的(相对)完整内容,更早的折叠为一行
        self.keep_recent_observations = keep_recent_observations
        self.max_obs_chars = max_obs_chars
        self.snapshot_max_lines = snapshot_max_lines
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
