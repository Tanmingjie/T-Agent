from __future__ import annotations

import pytest

from harness.llm import LLMClient, LLMResponse
from harness.midscene_agent import MidsceneCaseAgent
from harness.visual_executor import VisualExecutionResult, VisualPhaseResult
from input.models import Phase, TestCase, TestSpec


class _NoopLLM(LLMClient):
    async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
        return LLMResponse(content="{}")


class _FakeVisualExecutor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def run_case(self, *, run_id, case, spec, execution_context=""):
        self.calls.append((run_id, case.id, len(spec.phases), execution_context))
        return self.result


def _case() -> TestCase:
    return TestCase(id="tc1", name="C1", base_url="https://x", suite_id="sx")


def _spec() -> TestSpec:
    return TestSpec(
        case_id="tc1",
        name="C1",
        base_url="https://x",
        phases=[
            Phase(steps=["点击阀门"], expected="阀门变红"),
            Phase(steps=["读取液位"], expected="液位显示 80%"),
        ],
    )


@pytest.mark.asyncio
async def test_midscene_agent_maps_visual_result_to_execution_record():
    visual = _FakeVisualExecutor(
        VisualExecutionResult(
            passed=True,
            stop_reason="completed",
            phase_results=[
                VisualPhaseResult(
                    phase_index=0, status="pass", expected="阀门变红", evidence="red"
                ),
                VisualPhaseResult(
                    phase_index=1, status="pass", expected="液位显示 80%", evidence="80%"
                ),
            ],
            artifacts={"report": "r.html"},
        )
    )
    events = []

    async def cb(ev, data):
        events.append(ev)

    agent = MidsceneCaseAgent(llm=_NoopLLM(), visual_executor=visual, translation_knowledge="规则A")
    record = await agent.run(_case(), spec=_spec(), step_callback=cb, run_id="run1")

    assert record.passed is True
    assert record.spec is not None
    assert [a["status"] for a in record.case_assertions] == ["pass", "pass"]
    assert record.metrics["execution_kernel"] == "midscene"
    assert record.metrics["midscene"]["artifacts"]["report"] == "r.html"
    assert visual.calls == [("run1", "tc1", 2, "规则A")]
    assert "spec_ready" in events and "step_change" in events


@pytest.mark.asyncio
async def test_midscene_agent_fills_missing_phase_as_fail():
    visual = _FakeVisualExecutor(
        VisualExecutionResult(
            passed=False,
            stop_reason="phase_failed",
            phase_results=[VisualPhaseResult(phase_index=0, status="pass", expected="阀门变红")],
        )
    )
    agent = MidsceneCaseAgent(llm=_NoopLLM(), visual_executor=visual)

    record = await agent.run(_case(), spec=_spec(), run_id="run1")

    assert record.passed is False
    assert [a["phase_index"] for a in record.case_assertions] == [0, 1]
    assert record.case_assertions[1]["status"] == "fail"
    assert "未触达" in record.case_assertions[1]["reason"]


@pytest.mark.asyncio
async def test_midscene_agent_surfaces_runner_startup_error_as_step_and_assertion_reason():
    visual = _FakeVisualExecutor(
        VisualExecutionResult(
            passed=False,
            stop_reason="runner_exception",
            error="Missing Midscene model config: MIDSCENE_MODEL_NAME",
            phase_results=[],
        )
    )
    agent = MidsceneCaseAgent(llm=_NoopLLM(), visual_executor=visual)

    record = await agent.run(_case(), spec=_spec(), run_id="run1")

    assert record.passed is False
    assert record.steps[0].tool_name == "midscene_runner"
    assert "Missing Midscene model config" in record.steps[0].tool_result
    assert "Missing Midscene model config" in record.case_assertions[0]["reason"]
