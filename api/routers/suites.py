"""Suite CRUD + Excel 上传路由(Spec §4.1)。"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from api.auth import Principal, get_principal, require_member, require_suite_access, role_in_project
from api.server import get_repo, get_store
from input.excel_parser import parse_excel
from input.models import Suite, TestCase

router = APIRouter(tags=["suites"])

_CASE_ID_SEP = "--"


def namespaced_case_id(suite_id: str, case_id: str) -> str:
    """把用例编号加上套件前缀,避免不同套件同号用例(TC101)主键冲突互相覆盖。

    分隔符 '--' 文件系统/URL 安全(截图目录以 case_id 命名)。已带本套件前缀则不重复加。
    """
    prefix = f"{suite_id}{_CASE_ID_SEP}"
    return case_id if case_id.startswith(prefix) else f"{prefix}{case_id}"


class SuiteCreateRequest(BaseModel):
    name: str
    base_url: str
    project_id: str = ""  # 多租户(T-P07);单机留空
    version_id: str = ""


class UploadResult(BaseModel):
    total: int
    inserted: int
    warnings: list[str]


@router.get("/suites")
async def list_suites(
    project_id: str = "",
    version_id: str = "",
    with_status: bool = False,
    principal: Principal = Depends(get_principal),
    store=Depends(get_store),
    repo=Depends(get_repo),
):
    # 指定项目 → 要求成员 + 作用域过滤;未指定 → 仅平台管理员(含单机隐式 admin)可看全部。
    if project_id:
        if await role_in_project(store, principal.user_id, project_id) is None:
            raise HTTPException(403, "无权访问该项目")
        # with_status:版本工作区套件表用,顺带返回用例数 + 最近执行摘要(避免前端 N+1)。
        if with_status:
            return await store.list_suite_status(
                project_id=project_id, version_id=version_id or None
            )
        suites = await store.list_suites(project_id=project_id, version_id=version_id or None)
    else:
        if not principal.is_platform_admin:
            raise HTTPException(400, "请指定 project_id")
        suites = await repo.list_all()
    return [s.model_dump() for s in suites]


@router.post("/suites")
async def create_suite(
    body: SuiteCreateRequest,
    principal: Principal = Depends(get_principal),
    store=Depends(get_store),
    repo=Depends(get_repo),
):
    # 项目内全开放:测试人员也能建 Suite(任一成员角色即可);指定项目时校验成员资格。
    if body.project_id:
        if await role_in_project(store, principal.user_id, body.project_id) is None:
            raise HTTPException(403, "无权在该项目创建套件")
    suite = Suite(
        id=uuid.uuid4().hex[:12],
        name=body.name,
        base_url=body.base_url,
        project_id=body.project_id,
        version_id=body.version_id,
        owner=principal.user_id,
    )
    await repo.create(suite)
    return suite.model_dump()


@router.get("/suites/{suite_id}")
async def get_suite(suite_id: str, suite=Depends(require_suite_access), repo=Depends(get_repo)):
    cases = await repo.list_by_suite(suite_id)
    runs = await repo.list_runs_by_suite(suite_id)
    return {
        **suite.model_dump(),
        "cases": [c.model_dump() for c in cases],
        "runs": runs,
    }


@router.delete("/suites/{suite_id}")
async def delete_suite(suite_id: str, _suite=Depends(require_suite_access), repo=Depends(get_repo)):
    if not await repo.delete(suite_id):
        raise HTTPException(404, "Suite not found")


@router.post("/suites/{suite_id}/upload")
async def upload_excel(
    suite_id: str,
    file: UploadFile = File(...),
    suite=Depends(require_suite_access),
    repo=Depends(get_repo),
) -> UploadResult:
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
            # 跨 suite 命名空间:用例 id 取自 Excel「用例编号」(常跨套件重复,如 TC101)。
            # 主键直接用它,第二个套件上传同号用例会 merge 覆盖第一个 → 旧套件用例丢失。
            # 加套件前缀消歧;分隔符 '--' 文件系统/URL 安全(截图目录用 case_id 命名,
            # 冒号在 Windows 非法)。前端展示时去前缀还原编号。
            c.id = namespaced_case_id(suite_id, c.id)
        n = await repo.bulk_insert(cases)
        return UploadResult(total=len(cases), inserted=n, warnings=[])
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.get("/suites/{suite_id}/cases/{case_id}")
async def get_case(
    suite_id: str, case_id: str, _suite=Depends(require_suite_access), repo=Depends(get_repo)
):
    tc = await repo.get_case(case_id)
    if tc is None:
        raise HTTPException(404, "Case not found")
    return tc.model_dump()


@router.get("/suites/{suite_id}/cases/{case_id}/spec-prompt")
async def get_case_spec_prompt(
    suite_id: str,
    case_id: str,
    suite=Depends(require_suite_access),
    repo=Depends(get_repo),
    store=Depends(get_store),
):
    """只读预览:这条用例**实际喂给翻译 LLM 的 prompt**(system + user),含项目「用例规范」注入。

    用于在前端核对"用例规范是否进了翻译、prompt 长什么样"。仅组装消息,不调用 LLM。
    """
    from intelligence.pre_analysis import build_spec_messages

    tc = await repo.get_case(case_id)
    if tc is None:
        raise HTTPException(404, "Case not found")
    knowledge = ""
    if suite.project_id:
        project = await store.get_project(suite.project_id)
        if project is not None:
            knowledge = project.translation_knowledge or ""
    msgs = build_spec_messages(tc, knowledge=knowledge)
    system = next((m["content"] for m in msgs if m["role"] == "system"), "")
    user = next((m["content"] for m in msgs if m["role"] == "user"), "")
    return {"system": system, "user": user, "knowledge_used": bool(knowledge.strip())}


# 〔2026-06-22 翻译阶段化重设计:预置条件不再分类/确认(纯背景),原
#   /precondition 与 /precondition-item 标黄确认端点随分类器一并退役。〕
