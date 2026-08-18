from __future__ import annotations

import pytest

from harness.execution_memory import case_fingerprint, learn_from_success
from harness.llm import LLMResponse
from input.models import ActionStep, ExecutionRecord, Phase, Suite, TestCase, TestSpec
from storage.db import Store


class FakeLLM:
    async def chat(self, messages, tools=None, **kwargs):
        return LLMResponse(content="- 进入 LLL 腔室时点击左下角 LLL 区域\n- 状态切换后等待 5 秒")


@pytest.mark.asyncio
async def test_case_fingerprint_changes_with_expected():
    case = TestCase(id="c1", name="C", steps=["点 A"], expected=["出现 B"])
    h1 = case_fingerprint(case, project_id="p", version_id="v", suite_id="s", base_url="u")
    changed = case.model_copy(update={"expected": ["出现 C"]})
    h2 = case_fingerprint(changed, project_id="p", version_id="v", suite_id="s", base_url="u")
    assert h1 != h2


@pytest.mark.asyncio
async def test_learn_from_success_persists_memory(tmp_path):
    store = Store(f"sqlite+aiosqlite:///{tmp_path}/m.db")
    await store.init()
    try:
        suite = Suite(id="s1", name="S", base_url="https://x", project_id="p1", version_id="v1")
        case = TestCase(id="c1", name="C", steps=["点"], expected=["成"], suite_id="s1")
        spec = TestSpec(
            case_id="c1",
            name="C",
            base_url="https://x",
            phases=[Phase(steps=["点"], expected="成")],
        )
        record = ExecutionRecord(
            exec_id="e1",
            run_id="r1",
            case_id="c1",
            passed=True,
            spec=spec,
            steps=[ActionStep(step_no=1, tool_name="midscene_aiAct", intent="点")],
            case_assertions=[{"status": "pass", "expected": "成"}],
        )
        memory = await learn_from_success(
            store=store, llm=FakeLLM(), suite=suite, case=case, record=record
        )
        assert memory is not None
        got = await store.get_case_memory(memory.id)
        assert got is not None
        assert got.spec.phases[0].expected == "成"
        assert "LLL" in got.experience
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_learn_from_failure_is_ignored(tmp_path):
    store = Store(f"sqlite+aiosqlite:///{tmp_path}/m.db")
    await store.init()
    try:
        suite = Suite(id="s1", name="S", base_url="https://x")
        case = TestCase(id="c1", name="C")
        record = ExecutionRecord(exec_id="e1", case_id="c1", passed=False)
        assert (
            await learn_from_success(
                store=store, llm=FakeLLM(), suite=suite, case=case, record=record
            )
            is None
        )
    finally:
        await store.close()
