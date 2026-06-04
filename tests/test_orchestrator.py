"""T-18 单元测试:Orchestrator(Suite 调度 + 用例间隔离)。

TDD:先定义调度/隔离/汇总行为,再实现 harness/orchestrator.py。
用 fake agent(实现 async run(case, ctx))驱动,不连真实执行。
"""

from __future__ import annotations

from harness.hooks import AFTER_SUITE, BEFORE_SUITE, HookError, HookManager
from harness.orchestrator import Orchestrator, SuiteResult
from input.models import ExecutionRecord, Suite, TestCase


def _cases(*ids):
    return [TestCase(id=i, name=f"用例{i}", steps=["x"]) for i in ids]


class _FakeAgent:
    """记录调用顺序;按 case_id 决定通过/失败/抛异常。"""

    def __init__(self, fail_ids=None, raise_ids=None):
        self.calls = []
        self.contexts = []
        self.fail_ids = set(fail_ids or [])
        self.raise_ids = set(raise_ids or [])

    async def run(self, case, spec=None, ctx=None, step_callback=None, run_id=None):
        self.calls.append(case.id)
        self.contexts.append(ctx)
        if case.id in self.raise_ids:
            raise RuntimeError(f"用例 {case.id} 执行炸了")
        return ExecutionRecord(
            exec_id=f"e{case.id}",
            case_id=case.id,
            passed=case.id not in self.fail_ids,
            start_time=0.0,
        )


# ── 串行调度 + 汇总 ──────────────────────────────────────────


async def test_runs_all_cases_serially_in_order():
    agent = _FakeAgent()
    orch = Orchestrator(agent)
    result = await orch.run_suite(_cases("A", "B", "C"))
    assert isinstance(result, SuiteResult)
    assert agent.calls == ["A", "B", "C"]  # 顺序、串行
    assert [r.case_id for r in result.records] == ["A", "B", "C"]


async def test_aggregates_pass_fail_counts():
    agent = _FakeAgent(fail_ids=["B"])
    result = await Orchestrator(agent).run_suite(_cases("A", "B", "C"))
    assert result.total == 3
    assert result.passed_count == 2
    assert result.failed_count == 1


# ── 用例间隔离 ───────────────────────────────────────────────


async def test_case_exception_isolated_others_continue():
    agent = _FakeAgent(raise_ids=["B"])
    result = await Orchestrator(agent).run_suite(_cases("A", "B", "C"))
    # B 抛异常但 A、C 仍执行
    assert agent.calls == ["A", "B", "C"]
    assert result.total == 3
    # B 记为 FAIL 记录(不丢)
    rec_b = [r for r in result.records if r.case_id == "B"][0]
    assert rec_b.passed is False
    assert "炸了" in rec_b.final_result


async def test_each_case_gets_independent_context():
    agent = _FakeAgent()
    await Orchestrator(agent).run_suite(_cases("A", "B"))
    a_ctx, b_ctx = agent.contexts
    assert a_ctx is not b_ctx  # 各自独立 ExecutionContext
    assert a_ctx.case.id == "A" and b_ctx.case.id == "B"


# ── Suite 级 Hooks ───────────────────────────────────────────


async def test_before_after_suite_hooks_run_once():
    events = []
    mgr = HookManager()
    mgr.register(BEFORE_SUITE, lambda ctx: events.append("before"))
    mgr.register(AFTER_SUITE, lambda ctx: events.append("after"))

    agent = _FakeAgent()
    await Orchestrator(agent, hooks=mgr).run_suite(
        _cases("A", "B"), suite=Suite(id="S", name="s", base_url="http://x")
    )
    assert events == ["before", "after"]


async def test_before_suite_failure_aborts_suite():
    mgr = HookManager()

    def boom(ctx):
        raise HookError("环境没起来")

    mgr.register(BEFORE_SUITE, boom)
    agent = _FakeAgent()
    result = await Orchestrator(agent, hooks=mgr).run_suite(_cases("A", "B"))
    assert agent.calls == []  # 一个用例都没跑
    assert result.aborted is True
    assert "环境没起来" in result.error
