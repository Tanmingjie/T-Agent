from __future__ import annotations

import json
import sys

import pytest

from harness.visual_executor import VisualExecutor
from input.models import Phase, TestCase, TestSpec


def _case() -> TestCase:
    return TestCase(id="tc1", name="C1", base_url="https://x", suite_id="sx")


def _spec() -> TestSpec:
    return TestSpec(
        case_id="tc1",
        name="C1",
        base_url="https://x",
        phases=[Phase(steps=["点击阀门"], expected="阀门变红")],
    )


@pytest.mark.asyncio
async def test_visual_executor_returns_disabled_when_not_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDSCENE_ENABLED", "0")
    ex = VisualExecutor(
        command=[sys.executable, "-c", "print('should not run')"], artifact_root=tmp_path
    )

    result = await ex.run_case(run_id="r1", case=_case(), spec=_spec())

    assert result.passed is False
    assert result.stop_reason == "midscene_disabled"


@pytest.mark.asyncio
async def test_visual_executor_enabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MIDSCENE_ENABLED", raising=False)
    payload = {
        "passed": True,
        "stop_reason": "completed",
        "phase_results": [{"phase_index": 0, "status": "pass", "expected": "阀门变红"}],
    }
    ex = VisualExecutor(
        command=[sys.executable, "-c", f"import json; print({json.dumps(json.dumps(payload))})"],
        artifact_root=tmp_path,
    )

    result = await ex.run_case(run_id="r1", case=_case(), spec=_spec())

    assert result.passed is True


@pytest.mark.asyncio
async def test_visual_executor_parses_runner_json(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDSCENE_ENABLED", "1")
    payload = {
        "passed": True,
        "stop_reason": "completed",
        "phase_results": [{"phase_index": 0, "status": "pass", "expected": "阀门变红"}],
    }
    ex = VisualExecutor(
        command=[sys.executable, "-c", f"import json; print({json.dumps(json.dumps(payload))})"],
        artifact_root=tmp_path,
    )

    result = await ex.run_case(run_id="r1", case=_case(), spec=_spec())

    assert result.passed is True
    assert result.phase_results[0].status == "pass"
    assert "artifact_dir" in result.artifacts


@pytest.mark.asyncio
async def test_visual_executor_bad_output_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDSCENE_ENABLED", "1")
    ex = VisualExecutor(
        command=[sys.executable, "-c", "print('not-json')"],
        artifact_root=tmp_path,
    )

    result = await ex.run_case(run_id="r1", case=_case(), spec=_spec())

    assert result.passed is False
    assert result.stop_reason == "runner_bad_output"
