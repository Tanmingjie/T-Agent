"""T-20 单元测试:CodeGenerator 抽象 + BDDGenerator。

TDD:先定义生成产物的期望(Gherkin 结构、Playwright 映射、语法合法),再实现。
"""

from __future__ import annotations

import ast

from codegen.base import CodeGenerator, GeneratedCode
from codegen.bdd import BDDGenerator
from input.models import Assertion, ExecutionRecord, SpecStep, TestSpec


def _spec() -> TestSpec:
    return TestSpec(
        case_id="TC001",
        name="登录并提交订单",
        base_url="https://intranet.example",
        given=[SpecStep(action="execute", target="新建草稿订单")],
        steps=[
            SpecStep(action="navigate", target="订单列表页"),
            SpecStep(action="fill", target="用户名", data="admin"),
            SpecStep(action="click", target="提交"),
        ],
        assertions=[
            Assertion(type="url_contains", target="URL", expected="/list"),
            Assertion(type="element_visible", target="成功提示"),
            Assertion(type="text_equals", target="订单状态", expected="待审批"),
        ],
    )


def _record() -> ExecutionRecord:
    return ExecutionRecord(exec_id="e1", case_id="TC001", passed=True)


def _gen() -> GeneratedCode:
    return BDDGenerator().generate(_spec(), _record())


# ── feature(Gherkin)──────────────────────────────────────────


def test_feature_has_gherkin_structure():
    f = _gen().feature
    assert "Feature:" in f
    assert "Scenario:" in f
    assert "Given" in f and "When" in f and "Then" in f


def test_feature_contains_business_text():
    f = _gen().feature
    assert "登录并提交订单" in f  # 用例名
    assert "新建草稿订单" in f  # given
    assert "订单列表页" in f  # when
    assert "提交" in f
    assert "待审批" in f  # then(断言)


# ── step_defs(Python 合法 + Playwright 映射)─────────────────


def test_step_defs_is_valid_python():
    ast.parse(_gen().step_defs)  # 语法不合法会抛 SyntaxError


def test_step_defs_binds_feature_via_scenarios():
    assert 'scenarios("TC001.feature")' in _gen().step_defs


def test_click_maps_to_get_by_role():
    code = _gen().step_defs
    assert 'get_by_role("button", name="提交")' in code
    assert ".click()" in code


def test_fill_maps_to_fill_with_data():
    code = _gen().step_defs
    assert '.fill("admin")' in code


def test_navigate_maps_to_goto():
    assert "page.goto(" in _gen().step_defs


# ── 断言 → Playwright expect() ──────────────────────────────


def test_url_contains_maps_to_expect_url():
    code = _gen().step_defs
    assert "to_have_url" in code or ("/list" in code and "page.url" in code)


def test_element_visible_maps_to_to_be_visible():
    assert "to_be_visible()" in _gen().step_defs


def test_text_equals_maps_to_to_have_text():
    code = _gen().step_defs
    assert 'to_have_text("待审批")' in code


# ── conftest ─────────────────────────────────────────────────


def test_conftest_valid_python_with_page_fixture():
    conftest = _gen().conftest
    ast.parse(conftest)
    assert "page" in conftest  # 提供 page fixture / 引用


# ── 落盘 ─────────────────────────────────────────────────────


def test_write_creates_three_files(tmp_path):
    files = _gen().write(tmp_path)
    names = {p.name for p in tmp_path.iterdir()}
    assert "TC001.feature" in names
    assert "conftest.py" in names
    assert any(n.endswith(".py") and "TC001" in n for n in names)  # step_defs
    assert len(files) == 3


# ── 抽象基类契约 ─────────────────────────────────────────────


def test_bddgenerator_is_codegenerator():
    assert isinstance(BDDGenerator(), CodeGenerator)


def test_black_formatted_idempotent():
    # 生成的 step_defs 已 black 格式化:再格式化应无变化
    import black

    code = _gen().step_defs
    assert black.format_str(code, mode=black.Mode()) == code
