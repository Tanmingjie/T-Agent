"""Midscene 视觉执行 sidecar 封装。

第一阶段只定义 Python 边界:把 TestSpec 写成 JSON 送给 runner,再把 runner 的 JSON
结果归一。真实 Midscene 依赖留在 Node runner 侧,Python 单测用 fake runner 覆盖。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, Field

from input.models import TestCase, TestSpec

logger = logging.getLogger(__name__)


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

    _WINDOWS_DLL_INIT_FAILED = {0xC0000142, -0x3FFFFEBE}

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        timeout_seconds: float | None = None,
        artifact_root: str | Path | None = None,
    ) -> None:
        self.command = command or self._default_command()
        configured_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else float(os.getenv("MIDSCENE_RUNNER_TIMEOUT_SECONDS", "0") or "0")
        )
        self.timeout_seconds = configured_timeout if configured_timeout > 0 else None
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
        execution_context: str = "",
    ) -> VisualExecutionResult:
        if os.getenv("MIDSCENE_ENABLED", "1") == "0":
            return VisualExecutionResult(
                passed=False,
                stop_reason="midscene_disabled",
                error="MIDSCENE_ENABLED=0, Midscene 执行未启用",
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
            "execution_context": execution_context,
        }

        started = time.time()
        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        launch_log = artifact_dir / "runner-launch.log"
        executable = shutil.which(self.command[0]) or self.command[0]
        self._append_launch_log(
            launch_log,
            f"准备启动 runner: executable={executable!r}, cwd={os.getcwd()!r}, os={os.name}",
        )
        launch_retried = False
        try:
            proc, stdout, stderr = await self._run_runner(
                payload_bytes, launch_log=launch_log, attempt=1
            )
            if proc.returncode in self._WINDOWS_DLL_INIT_FAILED:
                launch_retried = True
                self._append_launch_log(
                    launch_log,
                    "首次启动返回 0xC0000142 (Windows DLL 初始化失败),1 秒后重试。",
                    warning=True,
                )
                await asyncio.sleep(1)
                proc, stdout, stderr = await self._run_runner(
                    payload_bytes, launch_log=launch_log, attempt=2
                )
        except asyncio.TimeoutError:
            self._append_launch_log(launch_log, "runner 执行超时。", warning=True)
            partial = self._read_partial_result(artifact_dir)
            if partial is not None:
                partial.passed = False
                partial.stop_reason = "runner_timeout"
                partial.error = partial.error or f"Midscene runner 超时({self.timeout_seconds}s)"
                partial.artifacts.setdefault("artifact_dir", str(artifact_dir))
                return partial
            return VisualExecutionResult(
                passed=False,
                stop_reason="runner_timeout",
                error=f"Midscene runner 超时({self.timeout_seconds}s)",
                artifacts={"artifact_dir": str(artifact_dir)},
            )
        except Exception as e:  # noqa: BLE001
            self._append_launch_log(
                launch_log,
                f"runner 启动异常: {type(e).__name__}: {e}",
                warning=True,
            )
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
        if launch_retried and proc.returncode == 0:
            self._append_launch_log(launch_log, "第二次启动成功。")

        if proc.returncode != 0:
            partial = self._read_partial_result(artifact_dir)
            if partial is not None:
                partial.passed = False
                partial.stop_reason = partial.stop_reason or "runner_failed"
                partial.error = (
                    partial.error or stderr_text.strip() or self._runner_exit_error(proc.returncode)
                )
                partial.artifacts.setdefault("artifact_dir", str(artifact_dir))
                return partial
            return VisualExecutionResult(
                passed=False,
                stop_reason="runner_failed",
                error=stderr_text.strip() or self._runner_exit_error(proc.returncode),
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

    async def _run_runner(self, payload: bytes, *, launch_log: Path, attempt: int):
        kwargs = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._append_launch_log(launch_log, f"启动尝试 {attempt}: 创建子进程。")
        proc = await asyncio.create_subprocess_exec(*self.command, **kwargs)
        self._append_launch_log(launch_log, f"启动尝试 {attempt}: pid={proc.pid}。")
        try:
            if self.timeout_seconds is None:
                stdout, stderr = await proc.communicate(payload)
            else:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(payload), timeout=self.timeout_seconds
                )
        except asyncio.TimeoutError:
            await self._terminate_process(proc)
            raise
        self._append_launch_log(launch_log, f"启动尝试 {attempt}: returncode={proc.returncode}。")
        return proc, stdout, stderr

    @staticmethod
    def _append_launch_log(path: Path, message: str, *, warning: bool = False) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
        if warning:
            logger.warning(line)
        else:
            logger.info(line)

    @classmethod
    def _runner_exit_error(cls, returncode: int) -> str:
        if returncode in cls._WINDOWS_DLL_INIT_FAILED:
            return (
                "Midscene runner 连续两次启动失败: Windows DLL 初始化失败 "
                "(0xC0000142)。请检查系统资源或重启执行服务后重试。"
            )
        return f"runner exited with code {returncode}"

    @staticmethod
    async def _terminate_process(proc) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            return
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            return

    @staticmethod
    def _read_partial_result(artifact_dir: Path) -> VisualExecutionResult | None:
        progress_file = artifact_dir / "midscene-result.json"
        if not progress_file.exists():
            return None
        try:
            data = json.loads(progress_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        data.setdefault("artifacts", {})
        return VisualExecutionResult(**data)

    @staticmethod
    def _model_config() -> dict:
        return {
            "modelName": os.getenv("MIDSCENE_MODEL_NAME", ""),
            "apiKey": os.getenv("MIDSCENE_MODEL_API_KEY", ""),
            "baseURL": os.getenv("MIDSCENE_MODEL_BASE_URL", ""),
            "family": os.getenv("MIDSCENE_MODEL_FAMILY", ""),
        }
