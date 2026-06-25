"""TestSpec 生成 / 用例预解析(阶段化重设计,2026-06-22)。

把一条业务 TestCase 翻译成**阶段化 TestSpec**(契约见 docs/test_spec_v2.md):

- 整体测试意图 ``intent``(背景,助 agent/Validator 理解,不是判据)。
- 前置声明 ``preconditions``(原样背景,不执行不 guard)。
- 有序 ``phases``:每个阶段 = 一组步骤(自然语言,数据内联,**驱动**)+ 一条组级预期
  ``expected``(只给阶段边界 Validator 偏-FAIL 证据核验,**不进驱动**)。

核心原则:**翻译只产意图,不接地**——不写 selector、不锁动作类型、不猜元素;元素定位与
动作选择全部交给运行时 agent 看真实页面决定。容错:LLM 输出宽松 JSON 解析;失败降级为
**近乎无损**的单阶段映射(steps 用 Excel 原文、expected 用预期结果原文),保证管线不硬失败。
"""

from __future__ import annotations

import logging

from harness.llm import LLMClient, loads_lenient
from input.models import Phase, TestCase, TestSpec

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
你是测试规格翻译器。把一条业务测试用例翻译成**阶段化执行规格(TestSpec)**。

【核心原则:只产意图,不接地】
- 你**看不到真实页面**,所以**绝不**写 CSS/XPath 选择器、绝不假设元素叫什么、绝不锁定具体
  动作类型。只用自然语言描述「这一步要达成什么」,元素定位和具体操作交给运行时的执行 agent。
- 步骤是自然语言祈使句,**数据写在句子里**(如「在用户名框输入 standard_user」「填写采购数量 100」)。

【阶段(phase):按子目标给步骤分组】
- 把连续若干步**为达成同一个可观察子目标**的归为一个阶段(如「步骤1-2 完成登录」「步骤3-4 进入订单模块」)。
- 每个阶段给**一条** expected:该阶段子目标达成后、**在当时所处页面上可观察到**的状态
  (执行时系统会在该阶段结束、页面还停在那一刻时,看真实页面核验这条 expected)。
- expected 要可观察、具体到该阶段所在页面,可含多个事实。例如:
  · 完成登录这一阶段 → "登录成功，进入商品列表页（URL 含 inventory.html，出现商品列表）"
  · 加入购物车这一阶段 → "购物车角标显示 1，该商品按钮变为 Remove"
- ⚠️【写"稳态可观测特征",别写"瞬态成功文案"】成功登录/提交后,页面几乎**不会**长期显示
  "登录成功""操作成功"这类字样(那是一闪而过的 toast)。expected 要写**登录后稳态页面上
  持续可见的东西**:出现导航菜单/用户名/业务功能模块、**登录表单(用户名/密码框)消失**、
  URL 从登录页跳走。**不要**写"页面显示‘登录成功’文案"。
- ⚠️【不要假设"典型"路径/文案】不同系统登录后的落地页路由各不相同(可能是 /home、/portal、
  /about、/index 等),你**不知道**也**不要猜**具体路径或主页该叫什么名。expected 只描述
  「已离开登录页、出现登录后才有的功能/元素」这类与具体路由无关的事实,把"到底跳哪个 URL"
  留给运行时核验,别在 expected 里钉死某个"主页路径"。
- ⚠️ expected 是【验证依据】,系统只在阶段边界用它核验,**不会**拿它驱动 agent;所以即使它
  描述得很「业务」也没关系——但**不要把多个阶段的预期混进一条**,每个阶段只写它自己那一刻的状态。
- 若用例的「预期结果」能对应到某个阶段,就把它写进那个阶段的 expected;最后一个阶段的 expected
  就是整条用例跑完的最终态(系统不再单独做"终态裁决",最后一阶段的核验即终态检查)。

