"""Midscene 视觉执行 sidecar 封装。

第一阶段只定义 Python 边界:把 TestSpec 写成 JSON 送给 runner,再把 runner 的 JSON
结果归一。真实 Midscene 依赖留在 Node runner 侧,Python 单测用 fake runner 覆盖。
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
from pathlib import Path

from pydantic import BaseModel, Field

from input.models import TestCase, TestSpec


class VisualPhaseResult(BaseModel):
    phase_index: int
    status: str = "fail"  # pass | fail
    expected: str = ""
    reason: str = ""
    evidence: str = ""
    query: dict = Field(default_factory=dict)


class VisualExecutionResult(BaseModel):
    passed: bool = False
    stop_reason: str = ""
    phase_results: list[VisualPhaseResult] = Field(default_factory=list)
    actions: list[dict] = Field(default_factory=list)
    artifacts: dict = Field(default_factory=dict)
    error: str = ""


class VisualExecutor:
    """调用 Midscene runner 的最小封装。"""

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        timeout_seconds: float | None = None,
        artifact_root: str | Path | None = None,
    ) -> None:
        self.command = command or self._default_command()
        self.timeout_seconds = timeout_seconds or float(
            os.getenv("MIDSCENE_RUNNER_TIMEOUT_SECONDS", "300")
        )
        self.artifact_root = Path(artifact_root or os.getenv("ARTIFACT_ROOT", "storage"))

    @staticmethod
    def _default_command() -> list[str]:
        cmd = os.getenv("MIDSCENE_RUNNER_CMD", "").strip()
        if cmd:
            return shlex.split(cmd, posix=False)
        node_cmd = os.getenv("MIDSCENE_NODE_CMD", "node")
        runner = os.getenv("MIDSCENE_RUNNER", "scripts/midscene_runner.js")
        return [node_cmd, runner]

    async def run_case(
        self,
        *,
        run_id: str,
        case: TestCase,
        spec: TestSpec,
    ) -> VisualExecutionResult:
        if os.getenv("MIDSCENE_ENABLED", "0") != "1":
            return VisualExecutionResult(
                passed=False,
                stop_reason="midscene_disabled",
                error="MIDSCENE_ENABLED != 1, Midscene 执行未启用",
            )

        artifact_dir = self.artifact_root / "midscene" / run_id / case.id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": run_id,
            "case_id": case.id,
            "base_url": case.base_url or spec.base_url,
            "spec": spec.model_dump(mode="json"),
            "artifact_dir": str(artifact_dir),
            "model_config": self._model_config(),
        }

        started = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return VisualExecutionResult(
                passed=False,
                stop_reason="runner_timeout",
                error=f"Midscene runner 超时({self.timeout_seconds}s)",
                artifacts={"artifact_dir": str(artifact_dir)},
            )
        except Exception as e:  # noqa: BLE001
            return VisualExecutionResult(
                passed=False,
                stop_reason="runner_failed_to_start",
                error=f"Midscene runner 启动失败:{type(e).__name__}: {e}",
                artifacts={"artifact_dir": str(artifact_dir)},
            )

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        (artifact_dir / "runner-stdout.log").write_text(stdout_text, encoding="utf-8")
        (artifact_dir / "runner-stderr.log").write_text(stderr_text, encoding="utf-8")

        if proc.returncode != 0:
            return VisualExecutionResult(
                passed=False,
                stop_reason="runner_failed",
                error=stderr_text.strip() or f"runner exited with code {proc.returncode}",
                artifacts={"artifact_dir": str(artifact_dir)},
            )

        try:
            data = json.loads(stdout_text)
        except json.JSONDecodeError:
            return VisualExecutionResult(
                passed=False,
                stop_reason="runner_bad_output",
                error="Midscene runner 输出不是合法 JSON",
                artifacts={"artifact_dir": str(artifact_dir)},
            )

        data.setdefault("artifacts", {})
        data["artifacts"].setdefault("artifact_dir", str(artifact_dir))
        data["artifacts"].setdefault("duration_ms", int((time.time() - started) * 1000))
        return VisualExecutionResult(**data)

    @staticmethod
    def _model_config() -> dict:
        return {
            "modelName": os.getenv("MIDSCENE_MODEL_NAME", ""),
            "apiKey": os.getenv("MIDSCENE_MODEL_API_KEY", ""),
            "baseURL": os.getenv("MIDSCENE_MODEL_BASE_URL", ""),
            "family": os.getenv("MIDSCENE_MODEL_FAMILY", ""),
        }
