"""断言机制 ★核心(规格 §5.3,T-08)。

核心思想:把「判断」从运行时移到翻译时。LLM 只在生成 TestSpec 时一次性把预期结果
翻译成结构化 Assertion(可审查);**执行时由本规则引擎确定性验证,绝不靠 LLM 眼判**。

阶段一实现三类(按可靠性):
- ``element_visible`` / ``element_count`` —— DOM 元素断言(最可靠)
- ``text_equals`` / ``text_contains`` —— **限定具体元素内**匹配(非全页搜)
- ``url_contains`` / ``url_equals`` —— URL/导航断言

``custom_tool`` —— 数据断言:接入 ``ToolRegistry`` 后经 Custom Tool 取业务真值并确定性
比较(未接入则 skipped)。``llm_judge`` —— **默认主裁决**(2026-06-17 Fix 3,据真实公网评测从
"最末档兜底"升格):接入 LLM 后偏-FAIL 判 PASS/FAIL 并计入裁决,**判 PASS 须逐字引证页面实证**
(evidence 字段,作可审计依据)。〔2026-06-24 撤销「平台确定性证据接地推翻」:eval_fg A/B 扩样
实测(n=63,3 站点,6 轮)接地层有益拦截恒为 0、仅偶发误伤 → 净 ≤0,偏-FAIL prompt 自身已扛
住全部 false-green;裁决权交回模型,evidence 仅作依据不再作推翻闸门。〕结果打
``ai_judged`` 标记(低置信、可审计),报告区分「结构化绿」与「AI 判绿」使 false green 可见可
回溯;**能用 URL/数据真值确定性验的预期仍优先用 URL/custom_tool**(高置信锚点)。**llm_judge
的主裁决缺失三态(未接 LLM / 调用失败 / 解析不出 verdict)一律 FAIL**(2026-06-23 G1:阶段化下
LLM 是主裁决,缺失不默认绿,自动化平台无人工复核环节——SKIPPED 在 llm_judge 路径退役;
custom_tool/未知类型未接入时仍 skipped,属非阶段化路径)。

断言失败的两种归因(§5.3):
- 真失败:元素在、值不对 → FAIL(``healable=False``)。
- selector 失效:元素找不到 → 标 ``healable=True``,阶段二由 Healing 重定位断言目标。

页面访问通过 ``PageProbe`` 抽象(高层语义查询),与具体浏览器实现解耦:引擎纯逻辑、
可确定性单测;真实实现(基于 playwright-mcp A11y 快照)在 T-10 接入。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from harness.llm import extract_verdict, loads_lenient
from input.models import Assertion

# E6 多模态裁判通道开关:默认**关**(JUDGE_VISUAL=0)。开启后,_check_llm_judge 抓截图
# 作第二通道喂给裁判(配合 a11y 快照 + URL),治 a11y 树看不全的角标/图标/canvas 类预期
# 容易被 judge 看走眼的情况。需多模态模型支持;一次失败后该 run 内不再尝试(沿用 healing
# 的 _vision_unsupported 设计哲学)。本地弱模型多模态质量不稳,故默认关,有多模态模型再开。
_JUDGE_VISUAL_DEFAULT = os.getenv("JUDGE_VISUAL", "0") not in ("0", "false", "False")

logger = logging.getLogger(__name__)

# llm_judge 裁判的 system prompt。偏向 FAIL + **强制逐字引证证据**(2026-06-17 Fix 3):据真实
# 公网评测,偏-FAIL、引证页面证据的 LLM 裁判 false-green=0/15(≈96% 正确),已是默认主裁决
# (非"降级兜底")。**判 PASS 必须在 evidence 字段里逐字摘录页面/URL 实证**——作可审计依据写入
# reason。〔2026-06-24 撤销平台对该证据的确定性接地推翻:eval_fg A/B 扩样实测接地层净 ≤0、偏-FAIL
# 自身已扛住全部 false-green;裁决权交回模型,evidence 不再作推翻闸门。〕偏-FAIL 的引证纪律仍保留
# (逼模型严格、reason 可审计)。
_JUDGE_SYSTEM = """你是测试断言裁判。根据下面提供的【当前页面 URL】+【页面无障碍(A11y)快照】,\
判断给定「期望」是否在当前页面**真实成立**。
要求:
- 严格按提供的事实判断,**绝不臆测、绝不凭记忆**;只有当快照/URL 里能找到支持该期望的【具体证据】时才判 PASS。
- 判 PASS 时**必须在 "evidence" 字段里逐字复制**你所依据的页面证据(从快照或 URL 里**原样摘录**
  一小段文本,如某行文案 / 标题 / URL 片段);**拿不出能逐字复制的证据,或证据不足/拿不准,一律判 FAIL**
  (测试平台宁可误报失败,不可误报通过)。
