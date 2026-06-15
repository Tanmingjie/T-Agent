"""TestSpec 生成 / 用例预解析(规格 §5.2 + §5.3 断言翻译,T-05)。

阶段一:**纯 LLM,不依赖词汇表**。把一条 TestCase 翻译成 TestSpec(软计划):

- 测试步骤 → SpecStep(action + 目标语义 target + 写死的 data),**不锁 selector**。
- 预期结果 → Assertion(断言翻译:把「判断」从运行时前移到翻译时,§5.3)。
- 预置条件中的「操作步骤」→ TestSpec.given(阶段一无分类器,交给 LLM 一并判断)。

断言翻译规则随 prompt 下发(五类按可靠性降级:DOM > 文本 > URL > custom_tool >
llm_judge;文本匹配限定具体元素内;一个预期可拆成多个断言)。

容错:LLM 输出用宽松 JSON 解析;解析失败时降级为 1:1 朴素映射(每条步骤 →
一个 SpecStep),保证阶段一管线不硬失败。生成的 TestSpec 供用户执行前审查/修改。
"""

from __future__ import annotations

import logging

from harness.llm import LLMClient, loads_lenient
from input.models import Assertion, PreconditionItem, SpecStep, TestCase, TestSpec

logger = logging.getLogger(__name__)

# 合法 action 词表(用于校验/归一;未知 action 保留原值,只告警)
_KNOWN_ACTIONS = {
    "navigate",
    "fill",
    "click",
    "select",
    "hover",
    "wait",
    "press",
    "check",
    "upload",
    "scroll",
    "execute",
}

_VALID_ASSERTION_TYPES = {
    "element_visible",
    "element_count",
    "text_equals",
    "text_contains",
    "url_contains",
    "url_equals",
    "custom_tool",
    "llm_judge",
}


_SYSTEM_PROMPT = """\
你是测试规格翻译器。把一条业务测试用例翻译成结构化执行规格(TestSpec)。

【核心原则】
- 这是「软计划」不是「硬脚本」:只描述「动作 + 目标语义 + 数据」,不要写 CSS/XPath 选择器。
- target 用业务语义描述目标元素(如「用户名输入框」「登录按钮」「订单状态」)。
- 步骤里写死的测试数据放进 data 字段。

【动作 action 取值】navigate | fill | click | select | hover | wait | press | check | upload | scroll | execute

【断言翻译规则(关键)】把「预期结果」翻译成结构化断言,执行时由规则引擎确定性验证,绝不靠肉眼判断。
按可靠性优先选择,能用前面的就不用后面的:
1. element_visible / element_count —— DOM 元素存在/数量(最可靠)
2. text_equals / text_contains —— 在【某个具体元素内】匹配文本,target 必须指明是哪个元素(不要全页搜)
3. url_contains / url_equals —— URL/导航断言
4. custom_tool —— 数据断言(查库/调接口),target 写要调用的数据校验意图
5. llm_judge —— 语义兜底,confidence 必须为 "low"
一个预期可拆成多个断言(如「提交成功并跳转列表页」→ url_contains("/list") + element_visible("成功提示"))。

【预置条件】预置条件中属于「操作步骤」的(如「设置环境变量」「新建一条订单」)放进 given;
属于「状态声明」的(如「已登录」)阶段一忽略(后续由 Hook 处理)。

【断言归属(关键)】预期结果通常是【按步骤】写的(第 N 条预期对应第 N 步)。把每条预期
翻译成**它所属那一步**的即时断言,放进该步的 "expect" 数组——因为它要在执行到那一步、
页面停在该子页面时验证。**不要**把属于中间步骤的预期攒到最后统一验:子页面的元素到了终态
页面可能已经不在,会被误判为失败。只有描述【整体/最终状态】、不专属某一具体步骤的预期,
才放进用例级 "assertions"。每条预期只写一次,不要既放 expect 又放 assertions。

【输出格式】只输出一个 JSON 对象,不要任何解释文字,结构如下:
{
  "given":  [{"action": "...", "target": "...", "data": null}],
  "steps":  [{"action": "...", "target": "...", "data": "写死的数据或null",
              "expect": [{"type": "...", "target": "...", "expected": "...", "confidence": "high"}]}],
  "assertions": [{"type": "...", "target": "...", "expected": "...", "confidence": "high"}]
}
"""


