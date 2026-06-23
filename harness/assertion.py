"""断言机制 ★核心(规格 §5.3,T-08)。

核心思想:把「判断」从运行时移到翻译时。LLM 只在生成 TestSpec 时一次性把预期结果
翻译成结构化 Assertion(可审查);**执行时由本规则引擎确定性验证,绝不靠 LLM 眼判**。

阶段一实现三类(按可靠性):
- ``element_visible`` / ``element_count`` —— DOM 元素断言(最可靠)
- ``text_equals`` / ``text_contains`` —— **限定具体元素内**匹配(非全页搜)
- ``url_contains`` / ``url_equals`` —— URL/导航断言

``custom_tool`` —— 数据断言:接入 ``ToolRegistry`` 后经 Custom Tool 取业务真值并确定性
比较(未接入则 skipped)。``llm_judge`` —— **默认主裁决**(2026-06-17 Fix 3,据真实公网评测从
"最末档兜底"升格):接入 LLM 后偏-FAIL 判 PASS/FAIL 并计入裁决,**判 PASS 必须逐字引证页面
实证、平台确定性核验该证据确在当前页**(脑补证据 → fail-closed 推翻为 FAIL);结果打
``ai_judged`` 标记(低置信、可审计),报告区分「结构化绿」与「AI 判绿」使 false green 可见可
回溯;**能用 URL/数据真值确定性验的预期仍优先用 URL/custom_tool**(高置信锚点)。未接入 LLM
则 skipped。skipped **不静默放过**(裁决时全 skipped 不算可信通过)。

断言失败的两种归因(§5.3):
- 真失败:元素在、值不对 → FAIL(``healable=False``)。
- selector 失效:元素找不到 → 标 ``healable=True``,阶段二由 Healing 重定位断言目标。

页面访问通过 ``PageProbe`` 抽象(高层语义查询),与具体浏览器实现解耦:引擎纯逻辑、
可确定性单测;真实实现(基于 playwright-mcp A11y 快照)在 T-10 接入。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from harness.llm import extract_verdict, loads_lenient
from input.models import Assertion

logger = logging.getLogger(__name__)

# llm_judge 裁判的 system prompt。偏向 FAIL + **强制逐字引证证据**(2026-06-17 Fix 3):据真实
# 公网评测,偏-FAIL、引证页面证据的 LLM 裁判 false-green=0/15(≈96% 正确),已是默认主裁决
# (非"降级兜底")。**判 PASS 必须在 evidence 字段里逐字摘录页面/URL 实证**——平台据此**确定性
# 核验该证据是否真的出现在当前页面**(见 `_check_llm_judge`:不在 → fail-closed 推翻为 FAIL),
# 治弱模型"脑补证据"刷绿(尤其把中间页/别的页面的预期在终态页上判过)。
_JUDGE_SYSTEM = """你是测试断言裁判。根据下面提供的【当前页面 URL】+【页面无障碍(A11y)快照】,\
判断给定「期望」是否在当前页面**真实成立**。
要求:
- 严格按提供的事实判断,**绝不臆测、绝不凭记忆**;只有当快照/URL 里能找到支持该期望的【具体证据】时才判 PASS。
- 判 PASS 时**必须在 "evidence" 字段里逐字复制**你所依据的页面证据(从快照或 URL 里**原样摘录**
  一小段文本,如某行文案 / 标题 / URL 片段);**拿不出能逐字复制的证据,或证据不足/拿不准,一律判 FAIL**
  (测试平台宁可误报失败,不可误报通过)。平台会确定性核验该证据确在当前页面,脑补的证据会被推翻。
- 若期望描述的是**更早步骤/别的页面**才有的状态,而**当前页面**快照里找不到对应证据,判 FAIL——
  不要因为"流程里它应该发生过"就判通过。
