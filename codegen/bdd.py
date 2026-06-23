"""BDDGenerator —— 默认代码生成器(阶段化重设计后的**最小适配**,2026-06-22)。

阶段化 TestSpec + 录制 → pytest-bdd 三件套骨架:

- ``.feature``(Gherkin):Given ← preconditions(背景);When ← 各阶段步骤(自然语言);
  Then ← 各阶段 expected(自然语言)。
- ``test_<case>.py``(step 定义):步骤体优先用**执行轨迹捕获的真实定位器**渲染交互(ground
  truth);拿不到则留 TODO 骨架。expected 是自然语言,无法确定性断言 → Then 留 TODO 注释。
- ``conftest.py``:最小 page fixture。

注:翻译重设计后 spec 只产意图(无结构化断言/动作类型),codegen 因而退化为**可读骨架 +
真实定位器提示**;**轨迹驱动 codegen**(从 record.steps 的 tool_name+定位器精确还原)列为后续
任务。产物经 ast.parse 校验 + black 格式化。
"""

from __future__ import annotations

import ast

import black

from codegen.base import CodeGenerator, GeneratedCode
from codegen.locators import Locator, LocatorStrategy
from input.models import ExecutionRecord, TestSpec


def _q(text: str) -> str:
    """安全的双引号字符串字面量(转义内部双引号/反斜杠)。"""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _gherkin(text: str) -> str:
    """Gherkin 行文本:折叠换行/竖线(避免破坏单行步骤)。"""
    return " ".join(str(text).split()).replace("|", "/")


def _render_locator(loc: Locator) -> str:
    """规范化 Locator → Playwright 定位表达式(解析到单元素)。"""
    s = loc.strategy
    if s == LocatorStrategy.ROLE:
        return f"page.get_by_role({_q(loc.role)}, name={_q(loc.name)})"
    if s == LocatorStrategy.TEST_ID:
        return f"page.get_by_test_id({_q(loc.name)})"
    if s == LocatorStrategy.LABEL:
        return f"page.get_by_label({_q(loc.name)})"
    if s == LocatorStrategy.PLACEHOLDER:
        return f"page.get_by_placeholder({_q(loc.name)})"
    if s == LocatorStrategy.CSS:
        return f"page.locator({_q(loc.value)}).first"
    return f"page.get_by_text({_q(loc.name or loc.target)}).first"


def _flatten_steps(spec: TestSpec) -> list[str]:
    """摊平所有阶段的步骤(自然语言串,保序)。"""
    out: list[str] = []
    for ph in spec.phases:
        out.extend(ph.steps)
    return out


class BDDGenerator(CodeGenerator):
    def generate(
        self,
        spec: TestSpec,
        record: ExecutionRecord,
        locators: dict[str, Locator] | None = None,
    ) -> GeneratedCode:
        name = spec.case_id
        feature = self._feature(spec)
        step_defs = self._step_defs(spec, name, locators or {})
        return GeneratedCode(name=name, feature=feature, step_defs=step_defs, conftest=_CONFTEST)

    def _feature(self, spec: TestSpec) -> str:
        lines = [f"Feature: {spec.name}", f"  # 用例 {spec.case_id}"]
        if spec.intent:
            lines.append(f"  # 意图:{_gherkin(spec.intent)}")
        lines.append("")
        lines.append(f"  Scenario: {spec.name}")

        first = True
        for p in spec.preconditions:
            kw = "Given" if first else "And"
            lines.append(f"    {kw} {_gherkin(p)}")
            first = False

        first = True
        for s in _flatten_steps(spec):
            kw = "When" if first else "And"
            lines.append(f"    {kw} {_gherkin(s)}")
            first = False

        first = True
        for ph in spec.phases:
            if not ph.expected:
                continue
            kw = "Then" if first else "And"
            lines.append(f"    {kw} {_gherkin(ph.expected)}")
            first = False

        return "\n".join(lines) + "\n"

    def _step_defs(self, spec: TestSpec, name: str, locators: dict[str, Locator]) -> str:
        blocks: list[str] = [
            "import re  # noqa: F401",
            "",
            "from playwright.sync_api import Page, expect  # noqa: F401",
            "from pytest_bdd import given, scenarios, then, when",
            "",
            f'scenarios("{name}.feature")',
            "",
        ]
        seen: set[str] = set()
        idx = 0

        def emit(decorator: str, text: str, body: str) -> None:
            nonlocal idx
            if text in seen:
                return
            seen.add(text)
            blocks.append("")
            blocks.append(f"@{decorator}({_q(text)})")
            blocks.append(f"def {decorator}_{idx}(page: Page):")
            blocks.append(body)
            idx += 1

        for p in spec.preconditions:
            emit("given", _gherkin(p), "    # 前置(背景,假设成立)\n    pass")
        for s in _flatten_steps(spec):
            text = _gherkin(s)
            loc = locators.get(s) or locators.get(text)
            if loc is not None:
                body = (
                    f"    # 步骤:{text}\n"
                    f"    # 执行期捕获的定位器(请按实际动作补 .click()/.fill(...) 等)\n"
                    f"    {_render_locator(loc)}"
                )
            else:
                body = f"    # TODO 步骤:{text} —— 请按实际操作补充\n    pass"
            emit("when", text, body)
        for ph in spec.phases:
            if not ph.expected:
                continue
            emit(
                "then",
                _gherkin(ph.expected),
                f"    # TODO 阶段预期(自然语言,需人工转成断言):{_gherkin(ph.expected)}\n    pass",
            )

        code = "\n".join(blocks) + "\n"
        ast.parse(code)  # 语法校验,失败即抛
        return black.format_str(code, mode=black.Mode())


_CONFTEST = black.format_str(
    '''\
"""pytest-bdd + Playwright 运行夹具(生成产物,可按需调整)。"""
import pytest
from playwright.sync_api import Page, sync_playwright


@pytest.fixture
def page() -> Page:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        yield pg
        browser.close()
''',
    mode=black.Mode(),
)
