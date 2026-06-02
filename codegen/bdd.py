"""BDDGenerator —— 默认代码生成器(规格 §5.6,T-20)。

TestSpec + 录制 → pytest-bdd 三件套:

- ``.feature``(Gherkin):Given ← TestSpec.given;When ← TestSpec.steps(**业务步骤粒度**,
  非 tool_call);Then ← TestSpec.assertions。
- ``test_<case>.py``(step 定义):断言**直接映射 Playwright expect()**;
  选择器优先 ``get_by_role`` / ``get_by_label``;中文业务步骤。
- ``conftest.py``:最小 page fixture。

产物经 ast.parse 校验 + black 格式化("可读性 > 简洁性")。定位是可读的长期资产,
不保证一次回放即过(规格:pytest 回放验证为"建议");语义选择器需人工微调时,
注释已给出业务意图。
"""

from __future__ import annotations

import ast
import re

import black

from codegen.base import CodeGenerator, GeneratedCode
from input.models import Assertion, ExecutionRecord, SpecStep, TestSpec


def _q(text: str) -> str:
    """安全的双引号字符串字面量(转义内部双引号/反斜杠)。"""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


# ── 业务步骤 → (Gherkin 文本, step 定义函数体) ─────────────────


def _step_text(step: SpecStep) -> str:
    a, t, d = step.action, step.target, step.data
    if a == "navigate":
        return f"打开 {t}"
    if a == "fill":
        return f"在 {t} 输入 {d}"
    if a == "select":
        return f"在 {t} 选择 {d}"
    if a == "click":
        return f"点击 {t}"
    if a == "hover":
        return f"悬停 {t}"
    if a == "wait":
        return f"等待 {t}"
    return t  # execute / 其它:用原始语义


def _step_body(step: SpecStep, base_url: str) -> str:
    a, t, d = step.action, step.target, step.data
    if a == "navigate":
        return f"    page.goto({_q(base_url)})  # 导航到「{t}」"
    if a == "fill":
        return f"    page.get_by_label({_q(t)}).fill({_q(d or '')})"
    if a == "select":
        return f"    page.get_by_label({_q(t)}).select_option({_q(d or '')})"
    if a == "click":
        return f'    page.get_by_role("button", name={_q(t)}).click()'
    if a == "hover":
        return f"    page.get_by_text({_q(t)}).first.hover()"
    if a == "wait":
        return f"    page.wait_for_timeout(1000)  # 等待「{t}」"
    return f"    # TODO 业务动作({a}):{t} —— 请按实际补充\n    pass"


# ── 断言 → (Gherkin 文本, expect() 函数体) ───────────────────


def _assertion_text(a: Assertion) -> str:
    mapping = {
        "url_contains": f"页面 URL 包含 {a.expected}",
        "url_equals": f"页面 URL 等于 {a.expected}",
        "element_visible": f"{a.target} 可见",
        "element_count": f"{a.target} 数量为 {a.expected}",
        "text_equals": f"{a.target} 文本为 {a.expected}",
        "text_contains": f"{a.target} 文本包含 {a.expected}",
    }
    return mapping.get(a.type, f"{a.target} 满足 {a.type}")


def _assertion_body(a: Assertion) -> str:
    exp = a.expected or ""
    if a.type == "url_contains":
        return f"    expect(page).to_have_url(re.compile({_q(re.escape(exp))}))"
    if a.type == "url_equals":
        return f"    expect(page).to_have_url({_q(exp)})"
    if a.type == "element_visible":
        return f"    expect(page.get_by_text({_q(a.target)}).first).to_be_visible()"
    if a.type == "element_count":
        n = exp if str(exp).isdigit() else "1"
        return f"    expect(page.get_by_text({_q(a.target)})).to_have_count({n})"
    if a.type == "text_equals":
        return f"    expect(page.get_by_text({_q(a.target)}).first).to_have_text({_q(exp)})"
    if a.type == "text_contains":
        return f"    expect(page.get_by_text({_q(a.target)}).first).to_contain_text({_q(exp)})"
    return f"    # TODO 断言({a.type}):{a.target} —— 阶段一/三未直接支持,请人工确认\n    pass"


# ── 生成器 ───────────────────────────────────────────────────


class BDDGenerator(CodeGenerator):
    def generate(self, spec: TestSpec, record: ExecutionRecord) -> GeneratedCode:
        name = spec.case_id
        feature = self._feature(spec)
        step_defs = self._step_defs(spec, name)
        conftest = _CONFTEST
        return GeneratedCode(name=name, feature=feature, step_defs=step_defs, conftest=conftest)

    def _feature(self, spec: TestSpec) -> str:
        lines = [
            f"Feature: {spec.name}",
            f"  # 用例 {spec.case_id}",
            "",
            f"  Scenario: {spec.name}",
        ]

        first = True
        for g in spec.given:
            kw = "Given" if first else "And"
            lines.append(f"    {kw} {g.target}")
            first = False

        first = True
        for s in spec.steps:
            kw = "When" if first else "And"
            lines.append(f"    {kw} {_step_text(s)}")
            first = False

        first = True
        for a in spec.assertions:
            kw = "Then" if first else "And"
            lines.append(f"    {kw} {_assertion_text(a)}")
            first = False

        return "\n".join(lines) + "\n"

    def _step_defs(self, spec: TestSpec, name: str) -> str:
        blocks: list[str] = [
            "import re",
            "",
            "from playwright.sync_api import Page, expect",
            "from pytest_bdd import given, scenarios, then, when",
            "",
            f'scenarios("{name}.feature")',
            "",
        ]
        seen: set[str] = set()
        idx = 0

        def emit(decorator: str, text: str, body: str, step_no: int = 0) -> None:
            nonlocal idx
            if text in seen:
                return  # 同名 step 复用一个定义(pytest-bdd 按文本匹配)
            seen.add(text)
            blocks.append("")
            blocks.append(f"@{decorator}({_q(text)})")
            blocks.append(f"def {decorator}_{idx}(page: Page):")
            if step_no:
                blocks.append(f"    # step_{step_no}")
            blocks.append(body)
            idx += 1

        for i, g in enumerate(spec.given, start=1):
            emit(
                "given",
                g.target,
                f"    # 业务前置({g.action}):{g.target} —— 请按实际补充\n    pass",
                i,
            )
        for i, s in enumerate(spec.steps, start=1):
            emit("when", _step_text(s), _step_body(s, spec.base_url), i)
        for i, a in enumerate(spec.assertions, start=1):
            emit("then", _assertion_text(a), _assertion_body(a), i)

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
