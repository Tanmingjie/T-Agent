"""CodeGenerator + BDDGenerator(阶段化重设计后的最小适配)。

阶段化 spec 只产意图,codegen 退化为「可读骨架 + 执行轨迹真实定位器提示」;
定位器解析层(框架无关)保持不变,单测完整保留。
"""

from __future__ import annotations

import ast

from codegen.base import CodeGenerator, GeneratedCode
from codegen.bdd import BDDGenerator
from input.models import ExecutionRecord, Phase, TestSpec


def _spec() -> TestSpec:
    return TestSpec(
        case_id="TC001",
        name="登录并提交订单",
        base_url="https://intranet.example",
        intent="验证能登录并提交订单",
        preconditions=["已新建草稿订单"],
        phases=[
            Phase(steps=["打开订单列表页", "在用户名框输入 admin"], expected="进入订单列表"),
            Phase(steps=["点击提交"], expected="订单状态变为待审批"),
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
    assert "已新建草稿订单" in f  # given ← precondition
    assert "打开订单列表页" in f  # when ← phase step
    assert "点击提交" in f
    assert "订单状态变为待审批" in f  # then ← phase expected


# ── step_defs(Python 合法)───────────────────────────────────


def test_step_defs_is_valid_python():
    ast.parse(_gen().step_defs)


def test_step_defs_binds_feature_via_scenarios():
    assert 'scenarios("TC001.feature")' in _gen().step_defs


def test_step_defs_renders_captured_locator():
    from codegen.locators import Locator, LocatorStrategy

    spec = TestSpec(
        case_id="TC9",
        name="登录",
        base_url="https://x",
        phases=[Phase(steps=["点击登录按钮"], expected="已登录")],
    )
    locators = {
        "点击登录按钮": Locator(
            LocatorStrategy.ROLE, role="button", name="Login", target="点击登录按钮"
        )
    }
    code = BDDGenerator().generate(spec, _record(), locators=locators).step_defs
    ast.parse(code)
    assert 'get_by_role("button", name="Login")' in code


def test_step_defs_unmatched_step_marks_todo():
    spec = TestSpec(
        case_id="TC8",
        name="点击",
        base_url="https://x",
        phases=[Phase(steps=["某个没有捕获定位器的步骤"], expected="ok")],
    )
    code = BDDGenerator().generate(spec, _record(), locators={}).step_defs
    assert "TODO 步骤" in code


# ── conftest / 落盘 / 契约 ───────────────────────────────────


def test_conftest_valid_python_with_page_fixture():
    conftest = _gen().conftest
    ast.parse(conftest)
    assert "page" in conftest


def test_write_creates_three_files(tmp_path):
    files = _gen().write(tmp_path)
    names = {p.name for p in tmp_path.iterdir()}
    assert "TC001.feature" in names
    assert "conftest.py" in names
    assert any(n.endswith(".py") and "TC001" in n for n in names)
    assert len(files) == 3


def test_bddgenerator_is_codegenerator():
    assert isinstance(BDDGenerator(), CodeGenerator)


def test_black_formatted_idempotent():
    import black

    code = _gen().step_defs
    assert black.format_str(code, mode=black.Mode()) == code


# ── 定位器解析层(框架无关,未改动)──────────────────────────────


def test_locator_from_vocab_prefers_role_over_selector():
    from codegen.locators import LocatorStrategy, locator_from_vocab

    loc = locator_from_vocab("登录按钮", {"role": "button", "name": "Login", "selector": ".btn"})
    assert loc.strategy == LocatorStrategy.ROLE
    assert loc.role == "button" and loc.name == "Login"
    loc2 = locator_from_vocab("购物车", {"selector": ".shopping_cart_badge"})
    assert loc2.strategy == LocatorStrategy.CSS and loc2.value == ".shopping_cart_badge"
    loc3 = locator_from_vocab("标题", {"name": "欢迎"})
    assert loc3.strategy == LocatorStrategy.TEXT and loc3.name == "欢迎"
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


def test_locator_from_executed_variants():
    from codegen.locators import LocatorStrategy, locator_from_executed

    r = locator_from_executed("page.getByRole('button', { name: 'Login' })", "登录")
    assert r.strategy == LocatorStrategy.ROLE and r.role == "button" and r.name == "Login"
    assert r.fallback is False

    css = locator_from_executed("page.locator('[data-test=\"username\"]')", "用户名")
    assert css.strategy == LocatorStrategy.CSS and css.value == '[data-test="username"]'

    tid = locator_from_executed("page.getByTestId('user')", "用户名")
    assert tid.strategy == LocatorStrategy.TEST_ID and tid.name == "user"

    ph = locator_from_executed("page.getByPlaceholder('Username')", "用户名")
    assert ph.strategy == LocatorStrategy.PLACEHOLDER and ph.name == "Username"

    assert locator_from_executed("", "x") is None
    assert locator_from_executed("await page.goto('http://x')", "x") is None


def test_locators_from_steps_prefers_executed_over_role_name():
    from codegen.locators import LocatorStrategy, locators_from_steps
    from input.models import ActionStep

    steps = [
        ActionStep(
            step_no=1,
            tool_name="browser_click",
            element_role="button",
            element_name="Add to cart",
            element_selector="page.locator('[data-test=\"add-to-cart-sauce-labs-backpack\"]')",
            step_target="加购背包",
        ),
        ActionStep(
            step_no=2,
            tool_name="browser_click",
            element_role="button",
            element_name="Login",
            step_target="登录按钮",
        ),
    ]
    locs = locators_from_steps(steps)
    assert locs["加购背包"].strategy == LocatorStrategy.CSS
    assert "add-to-cart" in locs["加购背包"].value
    assert locs["登录按钮"].strategy == LocatorStrategy.ROLE
    assert locs["登录按钮"].name == "Login"
