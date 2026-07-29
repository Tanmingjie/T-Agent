from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock

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
    launch_log = tmp_path / "midscene" / "r1" / "tc1" / "runner-launch.log"
    assert "准备启动 runner" in launch_log.read_text(encoding="utf-8")
    assert "returncode=0" in launch_log.read_text(encoding="utf-8")


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


@pytest.mark.asyncio
async def test_visual_executor_retries_windows_dll_init_failure_once(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDSCENE_ENABLED", "1")
    payload = {
        "passed": True,
        "stop_reason": "completed",
        "phase_results": [{"phase_index": 0, "status": "pass"}],
    }
    failed = AsyncMock(returncode=0xC0000142)
    succeeded = AsyncMock(returncode=0)
    ex = VisualExecutor(command=["node", "runner.js"], artifact_root=tmp_path)
    monkeypatch.setattr(
        ex,
        "_run_runner",
        AsyncMock(
            side_effect=[
                (failed, b"", b""),
                (succeeded, json.dumps(payload).encode(), b""),
            ]
        ),
    )
    monkeypatch.setattr("harness.visual_executor.asyncio.sleep", AsyncMock())

    result = await ex.run_case(run_id="r1", case=_case(), spec=_spec())

    assert result.passed is True
    assert ex._run_runner.await_count == 2
    launch_log = tmp_path / "midscene" / "r1" / "tc1" / "runner-launch.log"
    assert "0xC0000142" in launch_log.read_text(encoding="utf-8")


def test_visual_executor_explains_repeated_windows_dll_init_failure():
    error = VisualExecutor._runner_exit_error(3221225794)

    assert "Windows DLL 初始化失败" in error
    assert "0xC0000142" in error


def test_visual_executor_disables_runner_timeout_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MIDSCENE_RUNNER_TIMEOUT_SECONDS", raising=False)

    ex = VisualExecutor(command=["node", "runner.js"], artifact_root=tmp_path)

    assert ex.timeout_seconds is None


def test_visual_executor_supports_optional_runner_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDSCENE_RUNNER_TIMEOUT_SECONDS", "900")

    configured = VisualExecutor(command=["node", "runner.js"], artifact_root=tmp_path)
    disabled = VisualExecutor(
        command=["node", "runner.js"], timeout_seconds=0, artifact_root=tmp_path
    )

    assert configured.timeout_seconds == 900
    assert disabled.timeout_seconds is None


@pytest.mark.asyncio
async def test_visual_executor_logs_failure_before_runner_can_write_stderr(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDSCENE_ENABLED", "1")
    ex = VisualExecutor(command=["definitely-missing-midscene-node"], artifact_root=tmp_path)

    result = await ex.run_case(run_id="r1", case=_case(), spec=_spec())

    assert result.stop_reason == "runner_failed_to_start"
    launch_log = tmp_path / "midscene" / "r1" / "tc1" / "runner-launch.log"
    log_text = launch_log.read_text(encoding="utf-8")
    assert "准备启动 runner" in log_text
    assert "runner 启动异常" in log_text


@pytest.mark.asyncio
async def test_visual_executor_timeout_returns_partial_result(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDSCENE_ENABLED", "1")
    script = (
        "import json, pathlib, sys, time; "
        "payload=json.loads(sys.stdin.read()); "
        "p=pathlib.Path(payload['artifact_dir'])/'midscene-result.json'; "
        "p.write_text(json.dumps({"
        "'passed': False, "
        "'stop_reason': 'completed', "
        "'phase_results': [{'phase_index': 0, 'status': 'pass', 'expected': '阀门变红'}], "
        "'artifacts': {}"
        "}), encoding='utf-8'); "
        "time.sleep(5)"
    )
    ex = VisualExecutor(
        command=[sys.executable, "-c", script],
        timeout_seconds=0.2,
        artifact_root=tmp_path,
    )

    result = await ex.run_case(run_id="r1", case=_case(), spec=_spec())

    assert result.passed is False
    assert result.stop_reason == "runner_timeout"
    assert "超时" in result.error
    assert result.phase_results[0].status == "pass"
