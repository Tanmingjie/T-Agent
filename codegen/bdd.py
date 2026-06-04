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
from codegen.locators import Locator, LocatorStrategy
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


# ── Locator → Playwright 定位表达式(渲染层) ───────────────────


def _render_locator(loc: Locator, *, single: bool = True) -> str:
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
        base = f"page.locator({_q(loc.value)})"
        return f"{base}.first" if single else base
    # TEXT
    base = f"page.get_by_text({_q(loc.name or loc.target)})"
    return f"{base}.first" if single else base


def _locator_expr(
    target: str, action: str, locators: dict[str, Locator] | None, *, single: bool = True
) -> tuple[str, bool]:
    """求一个 (target, action) 的定位表达式。

    命中解析出的 Locator(词汇表)→ 按其策略渲染;否则回退**原有启发式**
    (fill/select→get_by_label,其余→get_by_text),保持向后兼容。
    返回 (表达式, 是否兜底需人工核对)。
    """
    loc = (locators or {}).get(target)
    if loc is not None:
        return _render_locator(loc, single=single), loc.fallback
    if action in ("fill", "select"):
        return f"page.get_by_label({_q(target)})", True
    base = f"page.get_by_text({_q(target)})"
    return (f"{base}.first" if single else base), True


# 兜底定位器的提醒(作为**前置注释行**,避免内联注释把语句撑长被 black 换行)
_REVIEW_NOTE = "    # TODO 定位器兜底(文本/标签匹配),建议核对真实选择器"


def _with_review(stmt: str, review: bool) -> str:
    """组装函数体:兜底时在语句前加一行提醒注释。stmt 不含前导缩进。"""
    return f"{_REVIEW_NOTE}\n    {stmt}" if review else f"    {stmt}"


def _step_body(step: SpecStep, base_url: str, locators: dict[str, Locator] | None = None) -> str:
    a, t, d = step.action, step.target, step.data
    if a == "navigate":
        url = f"{base_url.rstrip('/')}/{t.lstrip('/')}" if t.startswith("/") else t
        return f"    page.goto({_q(url)})  # 导航到「{t}」"
    if a == "wait":
        return f"    page.wait_for_timeout(1000)  # 等待「{t}」"
    if a in ("fill", "select", "click", "hover"):
        expr, review = _locator_expr(t, a, locators)
        tail = {
            "fill": f".fill({_q(d or '')})",
            "select": f".select_option({_q(d or '')})",
            "click": ".click()",
            "hover": ".hover()",
        }[a]
        return _with_review(f"{expr}{tail}", review)
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


def _assertion_body(a: Assertion, locators: dict[str, Locator] | None = None) -> str:
    exp = a.expected or ""
    if a.type == "url_contains":
        return f"    expect(page).to_have_url(re.compile({_q(re.escape(exp))}))"
    if a.type == "url_equals":
        return f"    expect(page).to_have_url({_q(exp)})"
    # 显式 selector(Assertion.selector)优先;否则走词汇表解析 / 文本兜底
    if a.selector:
        single = f"page.locator({_q(a.selector)}).first"
        multi = f"page.locator({_q(a.selector)})"
        review = False
    else:
        single, review = _locator_expr(a.target, "click", locators, single=True)
        multi, _ = _locator_expr(a.target, "click", locators, single=False)
    if a.type == "element_visible":
        return _with_review(f"expect({single}).to_be_visible()", review)
    if a.type == "element_count":
        n = exp if str(exp).isdigit() else "1"
        return _with_review(f"expect({multi}).to_have_count({n})", review)
    if a.type == "text_equals":
        return _with_review(f"expect({single}).to_have_text({_q(exp)})", review)
    if a.type == "text_contains":
        return _with_review(f"expect({single}).to_contain_text({_q(exp)})", review)
    return f"    # TODO 断言({a.type}):{a.target} —— 阶段一/三未直接支持,请人工确认\n    pass"


# ── 生成器 ───────────────────────────────────────────────────


class BDDGenerator(CodeGenerator):
    def generate(
        self,
        spec: TestSpec,
        record: ExecutionRecord,
        locators: dict[str, Locator] | None = None,
    ) -> GeneratedCode:
        name = spec.case_id
        feature = self._feature(spec)
        step_defs = self._step_defs(spec, name, locators)
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

    def _step_defs(
        self, spec: TestSpec, name: str, locators: dict[str, Locator] | None = None
    ) -> str:
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
            emit("when", _step_text(s), _step_body(s, spec.base_url, locators), i)
        for i, a in enumerate(spec.assertions, start=1):
            emit("then", _assertion_text(a), _assertion_body(a, locators), i)

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
