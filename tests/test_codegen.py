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


def test_click_maps_to_get_by_text():
    code = _gen().step_defs
    assert 'get_by_text("提交").first' in code
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


# ── 定位器解析层(框架无关)──────────────────────────────────────


def test_locator_from_vocab_prefers_role_over_selector():
    from codegen.locators import LocatorStrategy, locator_from_vocab

    # role+name 与 selector 同时存在 → 取更稳的 role+name
    loc = locator_from_vocab("登录按钮", {"role": "button", "name": "Login", "selector": ".btn"})
    assert loc.strategy == LocatorStrategy.ROLE
    assert loc.role == "button" and loc.name == "Login"
    # 仅 selector → CSS
    loc2 = locator_from_vocab("购物车", {"selector": ".shopping_cart_badge"})
    assert loc2.strategy == LocatorStrategy.CSS and loc2.value == ".shopping_cart_badge"
    # 仅 name → 文本
    loc3 = locator_from_vocab("标题", {"name": "欢迎"})
    assert loc3.strategy == LocatorStrategy.TEXT and loc3.name == "欢迎"
    # 空 → None
    assert locator_from_vocab("x", {}) is None
    assert locator_from_vocab("x", None) is None


async def test_resolve_locators_builds_map():
    from codegen.locators import LocatorStrategy, resolve_locators

    class _Resolver:
        async def resolve(self, target, *, url="", title=""):
            return {"role": "button", "name": "Login"} if target == "登录按钮" else None

    out = await resolve_locators(["登录按钮", "未知目标"], _Resolver())
    assert set(out) == {"登录按钮"}
    assert out["登录按钮"].strategy == LocatorStrategy.ROLE


async def test_resolve_locators_no_resolver_returns_empty():
    from codegen.locators import resolve_locators

    assert await resolve_locators(["a", "b"], None) == {}


def test_bdd_renders_role_and_css_locators_from_map():
    from codegen.locators import Locator, LocatorStrategy

    spec = TestSpec(
        case_id="TC9",
        name="登录",
        base_url="https://x",
        steps=[SpecStep(action="click", target="登录按钮")],
        assertions=[Assertion(type="text_equals", target="购物车角标", expected="1")],
    )
    locators = {
        "登录按钮": Locator(LocatorStrategy.ROLE, role="button", name="Login", target="登录按钮"),
        "购物车角标": Locator(
            LocatorStrategy.CSS, value=".shopping_cart_badge", target="购物车角标"
        ),
    }
    code = BDDGenerator().generate(spec, _record(), locators=locators).step_defs
    ast.parse(code)
    assert 'get_by_role("button", name="Login")' in code
    assert 'page.locator(".shopping_cart_badge")' in code
    # 命中词汇表的目标不应再带兜底提醒
    assert "TODO 定位器兜底" not in code


def test_bdd_unresolved_target_marks_review():
    # 未命中词汇表 → 文本兜底 + 提醒注释(供人工核对)
    spec = TestSpec(
        case_id="TC8",
        name="点击",
        base_url="https://x",
        steps=[SpecStep(action="click", target="某个没录入词汇表的按钮")],
    )
    code = BDDGenerator().generate(spec, _record(), locators={}).step_defs
    assert "TODO 定位器兜底" in code
