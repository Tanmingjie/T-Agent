"""T-13 单元测试:Hook 生命周期 + 与 Agent 集成。"""

from __future__ import annotations

import pytest

from harness.hooks import (
    AFTER_CASE,
    BEFORE_CASE,
    ON_FAILURE,
    ExecutionContext,
    HookError,
    HookManager,
)

# ── HookManager 基础 ─────────────────────────────────────────


async def test_hooks_run_in_registration_order():
    mgr = HookManager()
    order = []
    mgr.register(BEFORE_CASE, lambda ctx: order.append("a"))
    mgr.register(BEFORE_CASE, lambda ctx: order.append("b"))
    res = await mgr.run(BEFORE_CASE, ExecutionContext())
    assert res.ok and res.ran == 2
    assert order == ["a", "b"]


async def test_async_and_sync_hooks_both_supported():
    mgr = HookManager()
    hit = []

    async def ahook(ctx):
        hit.append("async")

    def shook(ctx):
        hit.append("sync")

    mgr.register(BEFORE_CASE, ahook)
    mgr.register(BEFORE_CASE, shook)
    await mgr.run(BEFORE_CASE, ExecutionContext())
    assert hit == ["async", "sync"]


async def test_hook_failure_stops_queue():
    mgr = HookManager()
    ran = []
    mgr.register(BEFORE_CASE, lambda ctx: ran.append(1))

    def boom(ctx):
        raise HookError("登录失败")

    mgr.register(BEFORE_CASE, boom)
    mgr.register(BEFORE_CASE, lambda ctx: ran.append(3))  # 不应执行

    res = await mgr.run(BEFORE_CASE, ExecutionContext())
    assert not res.ok
    assert res.error == "登录失败"
    assert res.failed_hook == "boom"
    assert res.ran == 1
    assert ran == [1]  # 第三个没跑


async def test_generic_exception_also_fails():
    mgr = HookManager()
    mgr.register(AFTER_CASE, lambda ctx: 1 / 0)
    res = await mgr.run(AFTER_CASE, ExecutionContext())
    assert not res.ok
    assert "ZeroDivisionError" in res.error


async def test_context_shared_across_hooks():
    mgr = HookManager()
    mgr.register(BEFORE_CASE, lambda ctx: ctx.set("token", "abc"))
    mgr.register(BEFORE_CASE, lambda ctx: ctx.set("token2", ctx.get("token") + "-2"))
    ctx = ExecutionContext()
    await mgr.run(BEFORE_CASE, ctx)
    assert ctx.get("token2") == "abc-2"


def test_register_unknown_event_raises():
    with pytest.raises(ValueError):
        HookManager().register("not_an_event", lambda ctx: None)


async def test_empty_event_ok():
    res = await HookManager().run(AFTER_CASE, ExecutionContext())
    assert res.ok and res.ran == 0


def test_hookresult_bool():
    from harness.hooks import HookResult

    assert bool(HookResult(event=BEFORE_CASE, ok=True))
    assert not bool(HookResult(event=BEFORE_CASE, ok=False))


# ── 与 Agent 集成 ────────────────────────────────────────────


async def test_agent_before_case_failure_skips_execution():
    from harness.agent import TestCaseAgent
    from tests.test_agent import SNAPSHOT_OK, _case, _FakeMCP, _resp, _ScriptedLLM, _spec

    mgr = HookManager()

    def fail_login(ctx):
        raise HookError("Cookie 失效且重登失败")

    mgr.register(BEFORE_CASE, fail_login)

    # LLM 不该被调用(用例没进 Agent)
    llm = _ScriptedLLM([_resp(content="不该出现")])
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK), hooks=mgr)
    record = await agent.run(_case(), spec=_spec())

    assert record.passed is False
    assert "before_case 失败" in record.final_result
    assert record.steps == []  # 没执行任何步骤


async def test_agent_after_case_runs_on_success():
    from harness.agent import TestCaseAgent
    from tests.test_agent import SNAPSHOT_OK, _case, _FakeMCP, _resp, _ScriptedLLM, _spec

    mgr = HookManager()
    cleaned = []
    mgr.register(AFTER_CASE, lambda ctx: cleaned.append(ctx.get("passed")))

    llm = _ScriptedLLM(
        [
            _resp(content="点", calls=[("browser_click", {"ref": "e3"})]),
            _resp(content="完成", calls=[("mark_step_done", {"step_no": 1})]),
            _resp(content="TEST_RESULT: PASS"),
        ]
    )
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK), hooks=mgr)
    await agent.run(_case(), spec=_spec())
    assert cleaned == [True]  # after_case 跑了,且能读到 passed
