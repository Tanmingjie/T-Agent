"""结果 / 截图 / 代码路由(Spec §4.3)。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse

from api.auth import require_suite_access
from api.server import get_repo
from storage.artifacts import get_artifact_store

router = APIRouter(tags=["results"])

# suite 维度鉴权:所有 /suites/{suite_id}/... 的 JSON 结果路由都要求该套件项目的成员资格
# (单机/无 project_id 放行)。截图端点(get_screenshot)无 suite_id 且服务于 <img>,不在此列。
_suite_guard = [Depends(require_suite_access)]

# 产物路径经 ArtifactStore 抽象(T-P10),不再散落字面量;M3 换对象存储只换实现。
_artifacts = get_artifact_store()
GENERATED_ROOT = _artifacts.generated_dir()


@router.get("/suites/{suite_id}/runs", dependencies=_suite_guard)
async def list_runs(suite_id: str, repo=Depends(get_repo)):
    return await repo.list_runs_by_suite(suite_id)


@router.get("/suites/{suite_id}/runs/{run_id}", dependencies=_suite_guard)
async def get_run_overview(suite_id: str, run_id: str, repo=Depends(get_repo)):
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    records = await repo.list_records_by_run(run_id)
    return {
        **run,
        "cases": [
            {
                "case_id": r.case_id,
                "passed": r.passed,
                "verdict": "PASS" if r.passed else "FAIL",
                "steps_count": len(r.steps),
                "token_usage": r.token_usage,
            }
            for r in records
        ],
    }


@router.get("/suites/{suite_id}/runs/{run_id}/cases/{case_id}/result", dependencies=_suite_guard)
async def get_case_result(suite_id: str, run_id: str, case_id: str, repo=Depends(get_repo)):
    records = await repo.list_records_by_run(run_id)
    record = next((r for r in records if r.case_id == case_id), None)
    if record is None:
        raise HTTPException(404, "Result not found")
    return {
        **record.model_dump(),
        "history": _build_history(record),
    }


def _build_history(record):
    """Produce model_output / action_result separated history."""
    history = []
    for s in record.steps:
        history.append(
            {
                "step_no": s.step_no,
                "model_output": {
                    "reasoning": s.reasoning,
                    "intent": s.intent,
                    "prompt": s.prompt,  # 供步骤详情「查看 prompt」(执行完成后仍可看)
                    "tool_name": s.tool_name,
                    "tool_input": s.tool_input,
                },
                "action_result": {
                    "tool_result": s.tool_result,
                    "url": s.url,
                    "screenshot": s.screenshot,
                    "assertion_results": s.assertion_results,
                    "heal_attempts": s.heal_attempts,  # 操作侧自愈(过程时间线展示)
                    "is_custom_tool": s.is_custom_tool,
                    "duration_ms": s.duration_ms,
                },
            }
        )
    return history


@router.get("/suites/{suite_id}/runs/{run_id}/cases/{case_id}/code", dependencies=_suite_guard)
async def get_case_code(suite_id: str, run_id: str, case_id: str, repo=Depends(get_repo)):
    records = await repo.list_records_by_run(run_id)
    record = next((r for r in records if r.case_id == case_id), None)
    if record is None:
        raise HTTPException(404, "Result not found")

    # 优先用本次 run 持久化的 generated_code(磁盘文件按 case_id 命名,会被后续
    # run 覆盖,对 per-run 抽屉不准);无则回退产物存储。
    files: dict[str, str] = {}
    if record.generated_code:
        files[f"{case_id}.py"] = record.generated_code
    else:
        files = _artifacts.read_generated(case_id)

    return {"files": files, "case_id": case_id}


@router.get(
    "/suites/{suite_id}/runs/{run_id}/cases/{case_id}/code/download", dependencies=_suite_guard
)
async def download_code(suite_id: str, run_id: str, case_id: str, repo=Depends(get_repo)):
    records = await repo.list_records_by_run(run_id)
    record = next((r for r in records if r.case_id == case_id), None)
    if record is None:
        raise HTTPException(404, "Result not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        feat = GENERATED_ROOT / f"{case_id}.feature"
        steps = GENERATED_ROOT / f"test_{case_id}.py"
        if feat.exists():
            zf.write(feat, f"{case_id}.feature")
        if steps.exists():
            zf.write(steps, f"test_{case_id}.py")
        if record.generated_code:
            zf.writestr("generated_code.txt", record.generated_code)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={case_id}.zip"},
    )


@router.get("/screenshots/{run_id}/{case_id}/{step_index}")
async def get_screenshot(run_id: str, case_id: str, step_index: str):
    data = _artifacts.read_screenshot(run_id, case_id, step_index)
    if data is None:
        raise HTTPException(404, "Screenshot not found")
    return Response(content=data, media_type="image/png")
