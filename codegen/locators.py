"""框架无关的定位器抽象 + 解析层(规格 §5.6 增强)。

设计目标(用户两点约束):

1. **稳健定位**:同一元素优先用语义层定位(role + 可及名 / test-id),它们**不随
   样式、布局、class 变化**;CSS/文本/xpath 脆弱,排后面。``Locator.strategy`` 即按
   稳健度分档。

2. **框架无关**:本模块只产出**规范化的 Locator**(语义 target → Locator),不含任何
   框架语法。具体渲染(Playwright ``get_by_role`` / Selenium / Cypress ...)由各
   ``CodeGenerator`` 实现。换框架只改渲染层,定位解析全复用——故 BDD 只是一种实现。

定位来源(优先级从高到低):**执行期捕获**的真实 a11y role+name
(``locators_from_steps``,覆盖未录入词汇表的目标)> **词汇表**(role+name 优先,
selector 次之)> 文本兜底。执行捕获更贴合本次实际渲染,故优先于静态词汇表。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class LocatorStrategy(str, Enum):
    """定位策略,**按稳健度从高到低**排列(简单 UI 变化不破坏靠前的)。"""

    ROLE = "role"  # role + 可及名:语义层,最稳
    TEST_ID = "test_id"  # data-testid:专为测试留的锚点
    LABEL = "label"  # 关联 label 文本
    PLACEHOLDER = "placeholder"  # 占位符
    TEXT = "text"  # 可见文本:易随文案/i18n 变
    CSS = "css"  # CSS 选择器:易随结构/class 变,最脆


# 稳健度序(供解析层在多来源可用时择优;数值越小越稳)
_RANK = {s: i for i, s in enumerate(LocatorStrategy)}


@dataclass
class Locator:
    """规范化定位器(框架无关)。各 CodeGenerator 据此渲染自身语法。"""

    strategy: LocatorStrategy
    name: str = ""  # 可及名 / label / placeholder / 文本 / testid 值
    role: str = ""  # ROLE 用
    value: str = ""  # CSS 选择器
    target: str = ""  # 原始语义 target(注释/兜底用)
    fallback: bool = False  # True=启发式兜底(无权威来源),生成代码应标注待人工核对

    @property
    def rank(self) -> int:
        return _RANK.get(self.strategy, len(_RANK))


def locator_from_vocab(target: str, entry: dict | None) -> Locator | None:
    """词汇表词条 → Locator。择优:role+name > selector(css) > 仅 name(文本)。

    role+name 是语义定位(最稳);selector 是用户显式维护的锚点(次稳,排 role 后);
    仅 name 退化为文本匹配。无可用信息返回 None。
    """
    if not isinstance(entry, dict):
        return None
    role = (entry.get("role") or "").strip()
    name = (entry.get("name") or "").strip()
    selector = (entry.get("selector") or "").strip()
    if role and name:
        return Locator(LocatorStrategy.ROLE, role=role, name=name, target=target)
    if selector:
        return Locator(LocatorStrategy.CSS, value=selector, target=target)
    if name:
        return Locator(LocatorStrategy.TEXT, name=name, target=target)
    return None


def locators_from_steps(steps) -> dict[str, Locator]:
    """执行期捕获的 ActionStep → ``{业务 target: ROLE Locator}``(框架无关)。

    执行时已从被操作元素的 ref 回查到真实 ``(role, name)`` 并记在 ActionStep 上;这里据此
    给**未录入词汇表**的目标也产出稳健 ``get_by_role`` 定位,覆盖面 > 仅词汇表。优先级最高
    (执行捕获 > 词汇表 > 文本兜底):真实页面身份比静态词汇表更贴合本次实际渲染。

    仅在 role 与 name 都齐备时采纳(role 无 name 的 get_by_role 过于宽泛,反不如词汇表);
    同一 target 取首个成功捕获。
    """
    out: dict[str, Locator] = {}
    for s in steps:
        role = (getattr(s, "element_role", "") or "").strip()
        name = (getattr(s, "element_name", "") or "").strip()
        target = (getattr(s, "step_target", "") or "").strip()
        if not (role and name and target):
            continue
        out.setdefault(target, Locator(LocatorStrategy.ROLE, role=role, name=name, target=target))
    return out


async def resolve_locators(
    targets: Iterable[str], resolver, *, url: str = "", title: str = ""
) -> dict[str, Locator]:
    """把一组语义 target 解析成 {target: Locator}(异步,词汇表查询)。

    在生成代码前由 agent 侧预解析(resolver.resolve 为 async,codegen 保持纯同步)。
    无 resolver 或未命中的 target 不入字典——渲染层据此回退启发式。
    """
    out: dict[str, Locator] = {}
    if resolver is None:
        return out
    for t in {t for t in targets if t}:  # 去重
        try:
            entry = await resolver.resolve(t, url=url, title=title)
        except Exception:  # noqa: BLE001 — 解析失败按未命中处理,不影响生成
            entry = None
        loc = locator_from_vocab(t, entry)
        if loc is not None:
            out[t] = loc
    return out
