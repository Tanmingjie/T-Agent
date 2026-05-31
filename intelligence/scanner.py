"""Scanner Subagent —— 词汇表扫描(规格 §5.5,T-22)。

登录(Session Profile,由调用方先准备好浏览器到目标页)→ 取 A11y 快照 → LLM 提炼
业务词→UI 元素映射 → 写 DB。扫描策略 A+C:A(首次手动扫 base_url)/ C(执行中增量补充)。

本模块只做「快照 → 词汇表」的提炼与持久化;独立 context(自带 messages),不污染主 Agent。
"""

from __future__ import annotations

import json
import logging
import re

from harness.page_probe import A11yNode, parse_snapshot
from input.models import PageVocabulary
from intelligence.vocabulary import AI, VocabularyManager

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _loads_obj(text: str) -> dict:
    if not text or not text.strip():
        return {}
    s = text.strip()
    for cand in (s, (_FENCE_RE.search(s).group(1).strip() if _FENCE_RE.search(s) else None)):
        if not cand:
            continue
        i, j = cand.find("{"), cand.rfind("}")
        if i != -1 and j > i:
            try:
                obj = json.loads(cand[i : j + 1])
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, ValueError):
                continue
    return {}


def _path_of(url: str) -> str:
    p = re.sub(r"^https?://[^/]+", "", url or "")
    p = p.split("?", 1)[0].split("#", 1)[0]
    return p or "/"


def _nodes_digest(nodes: list[A11yNode], limit: int = 80) -> str:
    lines = []
    for n in nodes[:limit]:
        parts = [n.role]
        if n.name:
            parts.append(f'"{n.name}"')
        if n.value:
            parts.append(f"= {n.value}")
        lines.append("- " + " ".join(parts))
    return "\n".join(lines)


_SYSTEM = """\
你是页面词汇表提炼器。根据页面的无障碍(A11y)元素清单,提炼「业务词 → UI 元素」映射:
键是用户/测试人员会用的业务术语(可能比页面文案更口语),值是 {role, name, confidence}。
name 取页面上真实的可及名,role 取元素角色。只输出 JSON 对象:
{"业务词": {"role": "button", "name": "页面真实文案", "confidence": 0.0~1.0}}
不要臆造页面上不存在的元素。"""


class Scanner:
    def __init__(self, llm) -> None:
        self.llm = llm

    async def extract(
        self, snapshot_text: str, *, login_role: str = "", url_pattern: str | None = None
    ) -> PageVocabulary:
        """快照 → PageVocabulary(LLM 提炼;失败则空词汇表)。"""
        snap = parse_snapshot(snapshot_text)
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"页面 A11y 元素清单:\n{_nodes_digest(snap.nodes)}"},
        ]
        try:
            resp = await self.llm.chat(messages)
            raw = _loads_obj(resp.content)
        except Exception as e:  # noqa: BLE001
            logger.warning("Scanner 提炼失败:%s", e)
            raw = {}

        vocab: dict = {}
        for term, entry in raw.items():
            if isinstance(entry, dict):
                entry.setdefault("source", AI)  # 标记 AI 来源(手动条目另行优先)
                vocab[str(term)] = entry

        return PageVocabulary(
            url_pattern=url_pattern or _path_of(snap.url),
            page_title=snap.title,
            login_role=login_role,
            vocabulary=vocab,
        )

    async def scan_and_save(
        self,
        snapshot_text: str,
        *,
        login_role: str,
        manager: VocabularyManager,
        url_pattern: str | None = None,
    ) -> PageVocabulary:
        """提炼并并入词汇表(增量补充,手动条目优先)。"""
        vocab = await self.extract(snapshot_text, login_role=login_role, url_pattern=url_pattern)
        return await manager.merge_scanned(vocab)
