"""结果 / 截图 / 代码路由(Spec §4.3)。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse

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
                "metrics": r.metrics,  # 分阶段成本/质量指标(#6),run 级一览/可聚合
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
        "midscene_artifacts": _build_midscene_artifacts(suite_id, run_id, case_id, record),
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


def _build_midscene_artifacts(suite_id: str, run_id: str, case_id: str, record) -> dict:
    """Expose Midscene native report/log/screenshot artifacts as first-class result data."""
    root = _midscene_root(record, run_id, case_id)
    if not root.is_dir():
        return {"available": False, "files": []}

    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        files.append(
            {
                "path": rel,
                "name": path.name,
                "kind": _artifact_kind(path),
                "size": path.stat().st_size,
                "url": _artifact_url(suite_id, run_id, case_id, rel),
            }
        )

    report = _artifact_relpath(root, record.metrics.get("midscene", {}).get("artifacts", {}).get("report"))
    if report is None:
        report = next((f["path"] for f in files if f["path"].endswith("midscene-report.html")), None)

    return {
        "available": bool(files),
        "report_path": report,
        "report_url": _artifact_url(suite_id, run_id, case_id, report) if report else "",
        "files": files,
    }


def _artifact_url(suite_id: str, run_id: str, case_id: str, relpath: str) -> str:
    encoded = quote(relpath, safe="")
    return f"/api/suites/{suite_id}/runs/{run_id}/cases/{case_id}/artifact?path={encoded}"


def _midscene_root(record, run_id: str, case_id: str) -> Path:
    artifact_dir = record.metrics.get("midscene", {}).get("artifacts", {}).get("artifact_dir")
    if artifact_dir:
        return Path(str(artifact_dir)).resolve()
    return _artifacts.midscene_dir(run_id, case_id).resolve()


def _artifact_relpath(root: Path, value: str | None) -> str | None:
    if not value:
        return None
    path = Path(str(value)).resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def _artifact_path(record, run_id: str, case_id: str, relpath: str) -> Path:
    root = _midscene_root(record, run_id, case_id)
    path = (root / relpath).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(400, "Invalid artifact path") from exc
    if not path.is_file():
        raise HTTPException(404, "Artifact not found")
    return path


def _artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "image"
    if suffix in {".html", ".htm"}:
        return "report"
    if suffix in {".log", ".txt"} or "stdout" in name or "stderr" in name:
        return "log"
    return "file"


def _artifact_media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix in {".log", ".txt", ".json"}:
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


@router.get(
    "/suites/{suite_id}/runs/{run_id}/cases/{case_id}/artifact",
    dependencies=_suite_guard,
)
async def get_case_artifact(
    suite_id: str,
    run_id: str,
    case_id: str,
    path: str = Query(..., min_length=1),
    repo=Depends(get_repo),
):
    records = await repo.list_records_by_run(run_id)
    record = next((r for r in records if r.case_id == case_id), None)
    if record is None:
        raise HTTPException(404, "Result not found")
    artifact = _artifact_path(record, run_id, case_id, path)
    return FileResponse(artifact, media_type=_artifact_media_type(artifact))


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
