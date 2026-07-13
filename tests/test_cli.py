"""CLI 纯逻辑测试(用例选择 / spec 打印)。"""

from __future__ import annotations

import pytest

from cli.run_case import _print_spec, _select_case
from input.models import Phase, TestCase, TestSpec


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
        intent="验证提交订单",
        preconditions=["已新建订单"],
        phases=[Phase(steps=["点击提交"], expected="状态变为待审批")],
    )
    _print_spec(spec)
    out = capsys.readouterr().out
    assert "提交订单" in out
    assert "已新建订单" in out
    assert "点击提交" in out
    assert "状态变为待审批" in out
