"""T-17 单元测试:Permission 拦截(高危词 + prod 环境锁)。

TDD:先定义期望行为,再实现 harness/permission.py。
"""

from __future__ import annotations

from harness.permission import PermissionChecker, PermissionRequest

# ── evaluate:判定是否需要审批 ───────────────────────────────


def test_flags_dangerous_word_in_arguments():
    chk = PermissionChecker()
    req = chk.evaluate(
        "browser_click", {"element": "删除订单按钮", "ref": "e9"}, url="http://test/x"
    )
    assert req is not None
    assert "删除" in req.reason


def test_flags_dangerous_word_in_tool_name():
    chk = PermissionChecker()
    req = chk.evaluate("submit_form", {"data": "x"}, url="http://test/x")
    assert req is not None


def test_flags_prod_url():
    chk = PermissionChecker()
    req = chk.evaluate("browser_click", {"element": "查看"}, url="https://prod.intranet/orders")
    assert req is not None
    assert "prod" in req.reason.lower()


def test_safe_action_no_approval():
    chk = PermissionChecker()
    assert (
        chk.evaluate("browser_click", {"element": "查看详情"}, url="https://test.intranet/x")
        is None
    )


def test_custom_dangerous_words_and_prod_markers():
    chk = PermissionChecker(dangerous_words=["销毁"], prod_markers=["生产环境"])
    assert chk.evaluate("t", {"x": "销毁数据"}, url="http://x") is not None
    assert chk.evaluate("t", {"x": "查看"}, url="http://生产环境/x") is not None
    assert chk.evaluate("t", {"x": "提交"}, url="http://x") is None  # 默认词被覆盖,提交不再高危


# ── check:审批流 ────────────────────────────────────────────


async def test_trust_mode_allows_everything():
    chk = PermissionChecker(trust_mode=True)
    allowed = await chk.check("browser_click", {"element": "删除"}, url="https://prod.x")
    assert allowed is True


async def test_safe_action_allowed_without_approver():
    chk = PermissionChecker()  # 无 approver
    allowed = await chk.check("browser_click", {"element": "查看"}, url="https://test.x")
    assert allowed is True  # 安全操作无需审批


async def test_dangerous_action_calls_approver():
    seen = []

    async def approver(req: PermissionRequest) -> bool:
        seen.append(req)
        return True  # 批准

    chk = PermissionChecker(approver=approver)
    allowed = await chk.check("browser_click", {"element": "提交申请"}, url="http://test/x")
    assert allowed is True
    assert len(seen) == 1
    assert seen[0].tool_name == "browser_click"


async def test_approver_can_reject():
    async def approver(req):
        return False

    chk = PermissionChecker(approver=approver)
    allowed = await chk.check("browser_click", {"element": "支付"}, url="http://test/x")
    assert allowed is False


async def test_no_approver_default_denies_dangerous():
    # 需审批但没人能批 → 默认拒绝(最安全),不放行高危操作
    chk = PermissionChecker()
    allowed = await chk.check("browser_click", {"element": "确认删除"}, url="http://test/x")
    assert allowed is False


async def test_sync_approver_supported():
    chk = PermissionChecker(approver=lambda req: True)
    assert await chk.check("t", {"x": "提交"}, url="http://test/x") is True


# ── 与 Agent 集成:被拒的工具不执行 ─────────────────────────


async def test_agent_blocks_denied_tool_and_does_not_execute():
    from harness.agent import TestCaseAgent
    from tests.test_agent import SNAPSHOT_OK, _case, _FakeMCP, _resp, _ScriptedLLM, _spec

    chk = PermissionChecker(approver=lambda req: False)  # 一律拒绝
    mcp = _FakeMCP(SNAPSHOT_OK)
    llm = _ScriptedLLM(
        [
            _resp(
                content="点删除", calls=[("browser_click", {"element": "删除按钮", "ref": "e3"})]
            ),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, mcp, permission=chk)
    record = await agent.run(_case(), spec=_spec())

    # 高危 browser_click 被拦截 → MCP 从未真正执行它
    assert ("browser_click", {"element": "删除按钮", "ref": "e3"}) not in mcp.tool_calls
    # 被拒作为观察记录在步骤里
    assert any("[权限被拒]" in s.tool_result for s in record.steps)


async def test_agent_trust_mode_executes():
    from harness.agent import TestCaseAgent
    from tests.test_agent import SNAPSHOT_OK, _case, _FakeMCP, _resp, _ScriptedLLM, _spec

    chk = PermissionChecker(trust_mode=True)
    mcp = _FakeMCP(SNAPSHOT_OK)
    llm = _ScriptedLLM(
        [
            _resp(
                content="点删除", calls=[("browser_click", {"element": "删除按钮", "ref": "e3"})]
            ),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, mcp, permission=chk)
    await agent.run(_case(), spec=_spec())
    # trust_mode 放行 → browser_click 真正执行;dispatch 前 _normalize_ref_target 把 ref 同步进 target
    assert ("browser_click", {"element": "删除按钮", "ref": "e3", "target": "e3"}) in mcp.tool_calls
