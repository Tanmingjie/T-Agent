"""T-12 单元测试:Context Compact。"""

from __future__ import annotations

from harness.context import (
    ARCHIVED_PREFIX,
    OBS_PREFIX,
    THINK_ARCHIVED_PREFIX,
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


def test_truncate_keeps_interactive_even_when_keywords_miss():
    """内网血泪:步骤是中文、目标元素 a11y 名是英文/自定义组件 → 关键词命不中。
    截断必须仍保留可交互元素行(button / web component),否则模型拿不到 ref → 找不到元素。"""
    rows = "\n".join(f'  - listitem [ref=e{i}]: "data row {i}"' for i in range(2, 60))
    text = (
        "### Page URL: http://intranet/orders\n### Page Title: 订单\n"
        f"{rows}\n"
        '  - sl-button [ref=e120] "Submit":\n'
        "    - button [ref=e121]: Submit"
    )
    # 关键词是中文,命不中英文 Submit / 自定义组件
    out = truncate_snapshot(text, keywords=["点击提交审批按钮", "提交审批"], max_lines=20)
    assert "已按相关度截断" in out  # 确实截断了
    # 可交互目标行仍被保留(标准 button 角色 + 自定义组件 sl-button)
    assert "ref=e121" in out
    assert "ref=e120" in out
    assert len(out.splitlines()) <= 21


def test_truncate_keeps_svg_cursor_pointer_clickables():
    """内网 SVG 工艺图:可点元素 role=generic 但带 [cursor=pointer]+ref。关键词命不中(图形
    无英文/中文名匹配)时,截断仍须保留这些可点工艺元素行,否则模型拿不到 ref → 点不动。"""
    rows = "\n".join(f'  - generic [ref=e{i}]: "noise {i}"' for i in range(2, 60))
    text = (
        "### Page URL: http://intranet/hmi\n### Page Title: 反应釜\n"
        f"{rows}\n"
        "  - generic [ref=e120] [cursor=pointer]: 泵P1\n"
        "  - generic [ref=e121] [cursor=pointer]: 进料阀"
    )
    out = truncate_snapshot(text, keywords=["启动循环泵", "打开进料"], max_lines=20)
    assert "已按相关度截断" in out  # 确实截断了
    assert "ref=e120" in out  # 可点 SVG 泵被保留
    assert "ref=e121" in out  # 可点 SVG 阀被保留


def test_truncate_noop_when_short():
    text = '### Page\n- Page URL: x\n- button "a"'
    assert truncate_snapshot(text, [], max_lines=40) == text


def test_truncate_caps_single_line_megablob():
    """#3 回归:压缩 JS/巨型 JSON 整坨一行(行数=1 绕过行截断)→ 必须被硬字符上限砍短。

    根因:browser_network_request 拉回 MB 级单行响应体 → 单条观察撑爆上下文窗口致崩溃。
    """
    blob = "import{" + "x" * 200000 + "}"  # 单行 20 万字符
    out = truncate_snapshot(blob, ["密码"], max_lines=40, max_chars=12000)
    assert len(out) < len(blob)
    assert len(out) <= 12000 + 80  # 硬上限 + 截断标记余量
    assert "单行截断" in out or "观察总长截断" in out


def test_truncate_total_cap_across_many_lines():
    """多行但总量超标也要被总字符上限兜住(末位安全阀)。"""
    text = "### Page\n- Page URL: x\n" + "\n".join(
        f"- text: 行{i} " + "y" * 200 for i in range(200)
    )
    out = truncate_snapshot(text, [], max_lines=300, max_chars=5000)
    assert len(out) <= 5000 + 80
    assert "观察总长截断" in out


def test_compactor_hard_caps_megablob_observation():
    """端到端:ContextCompactor 对保留的近期观察里的单行 megablob 施加硬上限。"""
    blob = "x" * 500000  # 半 MB 单行
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "开始执行测试"},
        {"role": "user", "content": f"{OBS_PREFIX} {blob}"},
    ]
    comp = ContextCompactor(keep_recent_observations=2, hard_char_cap=12000)
    comp.compact_inplace(msgs, keywords=[])
    assert len(msgs[2]["content"]) <= 12000 + 120


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


def test_old_assistant_narration_archived():
    """B:旧 assistant 叙述折叠为一行、最近 N 条保留原文(治 narration churn 的叙述无限累积)。"""
    long = "这是一大段反复叙述的思考内容。" * 20  # 单行、足够长,折叠后更短
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": long + "A"},
        {"role": "user", "content": f"{OBS_PREFIX} obs"},
        {"role": "assistant", "content": long + "B"},
        {"role": "assistant", "content": long + "C"},
        {"role": "assistant", "content": long + "D"},
        {"role": "assistant", "content": long + "E"},
    ]
    saved = ContextCompactor(keep_recent_assistant=2).compact_inplace(msgs, [])
    assert saved > 0
    # assistant 在 idx 2,4,5,6,7;保留最近 2(6、7),更早 3 条(2、4、5)折叠
    assert msgs[2]["content"].startswith(THINK_ARCHIVED_PREFIX)
    assert msgs[4]["content"].startswith(THINK_ARCHIVED_PREFIX)
    assert msgs[5]["content"].startswith(THINK_ARCHIVED_PREFIX)
    assert msgs[6]["content"] == long + "D"  # 最近 2 条原文保留
    assert msgs[7]["content"] == long + "E"


def test_short_assistant_not_grown_by_archive():
    """折叠不应把短 assistant 改得更长(加前缀反而变长)→ 短叙述原样保留。"""
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "点击"},
        {"role": "assistant", "content": "再点击"},
        {"role": "assistant", "content": "继续"},
        {"role": "assistant", "content": "完成"},
    ]
    ContextCompactor(keep_recent_assistant=1).compact_inplace(msgs, [])
    assert msgs[2]["content"] == "点击"  # 短串折叠后更长 → 跳过,保留原文