# 合并调用附加段:让模型在翻译 spec 的**同时**对每条预置条件分类,省掉单独的分类往返。
_CLASSIFY_APPENDIX = """\

【额外任务:预置条件分类】对上面每一条预置条件判定类型,并在输出 JSON 里增加一个
"preconditions" 数组(与预置条件一一对应):
- state_hook:状态声明(如「已登录」「环境已部署」「具有管理员权限」)——由框架 Hook 保证,**不要放进 given**。
- action_step:需要实际执行的操作(如「设置环境变量」「新建一条订单」「导入数据」)——**放进 given**。
- ambiguous:含义模糊/信息不足/不确定。
"preconditions" 每项形如 {"text":"预置条件原文","type":"state_hook|action_step|ambiguous","confidence":0.0~1.0}。
给定的 given 必须与你的分类一致(只收 action_step)。"""


def _precondition_lines(case: TestCase, items: list[PreconditionItem] | None) -> list[str]:
    """渲染「预置条件」段。

    若已有分类结果(``items``),按三类**分组标注**下发,让 LLM 不必再自己猜:
    - action_step → 明确要求放进 given;
    - state_hook  → 告知由框架 Hook 保证,忽略(别塞进 given);
    - ambiguous   → 信息不足,尽力处理。
    无分类结果时退回原始平铺列表(向后兼容)。
    """
    if not items:
        return [f"  - {p}" for p in case.preconditions] or ["  (无)"]

    by_type: dict[str, list[str]] = {"action_step": [], "state_hook": [], "ambiguous": []}
    for it in items:
        by_type.get(it.type, by_type["ambiguous"]).append(it.text)

    lines: list[str] = []
    if by_type["action_step"]:
        lines.append("  【需执行的前置操作 → 放进 given】")
        lines += [f"    - {t}" for t in by_type["action_step"]]
    if by_type["state_hook"]:
        lines.append("  【状态声明 → 由框架 Hook 保证,忽略,不要放进 given】")
        lines += [f"    - {t}" for t in by_type["state_hook"]]
    if by_type["ambiguous"]:
        lines.append("  【含义模糊 → 信息不足,可尽力处理】")
        lines += [f"    - {t}" for t in by_type["ambiguous"]]
    return lines or ["  (无)"]


