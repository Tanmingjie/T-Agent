"""Suite CRUD + Excel 上传路由(Spec §4.1)。"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from api.server import get_repo
from harness.precondition import USER_SETTABLE_TYPES
from input.excel_parser import parse_excel
from input.models import Suite, TestCase

router = APIRouter(tags=["suites"])


class SuiteCreateRequest(BaseModel):
    name: str
    base_url: str
    session_profile: str | None = None


class UploadResult(BaseModel):
    total: int
    inserted: int
    warnings: list[str]


@router.get("/suites")
async def list_suites(repo=Depends(get_repo)):
    suites = await repo.list_all()
    return [s.model_dump() for s in suites]


@router.post("/suites")
async def create_suite(body: SuiteCreateRequest, repo=Depends(get_repo)):
    suite = Suite(
        id=uuid.uuid4().hex[:12],
        name=body.name,
        base_url=body.base_url,
        session_profile=body.session_profile,
    )
    await repo.create(suite)
    return suite.model_dump()


@router.get("/suites/{suite_id}")
async def get_suite(suite_id: str, repo=Depends(get_repo)):
    suite = await repo.get_suite(suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")
    cases = await repo.list_by_suite(suite_id)
    runs = await repo.list_runs_by_suite(suite_id)
    return {
        **suite.model_dump(),
        "cases": [c.model_dump() for c in cases],
        "runs": runs,
    }


@router.delete("/suites/{suite_id}")
async def delete_suite(suite_id: str, repo=Depends(get_repo)):
    if not await repo.delete(suite_id):
        raise HTTPException(404, "Suite not found")


@router.post("/suites/{suite_id}/upload")
async def upload_excel(
    suite_id: str,
    file: UploadFile = File(...),
    repo=Depends(get_repo),
) -> UploadResult:
    suite = await repo.get_suite(suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "只支持 .xlsx 文件")

    # Save uploaded file to temp, then parse
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        cases = parse_excel(tmp_path, base_url=suite.base_url, suite_id=suite_id)
        for c in cases:
            c.base_url = suite.base_url
        n = await repo.bulk_insert(cases)
        return UploadResult(total=len(cases), inserted=n, warnings=[])
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.get("/suites/{suite_id}/cases/{case_id}")
async def get_case(suite_id: str, case_id: str, repo=Depends(get_repo)):
    tc = await repo.get_case(case_id)
    if tc is None:
        raise HTTPException(404, "Case not found")
    return tc.model_dump()


class PreconditionUpdate(BaseModel):
    index: int
    confirmed: bool


@router.put("/suites/{suite_id}/cases/{case_id}/precondition")
async def update_precondition(
    suite_id: str,
    case_id: str,
    body: PreconditionUpdate,
    repo=Depends(get_repo),
):
    ok = await repo.update_precondition(case_id, body.index, body.confirmed)
    if not ok:
        raise HTTPException(404, "Case not found")
    return {"ok": True}


class PreconditionItemUpdate(BaseModel):
    index: int
    type: str  # state_hook | action_step | ignore(用户标黄确认的三选一)
    hook_ref: str | None = None  # type=state_hook 时指定 Hook 名


@router.put("/suites/{suite_id}/cases/{case_id}/precondition-item")
async def update_precondition_item(
    suite_id: str,
    case_id: str,
    body: PreconditionItemUpdate,
    repo=Depends(get_repo),
):
    """标黄确认:把某条预置条件分类改为用户选择(Hook/Given/忽略),落库并标记已确认。"""
    if body.type not in USER_SETTABLE_TYPES:
        raise HTTPException(
            422, f"type 非法:{body.type}(合法:{', '.join(sorted(USER_SETTABLE_TYPES))})"
        )
    ok = await repo.update_precondition_item(case_id, body.index, body.type, body.hook_ref)
    if not ok:
        raise HTTPException(404, "Case or precondition item not found")
    return {"ok": True}