- 若期望描述的是**更早步骤/别的页面**才有的状态,而**当前页面**快照里找不到对应证据,判 FAIL——
  不要因为"流程里它应该发生过"就判通过。
- 【URL 跳转是确定性锚点】若给出了【本阶段开始时 URL】且与【当前页面 URL】**不同**,说明页面
  确实发生了跳转——这是确定性事实,不是臆测。当期望属于**导航/登录达成类**(如「登录成功」
  「进入主页/某模块」「跳转到某页面」)时:URL 已从起始页(**尤其登录页**)跳走、且当前页**不再
  是登录表单页**,即可作为该期望达成的**有效证据判 PASS**——**无需**纠结目标路径是否「典型」、
  也**无需**在快照里逐字找到导航菜单/欢迎文案(不同系统落地页路由与文案各异,你不认识属正常)。
  但若期望是关于**具体内容/数值**(某条文案、某个数字、某状态值),仍须在快照里找到对应证据,
  不能只凭 URL 跳转就判通过。
- **输出务必简短**:evidence 只摘**一小段**关键证据(如一行文案/标题/URL 片段,别整段复制快照);
  reason 一句话即可。冗长输出会撞模型输出上限被截断成坏 JSON。
只输出 JSON:{"verdict":"PASS"|"FAIL","evidence":"从当前页面逐字摘录的一小段证据(PASS 必填)","reason":"一句话结论"}"""

# E6 视觉裁判附加规则(仅当截图在场时拼到 _JUDGE_SYSTEM 之后)。a11y 快照是 DOM 的语义投影,
# **主动丢弃颜色/样式/图标位图**,故「状态灯变绿」「按钮红色」这类视觉态在纯文本通道下永远
# "拿不出逐字证据" → 被偏-FAIL 纪律判 FAIL(内网实测正是此现象)。开 JUDGE_VISUAL 喂截图后,
# 放开此类期望走视觉判定:evidence 改为描述截图所见;分辨不清仍判 FAIL(偏-FAIL 不变)。
_JUDGE_VISION_SUFFIX = """

【已附页面截图 —— 视觉证据规则】
- 另提供了一张当前页面的截图。对于**颜色、状态灯、图标、按钮配色等视觉状态**类期望
  (这些信息不在 A11y 快照文本里、无法逐字摘录),你可以依据在**截图中清晰看到**的视觉事实判定;
  此时 evidence 字段改为**简短描述你在截图中所见**(如"状态灯渲染为绿色""登录按钮为红色"),
  这视为合法证据,无需从快照文本逐字摘录。