【整体测试意图 intent】用一两句话概括这条用例**整体在验什么**(业务目的/背景),帮助执行
agent 和裁判理解上下文。这是背景,**不是** pass/fail 判据。尽量贴用例名称与步骤的真实意图。

【前置条件 preconditions】把用例的预置条件**原样**列进 preconditions 数组(自然语言)。
它们只是**背景**(让 agent 知道假设的初始状态),系统**不执行、不核验**。不要把前置当步骤。

【输出格式】只输出一个 JSON 对象,不要任何解释文字:
{
  "intent": "整条用例整体在验什么(一两句,背景)",
  "preconditions": ["前置声明原文", "..."],
  "phases": [
    {
      "steps": ["这一步要达成什么(自然语言,数据内联)", "..."],
      "expected": "该阶段子目标达成后、当时页面上应出现/变成什么(自然语言,可含多个事实)"
    }
  ]
}
"""


def build_spec_messages(case: TestCase) -> list[dict]:
    """组装给 LLM 的消息(纯函数,便于单测)。"""
    pre_lines = [f"  - {p}" for p in case.preconditions] or ["  (无)"]
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
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user)},
    ]


def _coerce_phase(raw: dict) -> Phase | None:
    """把一个 dict 转成 Phase。无任何步骤则丢弃(空阶段无意义)。"""
    if not isinstance(raw, dict):
        return None
    steps_raw = raw.get("steps") or []
    steps = [
        str(s).strip() for s in steps_raw if isinstance(s, (str, int, float)) and str(s).strip()
    ]
    if not steps:
        return None
    expected = str(raw.get("expected") or "").strip()
    return Phase(steps=steps, expected=expected)


def parse_spec_response(content: str, case: TestCase) -> TestSpec:
    """把 LLM 文本响应解析为阶段化 TestSpec(纯函数)。解析失败抛 ValueError。"""
    data = loads_lenient(content)  # 宽松 JSON;失败抛 ValueError
    intent = str(data.get("intent") or "").strip()
    pre_raw = data.get("preconditions") or []
    preconditions = (
        [str(p).strip() for p in pre_raw if str(p).strip()] if isinstance(pre_raw, list) else []
    )
    phases = [p for p in (_coerce_phase(x) for x in data.get("phases", [])) if p is not None]
    return TestSpec(
        case_id=case.id,
        name=case.name,
        base_url=case.base_url,
        intent=intent,
        preconditions=preconditions,
        phases=phases,
    )


def naive_fallback_spec(case: TestCase) -> TestSpec:
    """降级:LLM 不可用/解析失败时的**近乎无损**映射,保证管线不硬失败。

    所有测试步骤原样塞进**单个阶段**(Excel 原文即自然语言步骤),expected 用预期结果原文
    拼接;preconditions 原样;intent 用用例名。质量不如分阶段,但不丢信息、可继续执行。
    """
    logger.warning("用例 %s 走 TestSpec 朴素降级映射(单阶段,建议人工修订)", case.id)
    expected = "；".join(e.strip() for e in case.expected if e.strip())
    phases = [Phase(steps=list(case.steps), expected=expected)] if case.steps else []
    return TestSpec(
        case_id=case.id,
        name=case.name,
        base_url=case.base_url,
        intent=case.name,
        preconditions=list(case.preconditions),
        phases=phases,
    )


class SpecGenerator:
    """TestSpec 生成器(纯 LLM 翻译,产阶段化 spec)。"""

    def __init__(self, llm: LLMClient, *, fallback_on_error: bool = True) -> None:
        self.llm = llm
        self.fallback_on_error = fallback_on_error

    async def generate(self, case: TestCase, *, on_delta=None) -> TestSpec:
        """生成阶段化 TestSpec。LLM 或解析失败时按配置降级或抛出。

        ``on_delta``:给定则走流式(逐 token 回调),让慢模型长生成不被网关空闲超时切断。
        """
        messages = build_spec_messages(case)
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
