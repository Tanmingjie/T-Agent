"""鉴权与 RBAC(平台化 T-P05)。

分两层:
- **认证(你是谁)**:`AuthProvider` 接口把请求解析成 `Principal`(user_id + 是否平台管理员)。
  一期 `HeaderAuthProvider` 从 header(默认 ``X-User``)透传用户名;M4 换 IDaaS(OIDC)只换实现。
- **授权(你能干嘛)**:三角色——平台管理员 / 项目管理员(ProjectMember.role=admin)/
  测试人员(role=tester)。`role_in_project` 解析某人在某项目的有效角色;FastAPI 依赖
  `require_member` / `require_project_admin` / `require_platform_admin` 做路由级闸门。

设计取舍:平台管理员对所有项目有 admin 等效权限(可审批建项目、运维);项目管理员管本项目
配置/成员/版本;测试人员建 Suite、执行、维护词汇表,但不能改项目配置或成员。

注:T-P05 只建原语;**应用到具体路由在 T-P07**(API 租户化)。现有非租户路由暂不强制鉴权,
单机 CLI 路径完全不经过这里(不回归)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

from storage.db import Store

# 角色常量
ROLE_ADMIN = "admin"  # 项目管理员
ROLE_TESTER = "tester"  # 测试人员


@dataclass
class Principal:
    """已认证的调用者身份。"""

    user_id: str
    is_platform_admin: bool = False


class AuthProvider(ABC):
    """认证接口。M4 换 IDaaS 只换实现。"""

    @abstractmethod
    async def authenticate(self, user_header: str | None) -> Principal | None:
        """从请求凭据解析 Principal;无法认证返回 None。"""


class HeaderAuthProvider(AuthProvider):
    """一期实现:从 header 透传用户名,平台管理员标志查 app_user 表。

    适配「内网网关已鉴权、把用户名放 header」的常见部署;开发期也可手动传 header。
    """

    def __init__(self, store: Store) -> None:
        self.store = store

    async def authenticate(self, user_header: str | None) -> Principal | None:
        if not user_header:
            return None
        user = await self.store.get_user(user_header)
        is_admin = bool(user and user.is_platform_admin)
        return Principal(user_id=user_header, is_platform_admin=is_admin)


async def role_in_project(store: Store, user_id: str, project_id: str) -> str | None:
    """某人在某项目的有效角色:平台管理员→admin 等效;否则查成员表;非成员→None。

    **单机/开放模式**(未配置 AuthProvider):全员等效项目管理员,保留无登录全开放
    + 向后兼容(隐式 ``system`` 主体不在用户表,故须在此短路,否则项目级路由会误判 403)。
    """
    if _auth_provider is None:
        return ROLE_ADMIN
    user = await store.get_user(user_id)
    if user and user.is_platform_admin:
        return ROLE_ADMIN
    member = await store.get_member(project_id, user_id)
    return member.role if member else None


# ── FastAPI 依赖(在 server.py 注入 provider/store)─────────────

# 模块级注入点(lifespan 设置,与 _store/_repo 同套路);测试可直接覆盖。
_auth_provider: AuthProvider | None = None


def set_auth_provider(provider: AuthProvider | None) -> None:
    global _auth_provider
    _auth_provider = provider


async def get_principal(x_user: str | None = Header(default=None)) -> Principal:
    """认证依赖:解析 Principal,失败 401。

    **单机模式**(未配置 AuthProvider,如 CLI/本地/现有非鉴权测试):返回隐式平台管理员
    `system`,保留单机全开放 + 向后兼容。平台部署一旦 `set_auth_provider`,即走真实 RBAC。
    """
    if _auth_provider is None:
        return Principal(user_id="system", is_platform_admin=True)
    principal = await _auth_provider.authenticate(x_user)
    if principal is None:
        raise HTTPException(401, "未认证(缺少 X-User)")
    return principal


def _get_store_dep():
    # 延迟取 store,避免与 server 模块循环导入
    from api.server import get_store

    return get_store()


# 以下依赖直接声明 ``project_id`` 路径参数,FastAPI 从路由路径注入(故路由须含 {project_id})。


async def require_member(project_id: str, principal: Principal = Depends(get_principal)) -> str:
    """要求调用者是该项目成员(任一角色)。返回其有效角色字符串。"""
    store = _get_store_dep()
    role = await role_in_project(store, principal.user_id, project_id)
    if role is None:
        raise HTTPException(403, "无权访问该项目")
    return role


async def require_project_admin(
    project_id: str, principal: Principal = Depends(get_principal)
) -> Principal:
    """要求项目管理员(平台管理员等效)。"""
    store = _get_store_dep()
    role = await role_in_project(store, principal.user_id, project_id)
    if role != ROLE_ADMIN:
        raise HTTPException(403, "需要项目管理员权限")
    return principal


async def require_platform_admin(principal: Principal = Depends(get_principal)) -> Principal:
    """要求平台管理员。"""
    if not principal.is_platform_admin:
        raise HTTPException(403, "需要平台管理员权限")
    return principal


async def require_suite_access(suite_id: str, principal: Principal = Depends(get_principal)):
    """suite 维度的鉴权:加载 suite → 按其所属项目查成员资格。返回 suite(供路由复用)。

    无 project_id 的 suite(单机/历史)放行;有则要求成员。404 在此统一抛。
    用于 suites/execution/results 等以 suite_id 为入口的路由。
    """
    store = _get_store_dep()
    suite = await store.get_suite(suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")
    if suite.project_id:
        role = await role_in_project(store, principal.user_id, suite.project_id)
        if role is None:
            raise HTTPException(403, "无权访问该套件所属项目")
    return suite