- 偏-FAIL 纪律不变:视觉特征**看不清 / 分辨不出颜色 / 截图里找不到该元素** → 一律判 FAIL。
- 文本 / URL / 数值类期望仍以 A11y 快照 + URL 的逐字证据为准(截图只是补充,别拿它替代文本证据)。"""

# 喂裁判的快照字符上限。超过则**按期望锚点做相关度窗口截断**(而非头部硬切)——治长页面
# (IoT 仪表盘等)证据(状态/数值)排在 6000 字之后被切掉、裁判"找不到证据"误判 FAIL。
_JUDGE_SNAPSHOT_LIMIT = int(os.getenv("JUDGE_SNAPSHOT_LIMIT", "9000"))

# 从期望抽强锚点:引号字面 / 数字(含小数,如 32.00)/ 较长英文词(≥3)/ CJK 串(≥2)。
# 刻意不取超短英文(如 "On"),其子串("on")会命中 console/button 等噪声行。
_ANCHOR_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_ANCHOR_WORD_RE = re.compile(r"[A-Za-z]{3,}|[一-鿿]{2,}")


def _judge_anchors(expectation: str) -> list[str]:
    raw: list[str] = []
    raw += re.findall(r'"([^"]+)"', expectation)
    raw += re.findall(r"'([^']+)'", expectation)
    raw += _ANCHOR_NUM_RE.findall(expectation)
    raw += _ANCHOR_WORD_RE.findall(expectation)
    seen: set[str] = set()
    out: list[str] = []
    for a in raw:
        a = a.strip()
        if len(a) >= 2 and a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


def _snapshot_for_judge(
    snapshot: str, expectation: str, *, limit: int = _JUDGE_SNAPSHOT_LIMIT
) -> str:
    """长快照按期望锚点做**相关度窗口**截断,确保证据区进裁判视野。

    ≤limit 原样返回。否则:保留命中任一锚点的行 + 其上下文邻居(±3 行)+ 头部页面元信息,
    用「... [略]」标省略;无锚点(纯中文无引号无数字等)→ 退回头部硬截断(证据真缺则裁判照样
    FAIL,正确)。仅截断,不替裁判下结论。
    """
    if len(snapshot) <= limit:
        return snapshot
    lines = snapshot.splitlines()
    anchors = [a.lower() for a in _judge_anchors(expectation)]
    if not anchors:
        return snapshot[:limit] + "\n... [快照已截断]"
    keep = [False] * len(lines)
    ctx = 3
    for i, ln in enumerate(lines):
        low = ln.lower()
        if any(a in low for a in anchors):
            for j in range(max(0, i - ctx), min(len(lines), i + ctx + 1)):
                keep[j] = True
    for i, ln in enumerate(lines[:8]):  # 头部页面元信息(URL/Title)
        low = ln.lower()
        if "page url" in low or "page title" in low or ln.strip().startswith("###"):
            keep[i] = True
    out: list[str] = []
    used = 0
    gap = False
    for i, ln in enumerate(lines):
        if keep[i]:
            if gap:
                out.append("... [略]")
                gap = False
            out.append(ln)
            used += len(ln) + 1
            if used >= limit:
                out.append("... [快照已截断]")
                break
        else:
            gap = True
    # 锚点一行都没命中(证据可能真缺,或锚点形态对不上)→ 退回头部截断,至少让裁判看到页面、
    # 据缺失判 FAIL(给空快照反而让裁判无从判断)。
    if not out:
        return snapshot[:limit] + "\n... [快照已截断]"
    return "\n".join(out)


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
    SKIPPED = "skipped"  # 无法确定性验证(custom_tool 未接)。〔G1 后 llm_judge 主裁决缺失=FAIL,不再 skipped〕


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
    # 阶段化重设计后(FP0-3)裁决以阶段 Validator 为单位:此字段标识该裁决归属哪个 phase
    # (0-based);-1 = 非阶段裁决(历史/外部调用)。前端按此分组展示。
    phase_index: int = -1

    @property
    def passed(self) -> bool:
        return self.status == AssertionStatus.PASS

    def to_dict(self) -> dict:
        """录制进 ActionStep.assertion_results / ExecutionRecord.case_assertions 用。"""
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
            "phase_index": self.phase_index,
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
        # E6 多模态裁判通道:运行期记忆「该模型不支持图像」,首次失败后退回纯文本通道,
        # 避免每次浪费一次失败请求(对齐 healing._vision_unsupported)。
        self._vision_unsupported = False

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

    async def _check_llm_judge(self, a: Assertion, *, prev_url: str = "") -> AssertionResult:
        """LLM 语义断言(默认主裁决,偏-FAIL)。

        偏-FAIL 判 PASS/FAIL 并计入裁决;**判 PASS 须逐字引证页面实证**(evidence 字段,仅作
        可审计依据写入 reason)。〔2026-06-24 撤销「平台对 evidence 的确定性接地推翻」(用户拍板①):
        eval_fg A/B 扩样实测(deepseek-v4-flash,n=63,3 站点,6 轮)——接地层「有益拦截」恒为 0、
        仅偶发误伤(全落在 expected 无强锚点的脑补疑似分支)→ 净 ≤0;偏-FAIL 的 _JUDGE_SYSTEM 自身
        已扛住全部 false-green(0/34)。故裁决权交回模型,evidence 不再作推翻闸门。回归基准见
        `eval_fg/ab_grounding.py`。〕结果打 ``ai_judged`` 标记,报告区分「结构化绿」与「AI 判绿」使
        false green 可见可回溯;能用 URL/数据真值确定性验的预期仍应优先用 URL/custom_tool。
        **主裁决缺失三态(未接 LLM / 调用失败 / 解析不出 verdict)一律 FAIL**(2026-06-23 G1:
        阶段化下 LLM 是主裁决,缺失不能默认绿,且自动化平台无"skipped 等人工复核"环节)。

        ``prev_url``(2026-06-24 运行时锚点):本阶段开始时的 URL。与当前 URL 不同 = 确定性发生
        跳转,作免费锚点喂裁判,治"导航/登录达成类"期望在陌生内网落地页(裁判无先验、认不出是
        否主页)被偏-FAIL 误判 FAIL。不影响内容/数值类期望(仍须快照证据)。
        """
        if self.llm is None:
            # 阶段化重设计后 LLM judge 是主裁决:没有 LLM = 主裁决缺失 = 信号缺失,
            # 不能默认绿 → FAIL(而非旧设计的 skipped 等人工复核;自动化平台无此环节)。
            return AssertionResult(
                assertion=a,
                status=AssertionStatus.FAIL,
                ai_judged=True,
                reason="llm_judge 未接入 LLM,无法裁决 → FAIL(主裁决缺失不默认绿)",
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
        # 阶段起始 URL(运行时锚点):阶段开始与结束 URL 不同 = 确定性发生了跳转,治"导航/登录
        # 达成类"期望在陌生页面被偏-FAIL 误判(裁判认不出落地页是不是主页)。仅当与当前 URL
        # 不同才提示跳转,避免无跳转时给裁判噪声。
        prev_url = (prev_url or "").strip()
        nav_line = ""
        if prev_url and cur_url and prev_url != cur_url:
            nav_line = (
                f"本阶段开始时 URL:{prev_url}\n"
                f"(页面已从上面这个起始 URL 跳转到下面的当前 URL —— 确定性事实)\n\n"
            )
        # 按期望锚点相关度截断(治长页面证据排在头部 6000 字之后被切掉→裁判找不到证据误判 FAIL)
        snap_for_judge = _snapshot_for_judge(snapshot_text, expectation) if snapshot_text else ""
        user_text = (
            f"期望:{expectation}\n\n"
            f"{nav_line}"
            f"当前页面 URL:{cur_url or '(未知)'}\n\n"
            f"当前页面无障碍快照:\n{snap_for_judge or '(无快照)'}"
        )
        # E6 多模态裁判通道(默认关,env JUDGE_VISUAL=1 开启):
        # 抓一张截图作第二通道,治 a11y 树看不全的角标/图标/canvas 类预期(judge 容易看走眼)。
        # probe 需提供 `raw_screenshot() -> base64 | None`;模型不支持图像 → 本 run 内退回纯文本。
        screenshot_b64: str | None = None
        if _JUDGE_VISUAL_DEFAULT and not self._vision_unsupported:
            shot_fn = getattr(self.probe, "raw_screenshot", None)
            if callable(shot_fn):
                try:
                    screenshot_b64 = await shot_fn()
                except Exception as e:  # noqa: BLE001 — 取截图失败 → 退回纯文本,不阻断
                    logger.warning("llm_judge 取截图失败,退回纯文本:%s", e)
                    screenshot_b64 = None
        if screenshot_b64:
            url_data = (
                screenshot_b64
                if screenshot_b64.startswith("data:")
                else f"data:image/png;base64,{screenshot_b64}"
            )
            messages = [
                {"role": "system", "content": _JUDGE_SYSTEM + _JUDGE_VISION_SUFFIX},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": url_data}},
                    ],
                },
            ]
        else:
            messages = [
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": user_text},
            ]
        try:
            resp = await self.llm.chat(messages)
        except Exception as e:  # noqa: BLE001 — 调用失败 → FAIL(G1 fail-closed,主裁决缺失不默认绿)
            # E6:多模态首次失败 → 标记不支持图像,**本次直接退回纯文本重试**(贴 healing 同款)
            if screenshot_b64:
                self._vision_unsupported = True
                logger.warning("llm_judge 多模态调用失败(%s),退回纯文本重试", e)
                try:
                    resp = await self.llm.chat(
                        [
                            {"role": "system", "content": _JUDGE_SYSTEM},
                            {"role": "user", "content": user_text},
                        ]
                    )
                except Exception as e2:  # noqa: BLE001
                    logger.warning("llm_judge 退回纯文本仍失败:%s", e2)
                    return AssertionResult(
                        assertion=a,
                        status=AssertionStatus.FAIL,
                        ai_judged=True,
                        reason=f"llm_judge 调用失败,无法裁决 → FAIL(主裁决缺失不默认绿):{e2}",
                    )
            else:
                logger.warning("llm_judge 调用失败:%s", e)
                return AssertionResult(
                    assertion=a,
                    status=AssertionStatus.FAIL,
                    ai_judged=True,
                    reason=f"llm_judge 调用失败,无法裁决 → FAIL(主裁决缺失不默认绿):{e}",
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
            # 解析 + 正则都捞不出 verdict → 裁判输出不可用 = 主裁决缺失,fail-closed 判 FAIL
            # (绝不因输出乱默认绿;阶段化下无"skipped 等人工复核"环节)。
            return AssertionResult(
                assertion=a,
                status=AssertionStatus.FAIL,
                ai_judged=True,
                actual=content[:200],
                reason=f"llm_judge 未给出明确裁决,无法裁决 → FAIL:{reason or '(无)'}",
            )
        ok = verdict == "PASS"
        # 〔2026-06-24 撤销「证据接地推翻」(用户拍板①)〕——只信模型裁决 + 层(1)解析卫生
        # (上面 extract_verdict / 无 verdict→FAIL),**不再做平台确定性证据核验**。
        # 依据:`eval_fg` A/B 扩样实测(deepseek-v4-flash,n=63,3 站点,6 轮共 189 次裁决):
        #   · 偏-FAIL 的 _JUDGE_SYSTEM **自身**已扛住全部 false-green(0/34,跨 3 站点 0 漏绿);
        #   · 证据接地层「有益拦截」**恒为 0**(它要防的脑补刷绿一次都没发生、它一次都没拦);
        #   · 其唯一可测作用是**偶发误伤**(把真 PASS 推成 FAIL),且误伤全落在「expected 无强锚点」
        #     的"疑似脑补"分支(无 ground truth、纯跟模型对赌)→ 净贡献 ≤0。
        # 故撤掉该层,把裁决权交回模型;evidence 仍要求模型逐字引证(_JUDGE_SYSTEM),仅作
        # **可审计依据**写入 reason,不再作为推翻闸门。回归基准见 `eval_fg/ab_grounding.py`。
        return AssertionResult(
            assertion=a,
            status=_st(ok),
            actual=f"AI判定={verdict}",
            reason=(
                f"AI 判定通过(低置信,建议复核);依据:{evidence or reason or '(无)'}"
                if ok
                else f"AI 判定失败:{reason}"
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
        """[非阶段化路径化石] 旧的"至少一条 PASS、全 SKIPPED 不可信"裁决门槛。

        服务于旧设计"结构化为主、LLM 不可信需兜底"的假设——要求至少一条结构化绿。
        **阶段化重设计(FP0-3)后 LLM judge 是主裁决,本方法不再被 ``agent.run`` 调用**
        (⑤ 闸门改为「无阶段 FAIL + 执行完整」,G1 又把 llm_judge 的 SKIPPED 三态收成
        FAIL → 阶段裁决只剩 PASS/FAIL 二态)。保留供非阶段化/历史路径与外部调用使用。
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
