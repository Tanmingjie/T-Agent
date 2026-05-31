"""T-10 单元测试:CLI 纯逻辑(用例选择 / spec 打印)。"""

from __future__ import annotations

import pytest

from cli.run_case import _print_spec, _select_case
from input.models import Assertion, SpecStep, TestCase, TestSpec


def _cases():
    return [
        TestCase(id="TC001", name="用例一", steps=["a"]),
        TestCase(id="TC002", name="用例二", steps=["b"]),
    ]


def test_select_by_id():
    assert _select_case(_cases(), "TC002").name == "用例二"


def test_select_default_first():
    assert _select_case(_cases(), None).id == "TC001"


def test_select_missing_raises():
    with pytest.raises(SystemExit):
        _select_case(_cases(), "TC999")


def test_select_empty_raises():
    with pytest.raises(SystemExit):
        _select_case([], None)


def test_print_spec_runs(capsys):
    spec = TestSpec(
        case_id="TC001",
        name="提交订单",
        base_url="http://x",
        given=[SpecStep(action="execute", target="新建订单")],
        steps=[SpecStep(action="click", target="提交", data="now")],
        assertions=[Assertion(type="url_contains", target="URL", expected="/list")],
    )
    _print_spec(spec)
    out = capsys.readouterr().out
    assert "提交订单" in out
    assert "新建订单" in out
    assert "url_contains" in out
