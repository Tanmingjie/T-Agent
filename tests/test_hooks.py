"""T-13 单元测试:Hook 生命周期 + 与 Agent 集成。"""

from __future__ import annotations

import pytest

from harness.hooks import (
    AFTER_CASE,
    BEFORE_CASE,
    ON_FAILURE,
    ON_HEAL,
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
    from tests.test_agent import (
        SNAPSHOT_OK,
        _case,
        _FakeMCP,
        _PhaseJudgeLLM,
        _react_one_step,
        _spec,
    )

    mgr = HookManager()
    cleaned = []
    mgr.register(AFTER_CASE, lambda ctx: cleaned.append(ctx.get("passed")))

    # 带 expected + 判 PASS 才是成功路径(G1 后空 expected 会 PHASE_FAILED)
    llm = _PhaseJudgeLLM(_react_one_step(), ["PASS"])
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK), hooks=mgr)
    await agent.run(_case(), spec=_spec(expected="出现待审批"))
    assert cleaned == [True]  # after_case 跑了,且能读到 passed


async def test_agent_fires_on_heal_when_action_healed(monkeypatch):
    """发生**操作侧自愈**(react_loop 工具报错重定位重试)时,on_heal 触发且 ctx 带计数。

    H1:阶段化下断言裁决走 _check_llm_judge 直连、不过 healable 装饰 → 断言侧自愈不存在,
    on_heal 只由操作侧自愈触发。这里 wrap ReActLoop.run:正常循环跑完(阶段判 PASS)后,
    给首个 action_step 注入一条 heal_attempt,模拟执行期发生过操作侧自愈。
    """
    from harness import agent as agent_mod
    from harness.agent import TestCaseAgent
    from tests.test_agent import (
        SNAPSHOT_OK,
        _case,
        _FakeMCP,
        _PhaseJudgeLLM,
        _react_one_step,
        _spec,
    )

    orig_run = agent_mod.ReActLoop.run

    async def run_with_heal(self):
        result = await orig_run(self)
        if result.action_steps:
            result.action_steps[0].heal_attempts.append({"strategy": "P1", "note": "角色重定位"})
        return result

    monkeypatch.setattr(agent_mod.ReActLoop, "run", run_with_heal)

    mgr = HookManager()
    seen: list = []
    mgr.register(ON_HEAL, lambda ctx: seen.append(ctx.get("heal_count")))

    llm = _PhaseJudgeLLM(_react_one_step(), ["PASS"])
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK), hooks=mgr)
    record = await agent.run(_case(), spec=_spec(expected="出现待审批"))

    assert seen == [1]  # on_heal 触发,heal_count 来自 record 单一来源(操作侧累加)
    assert record.passed is True
    assert record.heal_count == 1  # H2:口径单一,record.heal_count 即操作侧自愈数


async def test_agent_no_on_heal_without_healing():
    """无自愈发生时不应触发 on_heal(避免噪声)。"""
    from harness.agent import TestCaseAgent
    from tests.test_agent import (
        SNAPSHOT_OK,
        _case,
        _FakeMCP,
        _PhaseJudgeLLM,
        _react_one_step,
        _spec,
    )

    mgr = HookManager()
    seen: list = []
    mgr.register(ON_HEAL, lambda ctx: seen.append(1))

    llm = _PhaseJudgeLLM(_react_one_step(), ["PASS"])
    agent = TestCaseAgent(llm, _FakeMCP(SNAPSHOT_OK), hooks=mgr)
    await agent.run(_case(), spec=_spec(expected="出现待审批"))

    assert seen == []  # 无自愈 → on_heal 不触发
