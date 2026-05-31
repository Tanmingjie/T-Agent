"""断言机制 ★核心(规格 §5.3,T-08)。

核心思想:把「判断」从运行时移到翻译时。LLM 只在生成 TestSpec 时一次性把预期结果
翻译成结构化 Assertion(可审查);**执行时由本规则引擎确定性验证,绝不靠 LLM 眼判**。

阶段一实现三类(按可靠性):
- ``element_visible`` / ``element_count`` —— DOM 元素断言(最可靠)
- ``text_equals`` / ``text_contains`` —— **限定具体元素内**匹配(非全页搜)
- ``url_contains`` / ``url_equals`` —— URL/导航断言

``custom_tool``(阶段二)/ ``llm_judge``(兜底)阶段一标记为 skipped,**不静默放过**。

断言失败的两种归因(§5.3):
- 真失败:元素在、值不对 → FAIL(``healable=False``)。
- selector 失效:元素找不到 → 标 ``healable=True``,阶段二由 Healing 重定位断言目标。

页面访问通过 ``PageProbe`` 抽象(高层语义查询),与具体浏览器实现解耦:引擎纯逻辑、
可确定性单测;真实实现(基于 playwright-mcp A11y 快照)在 T-10 接入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from input.models import Assertion

# 阶段一支持的确定性断言类型
_SUPPORTED = {
    "element_visible",
    "element_count",
    "text_equals",
    "text_contains",
    "url_contains",
    "url_equals",
}


@dataclass
class ElementQuery:
    """对某个语义目标的页面查询结果。"""

    found: bool = False  # 是否定位到该元素
    visible: bool = False
    count: int = 0
    text: str | None = None  # 元素内文本(限定该元素,非全页)


@runtime_checkable
class PageProbe(Protocol):
    """页面探针抽象。真实实现基于 playwright-mcp A11y 快照(T-10 接入)。"""

    async def current_url(self) -> str: ...

    async def query(self, target: str, selector: str | None = None) -> ElementQuery: ...


class AssertionStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"  # 阶段一不支持的类型(custom_tool / llm_judge)


@dataclass
class AssertionResult:
    assertion: Assertion
    status: AssertionStatus
    actual: str = ""
    reason: str = ""
    healable: bool = False  # 元素未找到 → 可能 selector 失效,触发自愈
    healed: bool = False  # 经自愈重定位后复验通过
    heal_note: str = ""  # 自愈摘要(重定位到哪个 target / 策略)

    @property
    def passed(self) -> bool:
        return self.status == AssertionStatus.PASS

    def to_dict(self) -> dict:
        """录制进 ActionStep.assertion_results 用。"""
        return {
            "type": self.assertion.type,
            "target": self.assertion.target,
            "expected": self.assertion.expected,
            "status": self.status.value,
            "actual": self.actual,
            "reason": self.reason,
            "healable": self.healable,
            "healed": self.healed,
            "heal_note": self.heal_note,
        }


class AssertionEngine:
    """确定性断言验证引擎。可选接入 Healing Subagent 做断言目标重定位。"""

    def __init__(self, probe: PageProbe, healer=None) -> None:
        self.probe = probe
        self.healer = healer

    async def verify(self, a: Assertion) -> AssertionResult:
        res = await self._verify_once(a)
        # healable 失败 + 有自愈器 + 探针能给原始快照 → 重定位后复验
        if res.healable and self.healer is not None:
            healed = await self._try_heal(a, res)
            if healed is not None:
                return healed
        return res

    async def _verify_once(self, a: Assertion) -> AssertionResult:
        if a.type not in _SUPPORTED:
            return AssertionResult(
                assertion=a,
                status=AssertionStatus.SKIPPED,
                reason=f"阶段一不支持断言类型 {a.type}(custom_tool/llm_judge 留待后续阶段)",
            )
        handler = getattr(self, f"_check_{a.type}")
        return await handler(a)

    async def _try_heal(self, a: Assertion, original: AssertionResult) -> AssertionResult | None:
        """用自愈重定位断言目标,再复验。成功返回新结果;不成功返回 None(保留原失败)。"""
        raw_fn = getattr(self.probe, "raw_snapshot", None)
        if not callable(raw_fn):
            return None
        snapshot_text = raw_fn()
        if not snapshot_text:
            return None
        heal = await self.healer.relocate(
            intent=f"断言 {a.type} 的目标",
            target=a.target,
            snapshot_text=snapshot_text,
            expected=a.expected,
        )
        if not heal.healed or heal.chosen is None:
            original.reason += f";自愈未能重定位({heal.summary})"
            return None
        # 用重定位后的 target 复验
        relocated = a.model_copy(update={"target": heal.chosen.target})
        retried = await self._verify_once(relocated)
        retried.assertion = a  # 仍归属原断言
        retried.healable = original.healable
        retried.healed = True
        retried.heal_note = heal.summary
        if not retried.passed:
            retried.reason = f"自愈重定位后仍未通过({heal.summary});{retried.reason}"
        return retried

    async def verify_all(self, assertions: list[Assertion]) -> list[AssertionResult]:
        return [await self.verify(a) for a in assertions]

    @staticmethod
    def verdict(results: list[AssertionResult]) -> bool:
        """裁决:只要有 FAIL 即 PASS=False;无断言或全为 SKIPPED 时不算可信通过。"""
        if not results:
            return False
        if any(r.status == AssertionStatus.FAIL for r in results):
            return False
        return any(r.status == AssertionStatus.PASS for r in results)

    # ── URL 类(纯字符串,最确定) ───────────────────────────

    async def _check_url_contains(self, a: Assertion) -> AssertionResult:
        url = await self.probe.current_url()
        expected = a.expected or ""
        ok = expected in url
        return AssertionResult(
            assertion=a,
            status=_st(ok),
            actual=url,
            reason="" if ok else f"URL 不含 {expected!r}",
        )

    async def _check_url_equals(self, a: Assertion) -> AssertionResult:
        url = await self.probe.current_url()
        expected = a.expected or ""
        ok = url == expected
        return AssertionResult(
            assertion=a,
            status=_st(ok),
            actual=url,
            reason="" if ok else f"URL 不等于 {expected!r}",
        )

    # ── DOM 类 ───────────────────────────────────────────────

    async def _check_element_visible(self, a: Assertion) -> AssertionResult:
        q = await self.probe.query(a.target, a.selector)
        if not q.found:
            return _not_found(a)
        ok = q.visible
        return AssertionResult(
            assertion=a,
            status=_st(ok),
            actual=f"visible={q.visible}",
            reason="" if ok else "元素存在但不可见",
        )

    async def _check_element_count(self, a: Assertion) -> AssertionResult:
        q = await self.probe.query(a.target, a.selector)
        try:
            expected = int(str(a.expected).strip())
        except (TypeError, ValueError):
            return AssertionResult(
                assertion=a,
                status=AssertionStatus.FAIL,
                actual=str(q.count),
                reason=f"element_count 的 expected 非整数:{a.expected!r}",
            )
        ok = q.count == expected
        return AssertionResult(
            assertion=a,
            status=_st(ok),
            actual=str(q.count),
            reason="" if ok else f"数量 {q.count} != 期望 {expected}",
        )

    # ── 文本类(限定具体元素内) ─────────────────────────────

    async def _check_text_equals(self, a: Assertion) -> AssertionResult:
        q = await self.probe.query(a.target, a.selector)
        if not q.found:
            return _not_found(a)
        actual = (q.text or "").strip()
        expected = (a.expected or "").strip()
        ok = actual == expected
        return AssertionResult(
            assertion=a,
            status=_st(ok),
            actual=actual,
            reason="" if ok else f"元素内文本 {actual!r} != 期望 {expected!r}",
        )

    async def _check_text_contains(self, a: Assertion) -> AssertionResult:
        q = await self.probe.query(a.target, a.selector)
        if not q.found:
            return _not_found(a)
        actual = q.text or ""
        expected = a.expected or ""
        ok = expected in actual
        return AssertionResult(
            assertion=a,
            status=_st(ok),
            actual=actual,
            reason="" if ok else f"元素内文本不含 {expected!r}",
        )


def _st(ok: bool) -> AssertionStatus:
    return AssertionStatus.PASS if ok else AssertionStatus.FAIL


def _not_found(a: Assertion) -> AssertionResult:
    """元素未找到:可能是真失败,也可能 selector 失效 → 标记可自愈。"""
    return AssertionResult(
        assertion=a,
        status=AssertionStatus.FAIL,
        actual="(元素未找到)",
        reason=f"未定位到目标元素「{a.target}」",
        healable=True,
    )
