"""T-10 单元测试:CLI 纯逻辑(用例选择 / spec 打印)。"""

from __future__ import annotations

import pytest

from cli.run_case import _load_vocab_resolver, _print_spec, _select_case
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


# ── --vocab 加载与校验 ────────────────────────────────────────


def test_load_vocab_resolver_none_path():
    assert _load_vocab_resolver(None) is None


async def test_load_vocab_resolver_valid(tmp_path):
    f = tmp_path / "v.json"
    f.write_text('{"购物车图标": {"selector": ".badge"}}', encoding="utf-8")
    resolver = _load_vocab_resolver(str(f))
    assert await resolver.resolve("购物车图标") == {"selector": ".badge"}


def test_load_vocab_resolver_rejects_string_entry(tmp_path):
    # 词条值是字符串(非 dict)→ 应在加载时报错,而非运行到 entry.get(...) 才崩
    f = tmp_path / "bad.json"
    f.write_text('{"购物车图标": "badge"}', encoding="utf-8")
    with pytest.raises(SystemExit, match="必须是对象"):
        _load_vocab_resolver(str(f))


def test_load_vocab_resolver_rejects_non_object_root(tmp_path):
    f = tmp_path / "arr.json"
    f.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SystemExit):
        _load_vocab_resolver(str(f))
