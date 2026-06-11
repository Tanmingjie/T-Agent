"""Permission 暂停交互路由(Spec §4.4, T-24)。

两条审批通道:
- embedded(单机进程内):内存 threading.Event(_permission_events)。
- queue(双进程,T-P09):permission_request 工单表(worker 写 pending、轮询;此处解决)。
同一端点先试内存、再落 DB,前端无感。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["permission"])


def get_store():
    # 延迟取 store/execution 内存态,避免 execution→server→routers→permission→execution
    # 的模块加载期循环导入(execution 在被 server 的 router 块导入时尚未完成初始化)。
    from api.server import get_store as _gs

    return _gs()


def _mem_events():
    from api.routers.execution import _permission_events, _permission_results

    return _permission_events, _permission_results


class PermissionChoice(BaseModel):
    choice: str  # "approve" | "reject"


@router.get("/suites/{suite_id}/runs/{run_id}/permission")
async def list_pending(suite_id: str, run_id: str, store=Depends(get_store)):
    """queue 模式:列出某 run 待审批的工单(前端轮询展示)。"""
    rows = await store.list_pending_permission_requests(run_id)
    return [
        {"event_id": r.id, "action": r.tool_name, "reason": r.reason, "created_at": r.created_at}
        for r in rows
    ]


@router.post("/suites/{suite_id}/permission/{event_id}")
async def confirm_permission(
    suite_id: str, event_id: str, body: PermissionChoice, store=Depends(get_store)
):
    if body.choice not in ("approve", "reject"):
        raise HTTPException(400, "choice must be 'approve' or 'reject'")
    approved = body.choice == "approve"

    # 1) embedded:内存事件
    _permission_events, _permission_results = _mem_events()
    event = _permission_events.get(event_id)
    if event is not None:
        _permission_results[event_id] = {"approved": approved}
        event.set()
        _permission_events.pop(event_id, None)
        return {"ok": True, "event_id": event_id, "choice": body.choice}

    # 2) queue:DB 工单
    if await store.resolve_permission_request(event_id, approved):
        return {"ok": True, "event_id": event_id, "choice": body.choice}

    raise HTTPException(404, "Permission event not found or already resolved")