只输出 JSON:{"verdict":"PASS"|"FAIL","evidence":"从当前页面逐字摘录的证据(PASS 必填)","reason":"结论说明"}"""

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
    SKIPPED = "skipped"  # 无法确定性验证(custom_tool 未接 / llm_judge 未接 LLM)


@dataclass
class AssertionResult:
    assertion: Assertion
    status: AssertionStatus
    actual: str = ""
    reason: str = ""
    healable: bool = False  # 元素未找到 → 可能 selector 失效,触发自愈
    healed: bool = False  # 经自愈重定位后复验通过
    heal_note: str = ""  # 自愈摘要(重定位到哪个 target / 策略)
    ai_judged: bool = False  # 该结果由 llm_judge 兜底判定(低置信),报告需与结构化绿区分

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
            "ai_judged": self.ai_judged,
        }


class AssertionEngine:
    """确定性断言验证引擎。可选接入 Healing Subagent 做断言目标重定位。"""

    def __init__(self, probe: PageProbe, healer=None, *, tool_registry=None, llm=None) -> None:
        self.probe = probe
        self.healer = healer
        # 数据断言(custom_tool)经 ToolRegistry 取业务真值(规格 §5.3#4/§5.4)。
        # 未接入时 custom_tool 断言保持 skipped(不静默放过)。
        self.tool_registry = tool_registry
        # llm_judge 兜底裁判用(方案A)。未接入则 llm_judge 断言保持 skipped。
        self.llm = llm

    async def verify(self, a: Assertion) -> AssertionResult:
        res = await self._verify_once(a)
        # healable 失败 + 有自愈器 + 探针能给原始快照 → 重定位后复验
        if res.healable and self.healer is not None:
            healed = await self._try_heal(a, res)
            if healed is not None:
                return healed
            res.healed = False  # 自愈未救回,res 仍是 _not_found 失败,下面试全页兜底
        # 文本断言:元素未定位、自愈也没救回 → **全页文本兜底**(确定性,带护栏)。
        # 放在自愈**之后**:优先元素级/自愈的精确绿,兜底只在它们都失败时托底。
        if not res.passed and res.healable and a.type in ("text_equals", "text_contains"):
            fb = self._text_page_fallback(a)
            if fb is not None:
                return fb
        return res

    async def _verify_once(self, a: Assertion) -> AssertionResult:
        if a.type in _SUPPORTED:
            handler = getattr(self, f"_check_{a.type}")
            return await handler(a)
        if a.type == "custom_tool":
            return await self._check_custom_tool(a)
        if a.type == "llm_judge":
            return await self._check_llm_judge(a)
        # 其余未知类型:标 skipped 而非静默放过;裁决时全 skipped 不算可信通过。
        return AssertionResult(
            assertion=a,
            status=AssertionStatus.SKIPPED,
            reason=f"断言类型 {a.type} 不支持确定性验证 → skipped(需人工复核)",
        )

    async def _check_llm_judge(self, a: Assertion) -> AssertionResult:
        """LLM 语义断言(Fix 3:默认主裁决,偏-FAIL + 逐字证据确定性核验)。

        偏-FAIL 判 PASS/FAIL 并计入裁决;**判 PASS 必须逐字引证页面实证**,本方法再**确定性核验**
        该 evidence 真出现在当前页(快照/URL,空白归一后子串匹配),不在 → fail-closed 推翻为
        FAIL(治弱模型"脑补证据"刷绿,尤其把中间页/别页预期在终态页判过)。结果打 ``ai_judged``
        标记,报告区分「结构化绿」与「AI 判绿」使 false green 可见可回溯;能用 URL/数据真值确定性
        验的预期仍应优先用 URL/custom_tool。未接入 LLM → skipped(不静默放过)。
        """
        if self.llm is None:
            return AssertionResult(
                assertion=a,
                status=AssertionStatus.SKIPPED,
                reason="llm_judge 未接入 LLM → skipped(需人工复核)",
            )
        snapshot_text = ""
        raw_fn = getattr(self.probe, "raw_snapshot", None)
        if callable(raw_fn):
            snapshot_text = raw_fn() or ""
        # 免费 URL 锚点(Fix 3):实时 URL 总能廉价观测到,显式喂给裁判作 grounding 证据,
        # 不再只依赖快照里可能缺失的 URL 行。取不到则留空,不阻断裁决。
        cur_url = ""
        url_fn = getattr(self.probe, "current_url", None)
        if callable(url_fn):
            try:
                cur_url = (await url_fn()) or ""
            except Exception as e:  # noqa: BLE001 — 取 URL 失败不阻断裁决
                logger.warning("llm_judge 取当前 URL 失败:%s", e)
        expectation = (a.expected or a.target or "").strip()
        messages = [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"期望:{expectation}\n\n"
                    f"当前页面 URL:{cur_url or '(未知)'}\n\n"
                    f"当前页面无障碍快照:\n{snapshot_text[:6000] or '(无快照)'}"
                ),
            },
        ]
        try:
            resp = await self.llm.chat(messages)
        except Exception as e:  # noqa: BLE001 — 调用失败 → skipped(fail-closed,不默认绿)
            logger.warning("llm_judge 调用失败:%s", e)
            return AssertionResult(
                assertion=a,
                status=AssertionStatus.SKIPPED,
                ai_judged=True,
                reason=f"llm_judge 调用失败 → skipped:{e}",
            )
        content = resp.content or ""
        verdict, reason, evidence = "", "", ""
        try:
            data = loads_lenient(content)
            verdict = str(data.get("verdict") or "").strip().upper()
            reason = str(data.get("reason") or "").strip()
            evidence = str(data.get("evidence") or "").strip()
        except Exception as e:  # noqa: BLE001 — JSON 不规整 → 下面正则兜底捞 verdict
            logger.warning("llm_judge JSON 解析失败,尝试正则兜底:%s", e)
        if verdict not in ("PASS", "FAIL"):
            # 解析没拿到明确裁决 → 从原文稳健捞 PASS/FAIL(治模型 reason 含未转义引号炸 JSON,
            # 2026-06-17)。仍捞不到才 skipped(fail-closed:裁决路径绝不因解析失败默认绿)。
            recovered = extract_verdict(content)
            if recovered:
                verdict = recovered
                reason = reason or "(从非规整输出中提取 verdict)"
        if verdict not in ("PASS", "FAIL"):
            return AssertionResult(
                assertion=a,
                status=AssertionStatus.SKIPPED,
                ai_judged=True,
                actual=content[:200],
                reason=f"llm_judge 未给出明确裁决 → skipped:{reason or '(无)'}",
            )
        ok = verdict == "PASS"
        # —— 证据接地核验(Fix 3 收尾,治"脑补证据"刷绿)——
        # 判 PASS 时,裁判引证的 evidence 必须**有据**:其中至少一个"实证锚点"(引号内片段 / 较长
        # 英文/数字串 / 较长中文串)逐字出现在当前页面(快照 / URL,空白归一)。全不在 = 脑补 →
        # fail-closed 推翻为 FAIL(贴铁律2「宁可误报失败」)。
        # 〔2026-06-18 据 eval_fg 实测改"整串子串匹配"为"锚点接地":整串匹配对**复合预期**
        #   (如"导航含 A、B、C")误伤 18%——模型把证据写成概括句、非单一逐字串;改为"任一锚点命中"
        #   后误伤→0、false-green 仍 0。仍能拦住整段脑补(无任何锚点落在页上,如"用户名框=standard_user"
        #   在无该字段的页)。〕仅当确有可核验来源时启用(无快照单测跳过,不误伤)。
        verified = ""
        if ok:
            haystack = _norm_evidence(f"{snapshot_text}\n{cur_url}")
            if haystack:  # 有可核验的页面文本
                if _evidence_grounded(evidence, haystack):
                    verified = evidence
                else:
                    ok = False
                    verdict = "FAIL"
                    reason = (
                        f"判 PASS 但引证证据无一落在当前页(evidence={evidence!r})"
                        f"→ 疑似脑补,fail-closed 推翻为 FAIL;原说明:{reason or '(无)'}"
                    )
                # E5 expected 自带锚点佐证:judge 引证有据后,再核验 expected 自身的强锚点
                # (引号片段 / 实词 / 长中文短语)是否至少有一个落在页面/URL。一个都没有 →
                # judge 与 expected 矛盾(判过了但 expected 的实质内容根本不在页上)→ 推翻。
                # 仅当 expected 抽得到强锚点时启用,空 expected / 无强锚点不参与判断。
                if ok and not _expected_grounded(expectation, haystack):
                    ok = False
                    verdict = "FAIL"
                    exp_anchors = _expected_anchors(expectation)
                    reason = (
                        f"判 PASS 但期望中的关键锚点 {exp_anchors} 一个都未落在当前页 → "
                        f"与 expected 矛盾,fail-closed 推翻为 FAIL"
                    )
        return AssertionResult(
            assertion=a,
            status=_st(ok),
            actual=f"AI判定={verdict}",
            reason=(
                f"AI 判定通过(低置信,建议复核);实证:{verified}" if ok else f"AI 判定失败:{reason}"
            ),
            ai_judged=True,
        )

    async def _check_custom_tool(self, a: Assertion) -> AssertionResult:
        """数据断言(规格 §5.3#4):经 Custom Tool 取业务真值并确定性比较。

        约定:``target`` = 已注册的工具名;``selector`` = 调用参数(JSON 对象,可空);
        ``expected`` = 期望子串(给定则要求结果包含它;为空则结果非空且非错误即视为通过)。
        未接 ToolRegistry 或工具名未注册 → skipped(不静默放过)。
        """
        if self.tool_registry is None or not self.tool_registry.has(a.target):
            return AssertionResult(
                assertion=a,
                status=AssertionStatus.SKIPPED,
                reason=f"custom_tool 断言未接入工具「{a.target}」→ skipped",
            )
        args: dict = {}
        if a.selector:
            try:
                parsed = json.loads(a.selector)
                if isinstance(parsed, dict):
                    args = parsed
            except (json.JSONDecodeError, ValueError):
                logger.warning("custom_tool 断言 selector 非合法 JSON,按无参调用:%r", a.selector)
        result = await self.tool_registry.call(a.target, args)
        # ToolRegistry.call 把工具内部失败/超时/非零退出转成 "[工具 ...]" 文本
        if result.startswith("[工具 "):
            return AssertionResult(
                assertion=a,
                status=AssertionStatus.FAIL,
                actual=result,
                reason="custom_tool 执行失败",
            )
        expected = (a.expected or "").strip()
        ok = (expected in result) if expected else bool(result.strip())
        return AssertionResult(
            assertion=a,
            status=_st(ok),
            actual=result,
            reason="" if ok else f"工具结果未满足期望 {expected!r}",
        )

    async def _try_heal(self, a: Assertion, original: AssertionResult) -> AssertionResult | None:
        """用自愈重定位断言目标,再复验。成功返回新结果;不成功返回 None(保留原失败)。"""
        raw_fn = getattr(self.probe, "raw_snapshot", None)
        if not callable(raw_fn):
            return None
        snapshot_text = raw_fn()
        if not snapshot_text:
            return None
        # 词汇表第一优先(§5.4/§5.5):若探针能解析出真实元素名,作为高置信候选直接喂给
        # 自愈,命中即用,免于 LLM 臆造。探针无解析器时 vocabulary 为 None,行为不变。
        vocabulary: dict | None = None
        resolve_entry = getattr(self.probe, "resolve_entry", None)
        if callable(resolve_entry):
            entry = await resolve_entry(a.target)
            if entry and entry.get("name"):
                vocabulary = {a.target: str(entry["name"]).strip()}
        # 视觉双通道(规格 §5.4 P5):探针能截图就一并喂给自愈,治"元素在 a11y 里但
        # 可及名缺失/与业务词不一致"的误判。取不到截图则纯文本通道,行为不变。
        screenshot: str | None = None
        shot_fn = getattr(self.probe, "raw_screenshot", None)
        if callable(shot_fn):
            screenshot = await shot_fn()
        heal = await self.healer.relocate(
            intent=f"断言 {a.type} 的目标",
            target=a.target,
            snapshot_text=snapshot_text,
            expected=a.expected,
            vocabulary=vocabulary,
            screenshot=screenshot,
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
        """裁决:只要有 FAIL 即 PASS=False;无断言或全为 SKIPPED 时不算可信通过。

        方案A 后 PASS 可来自结构化断言**或** llm_judge 兜底(后者标 ``ai_judged``);
        SKIPPED 收窄为「custom_tool 未接 / llm_judge 未接 LLM / 未知类型」。裁决本身仍确定性:
        任一 FAIL 即不通过,需至少一条 PASS 才算可信通过。
        """
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
        # 结尾斜杠容差:`https://x` 与 `https://x/`(根路径)语义等价(RFC),浏览器常自动补 `/`。
        # 不容差会让"打开首页"类步骤的 url_equals 因一个尾斜杠 false-fail(AE03 实测)。仅归一
        # 结尾斜杠,不碰路径/查询,保持 url_equals 的"精确"语义(比 url_contains 仍严格)。
        ok = url.rstrip("/") == expected.rstrip("/")
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

    def _text_page_fallback(self, a: Assertion) -> AssertionResult | None:
        """文本断言元素未定位时的**全页文本兜底**(确定性,带护栏)。命中返回 PASS,否则 None。

        断言 ``target`` 常是模糊区域名(如「成功提示区域」),与真实元素的可及名对不上
        (尤其跨语言:中文业务词 vs 英文页面文案)→ 元素级匹配 false-fail;而 ``expected``
        (成功提示原文)往往明确出现在页面上。于是在整页快照文本里**确定性**搜 ``expected``
        子串,命中即判 PASS,reason 标注「全页文本兜底」使其与元素级绿可区分、可审计。

        **护栏**(贴合铁律「宁可误报失败不可误报通过」,防短串误绿):
        - 显式 ``selector`` 的断言**不兜底**——selector 失败是真信号(如空购物车 ``.badge``
          求值不到),不能因页面别处恰有该串就刷绿;
        - ``expected`` 必须**够独特**(含空白的短语,或长度 ≥ 5):排除 "1"/"2"/状态短词
          这类在整页文本里随处可见的弱串;
        - 探针无 ``raw_snapshot`` 或页面文本不含 expected → 返回 None(维持原 _not_found 失败)。
        """
        if a.selector:  # 显式 selector 失败是真信号,不做全页兜底
            return None
        expected = (a.expected or "").strip()
        distinctive = len(expected) >= 5 or (" " in expected and len(expected) >= 3)
        if not expected or not distinctive:
            return None
        raw_fn = getattr(self.probe, "raw_snapshot", None)
        page_text = raw_fn() if callable(raw_fn) else ""
        if page_text and expected in page_text:
            return AssertionResult(
                assertion=a,
                status=AssertionStatus.PASS,
                actual=expected,
                reason=f"元素「{a.target}」未定位,全页文本兜底命中 {expected!r}",
            )
        return None


def _norm_evidence(text: str) -> str:
    """归一化用于「裁判证据确定性核验」的文本:折叠所有空白为单空格 + casefold。

    快照(YAML)里换行/缩进多变,逐字引证常有空白差异;折叠空白后做子串匹配更稳。
    casefold 让大小写不敏感(英文页面文案常见),对中文无副作用。
    """
    return " ".join((text or "").split()).casefold()


# 从裁判 evidence 里抽"实证锚点":引号内片段 / 较长英文数字串 / 较长中文串。用于"接地核验"——
# 只要有一个锚点逐字出现在当前页就认为有据,治复合证据(概括句)被整串匹配误伤(eval_fg 实测)。
_QUOTED_RE = re.compile(r"""["'“”「」『』]([^"'“”「」『』]{2,}?)["'“”「」『』]""")
_ASCII_RUN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._/\-]{3,}")
_CJK_RUN_RE = re.compile(r"[一-鿿]{3,}")


def _evidence_anchors(evidence: str) -> list[str]:
    """抽取并归一化 evidence 里的实证锚点(去重,norm 后长度≥3 才算"够独特")。

    长度阈值 3:英文/数字锚点经 `_ASCII_RUN_RE` 已强制≥4(避开 the/page 等泛词),中文锚点
    经 `_CJK_RUN_RE` 取≥3(3 字中文短语如"待审批""购物车"已足够特异)。统一 ≥3 过滤即可。
    """
    raw: list[str] = []
    raw += _QUOTED_RE.findall(evidence or "")
    raw += _ASCII_RUN_RE.findall(evidence or "")
    raw += _CJK_RUN_RE.findall(evidence or "")
    seen: set[str] = set()
    out: list[str] = []
    for a in raw:
        n = _norm_evidence(a)
        if len(n) >= 3 and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _evidence_grounded(evidence: str, haystack_norm: str) -> bool:
    """证据是否"有据":至少一个实证锚点逐字出现在(已归一的)当前页文本里。

    比"整串子串匹配"宽:模型把复合证据写成概括句(非单一逐字串)时,只要其中一个具体锚点
    (引号片段 / 页面英文文案 / URL 片段 / 中文短语)命中页面即算有据,大幅降误伤;但整段脑补
    (无任何锚点落在页上,如"用户名框=standard_user"在无该字段的页)仍判无据 → fail-closed。
    """
    anchors = _evidence_anchors(evidence)
    if not anchors:
        return False
    return any(a in haystack_norm for a in anchors)


# E5 确定性锚点佐证(预期自带锚点核验):**只取强信号**——引号片段(作者显式引的字面值)
# 和 URL-like 片段(.html/.htm/.aspx/.php 后缀或带 / 的路径)。一般中文短语/英文词不取
# (常被 expected 与页面文案/同义表达不一致而误伤,不是 E5 该处理的边界)。
# 判 PASS 通过后再核验:expected 抽出的强锚点**至少有一个**落在当前页/URL → 算自洽;
# 一个都没有 → judge 与 expected 矛盾,fail-closed 推翻 FAIL。佐证非主裁决,守底线
# 「宁可误报失败」。Eval_fg 验过:此严格版对真实公网用例(automationexercise 含 inventory.html
# 等明确锚点)有效;对纯中文 expected(无引号、无 URL)不参与判断(不误伤)。
_EXP_QUOTED_RE = _QUOTED_RE
_EXP_URLISH_RE = re.compile(
    r"""(?:
        [A-Za-z0-9_/\-]*\.(?:html?|aspx?|php|jsp|cgi|action)\b   # 显式 web 后缀
        |
        /[A-Za-z0-9_\-]{2,}(?:/[A-Za-z0-9_\-]{2,})*              # 路径段(至少一段≥2)
    )""",
    re.VERBOSE,
)


def _expected_anchors(expected: str) -> list[str]:
    """E5:从 expected 抽**强锚点**——只取引号字面值 + URL-like 片段。

    刻意保守:不取一般 CJK/ASCII 词(常被 expected 文风与页面文案表达差异误伤,例如
    expected 写「显示订单成功」而页面只显示英文版「Order completed」)。引号是作者**显式**
    要求的字面值,URL 是天然原文。两者都强相关、不容易被同义化。
    """
    raw: list[str] = []
    raw += _EXP_QUOTED_RE.findall(expected or "")
    raw += _EXP_URLISH_RE.findall(expected or "")
    seen: set[str] = set()
    out: list[str] = []
    for a in raw:
        n = _norm_evidence(a)
        if len(n) >= 3 and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _expected_grounded(expected: str, haystack_norm: str) -> bool:
    """E5 锚点佐证:expected 强锚点至少一个落在 haystack。无强锚点 → True(不参与判断)。

    返回 False 仅当 expected 抽得到强锚点 且 全都不在 haystack → 视为矛盾(由调用方推翻 PASS)。
    """
    anchors = _expected_anchors(expected)
    if not anchors:
        return True  # expected 没有可核验的强锚点 → 不参与判断
    return any(a in haystack_norm for a in anchors)


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