def build_spec_messages(
    case: TestCase,
    precondition_items: list[PreconditionItem] | None = None,
    *,
    request_classification: bool = False,
    available_hooks: list[str] | None = None,
) -> list[dict]:
    """组装给 LLM 的消息(纯函数,便于单测)。

    ``precondition_items``:预置条件三分类结果(规格 §5.2 输入)。给定时按类分组下发,
    LLM 据此只把 action_step 放进 given;不给时按原始平铺(阶段一行为)。

    ``request_classification=True``:**合并模式**——让模型在翻译的同时输出预置条件分类
    ("preconditions" 数组),省掉单独的分类 LLM 往返。此模式预置条件按原文平铺下发
    (不预分组,因为正要让模型分类)。

    ``available_hooks``:当前**实际配置**的 Hook 名列表(合并模式下注入,告知 LLM
    哪些 Hook 可用)。仅当状态声明能由可用 Hook 保证时才归 state_hook;无可用 Hook 时
    引导归 action_step(测试内执行)/ ambiguous,避免「分类成 Hook 却没人执行」的静默漏洞。
    """
    pre_lines = _precondition_lines(case, None if request_classification else precondition_items)
    # 预期结果按步对齐呈现(第 N 条预期 ↔ 第 N 步),引导 LLM 把断言归到所属步骤的 expect。
    # 仅当条数与步骤数一致才标注步号;否则平铺(交给 LLM 自行判断归属)。
    if case.expected and len(case.expected) == len(case.steps):
        exp_lines = [f"  (对应步骤 {i}){e}" for i, e in enumerate(case.expected, 1)]
    else:
        exp_lines = [f"  - {e}" for e in case.expected] or ["  (无)"]
    user = [
        f"用例名称:{case.name}",
        "",
        "预置条件:",
        *pre_lines,
        "",
        "测试步骤:",
        *(f"  {i}. {s}" for i, s in enumerate(case.steps, 1)),
        "",
        "预期结果:",
        *exp_lines,
    ]
    system = _SYSTEM_PROMPT + (_CLASSIFY_APPENDIX if request_classification else "")
    if request_classification:
        if available_hooks:
            system += (
                "\n\n【可用 Hook】当前已配置的 Hook:"
                + "、".join(available_hooks)
                + "。仅当某状态声明能由上述 Hook 之一保证时,才归 state_hook 并在 hook_ref "
                "写该 Hook 名;否则归 action_step(在测试步骤内实际执行)或 ambiguous,"
                "**不要指定不存在的 Hook**。"
            )
        else:
            system += (
                "\n\n【可用 Hook】当前**没有配置任何 Hook**。状态类前提请尽量归 action_step"
                "(在测试内实际执行,如先登录),确实无法执行的归 ambiguous;**不要归 state_hook**"
                "(没有 Hook 会执行它,归 state_hook 等于该前提被静默忽略)。"
            )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user)},
    ]


def _coerce_assertion(raw: dict) -> Assertion | None:
    """把一个 dict 转成 Assertion,非法则丢弃(返回 None)。"""
    if not isinstance(raw, dict):
        return None
    a_type = str(raw.get("type", "")).strip()
    target = str(raw.get("target") or "").strip()
    if a_type not in _VALID_ASSERTION_TYPES:
        logger.warning("丢弃非法断言(类型不支持):%r", raw)
        return None
    # URL 类断言不依赖元素 target,缺省填 "URL";其余类型 target 必填
    if a_type in ("url_contains", "url_equals"):
        target = target or "URL"
    elif not target:
        logger.warning("丢弃非法断言(缺 target):%r", raw)
        return None
    expected = raw.get("expected")
    confidence = str(raw.get("confidence") or "high").strip()
    if a_type == "llm_judge":
        confidence = "low"  # llm_judge 强制 low(§5.3)
    return Assertion(
        type=a_type,
        target=target,
        selector=raw.get("selector"),
        expected=None if expected is None else str(expected),
        confidence=confidence,
    )


def _coerce_step(raw: dict) -> SpecStep | None:
    """把一个 dict 转成 SpecStep,缺 action/target 则丢弃。"""
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action", "")).strip()
    target = str(raw.get("target", "")).strip()
    if not action or not target:
        logger.warning("丢弃非法步骤:%r", raw)
        return None
    if action not in _KNOWN_ACTIONS:
        logger.warning("未知 action %r,保留原值", action)
    data = raw.get("data")
    expect_raw = raw.get("expect") or []
    expect = [a for a in (_coerce_assertion(e) for e in expect_raw) if a is not None]
    return SpecStep(
        action=action,
        target=target,
        data=None if data is None else str(data),
        expect=expect,
    )


def parse_spec_response(content: str, case: TestCase) -> TestSpec:
    """把 LLM 文本响应解析为 TestSpec(纯函数)。解析失败抛 ValueError。"""
    data = loads_lenient(content)  # 宽松 JSON;失败抛 ValueError
    given = [s for s in (_coerce_step(x) for x in data.get("given", [])) if s is not None]
    steps = [s for s in (_coerce_step(x) for x in data.get("steps", [])) if s is not None]
    assertions = [
        a for a in (_coerce_assertion(x) for x in data.get("assertions", [])) if a is not None
    ]
    return TestSpec(
        case_id=case.id,
        name=case.name,
        base_url=case.base_url,
        given=given,
        steps=steps,
        assertions=assertions,
    )


