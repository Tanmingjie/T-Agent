"""项目级配置路由(平台化 T-P06):LLM 配置 CRUD + 连通自检。

api_key **加密落库**(storage.crypto),回显只露尾号(mask),绝不返明文。
执行链按项目构造 LLMClient(harness.llm.build_llm_client);CLI/单机仍走 env。
注:完整项目 CRUD + 路由租户化在 T-P07;此处先把 LLM 配置闭环(本期重点)。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.server import get_store
from harness.llm import build_llm_client
from input.models import ProjectLLMConfig
from storage import crypto

router = APIRouter(tags=["projects"])
logger = logging.getLogger(__name__)


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
async def get_llm_config(project_id: str, store=Depends(get_store)):
    cfg = await store.get_llm_config(project_id)
    if cfg is None:
        # 未配置:返回空壳(前端显示「未配置」)
        cfg = ProjectLLMConfig(project_id=project_id)
    return _to_out(cfg)


@router.put("/projects/{project_id}/llm-config", response_model=LLMConfigOut)
async def put_llm_config(project_id: str, body: LLMConfigIn, store=Depends(get_store)):
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
async def delete_llm_config(project_id: str, store=Depends(get_store)):
    if not await store.delete_llm_config(project_id):
        raise HTTPException(404, "未找到该项目的 LLM 配置")
    return {"ok": True}


@router.post("/projects/{project_id}/llm-config/check")
async def check_llm_config(project_id: str, store=Depends(get_store)):
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
