"""T-12 单元测试:Context Compact。"""

from __future__ import annotations

from harness.context import (
    ARCHIVED_PREFIX,
    OBS_PREFIX,
    ContextCompactor,
    truncate_snapshot,
)


def _big_snapshot(n_rows: int) -> str:
    rows = "\n".join(f'  - row "数据行{i}" [ref=e{i}]' for i in range(n_rows))
    return (
        "### Page\n- Page URL: http://x/list\n- Page Title: 列表\n### Snapshot\n```yaml\n"
        f'- button "提交" [ref=e1]\n{rows}\n- text: 待审批\n```'
    )


# ── truncate_snapshot(L2) ────────────────────────────────────


def test_truncate_keeps_head_and_keywords():
    text = _big_snapshot(100)
    out = truncate_snapshot(text, keywords=["提交", "待审批"], max_lines=20)
    assert "Page URL" in out  # 头部保留
    assert "提交" in out  # 命中关键词保留
    assert "待审批" in out
    assert "已按相关度截断" in out
    assert len(out.splitlines()) <= 21


def test_truncate_noop_when_short():
    text = '### Page\n- Page URL: x\n- button "a"'
    assert truncate_snapshot(text, [], max_lines=40) == text


# ── compact_inplace ──────────────────────────────────────────


def _msgs():
    return [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "开始执行测试"},
        {"role": "assistant", "content": "点击"},
        {"role": "user", "content": f"{OBS_PREFIX} " + _big_snapshot(80)},
        {"role": "assistant", "content": "再点击"},
        {"role": "user", "content": f"{OBS_PREFIX} " + _big_snapshot(80)},
        {"role": "assistant", "content": "继续"},
        {"role": "user", "content": f"{OBS_PREFIX} " + _big_snapshot(80)},
    ]


def test_old_observations_archived_recent_kept():
    msgs = _msgs()
    comp = ContextCompactor(keep_recent_observations=1, max_obs_chars=500)
    saved = comp.compact_inplace(msgs, keywords=["提交"])
    assert saved > 0
    # 前两条观察(索引 3、5)折叠成一行归档
    assert msgs[3]["content"].startswith(ARCHIVED_PREFIX)
    assert "\n" not in msgs[3]["content"]
    assert msgs[5]["content"].startswith(ARCHIVED_PREFIX)
    # 最近一条观察(索引 7)保留但被截断(仍是 [观察] 开头)
    assert msgs[7]["content"].startswith(OBS_PREFIX)
    assert "已按相关度截断" in msgs[7]["content"]


def test_system_and_task_never_touched():
    msgs = _msgs()
    ContextCompactor(protect_head=2).compact_inplace(msgs, [])
    assert msgs[0]["content"] == "SYS"
    assert msgs[1]["content"] == "开始执行测试"
    # assistant 消息不受影响
    assert msgs[2]["content"] == "点击"


def test_idempotent_archive():
    msgs = _msgs()
    comp = ContextCompactor(keep_recent_observations=1)
    comp.compact_inplace(msgs, [])
    archived_before = msgs[3]["content"]
    # 再压一次,已归档的不应被重复加前缀/再缩短
    comp.compact_inplace(msgs, [])
    assert msgs[3]["content"] == archived_before


def test_no_observations_returns_zero():
    msgs = [{"role": "system", "content": "x"}, {"role": "user", "content": "task"}]
    assert ContextCompactor().compact_inplace(msgs, []) == 0
