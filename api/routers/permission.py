"""Permission 暂停交互路由(Spec §4.4, T-24)。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.routers.execution import _permission_events, _permission_results

router = APIRouter(tags=["permission"])


class PermissionChoice(BaseModel):
    choice: str  # "approve" | "reject"


@router.post("/suites/{suite_id}/permission/{event_id}")
async def confirm_permission(suite_id: str, event_id: str, body: PermissionChoice):
    event = _permission_events.get(event_id)
    if event is None:
        raise HTTPException(404, "Permission event not found or already resolved")
    if body.choice not in ("approve", "reject"):
        raise HTTPException(400, "choice must be 'approve' or 'reject'")

    _permission_results[event_id] = {"approved": body.choice == "approve"}
    event.set()
    _permission_events.pop(event_id, None)
    return {"ok": True, "event_id": event_id, "choice": body.choice}
