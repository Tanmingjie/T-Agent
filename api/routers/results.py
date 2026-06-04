"""结果 / 截图 / 代码路由(Spec §4.3)。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from api.server import get_repo

router = APIRouter(tags=["results"])

SCREENSHOT_ROOT = Path("storage/screenshots")
GENERATED_ROOT = Path("storage/generated")


@router.get("/suites/{suite_id}/runs")
async def list_runs(suite_id: str, repo=Depends(get_repo)):
    return await repo.list_runs_by_suite(suite_id)


@router.get("/suites/{suite_id}/runs/{run_id}")
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


@router.get("/suites/{suite_id}/runs/{run_id}/cases/{case_id}/result")
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
                    "tool_name": s.tool_name,
                    "tool_input": s.tool_input,
                },
                "action_result": {
                    "tool_result": s.tool_result,
                    "url": s.url,
                    "screenshot": s.screenshot,
                    "assertion_results": s.assertion_results,
                    "is_custom_tool": s.is_custom_tool,
                    "duration_ms": s.duration_ms,
                },
            }
        )
    return history


@router.get("/suites/{suite_id}/runs/{run_id}/cases/{case_id}/code")
async def get_case_code(suite_id: str, run_id: str, case_id: str, repo=Depends(get_repo)):
    records = await repo.list_records_by_run(run_id)
    record = next((r for r in records if r.case_id == case_id), None)
    if record is None:
        raise HTTPException(404, "Result not found")

    # 优先用本次 run 持久化的 generated_code(磁盘文件按 case_id 命名,会被后续
    # run 覆盖,对 per-run 抽屉不准);无则回退磁盘文件。
    files: dict[str, str] = {}
    if record.generated_code:
        files[f"{case_id}.py"] = record.generated_code
    else:
        feat = GENERATED_ROOT / f"{case_id}.feature"
        steps = GENERATED_ROOT / f"test_{case_id}.py"
        if feat.exists():
            files[f"{case_id}.feature"] = feat.read_text()
        if steps.exists():
            files[f"test_{case_id}.py"] = steps.read_text()

    return {"files": files, "case_id": case_id}


@router.get("/suites/{suite_id}/runs/{run_id}/cases/{case_id}/code/download")
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
    path = SCREENSHOT_ROOT / run_id / case_id / step_index
    if not path.exists():
        raise HTTPException(404, "Screenshot not found")
    return FileResponse(path)
