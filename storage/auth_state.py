"""Suite-level uploaded Playwright storageState files."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


def auth_state_root() -> Path:
    return Path(os.getenv("TAGENT_AUTH_STATE_ROOT", "storage/auth-states"))


def suite_auth_state_path(suite_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", suite_id).strip(".-") or "suite"
    digest = hashlib.sha256(suite_id.encode("utf-8")).hexdigest()[:12]
    return auth_state_root() / f"{safe}-{digest}.json"


def suite_auth_state_meta(suite_id: str) -> dict[str, Any]:
    path = suite_auth_state_path(suite_id)
    if not path.is_file():
        return {"uploaded": False, "size": 0, "updated_at": None}
    stat = path.stat()
    return {
        "uploaded": True,
        "size": stat.st_size,
        "updated_at": stat.st_mtime,
    }


def normalize_storage_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("认证状态必须是 JSON object")
    cookies = value.get("cookies")
    if not isinstance(cookies, list):
        raise ValueError("认证状态缺少 cookies 数组")
    origins = value.get("origins", [])
    if origins is None:
        origins = []
    if not isinstance(origins, list):
        raise ValueError("origins 必须是数组")

    for index, cookie in enumerate(cookies):
        if not isinstance(cookie, dict):
            raise ValueError(f"cookies[{index}] 必须是 object")
        for field in ("name", "value", "domain", "path"):
            if field not in cookie:
                raise ValueError(f"cookies[{index}] 缺少 {field}")

    for index, origin in enumerate(origins):
        if not isinstance(origin, dict):
            raise ValueError(f"origins[{index}] 必须是 object")
        if not isinstance(origin.get("origin"), str):
            raise ValueError(f"origins[{index}] 缺少 origin")
        local_storage = origin.get("localStorage", [])
        if local_storage is None:
            local_storage = []
        if not isinstance(local_storage, list):
            raise ValueError(f"origins[{index}].localStorage 必须是数组")

    normalized = dict(value)
    normalized["cookies"] = cookies
    normalized["origins"] = origins
    return normalized


def save_suite_auth_state(suite_id: str, state: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_storage_state(state)
    path = suite_auth_state_path(suite_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return suite_auth_state_meta(suite_id)


def delete_suite_auth_state(suite_id: str) -> bool:
    path = suite_auth_state_path(suite_id)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def has_suite_auth_state(suite_id: str) -> bool:
    return suite_auth_state_path(suite_id).is_file()
