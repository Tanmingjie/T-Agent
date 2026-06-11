"""FastAPI 应用入口(phase 4 工程化界面)。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

# Load .env before anything else (LLM_MODEL / LLM_API_BASE / LLM_API_KEY)
from cli.run_case import _load_dotenv

_load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.repository import SQLModelRepository
from storage.db import Store

_store: Store | None = None
_repo: SQLModelRepository | None = None


def get_store() -> Store:
    assert _store is not None
    return _store


def get_repo() -> SQLModelRepository:
    assert _repo is not None
    return _repo


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store, _repo
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///storage/ai_test.db")
    _store = Store(url=db_url)
    await _store.init()
    _repo = SQLModelRepository(_store)
    yield
    await _store.close()


app = FastAPI(title="T-agent", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers registered incrementally as they are created across tasks.
from api.routers import (  # noqa: E402
    execution,
    permission,
    projects,
    results,
    suites,
    vocabulary,
)

app.include_router(suites.router, prefix="/api")
app.include_router(execution.router, prefix="/api")
app.include_router(permission.router, prefix="/api")
app.include_router(results.router, prefix="/api")
app.include_router(vocabulary.router, prefix="/api")
app.include_router(projects.router, prefix="/api")


# 纯 API 服务:前端由 Vite dev server(:5173)托管,不在此挂静态构建,
# 避免 :8000 服务到旧 dist 造成混乱。
@app.get("/")
async def root() -> dict:
    return {"service": "T-agent", "version": "0.2.0", "docs": "/docs", "api_prefix": "/api"}