def parse_classification(content: str) -> dict[str, dict]:
    """从合并响应里抽出预置条件分类 → {text: {type, confidence, reason}}。

    解析失败/无该字段 → 返回空 dict(下游按 ambiguous 兜底,不报错)。纯函数。
    """
    try:
        data = loads_lenient(content)
    except ValueError:
        return {}
    arr = data.get("preconditions") if isinstance(data, dict) else None
    if not isinstance(arr, list):
        return {}
    out: dict[str, dict] = {}
    for entry in arr:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "").strip()
        if text:
            out[text] = entry
    return out


def naive_fallback_spec(case: TestCase) -> TestSpec:
    """降级:LLM 不可用/解析失败时,1:1 朴素映射,保证管线不硬失败。

    每条步骤 → action="execute" 的 SpecStep(target=原文);每条预期 → llm_judge
    兜底断言(confidence=low,需人工复核)。生成结果质量差,仅保证可继续。
    """
    logger.warning("用例 %s 走 TestSpec 朴素降级映射(质量较低,建议人工修订)", case.id)
    steps = [SpecStep(action="execute", target=s) for s in case.steps]
    assertions = [
        Assertion(type="llm_judge", target=e, expected=e, confidence="low") for e in case.expected
    ]
    return TestSpec(
        case_id=case.id,
        name=case.name,
        base_url=case.base_url,
        steps=steps,
        assertions=assertions,
    )


class SpecGenerator:
    """TestSpec 生成器(阶段一纯 LLM)。"""

    def __init__(self, llm: LLMClient, *, fallback_on_error: bool = True) -> None:
        self.llm = llm
        self.fallback_on_error = fallback_on_error

    async def generate(
        self,
        case: TestCase,
        precondition_items: list[PreconditionItem] | None = None,
        *,
        on_delta=None,
    ) -> TestSpec:
        """生成 TestSpec。LLM 或解析失败时按配置降级或抛出。

        ``precondition_items``:预置条件三分类结果(规格 §5.2)。给定时按类分组下发,
        引导 LLM 只把 action_step 放进 given(state_hook 交给 Hook)。
        ``on_delta``:给定则走流式(逐 token 回调),让慢模型长生成不被网关空闲超时切断。
        """
        messages = build_spec_messages(case, precondition_items)
        try:
            resp = await (
                self.llm.chat_stream(messages, on_delta=on_delta)
                if on_delta is not None
                else self.llm.chat(messages)
            )
            return parse_spec_response(resp.content, case)
        except Exception as e:  # noqa: BLE001 — 翻译层兜底,避免炸管线
            logger.warning("TestSpec 生成失败(%s):%s", case.id, e)
            if self.fallback_on_error:
                return naive_fallback_spec(case)
            raise

    async def generate_with_classification(
        self, case: TestCase, *, on_delta=None, available_hooks: list[str] | None = None
    ) -> tuple[TestSpec, dict[str, dict]]:
        """**合并模式**:一次 LLM 调用同时生成 TestSpec + 预置条件分类。

        返回 ``(spec, raw_classification)``,``raw_classification`` 为 {text: {type,...}},
        交由 ``PreconditionClassifier.classify_from_raw`` 做确定性建项(不再单独调 LLM)。
        失败时降级 ``(naive_fallback_spec, {})``(分类为空 → 下游全 ambiguous)。
        ``on_delta``:给定则走流式(逐 token 回调),长生成不被网关空闲超时切断。
        """
        messages = build_spec_messages(
            case, request_classification=True, available_hooks=available_hooks
        )
        try:
            resp = await (
                self.llm.chat_stream(messages, on_delta=on_delta)
                if on_delta is not None
                else self.llm.chat(messages)
            )
            spec = parse_spec_response(resp.content, case)
            classification = parse_classification(resp.content)
            return spec, classification
        except Exception as e:  # noqa: BLE001 — 翻译层兜底,避免炸管线
            logger.warning("TestSpec 合并生成失败(%s):%s", case.id, e)
            if self.fallback_on_error:
                return naive_fallback_spec(case), {}
            raise
