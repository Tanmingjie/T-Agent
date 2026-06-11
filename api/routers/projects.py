"""项目路由(平台化 T-P05/T-P06):项目/成员/版本 CRUD + RBAC + LLM 配置。

- T-P05:项目自助开通(创建者→admin)、成员管理(admin)、版本建/克隆;三角色 RBAC 闸门
  (require_member / require_project_admin / require_platform_admin)。
- T-P06:LLM 配置 CRUD + 自检;api_key **加密落库**(storage.crypto),回显只露尾号,绝不返明文。
  执行链按项目构造 LLMClient(harness.llm.build_llm_client);CLI/单机仍走 env。
注:suites/execution/results/vocabulary 的租户化在 T-P07。
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import (
    ROLE_ADMIN,
    ROLE_TESTER,
    Principal,
    get_principal,
    require_member,
    require_project_admin,
)
from api.server import get_store
from harness.llm import build_llm_client
from input.models import (
    Project,
    ProjectHttpTool,
    ProjectLLMConfig,
    ProjectMember,
    ProjectSkill,
    SessionProfile,
    Version,
)
from storage import crypto

router = APIRouter(tags=["projects"])
logger = logging.getLogger(__name__)


# ── 项目 CRUD(自助开通:创建者自动成为项目管理员;平台审批流留 M4)─────


class ProjectIn(BaseModel):
    name: str
    description: str = ""


@router.post("/projects")
async def create_project(
    body: ProjectIn, principal: Principal = Depends(get_principal), store=Depends(get_store)
):
    pid = uuid.uuid4().hex
    await store.save_project(
        Project(id=pid, name=body.name, description=body.description, owner=principal.user_id)
    )
    # 注册用户(若首次出现)+ 创建者即项目管理员
    if await store.get_user(principal.user_id) is None:
        from input.models import User

        await store.save_user(User(id=principal.user_id, display_name=principal.user_id))
    await store.save_member(
        ProjectMember(project_id=pid, user_id=principal.user_id, role=ROLE_ADMIN)
    )
    return {"id": pid, "name": body.name, "description": body.description}


@router.get("/projects")
async def list_my_projects(principal: Principal = Depends(get_principal), store=Depends(get_store)):
    """当前用户加入的项目(平台管理员看全部)。"""
    if principal.is_platform_admin:
        projects = await store.list_projects()
    else:
        memberships = await store.list_memberships(principal.user_id)
        pids = {m.project_id for m in memberships}
        projects = [p for p in await store.list_projects() if p.id in pids]
    return [p.model_dump() for p in projects]


@router.get("/projects/{project_id}")
async def get_project(
    project_id: str, _role: str = Depends(require_member), store=Depends(get_store)
):
    p = await store.get_project(project_id)
    if p is None:
        raise HTTPException(404, "项目不存在")
    return p.model_dump()


# ── 成员管理(项目管理员)──────────────────────────────────────


class MemberIn(BaseModel):
    user_id: str
    role: str = ROLE_TESTER  # admin | tester


@router.get("/projects/{project_id}/members")
async def list_members(
    project_id: str, _role: str = Depends(require_member), store=Depends(get_store)
):
    return [m.model_dump() for m in await store.list_members(project_id)]


@router.post("/projects/{project_id}/members")
async def add_member(
    project_id: str,
    body: MemberIn,
    _: Principal = Depends(require_project_admin),
    store=Depends(get_store),
):
    if body.role not in (ROLE_ADMIN, ROLE_TESTER):
        raise HTTPException(400, "role 必须是 admin 或 tester")
    if await store.get_user(body.user_id) is None:
        from input.models import User

        await store.save_user(User(id=body.user_id, display_name=body.user_id))
    await store.save_member(
        ProjectMember(project_id=project_id, user_id=body.user_id, role=body.role)
    )
    return {"ok": True, "user_id": body.user_id, "role": body.role}


@router.delete("/projects/{project_id}/members/{user_id}")
async def remove_member(
    project_id: str,
    user_id: str,
    _: Principal = Depends(require_project_admin),
    store=Depends(get_store),
):
    if not await store.delete_member(project_id, user_id):
        raise HTTPException(404, "成员不存在")
    return {"ok": True}


# ── 版本(项目管理员建/克隆,成员看)─────────────────────────────


class VersionIn(BaseModel):
    name: str


@router.get("/projects/{project_id}/versions")
async def list_versions(
    project_id: str, _role: str = Depends(require_member), store=Depends(get_store)
):
    return [v.model_dump() for v in await store.list_versions(project_id)]


@router.post("/projects/{project_id}/versions")
async def create_version(
    project_id: str,
    body: VersionIn,
    _: Principal = Depends(require_project_admin),
    store=Depends(get_store),
):
    vid = uuid.uuid4().hex
    await store.save_version(Version(id=vid, project_id=project_id, name=body.name))
    return {"id": vid, "project_id": project_id, "name": body.name}


@router.post("/projects/{project_id}/versions/{version_id}/clone-suites")
async def clone_version_suites(
    project_id: str,
    version_id: str,
    from_version_id: str,
    _: Principal = Depends(require_project_admin),
    store=Depends(get_store),
):
    """从 from_version_id 拷 Suite 到 version_id(版本继承,显式动作)。"""
    try:
        n = await store.clone_version_suites(from_version_id, version_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "cloned": n}


class LLMConfigIn(BaseModel):
    model: str = ""
    api_base: str = ""
    api_key: str = ""  # 明文提交;留空或仍是掩码值 → 保留原 key(不覆盖)
    temperature: float = 0.0


class LLMConfigOut(BaseModel):
    project_id: str
    model: str
    api_base: str
    api_key_masked: str  # 只露尾号,绝不返明文
    has_key: bool
    temperature: float


def _to_out(cfg: ProjectLLMConfig) -> LLMConfigOut:
    return LLMConfigOut(
        project_id=cfg.project_id,
        model=cfg.model,
        api_base=cfg.api_base,
        api_key_masked=crypto.mask(cfg.api_key),
        has_key=bool(cfg.api_key),
        temperature=cfg.temperature,
    )


def _is_mask(value: str) -> bool:
    """前端回填的掩码值(以 • 开头)视为「未改动」,保留原 key。"""
    return value.startswith("•")


@router.get("/projects/{project_id}/llm-config", response_model=LLMConfigOut)
async def get_llm_config(
    project_id: str, _role: str = Depends(require_member), store=Depends(get_store)
):
    cfg = await store.get_llm_config(project_id)
    if cfg is None:
        # 未配置:返回空壳(前端显示「未配置」)
        cfg = ProjectLLMConfig(project_id=project_id)
    return _to_out(cfg)


@router.put("/projects/{project_id}/llm-config", response_model=LLMConfigOut)
async def put_llm_config(
    project_id: str,
    body: LLMConfigIn,
    _: Principal = Depends(require_project_admin),
    store=Depends(get_store),
):
    # api_key 留空或仍是掩码 → 保留已存 key(避免前端不重输就被清空)
    api_key = body.api_key
    if not api_key or _is_mask(api_key):
        existing = await store.get_llm_config(project_id)
        api_key = existing.api_key if existing else ""
    cfg = ProjectLLMConfig(
        project_id=project_id,
        model=body.model,
        api_base=body.api_base,
        api_key=api_key,
        temperature=body.temperature,
    )
    await store.save_llm_config(cfg)
    return _to_out(cfg)


@router.delete("/projects/{project_id}/llm-config")
async def delete_llm_config(
    project_id: str, _: Principal = Depends(require_project_admin), store=Depends(get_store)
):
    if not await store.delete_llm_config(project_id):
        raise HTTPException(404, "未找到该项目的 LLM 配置")
    return {"ok": True}


# ── HTTP 型 Custom Tool(M2;admin 管,headers 加密、列表不返明文)──────


class HttpToolIn(BaseModel):
    name: str
    description: str = ""
    method: str = "GET"
    url: str = ""
    headers: dict = {}
    body: str = ""
    parameters: dict = {}
    when_to_use: str = ""
    timeout_seconds: int = 30


@router.get("/projects/{project_id}/http-tools")
async def list_http_tools(
    project_id: str, _role: str = Depends(require_member), store=Depends(get_store)
):
    tools = await store.list_http_tools(project_id)
    # 不返 headers 明文,只示其 key(可能含凭据)
    return [
        {
            "name": t.name,
            "description": t.description,
            "method": t.method,
            "url": t.url,
            "header_keys": sorted(t.headers.keys()),
            "body": t.body,
            "parameters": t.parameters,
            "when_to_use": t.when_to_use,
            "timeout_seconds": t.timeout_seconds,
        }
        for t in tools
    ]


@router.put("/projects/{project_id}/http-tools/{name}")
async def put_http_tool(
    project_id: str,
    name: str,
    body: HttpToolIn,
    _: Principal = Depends(require_project_admin),
    store=Depends(get_store),
):
    tool = ProjectHttpTool(
        project_id=project_id,
        name=name,
        description=body.description,
        method=body.method,
        url=body.url,
        headers=body.headers,
        body=body.body,
        parameters=body.parameters,
        when_to_use=body.when_to_use,
        timeout_seconds=body.timeout_seconds,
    )
    await store.save_http_tool(tool)
    return {"ok": True, "name": name}


@router.delete("/projects/{project_id}/http-tools/{name}")
async def delete_http_tool(
    project_id: str,
    name: str,
    _: Principal = Depends(require_project_admin),
    store=Depends(get_store),
):
    if not await store.delete_http_tool(project_id, name):
        raise HTTPException(404, "工具不存在")
    return {"ok": True}


# ── 项目级 Skill(M2;admin 改,成员看)──────────────────────


class SkillIn(BaseModel):
    name: str
    content: str = ""


@router.get("/projects/{project_id}/skills")
async def list_skills(
    project_id: str, _role: str = Depends(require_member), store=Depends(get_store)
):
    return [s.model_dump() for s in await store.list_skills(project_id)]


@router.put("/projects/{project_id}/skills/{name}")
async def put_skill(
    project_id: str,
    name: str,
    body: SkillIn,
    _: Principal = Depends(require_project_admin),
    store=Depends(get_store),
):
    await store.save_skill(ProjectSkill(project_id=project_id, name=name, content=body.content))
    return {"ok": True, "name": name}


@router.delete("/projects/{project_id}/skills/{name}")
async def delete_skill(
    project_id: str,
    name: str,
    _: Principal = Depends(require_project_admin),
    store=Depends(get_store),
):
    if not await store.delete_skill(project_id, name):
        raise HTTPException(404, "Skill 不存在")
    return {"ok": True}


# ── 项目级 SessionProfile(M2;admin 改,成员看;cookies 不返明文)────


class SessionProfileIn(BaseModel):
    name: str
    base_url: str = ""
    login_aw: str = ""
    cookie_store: str = ""
    cookies: list = []  # 可选:直接存 cookie(加密落库)
    valid_until: float | None = None


@router.get("/projects/{project_id}/session-profiles")
async def list_session_profiles(
    project_id: str, _role: str = Depends(require_member), store=Depends(get_store)
):
    profs = await store.list_session_profiles(project_id)
    return [
        {
            "name": p.name,
            "base_url": p.base_url,
            "login_aw": p.login_aw,
            "cookie_store": p.cookie_store,
            "has_cookies": bool(p.cookies),  # 不返 cookie 明文
            "valid_until": p.valid_until,
        }
        for p in profs
    ]


@router.put("/projects/{project_id}/session-profiles/{name}")
async def put_session_profile(
    project_id: str,
    name: str,
    body: SessionProfileIn,
    _: Principal = Depends(require_project_admin),
    store=Depends(get_store),
):
    await store.save_session_profile(
        SessionProfile(
            name=name,
            login_aw=body.login_aw,
            cookie_store=body.cookie_store,
            valid_until=body.valid_until,
            base_url=body.base_url,
            project_id=project_id,
            cookies=body.cookies,
        )
    )
    return {"ok": True, "name": name}


@router.delete("/projects/{project_id}/session-profiles/{name}")
async def delete_session_profile(
    project_id: str,
    name: str,
    _: Principal = Depends(require_project_admin),
    store=Depends(get_store),
):
    if not await store.delete_session_profile(name):
        raise HTTPException(404, "SessionProfile 不存在")
    return {"ok": True}


@router.post("/projects/{project_id}/llm-config/check")
async def check_llm_config(
    project_id: str, _role: str = Depends(require_member), store=Depends(get_store)
):
    """用项目已存配置发一条测试消息,验证连通。返回 {ok, model, reply?/error?}。"""
    cfg = await store.get_llm_config(project_id)
    if cfg is None or not cfg.model:
        raise HTTPException(400, "该项目尚未配置 LLM(至少需要 model)")
    llm = build_llm_client(cfg)
    try:
        r = await llm.chat([{"role": "user", "content": "只回复两个字:正常"}])
    except Exception as e:  # noqa: BLE001
        detail = str(e).replace("\n", " ")
        return {"ok": False, "model": llm.model, "error": f"{type(e).__name__}: {detail[:300]}"}
    return {
        "ok": True,
        "model": llm.model,
        "reply": r.content,
        "total_tokens": r.usage.total_tokens,
    }
