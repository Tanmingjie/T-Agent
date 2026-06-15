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


def _origin_of(url: str) -> str:
    """取 URL 的根地址(scheme://host[:port])作 base_url 作用域键。"""
    m = re.match(r"^https?://[^/]+", url or "")
    return m.group(0) if m else ""


def url_scope(url: str) -> tuple[str, str]:
    """URL → (base_url 作用域键, url_pattern 路由键)。公开给执行后增量补充复用。"""
    return _origin_of(url), _path_of(url)


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

# 执行后增量补充(策略C):基于**用例执行时真实操作过的元素**(ground truth)总结挑词。
# 与全量提炼(_SYSTEM)不同:这里输入是已跑通的「业务描述 + 真实 role/name/selector」,
# 只需挑出值得补充的、规范化业务词,不必从原始快照重新认元素。
_SUPPLEMENT_SYSTEM = """\
你是测试词汇表的增量补充员。下面是一条用例**执行时实际操作过**的元素清单,每条含:
业务描述(测试人员用语)、真实角色 role、真实可及名 name、实际定位器 selector。
另给出该页面词汇表**已有**的业务词。请挑出**值得补充为新业务词**的项:
- 过滤掉与已有业务词重复或同义的;
- 过滤掉「继续 / 确定 / 取消 / 返回」等无业务含义的通用控件;
- 为每个补充项规范化一个简洁的业务词作为键。
只输出 JSON 对象:{"业务词": {"role": .., "name": .., "selector": .., "confidence": 0.0~1.0}}。
name / selector 必须来自给定清单,不要臆造。没有值得补充的就输出 {}。"""


class Scanner:
    def __init__(self, llm) -> None:
        self.llm = llm

    async def extract(
        self,
        snapshot_text: str,
        *,
        login_role: str = "",
        url_pattern: str | None = None,
        base_url: str | None = None,
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
            base_url=base_url if base_url is not None else _origin_of(snap.url),
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
        base_url: str | None = None,
    ) -> PageVocabulary:
        """提炼并并入词汇表(增量补充,手动条目优先)。"""
        vocab = await self.extract(
            snapshot_text, login_role=login_role, url_pattern=url_pattern, base_url=base_url
        )
        return await manager.merge_scanned(vocab)

    async def summarize_supplements(
        self, candidates: list[dict], *, existing_terms: list[str] | None = None
    ) -> dict:
        """执行后增量补充(策略C):从执行轨迹的「业务词→真实元素」候选里,让 LLM 总结挑出
        值得补充的业务词。返回 delta 词汇表 ``{业务词: {role,name,selector,source,confidence}}``。

        ``candidates``:每项 ``{term, role, name, selector}``(term=业务描述,后三者为执行
        时跑通的真实元素证据)。``existing_terms``:该页已有业务词,供 LLM 去重。无候选 → {}。
        """
        if not candidates:
            return {}
        lines = []
        for c in candidates:
            parts = [f"业务描述={ (c.get('term') or '').strip()!r}"]
            if c.get("role"):
                parts.append(f"role={c['role']}")
            if c.get("name"):
                parts.append(f"name={str(c['name']).strip()!r}")
            if c.get("selector"):
                parts.append(f"selector={str(c['selector']).strip()}")
            lines.append("- " + " ".join(parts))
        existing = "、".join(t for t in (existing_terms or []) if t) or "(无)"
        messages = [
            {"role": "system", "content": _SUPPLEMENT_SYSTEM},
            {
                "role": "user",
                "content": (f"已有业务词:{existing}\n\n本次执行操作过的元素:\n" + "\n".join(lines)),
            },
        ]
        try:
            resp = await self.llm.chat(messages)
            raw = _loads_obj(resp.content)
        except Exception as e:  # noqa: BLE001 — 总结失败不影响用例结果
            logger.warning("执行后词汇总结失败:%s", e)
            return {}
        delta: dict = {}
        for term, entry in raw.items():
            # name/selector 至少有一个(确保有可落地的元素证据),标 AI 来源
            if isinstance(entry, dict) and (entry.get("name") or entry.get("selector")):
                entry.setdefault("source", AI)
                delta[str(term)] = entry
        return delta
