"""Self-Healing —— Healing Subagent(规格 §5.4 Self-Healing,T-11)。

独立 context(不污染主 Agent),在「元素/断言目标定位不到」时重定位:

触发(由调用方判断后调入):
- 操作元素未找到 / 操作无效 / 连续重复 / 超时
- 断言 selector 失效(AssertionResult.healable=True)

输入:失败信息 + 当前 A11y 快照(文本)+ 操作意图 intent + (可选)词汇表。
策略优先级:P1 语义角色(role+name) → P2 文本 → P3 属性 → P4 位置关系 → P5 视觉。
产出:最多 3 个候选(含置信度),按 P1→P5 + 置信度排序;校验候选确实落在当前快照里,
选出可用项。成功只回主 context 一行摘要;失败到上限 → 交由调用方判 FAIL。

关键原则(§5.4):ActionStep 存「操作意图」而非只存选择器;词汇表第一优先查询
(本阶段词汇表为空,留扩展点)。本模块只做「重定位决策」,不直接驱动浏览器/断言,
由调用方拿候选去复验,保持职责单一、可单测。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from harness.llm import LLMClient, loads_lenient
from harness.page_probe import A11yNode, parse_snapshot

logger = logging.getLogger(__name__)

# 策略优先级(数值越小越优先)
_STRATEGY_ORDER = {
    "P1_role": 1,
    "P2_text": 2,
    "P3_attr": 3,
    "P4_position": 4,
    "P5_visual": 5,
}


@dataclass
class HealCandidate:
    """一个重定位候选。"""

    target: str  # 重定位后的目标(优先填快照里真实存在的可及名/文本)
    strategy: str = "P1_role"  # P1_role | P2_text | P3_attr | P4_position | P5_visual
    confidence: float = 0.0
    selector: str | None = None
    reason: str = ""

    @property
    def priority(self) -> int:
        return _STRATEGY_ORDER.get(self.strategy, 9)


@dataclass
class HealResult:
    healed: bool = False
    chosen: HealCandidate | None = None
    candidates: list[HealCandidate] = field(default_factory=list)
    summary: str = ""  # 回主 context 的一行摘要
    attempts: int = 0


_SYSTEM = """\
你是 UI 元素重定位专家。某个测试动作或断言要找的目标元素没定位到,需要你根据当前页面的
无障碍(A11y)快照,判断业务语义目标实际对应页面上的哪个元素。

策略优先级(尽量用靠前的):
- P1_role:语义角色 + 可及名(最可靠)
- P2_text:可见文本
- P3_attr:属性(如 aria-label / placeholder)
- P4_position:位置关系(如"第一个商品的按钮")
- P5_visual:视觉判断(仅在前面都不行时)

只输出 JSON,给出至多 3 个候选,按你认为的可能性从高到低排序:
{"candidates":[
  {"target":"页面上真实存在的可及名或文本","strategy":"P1_role","confidence":0.0~1.0,"reason":"为什么"}
]}
target 必须尽量取自下面快照里真实出现的元素名/文本,不要臆造。"""


def _nodes_digest(nodes: list[A11yNode], limit: int = 60) -> str:
    """把快照节点压成给 LLM 看的清单(role / name / value)。"""
    lines = []
    for n in nodes[:limit]:
        parts = [n.role]
        if n.name:
            parts.append(f'"{n.name}"')
        if n.value:
            parts.append(f"= {n.value}")
        lines.append("- " + " ".join(parts))
    return "\n".join(lines)


class HealingSubagent:
    """重定位子代理。每次 relocate 用独立 messages,不污染主 Agent。"""

    def __init__(self, llm: LLMClient, *, max_attempts: int = 3) -> None:
        self.llm = llm
        self.max_attempts = max_attempts

    async def relocate(
        self,
        *,
        intent: str,
        target: str,
        snapshot_text: str,
        expected: str | None = None,
        vocabulary: dict | None = None,
    ) -> HealResult:
        """重定位语义 target。返回校验过(确实落在快照里)的候选结果。"""
        snap = parse_snapshot(snapshot_text)
        node_names = {n.name for n in snap.nodes if n.name} | {
            n.text_content for n in snap.nodes if n.text_content
        }

        # 词汇表优先(本阶段通常为空,留扩展点)
        if vocabulary and target in vocabulary:
            cand = HealCandidate(
                target=str(vocabulary[target]),
                strategy="P1_role",
                confidence=0.95,
                reason="命中词汇表",
            )
            return HealResult(
                healed=True,
                chosen=cand,
                candidates=[cand],
                summary=f"自愈:'{target}' 由词汇表命中 → '{cand.target}'",
                attempts=0,
            )

        # 独立 context
        user = (
            f"业务语义目标:{target}\n"
            f"操作意图:{intent or '(未提供)'}\n"
            f"期望值/上下文:{expected if expected is not None else '(无)'}\n\n"
            f"当前页面 A11y 元素清单:\n{_nodes_digest(snap.nodes)}"
        )
        messages = [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]

        attempts = 0
        try:
            attempts = 1
            resp = await self.llm.chat(messages)
            candidates = self._parse_candidates(resp.content)
        except Exception as e:  # noqa: BLE001 — 自愈失败不应炸主流程
            logger.warning("自愈 relocate 调用失败:%s", e)
            return HealResult(healed=False, summary=f"自愈失败:{e}", attempts=attempts)

        # 只保留 target 确实出现在快照里的候选(防臆造),按优先级+置信度排序
        valid = [c for c in candidates if _resolves(c.target, node_names)]
        valid.sort(key=lambda c: (c.priority, -c.confidence))

        if not valid:
            return HealResult(
                healed=False,
                candidates=candidates,
                attempts=attempts,
                summary=f"自愈未找到可靠候选(原目标 '{target}')",
            )
        chosen = valid[0]
        return HealResult(
            healed=True,
            chosen=chosen,
            candidates=valid,
            attempts=attempts,
            summary=f"自愈:'{target}' → '{chosen.target}'({chosen.strategy},conf={chosen.confidence:.2f})",
        )

    def _parse_candidates(self, content: str) -> list[HealCandidate]:
        try:
            data = loads_lenient(content)
        except ValueError:
            return []
        out: list[HealCandidate] = []
        for raw in data.get("candidates", []):
            if not isinstance(raw, dict):
                continue
            tgt = str(raw.get("target") or "").strip()
            if not tgt:
                continue
            strat = str(raw.get("strategy") or "P1_role").strip()
            if strat not in _STRATEGY_ORDER:
                strat = "P1_role"
            try:
                conf = float(raw.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            out.append(
                HealCandidate(
                    target=tgt, strategy=strat, confidence=conf, reason=str(raw.get("reason") or "")
                )
            )
        return out


def _resolves(target: str, node_names: set[str]) -> bool:
    """候选 target 是否能对上快照里某个元素名/文本(双向包含)。"""
    t = target.strip()
    if not t:
        return False
    for name in node_names:
        if t == name or t in name or name in t:
            return True
    return False
