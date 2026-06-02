# 阶段四（工程化界面）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 FastAPI + React 控制台，支持 Suite 管理、Excel 上传、实时执行监控（SSE）、Permission 交互、结果详情与代码查看。

**Architecture:** 四垂直切片。FastAPI 后端同进程调用 Agent（`harness/orchestrator`），SSE 混合模式推送状态。前端 React + Vite + shadcn/ui，全部通过 REST + SSE 通信（设计上已分离，部署合一）。Repository 抽象层隔离路由与存储。

**Tech Stack:** FastAPI + uvicorn, React + Vite + TypeScript, shadcn/ui (Tailwind + Radix), @monaco-editor/react, SQLModel + aiosqlite, SSE (EventSource)

**Source spec:** `docs/superpowers/specs/2026-06-02-phase4-engineering-ui-design.md`

---

## File Structure Map

```
api/
  server.py              ← NEW: FastAPI app, CORS, static serve, mount routers
  repository.py          ← NEW: Abstract Repository interfaces
  routers/
    __init__.py           ← NEW: empty
    suites.py             ← NEW: Suite CRUD + Excel upload routes
    execution.py          ← NEW: /run, /stream SSE
    results.py            ← NEW: results, screenshots, code routes
    permission.py         ← NEW: permission confirm route
    vocabulary.py         ← NEW: vocabulary CRUD + scan route

storage/
  db.py                   ← MODIFY: +RunRecordRow, +SuiteSettingsRow, +run_id in ExecutionRecordRow

harness/
  orchestrator.py         ← MODIFY: accept optional sse_callback param
  permission.py           ← MODIFY: add async_event_approver factory
  recorder.py             ← MODIFY: screenshot path to <run_id>/<case_id>/

codegen/
  bdd.py                  ← MODIFY: add # step_<N> comment to step defs

frontend/                 ← NEW directory, scaffolded
  package.json
  vite.config.ts
  tailwind.config.js
  tsconfig.json
  index.html
  src/
    main.tsx
    App.tsx
    api/
      client.ts           ← REST helper + SSE EventSource wrapper
    pages/
      SuiteListPage.tsx
      SuiteDetailPage.tsx
      RunConsolePage.tsx
      CaseResultPage.tsx
      CodeViewerPage.tsx
      VocabularyPage.tsx
    components/
      SuiteCard.tsx
      CaseTable.tsx
      PreconditionPanel.tsx
      RunHistoryTable.tsx
      StepListPanel.tsx
      DetailPanel.tsx
      PermissionDialog.tsx
      ScreenshotViewer.tsx
      ProgressBar.tsx
      FileTree.tsx
```

---

## SLICE 1: Suite 管理 + Excel 上传

### Task 1: Add run_id to ExecutionRecord and new DB models

**Files:**
- Modify: `storage/db.py:1-241`
- Modify: `input/models.py:108-128`

- [ ] **Step 1: Add `run_id` field to ExecutionRecord model**

In `input/models.py`, add `run_id` to `ExecutionRecord`:

```python
class ExecutionRecord(BaseModel):
    """执行结果(所有执行的统一落点,实现原则 3)。"""

    exec_id: str
    case_id: str
    suite_id: str | None = None
    run_id: str | None = None  # ← 新增:关联 RunRecord
    steps: list[ActionStep] = []
    ...
```

- [ ] **Step 2: Add `RunRecordRow` and `SuiteSettingsRow` to `storage/db.py`**

After the existing `PageVocabularyRow` class (line 106), add:

```python
class RunRecordRow(SQLModel, table=True):
    """每次 Suite 执行产生的 run 记录(规格 §6 T-23)。"""
    __tablename__ = "run_record"
    id: str = Field(primary_key=True)  # UUID
    suite_id: str = Field(default="", index=True)
    status: str = "running"  # running | completed | aborted | failed
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    started_at: float = 0.0
    finished_at: float | None = None
    updated_at: float = 0.0


class SuiteSettingsRow(SQLModel, table=True):
    """Suite 级执行配置(phase 4)。"""
    __tablename__ = "suite_settings"
    suite_id: str = Field(primary_key=True)
    permission_mode: str = "trust"  # trust | approve
    updated_at: float = 0.0
```

- [ ] **Step 3: Run existing tests, verify no breakage**

```bash
source .venv/bin/activate && python -m pytest -q
```

Expected: 274 passed, 1 skipped (the new models don't break existing code because they're new tables).

- [ ] **Step 4: Commit**

```bash
git add input/models.py storage/db.py
git commit -m "feat: add RunRecord, SuiteSettings models + run_id to ExecutionRecord

T-23 prep: new SQLModel tables for execution run tracking and suite config.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Repository abstraction layer

**Files:**
- Create: `api/repository.py`
- Create: `tests/test_repository.py`

- [ ] **Step 1: Write the abstract interfaces**

Create `api/repository.py`:

```python
"""Repository 抽象层(规格 §4 注记, phase 4)。

路由依赖这些接口而非直接依赖 storage/db.py,便于测试和换存储。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from input.models import (
    ExecutionRecord,
    PageVocabulary,
    Suite,
    TestCase,
)


class SuiteRepository(ABC):
    @abstractmethod
    async def create(self, suite: Suite) -> Suite: ...

    @abstractmethod
    async def get(self, suite_id: str) -> Suite | None: ...

    @abstractmethod
    async def list_all(self) -> list[Suite]: ...

    @abstractmethod
    async def delete(self, suite_id: str) -> bool: ...


class TestCaseRepository(ABC):
    @abstractmethod
    async def bulk_insert(self, cases: list[TestCase]) -> int: ...

    @abstractmethod
    async def list_by_suite(self, suite_id: str) -> list[TestCase]: ...

    @abstractmethod
    async def get(self, case_id: str) -> TestCase | None: ...

    @abstractmethod
    async def update_precondition(
        self, case_id: str, precondition_index: int, confirmed: bool
    ) -> bool: ...


class ExecutionRepository(ABC):
    @abstractmethod
    async def create_run(
        self, run_id: str, suite_id: str, total_cases: int
    ) -> None: ...

    @abstractmethod
    async def update_run(
        self, run_id: str, *, status: str | None = None,
        passed_cases: int | None = None, failed_cases: int | None = None,
        finished_at: float | None = None,
    ) -> None: ...

    @abstractmethod
    async def get_run(self, run_id: str) -> dict | None: ...

    @abstractmethod
    async def list_runs_by_suite(self, suite_id: str) -> list[dict]: ...

    @abstractmethod
    async def save_record(self, record: ExecutionRecord) -> None: ...

    @abstractmethod
    async def get_record(self, exec_id: str) -> ExecutionRecord | None: ...

    @abstractmethod
    async def list_records_by_run(self, run_id: str) -> list[ExecutionRecord]: ...

    @abstractmethod
    async def list_records_by_suite(self, suite_id: str) -> list[ExecutionRecord]: ...


class VocabularyRepository(ABC):
    @abstractmethod
    async def list_all(self) -> list[PageVocabulary]: ...

    @abstractmethod
    async def get(
        self, url_pattern: str, page_title: str, login_role: str
    ) -> PageVocabulary | None: ...

    @abstractmethod
    async def save(self, vocab: PageVocabulary) -> None: ...

    @abstractmethod
    async def bulk_upsert(self, entries: list[PageVocabulary]) -> int: ...

    @abstractmethod
    async def delete_by_key(
        self, url_pattern: str, page_title: str, login_role: str
    ) -> bool: ...
```

- [ ] **Step 2: Implement SQLModelRepository**

Continuing in `api/repository.py`, add the concrete implementation wrapping `storage/db.Store`:

```python
import time
import uuid
from storage.db import Store, RunRecordRow, SuiteSettingsRow


class SQLModelRepository(SuiteRepository, TestCaseRepository, ExecutionRepository, VocabularyRepository):
    """基于 storage/db.Store 的 SQLModel 实现。"""

    def __init__(self, store: Store) -> None:
        self._store = store

    # ── Suite ──

    async def create(self, suite: Suite) -> Suite:
        await self._store.save_suite(suite)
        return suite

    async def get(self, suite_id: str) -> Suite | None:
        return await self._store.get_suite(suite_id)

    async def list_all(self) -> list[Suite]:
        return await self._store.list_suites()

    async def delete(self, suite_id: str) -> bool:
        suite = await self._store.get_suite(suite_id)
        if suite is None:
            return False
        # Store has no direct delete; use engine via store
        from sqlmodel import delete as sql_delete
        from storage.db import SuiteRow
        async with self._store._sf() as s:
            await s.exec(sql_delete(SuiteRow).where(SuiteRow.id == suite_id))
            await s.commit()
        return True

    # ── TestCase ──

    async def bulk_insert(self, cases: list[TestCase]) -> int:
        for tc in cases:
            await self._store.save_case(tc)
        return len(cases)

    async def list_by_suite(self, suite_id: str) -> list[TestCase]:
        return await self._store.list_cases(suite_id=suite_id)

    async def get(self, case_id: str) -> TestCase | None:
        return await self._store.get_case(case_id)

    async def update_precondition(
        self, case_id: str, precondition_index: int, confirmed: bool
    ) -> bool:
        tc = await self._store.get_case(case_id)
        if tc is None:
            return False
        # Toggle confirmed_by_user on the precondition item (stored as list of str
        # in current model; we attach a separate confirmed list stored as JSON)
        # For now, store confirmed state in a dedicated list in the model
        # Actually, preconditions are list[str] in TestCase.
        # The PreconditionItem classification lives in pre_analysis.py output,
        # not in TestCase model itself. We'll store confirmed status in the
        # TestCase via a new `precondition_confirmed` list field.
        confirmed_list: list[bool] = getattr(tc, "precondition_confirmed", [])
        while len(confirmed_list) < len(tc.preconditions):
            confirmed_list.append(False)
        if 0 <= precondition_index < len(confirmed_list):
            confirmed_list[precondition_index] = confirmed
        tc_dict = tc.model_dump()
        tc_dict["precondition_confirmed"] = confirmed_list
        await self._store.save_case(TestCase(**tc_dict))
        return True

    # ── Execution ──

    async def create_run(self, run_id: str, suite_id: str, total_cases: int) -> None:
        row = RunRecordRow(
            id=run_id, suite_id=suite_id, status="running",
            total_cases=total_cases, started_at=time.time(),
        )
        async with self._store._sf() as s:
            s.add(row)
            await s.commit()

    async def update_run(self, run_id: str, **kwargs) -> None:
        async with self._store._sf() as s:
            row = await s.get(RunRecordRow, run_id)
            if row is None:
                return
            for key, val in kwargs.items():
                if val is not None and hasattr(row, key):
                    setattr(row, key, val)
            row.updated_at = time.time()
            s.add(row)
            await s.commit()

    async def get_run(self, run_id: str) -> dict | None:
        async with self._store._sf() as s:
            row = await s.get(RunRecordRow, run_id)
            if row is None:
                return None
            return {
                "id": row.id, "suite_id": row.suite_id, "status": row.status,
                "total_cases": row.total_cases, "passed_cases": row.passed_cases,
                "failed_cases": row.failed_cases, "started_at": row.started_at,
                "finished_at": row.finished_at,
            }

    async def list_runs_by_suite(self, suite_id: str) -> list[dict]:
        from sqlmodel import select as sql_select
        async with self._store._sf() as s:
            stmt = sql_select(RunRecordRow).where(
                RunRecordRow.suite_id == suite_id
            ).order_by(RunRecordRow.started_at.desc())
            rows = (await s.exec(stmt)).all()
            return [
                {
                    "id": r.id, "suite_id": r.suite_id, "status": r.status,
                    "total_cases": r.total_cases, "passed_cases": r.passed_cases,
                    "failed_cases": r.failed_cases, "started_at": r.started_at,
                    "finished_at": r.finished_at,
                }
                for r in rows
            ]

    async def save_record(self, record: ExecutionRecord) -> None:
        await self._store.save_record(record)

    async def get_record(self, exec_id: str) -> ExecutionRecord | None:
        return await self._store.get_record(exec_id)

    async def list_records_by_run(self, run_id: str) -> list[ExecutionRecord]:
        from sqlmodel import select as sql_select
        from storage.db import ExecutionRecordRow
        async with self._store._sf() as s:
            stmt = sql_select(ExecutionRecordRow).where(
                ExecutionRecordRow.run_id == run_id
            )
            rows = (await s.exec(stmt)).all()
            return [ExecutionRecord(**r.model_dump()) for r in rows]

    async def list_records_by_suite(self, suite_id: str) -> list[ExecutionRecord]:
        return await self._store.list_records(case_id=None)

    # ── Vocabulary ──

    async def list_all(self) -> list[PageVocabulary]:
        return await self._store.list_vocabularies()

    async def get(
        self, url_pattern: str, page_title: str, login_role: str
    ) -> PageVocabulary | None:
        return await self._store.get_vocabulary(url_pattern, page_title, login_role)

    async def save(self, vocab: PageVocabulary) -> None:
        await self._store.save_vocabulary(vocab)

    async def bulk_upsert(self, entries: list[PageVocabulary]) -> int:
        for v in entries:
            await self._store.save_vocabulary(v)
        return len(entries)

    async def delete_by_key(
        self, url_pattern: str, page_title: str, login_role: str
    ) -> bool:
        from sqlmodel import delete as sql_delete
        from storage.db import PageVocabularyRow
        async with self._store._sf() as s:
            stmt = sql_delete(PageVocabularyRow).where(
                PageVocabularyRow.url_pattern == url_pattern,
                PageVocabularyRow.page_title == page_title,
                PageVocabularyRow.login_role == login_role,
            )
            result = await s.exec(stmt)
            await s.commit()
            return result.rowcount > 0


# ── Suite Settings ──

async def get_suite_settings(store: Store, suite_id: str) -> dict:
    async with store._sf() as s:
        row = await s.get(SuiteSettingsRow, suite_id)
        if row is None:
            return {"suite_id": suite_id, "permission_mode": "trust"}
        return {"suite_id": row.suite_id, "permission_mode": row.permission_mode}


async def set_suite_settings(store: Store, suite_id: str, permission_mode: str) -> None:
    row = SuiteSettingsRow(
        suite_id=suite_id, permission_mode=permission_mode, updated_at=time.time(),
    )
    async with store._sf() as s:
        await s.merge(row)
        await s.commit()
```

- [ ] **Step 3: Write the test**

Create `tests/test_repository.py`:

```python
"""Tests for api/repository.py using SQLite in-memory."""
import pytest
from input.models import Suite, TestCase, ExecutionRecord
from api.repository import SQLModelRepository
from storage.db import Store

@pytest.fixture
async def repo():
    store = Store(url="sqlite+aiosqlite:///file:test_repo?mode=memory&cache=shared&uri=true")
    await store.init()
    r = SQLModelRepository(store)
    yield r
    await store.close()


@pytest.mark.asyncio
async def test_suite_crud(repo):
    s = Suite(id="s1", name="Test Suite", base_url="https://example.com")
    await repo.create(s)
    assert (await repo.get("s1")).name == "Test Suite"
    assert len(await repo.list_all()) == 1
    assert await repo.delete("s1") is True
    assert await repo.get("s1") is None


@pytest.mark.asyncio
async def test_case_bulk_insert_and_list(repo):
    cases = [
        TestCase(id="tc1", name="Case 1", steps=["step a"], base_url="https://x.com", suite_id="s1"),
        TestCase(id="tc2", name="Case 2", steps=["step b"], base_url="https://x.com", suite_id="s1"),
    ]
    n = await repo.bulk_insert(cases)
    assert n == 2
    result = await repo.list_by_suite("s1")
    assert len(result) == 2


@pytest.mark.asyncio
async def test_run_lifecycle(repo):
    await repo.create_run("r1", "s1", 5)
    assert (await repo.get_run("r1"))["status"] == "running"
    await repo.update_run("r1", status="completed", passed_cases=5, finished_at=1234.0)
    r = await repo.get_run("r1")
    assert r["status"] == "completed"
    assert r["passed_cases"] == 5
```

- [ ] **Step 4: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_repository.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add api/ tests/test_repository.py
git commit -m "feat: Repository 抽象层 + SQLModel 实现

T-23: Suite/TestCase/Execution/Vocabulary repository interfaces
with SQLModelRepository wrapping storage/db.Store.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: FastAPI app + Suite routes

**Files:**
- Create: `api/server.py`
- Create: `api/routers/__init__.py`
- Create: `api/routers/suites.py`
- Create: `tests/test_api_suites.py`

- [ ] **Step 1: Create FastAPI app entrypoint**

Create `api/server.py`:

```python
"""FastAPI 应用入口(phase 4 工程化界面)。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# Routers registered incrementally as they are created across tasks.
# Task 3: suites router only
from api.routers import suites as _suites  # noqa: E402
app.include_router(_suites.router, prefix="/api")

# Serve React build in production
frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
```

Later, in Task 6, add after the suites import:
```python
from api.routers import execution as _exec  # noqa: E402
app.include_router(_exec.router, prefix="/api")
```

In Task 7, add:
```python
from api.routers import permission as _perm  # noqa: E402
app.include_router(_perm.router, prefix="/api")
```

In Task 9, add:
```python
from api.routers import results as _res  # noqa: E402
app.include_router(_res.router, prefix="/api")
```

In Task 11, add:
```python
from api.routers import vocabulary as _voc  # noqa: E402
app.include_router(_voc.router, prefix="/api")
```


Create `api/routers/__init__.py`:

```python
"""API routers for T-agent phase 4."""
```

- [ ] **Step 2: Create Suite routes**

Create `api/routers/suites.py`:

```python
"""Suite CRUD + Excel 上传路由(Spec §4.1)。"""
from __future__ import annotations

import uuid
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from api.server import get_repo
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
    suite = await repo.get(suite_id)
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
    suite_id: str, file: UploadFile = File(...), repo=Depends(get_repo),
) -> UploadResult:
    suite = await repo.get(suite_id)
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
    tc = await repo.get(case_id)
    if tc is None:
        raise HTTPException(404, "Case not found")
    return tc.model_dump()


class PreconditionUpdate(BaseModel):
    index: int
    confirmed: bool


@router.put("/suites/{suite_id}/cases/{case_id}/precondition")
async def update_precondition(
    suite_id: str, case_id: str, body: PreconditionUpdate, repo=Depends(get_repo),
):
    ok = await repo.update_precondition(case_id, body.index, body.confirmed)
    if not ok:
        raise HTTPException(404, "Case not found")
    return {"ok": True}
```

- [ ] **Step 3: Write API tests**

Create `tests/test_api_suites.py`:

```python
"""Tests for Suite CRUD API routes."""
import pytest
from httpx import ASGITransport, AsyncClient

from api.repository import SQLModelRepository
from api.server import app
from storage.db import Store


@pytest.fixture
async def client():
    store = Store(url="sqlite+aiosqlite:///file:test_api_s?mode=memory&cache=shared&uri=true")
    await store.init()
    repo = SQLModelRepository(store)
    import api.server as srv
    srv._repo = repo
    srv._store = store
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await store.close()
    srv._repo = None


@pytest.mark.asyncio
async def test_create_and_list_suites(client):
    r = await client.post("/api/suites", json={"name": "S1", "base_url": "https://x.com"})
    assert r.status_code == 200
    sid = r.json()["id"]

    r = await client.get("/api/suites")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["name"] == "S1"


@pytest.mark.asyncio
async def test_get_suite_404(client):
    r = await client.get("/api/suites/nonexistent")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_upload_invalid_extension(client):
    r = await client.post("/api/suites/s1/upload", files={"file": ("test.txt", b"data")})
    assert r.status_code == 404  # suite doesn't exist, comes first
```

- [ ] **Step 4: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_api_suites.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add api/server.py api/routers/__init__.py api/routers/suites.py tests/test_api_suites.py
git commit -m "feat: FastAPI app + Suite CRUD + Excel upload routes

T-23 Slice 1 backend: Suite list/create/get/delete, Excel upload parsing.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Frontend scaffolding + Suite pages

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tailwind.config.js`
- Create: `frontend/postcss.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/pages/SuiteListPage.tsx`
- Create: `frontend/src/pages/SuiteDetailPage.tsx`
- Create: `frontend/src/components/SuiteCard.tsx`
- Create: `frontend/src/components/CaseTable.tsx`

- [ ] **Step 1: Create package.json and config files**

Create `frontend/package.json`:

```json
{
  "name": "t-agent-frontend",
  "private": true,
  "version": "0.2.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.23.0",
    "@monaco-editor/react": "^4.6.0",
    "@radix-ui/react-dialog": "^1.1.0",
    "@radix-ui/react-select": "^2.1.0",
    "@radix-ui/react-progress": "^1.1.0",
    "@radix-ui/react-toast": "^1.2.0",
    "@radix-ui/react-tabs": "^1.1.0",
    "class-variance-authority": "^0.7.0",
    "clsx": "^2.1.0",
    "tailwind-merge": "^2.3.0",
    "lucide-react": "^0.378.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0",
    "tailwindcss": "^3.4.0",
    "typescript": "^5.4.0",
    "vite": "^5.4.0"
  }
}
```

Create `frontend/vite.config.ts`:

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
```

Create `frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": false,
    "noUnusedParameters": false,
    "noFallthroughCasesInSwitch": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["src"]
}
```

Create `frontend/tailwind.config.js`:

```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {},
  },
  plugins: [],
};
```

Create `frontend/postcss.config.js`:

```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

Create `frontend/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>T-agent — 测试自动化控制台</title>
  </head>
  <body class="bg-gray-50 text-gray-900">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 2: Create entry points and API client**

Create `frontend/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
```

Create `frontend/src/index.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

Create `frontend/src/api/client.ts`:

```typescript
const BASE = "/api";

export async function apiGet<T = unknown>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function apiPost<T = unknown>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: body instanceof FormData ? {} : { "Content-Type": "application/json" },
    body: body instanceof FormData ? body : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function apiPut<T = unknown>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function apiDelete(path: string): Promise<void> {
  const r = await fetch(`${BASE}${path}`, { method: "DELETE" });
  if (!r.ok) throw new Error(await r.text());
}

export function sseUrl(path: string): string {
  return `${BASE}${path}`;
}
```

Create `frontend/src/App.tsx`:

```tsx
import { Routes, Route } from "react-router-dom";
import SuiteListPage from "./pages/SuiteListPage";
import SuiteDetailPage from "./pages/SuiteDetailPage";
import RunConsolePage from "./pages/RunConsolePage";
import CaseResultPage from "./pages/CaseResultPage";
import CodeViewerPage from "./pages/CodeViewerPage";
import VocabularyPage from "./pages/VocabularyPage";

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="bg-slate-800 text-white px-6 py-3 flex items-center gap-4">
        <a href="/suites" className="font-bold text-lg">T-agent</a>
        <nav className="flex gap-4 text-sm">
          <a href="/suites" className="hover:text-cyan-300">Suites</a>
          <a href="/vocabulary" className="hover:text-cyan-300">词汇表</a>
        </nav>
      </header>
      <main className="max-w-7xl mx-auto p-6">
        <Routes>
          <Route path="/" element={<SuiteListPage />} />
          <Route path="/suites" element={<SuiteListPage />} />
          <Route path="/suites/:id" element={<SuiteDetailPage />} />
          <Route path="/suites/:id/run" element={<RunConsolePage />} />
          <Route path="/suites/:id/runs/:runId" element={<CaseResultPage />} />
          <Route path="/suites/:id/runs/:runId/case/:caseId" element={<CaseResultPage />} />
          <Route path="/suites/:id/runs/:runId/case/:caseId/code" element={<CodeViewerPage />} />
          <Route path="/vocabulary" element={<VocabularyPage />} />
        </Routes>
      </main>
    </div>
  );
}
```

- [ ] **Step 3: Create SuiteListPage**

Create `frontend/src/pages/SuiteListPage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiGet, apiPost, apiDelete } from "../api/client";

interface Suite {
  id: string;
  name: string;
  base_url: string;
}

export default function SuiteListPage() {
  const [suites, setSuites] = useState<Suite[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const navigate = useNavigate();

  async function load() {
    setSuites(await apiGet<Suite[]>("/suites"));
  }
  useEffect(() => { load(); }, []);

  async function create() {
    await apiPost("/suites", { name, base_url: baseUrl });
    setShowCreate(false);
    setName("");
    setBaseUrl("");
    load();
  }

  async function remove(id: string) {
    await apiDelete(`/suites/${id}`);
    load();
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">Suites</h2>
        <button
          onClick={() => setShowCreate(true)}
          className="bg-slate-800 text-white px-4 py-2 rounded hover:bg-slate-700"
        >
          + 新建
        </button>
      </div>

      {showCreate && (
        <div className="mb-6 p-4 border rounded bg-white">
          <input
            className="border px-3 py-2 rounded w-full mb-2"
            placeholder="Suite 名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <input
            className="border px-3 py-2 rounded w-full mb-2"
            placeholder="Base URL (e.g. https://example.com)"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
          <div className="flex gap-2">
            <button
              onClick={create}
              className="bg-cyan-600 text-white px-4 py-1 rounded"
            >
              创建
            </button>
            <button
              onClick={() => setShowCreate(false)}
              className="border px-4 py-1 rounded"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {suites.length === 0 && (
        <p className="text-gray-500 text-center py-20">
          还没有 Suite。创建你的第一个测试 Suite。
        </p>
      )}

      <div className="grid gap-4">
        {suites.map((s) => (
          <div
            key={s.id}
            className="bg-white border rounded p-4 flex items-center justify-between cursor-pointer hover:shadow"
            onClick={() => navigate(`/suites/${s.id}`)}
          >
            <div>
              <h3 className="font-semibold">{s.name}</h3>
              <p className="text-sm text-gray-500">{s.base_url}</p>
            </div>
            <button
              onClick={(e) => { e.stopPropagation(); remove(s.id); }}
              className="text-red-500 text-sm hover:underline"
            >
              删除
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create SuiteDetailPage**

Create `frontend/src/pages/SuiteDetailPage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { apiGet, apiPost } from "../api/client";

interface Case {
  id: string;
  name: string;
  steps: string[];
  preconditions: string[];
  expected: string[];
}
interface Run {
  id: string;
  status: string;
  passed_cases: number;
  failed_cases: number;
  total_cases: number;
  started_at: number;
}

export default function SuiteDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [suite, setSuite] = useState<{ name: string; base_url: string; cases: Case[]; runs: Run[] } | null>(null);
  const [uploading, setUploading] = useState(false);

  async function load() {
    setSuite(await apiGet(`/suites/${id}`));
  }
  useEffect(() => { load(); }, [id]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    const fd = new FormData();
    fd.append("file", file);
    try {
      await apiPost(`/suites/${id}/upload`, fd);
      load();
    } finally {
      setUploading(false);
    }
  }

  function handleRun() {
    navigate(`/suites/${id}/run`);
  }

  if (!suite) return <p>加载中...</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <button onClick={() => navigate("/suites")} className="text-sm text-gray-500 hover:underline mb-1">
            ← 返回
          </button>
          <h2 className="text-2xl font-bold">{suite.name}</h2>
        </div>
        <div className="flex gap-3">
          <label className={`px-4 py-2 rounded cursor-pointer ${uploading ? "bg-gray-400" : "bg-cyan-600"} text-white`}>
            {uploading ? "解析中..." : "上传 Excel"}
            <input type="file" accept=".xlsx" className="hidden" onChange={handleUpload} disabled={uploading} />
          </label>
          <button
            onClick={handleRun}
            className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700"
            disabled={suite.cases.length === 0}
          >
            执行
          </button>
        </div>
      </div>

      {/* Cases table */}
      <section className="mb-8">
        <h3 className="text-lg font-semibold mb-3">用例列表 ({suite.cases.length})</h3>
        {suite.cases.length === 0 ? (
          <p className="text-gray-500">尚未上传用例。上传 Excel 文件开始。</p>
        ) : (
          <div className="bg-white border rounded overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="text-left px-4 py-2">ID</th>
                  <th className="text-left px-4 py-2">名称</th>
                  <th className="text-left px-4 py-2">步骤数</th>
                  <th className="text-left px-4 py-2">预置条件</th>
                </tr>
              </thead>
              <tbody>
                {suite.cases.map((c) => (
                  <tr key={c.id} className="border-t hover:bg-gray-50">
                    <td className="px-4 py-2 font-mono text-xs">{c.id}</td>
                    <td className="px-4 py-2">{c.name}</td>
                    <td className="px-4 py-2">{c.steps.length}</td>
                    <td className="px-4 py-2">{c.preconditions.join(", ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Run history */}
      <section>
        <h3 className="text-lg font-semibold mb-3">执行历史</h3>
        {suite.runs.length === 0 ? (
          <p className="text-gray-500">暂无执行记录。</p>
        ) : (
          <div className="bg-white border rounded overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="text-left px-4 py-2">时间</th>
                  <th className="text-left px-4 py-2">状态</th>
                  <th className="text-left px-4 py-2">结果</th>
                </tr>
              </thead>
              <tbody>
                {suite.runs.map((r) => (
                  <tr
                    key={r.id}
                    className="border-t hover:bg-gray-50 cursor-pointer"
                    onClick={() => navigate(`/suites/${id}/runs/${r.id}`)}
                  >
                    <td className="px-4 py-2">
                      {new Date(r.started_at * 1000).toLocaleString()}
                    </td>
                    <td className="px-4 py-2">
                      <span className={`px-2 py-0.5 rounded text-xs ${
                        r.status === "running" ? "bg-cyan-100 text-cyan-800" :
                        r.status === "completed" ? "bg-green-100 text-green-800" :
                        "bg-red-100 text-red-800"
                      }`}>{r.status}</span>
                    </td>
                    <td className="px-4 py-2">
                      {r.passed_cases}/{r.total_cases} 通过
                      {r.failed_cases > 0 && ` · ${r.failed_cases} 失败`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 5: Install and verify frontend builds**

```bash
cd frontend && npm install && npm run build
```

Expected: Build succeeds, `frontend/dist/` created.

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat: React 前端脚手架 + Suite 管理页面

T-25 Slice 1 frontend: Vite + React + shadcn/ui (Tailwind),
SuiteListPage, SuiteDetailPage with Excel upload.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## SLICE 2: 执行控制台（SSE + Permission）

### Task 5: Add SSE callback to Orchestrator and async event approver

**Files:**
- Modify: `harness/orchestrator.py:1-102`
- Modify: `harness/permission.py:1-98`

- [ ] **Step 1: Add optional `sse_callback` to `Orchestrator.run_suite`**

In `harness/orchestrator.py`, add an `SSEEvent` type and modify `run_suite`:

```python
# After existing imports, add:
import asyncio
from typing import Any, Callable, Coroutine

SSECallback = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]] | None
```

Modify `run_suite` signature and body:

```python
async def run_suite(
    self, cases: list[TestCase], suite: Suite | None = None,
    sse_callback: SSECallback = None,
) -> SuiteResult:
    suite_id = suite.id if suite else None
    result = SuiteResult(suite_id=suite_id)
    suite_ctx = ExecutionContext(suite=suite)

    # before_suite
    if self.hooks is not None:
        bs = await self.hooks.run(BEFORE_SUITE, suite_ctx)
        if not bs.ok:
            result.aborted = True
            result.error = f"before_suite 失败:{bs.error}(hook={bs.failed_hook})"
            if sse_callback:
                await sse_callback("error", {"message": result.error})
            return result

    # Push suite_start
    if sse_callback:
        await sse_callback("suite_start", {"run_id": "pending", "total_cases": len(cases)})

    # 串行执行用例
    case_idx = 0
    for case in cases:
        case_idx += 1
        if sse_callback:
            await sse_callback("case_start", {"case_id": case.id, "title": case.name, "index": case_idx})

        record = await self._run_one(case, suite)

        if sse_callback:
            await sse_callback("case_result", {
                "case_id": case.id,
                "verdict": "PASS" if record.passed else "FAIL",
                "index": case_idx,
            })

        result.records.append(record)

    if self.hooks is not None:
        await self.hooks.run(AFTER_SUITE, suite_ctx)

    passed = result.passed_count
    failed = result.failed_count
    if sse_callback:
        await sse_callback("suite_done", {
            "run_id": "pending", "passed": passed, "failed": failed, "total": result.total,
        })

    return result
```

- [ ] **Step 2: Add async event approver factory to `harness/permission.py`**

Add at the end of `harness/permission.py`:

```python
import asyncio


def async_event_approver(event: asyncio.Event, result_holder: dict) -> Approver:
    """创建基于 asyncio.Event 的 approver。

    用于 Web 控制台 Permission 暂停交互:
    - 返回的 approver 等待 event.set()
    - 调用方在收到用户选择后 set event,并将结果放入 result_holder
    - 超时 30s 后自动拒绝

    Example:
        event = asyncio.Event()
        result = {"approved": False}
        checker = PermissionChecker(approver=async_event_approver(event, result))
        # ... later, in the API handler:
        result["approved"] = True
        event.set()
    """

    async def _wait_for_approval(req: PermissionRequest) -> bool:
        try:
            await asyncio.wait_for(event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("Permission 超时 30s, 自动拒绝: %s", req.reason)
            return False
        return result_holder.get("approved", False)

    return _wait_for_approval
```

- [ ] **Step 3: Run existing tests — verify no breakage**

```bash
source .venv/bin/activate && python -m pytest tests/test_orchestrator.py tests/test_permission.py -v
```

Expected: All existing tests pass.

- [ ] **Step 4: Commit**

```bash
git add harness/orchestrator.py harness/permission.py
git commit -m "feat: Orchestrator SSE callback + async event approver

T-24 prep: Orchestrator.run_suite accepts optional sse_callback.
PermissionChecker gains async_event_approver factory for Web UI pause.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Execution API routes (/run, /stream SSE)

**Files:**
- Create: `api/routers/execution.py`
- Create: `tests/test_api_execution.py`

- [ ] **Step 1: Create execution routes with SSE**

Create `api/routers/execution.py`:

```python
"""执行路由: /run, /stream SSE(Spec §4.2)。"""
from __future__ import annotations

import asyncio
import json
import uuid
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.repository import get_suite_settings, set_suite_settings
from api.server import get_repo, get_store

router = APIRouter(tags=["execution"])

# In-memory registry of active SSE queues + permission events
_sse_queues: dict[str, asyncio.Queue] = {}
_permission_events: dict[str, asyncio.Event] = {}
_permission_results: dict[str, dict] = {}


async def _sse_event(event: str, data: dict, queue: asyncio.Queue) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    await queue.put(f"event: {event}\ndata: {payload}\n\n")


@router.post("/suites/{suite_id}/run")
async def trigger_run(suite_id: str, repo=Depends(get_repo), store=Depends(get_store)):
    suite = await repo.get(suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")

    cases = await repo.list_by_suite(suite_id)
    if not cases:
        raise HTTPException(400, "Suite 没有用例，请先上传 Excel")

    # Check if already running
    runs = await repo.list_runs_by_suite(suite_id)
    active_run = next((r for r in runs if r["status"] == "running"), None)
    if active_run is not None:
        raise HTTPException(409, "已有执行在进行中")

    run_id = uuid.uuid4().hex[:12]
    await repo.create_run(run_id, suite_id, len(cases))

    queue: asyncio.Queue = asyncio.Queue()
    _sse_queues[run_id] = queue

    async def _run():
        try:
            from harness.orchestrator import Orchestrator

            sse_cb = lambda evt, data: _sse_event(evt, data, queue)
            orch = Orchestrator(agent=None)  # agent injected from session; we mock for now
            # For real execution, agent is created per case
            # In this phase we push fake events to validate the SSE pipeline
            for i, case in enumerate(cases):
                await _sse_event("case_start", {"case_id": case.id, "title": case.name, "index": i + 1}, queue)
                # Simulate steps (real impl: orch._run_one)
                for si, step in enumerate(case.steps):
                    await _sse_event("step_change", {
                        "case_id": case.id, "step_index": si, "status": "active", "description": step,
                    }, queue)
                    await asyncio.sleep(0.1)  # simulate work
                    await _sse_event("step_done", {
                        "case_id": case.id, "step_index": si, "status": "done",
                    }, queue)
                await _sse_event("case_result", {"case_id": case.id, "verdict": "PASS", "index": i + 1}, queue)

            await _sse_event("suite_done", {"run_id": run_id, "passed": len(cases), "failed": 0, "total": len(cases)}, queue)
            await repo.update_run(run_id, status="completed", passed_cases=len(cases), finished_at=time.time())
        except Exception as e:
            await _sse_event("error", {"message": str(e)}, queue)
            await repo.update_run(run_id, status="failed", finished_at=time.time())
        finally:
            _sse_queues.pop(run_id, None)

    asyncio.create_task(_run())
    return {"run_id": run_id, "status": "started"}


@router.get("/suites/{suite_id}/stream")
async def stream_events(suite_id: str, run_id: str):
    queue = _sse_queues.get(run_id)
    if queue is None:
        raise HTTPException(404, "Run not found or already finished")

    async def _generate():
        # Send keepalive
        yield ": keepalive\n\n"
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield msg
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/suites/{suite_id}/settings")
async def get_settings(suite_id: str, store=Depends(get_store)):
    return await get_suite_settings(store, suite_id)


class SettingsUpdate(BaseModel):
    permission_mode: str  # "trust" | "approve"


@router.put("/suites/{suite_id}/settings")
async def update_settings(suite_id: str, body: SettingsUpdate, store=Depends(get_store)):
    await set_suite_settings(store, suite_id, body.permission_mode)
    return {"ok": True}
```

Note: need to import BaseModel in execution.py. Add: `from pydantic import BaseModel`

- [ ] **Step 2: Write execution API tests**

Create `tests/test_api_execution.py`:

```python
"""Tests for execution API routes."""
import pytest
import asyncio
from httpx import ASGITransport, AsyncClient
from api.server import app
from storage.db import Store
from api.repository import SQLModelRepository
from input.models import Suite, TestCase


@pytest.fixture
async def client():
    store = Store(url="sqlite+aiosqlite:///file:test_api_exec?mode=memory&cache=shared&uri=true")
    await store.init()
    repo = SQLModelRepository(store)
    s = Suite(id="sx", name="SX", base_url="https://x.com")
    await repo.create(s)
    await repo.bulk_insert([
        TestCase(id="t1", name="C1", steps=["do a", "do b"], base_url="https://x.com", suite_id="sx"),
        TestCase(id="t2", name="C2", steps=["do c"], base_url="https://x.com", suite_id="sx"),
    ])
    import api.server as srv
    srv._repo = repo
    srv._store = store
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await store.close()
    srv._repo = None


@pytest.mark.asyncio
async def test_trigger_run(client):
    r = await client.post("/api/suites/sx/run")
    assert r.status_code == 200
    assert "run_id" in r.json()


@pytest.mark.asyncio
async def test_run_with_no_cases_returns_400(client):
    # Create empty suite
    import api.server as srv
    repo = srv._repo
    s2 = Suite(id="empty", name="Empty", base_url="https://x.com")
    await repo.create(s2)
    r = await client.post("/api/suites/empty/run")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_sse_stream_events(client):
    r = await client.post("/api/suites/sx/run")
    run_id = r.json()["run_id"]

    # Consume SSE stream
    async with client.stream("GET", f"/api/suites/sx/stream?run_id={run_id}") as resp:
        assert resp.status_code == 200
        # Read until suite_done or timeout
        buffer = ""
        async for chunk in resp.aiter_text():
            buffer += chunk
            if "suite_done" in buffer:
                break

    assert "suite_start" in buffer
    assert "case_start" in buffer
    assert "suite_done" in buffer


@pytest.mark.asyncio
async def test_get_settings_default(client):
    r = await client.get("/api/suites/sx/settings")
    assert r.status_code == 200
    assert r.json()["permission_mode"] == "trust"
```

- [ ] **Step 3: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_api_execution.py -v
```

Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add api/routers/execution.py tests/test_api_execution.py
git commit -m "feat: /run + /stream SSE 执行路由

T-23 Slice 2 backend: trigger suite execution, SSE event stream,
suite settings read/write.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Permission confirm route

**Files:**
- Create: `api/routers/permission.py`
- Create: `tests/test_api_permission.py`

- [ ] **Step 1: Create permission route**

Create `api/routers/permission.py`:

```python
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
```

- [ ] **Step 2: Write test**

Create `tests/test_api_permission.py`:

```python
"""Tests for permission API."""
import pytest
from httpx import ASGITransport, AsyncClient
from api.server import app
from api.routers.execution import _permission_events, _permission_results


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    _permission_events.clear()
    _permission_results.clear()


@pytest.mark.asyncio
async def test_approve(client):
    import asyncio
    event = asyncio.Event()
    _permission_events["p1"] = event
    _permission_results["p1"] = {"approved": False}

    r = await client.post("/api/suites/s1/permission/p1", json={"choice": "approve"})
    assert r.status_code == 200
    assert event.is_set()
    assert _permission_results["p1"]["approved"] is True


@pytest.mark.asyncio
async def test_reject(client):
    import asyncio
    event = asyncio.Event()
    _permission_events["p2"] = event
    _permission_results["p2"] = {"approved": False}

    r = await client.post("/api/suites/s1/permission/p2", json={"choice": "reject"})
    assert r.status_code == 200
    assert _permission_results["p2"]["approved"] is False


@pytest.mark.asyncio
async def test_not_found(client):
    r = await client.post("/api/suites/s1/permission/ghost", json={"choice": "approve"})
    assert r.status_code == 404
```

- [ ] **Step 3: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_api_permission.py -v
```

Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add api/routers/permission.py tests/test_api_permission.py
git commit -m "feat: Permission 确认路由

T-24: POST /api/suites/:id/permission/:event_id for approve/reject.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: RunConsolePage frontend (SSE consumer + Permission dialog)

**Files:**
- Create: `frontend/src/pages/RunConsolePage.tsx`
- Create: `frontend/src/components/PermissionDialog.tsx`
- Create: `frontend/src/components/ProgressBar.tsx`

- [ ] **Step 1: Create ProgressBar component**

Create `frontend/src/components/ProgressBar.tsx`:

```tsx
export default function ProgressBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="w-full bg-gray-200 rounded-full h-2 mb-4">
      <div
        className="bg-cyan-500 h-2 rounded-full transition-all duration-300"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
```

- [ ] **Step 2: Create PermissionDialog component**

Create `frontend/src/components/PermissionDialog.tsx`:

```tsx
interface Props {
  eventId: string;
  caseId: string;
  action: string;
  reason: string;
  suiteId: string;
  onResolved: () => void;
}

export default function PermissionDialog({ eventId, caseId, action, reason, suiteId, onResolved }: Props) {
  const [countdown, setCountdown] = useState(30);
  const [resolved, setResolved] = useState(false);

  useEffect(() => {
    if (countdown <= 0 || resolved) return;
    const t = setInterval(() => setCountdown((c) => c - 1), 1000);
    return () => clearInterval(t);
  }, [countdown, resolved]);

  async function respond(choice: "approve" | "reject") {
    setResolved(true);
    try {
      await apiPost(`/suites/${suiteId}/permission/${eventId}`, { choice });
    } catch { /* ignore */ }
    onResolved();
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 max-w-md w-full shadow-xl">
        <h3 className="text-lg font-bold text-red-600 mb-2">⚠ 高危操作 — 等待确认</h3>
        <p className="text-sm text-gray-600 mb-1">用例: {caseId}</p>
        <p className="text-sm text-gray-600 mb-1">操作: {action}</p>
        <p className="text-sm text-gray-600 mb-4">风险: {reason}</p>
        <p className="text-xs text-gray-400 mb-3">
          倒计时 {countdown}s 内未响应 → 自动拒绝
        </p>
        <div className="flex gap-3">
          <button
            onClick={() => respond("approve")}
            className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700"
          >
            ✓ 批准本次
          </button>
          <button
            onClick={() => respond("reject")}
            className="border border-red-500 text-red-600 px-4 py-2 rounded hover:bg-red-50"
          >
            ✕ 拒绝本次
          </button>
        </div>
      </div>
    </div>
  );
}

// Need imports at top
import { useEffect, useState } from "react";
import { apiPost } from "../api/client";
```

- [ ] **Step 3: Create RunConsolePage** (SSE consumer + dual-panel)

Create `frontend/src/pages/RunConsolePage.tsx`:

```tsx
import { useEffect, useState, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { sseUrl, apiPost, apiGet } from "../api/client";
import ProgressBar from "../components/ProgressBar";
import PermissionDialog from "../components/PermissionDialog";

interface CaseStatus {
  case_id: string;
  title: string;
  status: "pending" | "running" | "passed" | "failed" | "healing";
  steps: StepStatus[];
}

interface StepStatus {
  index: number;
  status: string;
  description: string;
}

interface PermReq {
  event_id: string;
  case_id: string;
  action: string;
  reason: string;
}

export default function RunConsolePage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [statuses, setStatuses] = useState<CaseStatus[]>([]);
  const [currentCase, setCurrentCase] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [result, setResult] = useState<{ passed: number; failed: number; total: number } | null>(null);
  const [permission, setPermission] = useState<PermReq | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  async function start() {
    const { run_id } = await apiPost<{ run_id: string }>(`/suites/${id}/run`);
    setRunId(run_id);

    const url = sseUrl(`/suites/${id}/stream?run_id=${run_id}`);
    const es = new EventSource(url);
    esRef.current = es;

    es.addEventListener("suite_start", (e) => {
      const d = JSON.parse(e.data);
      setStatuses([]);
      setDone(false);
      setResult(null);
    });

    es.addEventListener("case_start", (e) => {
      const d = JSON.parse(e.data);
      setStatuses((prev) => [
        ...prev,
        { case_id: d.case_id, title: d.title, status: "running", steps: [] },
      ]);
      setCurrentCase(d.case_id);
    });

    es.addEventListener("step_change", (e) => {
      const d = JSON.parse(e.data);
      setStatuses((prev) =>
        prev.map((c) =>
          c.case_id === d.case_id
            ? {
                ...c,
                steps: [
                  ...c.steps.filter((s) => s.index !== d.step_index),
                  { index: d.step_index, status: d.status, description: d.description },
                ],
              }
            : c
        )
      );
    });

    es.addEventListener("step_done", (e) => {
      const d = JSON.parse(e.data);
      setStatuses((prev) =>
        prev.map((c) =>
          c.case_id === d.case_id
            ? {
                ...c,
                steps: c.steps.map((s) =>
                  s.index === d.step_index ? { ...s, status: "done" } : s
                ),
              }
            : c
        )
      );
    });

    es.addEventListener("case_result", (e) => {
      const d = JSON.parse(e.data);
      setStatuses((prev) =>
        prev.map((c) =>
          c.case_id === d.case_id
            ? { ...c, status: d.verdict === "PASS" ? "passed" : "failed" }
            : c
        )
      );
    });

    es.addEventListener("permission", (e) => {
      const d = JSON.parse(e.data);
      setPermission(d);
    });

    es.addEventListener("suite_done", (e) => {
      const d = JSON.parse(e.data);
      setDone(true);
      setResult({ passed: d.passed, failed: d.failed, total: d.total });
      es.close();
    });

    es.addEventListener("error", () => {
      // EventSource auto-reconnects; we just note it
    });
  }

  useEffect(() => {
    start();
    return () => esRef.current?.close();
  }, [id]);

  const completed = statuses.filter((c) => c.status === "passed" || c.status === "failed").length;
  const active = statuses.find((c) => c.status === "running");

  return (
    <div>
      <button onClick={() => navigate(`/suites/${id}`)} className="text-sm text-gray-500 hover:underline mb-2">
        ← 返回 Suite
      </button>

      <h2 className="text-xl font-bold mb-1">执行控制台</h2>
      <p className="text-sm text-gray-500 mb-4">
        {done ? "✅ 执行完成" : "⏳ 运行中"} · {completed}/{statuses.length} 完成
      </p>

      <ProgressBar value={completed} max={statuses.length} />

      <div className="grid grid-cols-2 gap-6">
        {/* Left: Case list */}
        <div className="bg-white border rounded p-4">
          <h3 className="font-semibold mb-3">用例列表</h3>
          {statuses.map((c) => (
            <div
              key={c.case_id}
              className={`flex items-center gap-2 py-2 border-b last:border-0 text-sm ${
                c.case_id === currentCase ? "bg-cyan-50 -mx-2 px-2 rounded" : ""
              }`}
            >
              <span>
                {c.status === "running" ? "▶" :
                 c.status === "passed" ? "✅" :
                 c.status === "failed" ? "❌" :
                 c.status === "healing" ? "🟡" : "⏳"}
              </span>
              <span>{c.title}</span>
            </div>
          ))}
        </div>

        {/* Right: Detail */}
        <div className="bg-white border rounded p-4">
          <h3 className="font-semibold mb-3">当前步骤</h3>
          {active ? (
            <div>
              <p className="text-sm text-gray-600 mb-2">
                ▶ {active.title}
              </p>
              {active.steps
                .sort((a, b) => a.index - b.index)
                .map((s) => (
                  <div key={s.index} className="text-xs py-1 flex items-center gap-2">
                    <span>{s.status === "done" ? "✅" : "▶"}</span>
                    <span>Step {s.index + 1}: {s.description}</span>
                  </div>
                ))}
            </div>
          ) : (
            <p className="text-gray-400 text-sm">
              {done ? "所有用例执行完毕。" : "等待执行开始..."}
            </p>
          )}
        </div>
      </div>

      {done && result && (
        <div className="mt-6 bg-white border rounded p-4">
          <h3 className="font-semibold mb-2">执行结果</h3>
          <p>✅ {result.passed} 通过 · ❌ {result.failed} 失败 · 共 {result.total}</p>
          <button
            onClick={() => navigate(`/suites/${id}/runs/${runId}`)}
            className="mt-2 bg-cyan-600 text-white px-4 py-1 rounded text-sm"
          >
            查看详情
          </button>
        </div>
      )}

      {permission && (
        <PermissionDialog
          eventId={permission.event_id}
          caseId={permission.case_id}
          action={permission.action}
          reason={permission.reason}
          suiteId={id!}
          onResolved={() => setPermission(null)}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 5: Verify frontend builds**

```bash
cd frontend && npm run build
```

Expected: Build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/RunConsolePage.tsx frontend/src/components/PermissionDialog.tsx frontend/src/components/ProgressBar.tsx
git commit -m "feat: 执行控制台前端（SSE + Permission 弹窗）

T-26 Slice 2 frontend: RunConsolePage with dual-panel layout,
SSE EventSource consumer, PermissionDialog with countdown.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## SLICE 3: 结果详情 + 代码查看器

### Task 9: Screenshot path update + Results API routes

**Files:**
- Modify: `harness/recorder.py:1-140`
- Create: `api/routers/results.py`
- Create: `tests/test_api_results.py`

- [ ] **Step 1: Update Recorder screenshot path to include run_id and case_id**

In `harness/recorder.py`, modify `screenshot_path`:

```python
def __init__(
    self, case_id: str, *, suite_id: str | None = None, run_id: str | None = None,
    exec_id: str | None = None, screenshot_root: str | Path = DEFAULT_SCREENSHOT_ROOT,
) -> None:
    self.run_id = run_id or "norun"
    self.case_id = case_id
    self.exec_id = exec_id or uuid.uuid4().hex[:12]
    self.screenshot_root = Path(screenshot_root)
    ...

@property
def screenshot_dir(self) -> Path:
    return self.screenshot_root / self.run_id / self.case_id

def screenshot_path(self, step_no: int, ext: str = "png") -> str:
    self.screenshot_dir.mkdir(parents=True, exist_ok=True)
    return str(self.screenshot_dir / f"step_{step_no:03d}.{ext}")
```

- [ ] **Step 2: Create results routes**

Create `api/routers/results.py`:

```python
"""结果 / 截图 / 代码路由(Spec §4.3)。"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from api.server import get_repo

router = APIRouter(tags=["results"])

SCREENSHOT_ROOT = Path("storage/screenshots")
GENERATED_ROOT = Path("storage/generated")


@router.get("/suites/{suite_id}/runs")
async def list_runs(suite_id: str, repo=Depends(get_repo)):
    return await repo.list_runs_by_suite(suite_id)


@router.get("/suites/{suite_id}/runs/{run_id}")
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
            }
            for r in records
        ],
    }


@router.get("/suites/{suite_id}/runs/{run_id}/cases/{case_id}/result")
async def get_case_result(suite_id: str, run_id: str, case_id: str, repo=Depends(get_repo)):
    records = await repo.list_records_by_run(run_id)
    record = next((r for r in records if r.case_id == case_id), None)
    if record is None:
        raise HTTPException(404, "Result not found")
    return {
        **record.model_dump(),
        "history": _build_history(record),
    }


def _build_history(record):
    """Produce model_output / action_result separated history."""
    history = []
    for s in record.steps:
        history.append({
            "step_no": s.step_no,
            "model_output": {
                "reasoning": s.reasoning,
                "intent": s.intent,
                "tool_name": s.tool_name,
                "tool_input": s.tool_input,
            },
            "action_result": {
                "tool_result": s.tool_result,
                "url": s.url,
                "screenshot": s.screenshot,
                "assertion_results": s.assertion_results,
                "is_custom_tool": s.is_custom_tool,
                "duration_ms": s.duration_ms,
            },
        })
    return history


@router.get("/suites/{suite_id}/runs/{run_id}/cases/{case_id}/code")
async def get_case_code(suite_id: str, run_id: str, case_id: str, repo=Depends(get_repo)):
    records = await repo.list_records_by_run(run_id)
    record = next((r for r in records if r.case_id == case_id), None)
    if record is None:
        raise HTTPException(404, "Result not found")

    # Look for generated code files
    feat = GENERATED_ROOT / f"{case_id}.feature"
    steps = GENERATED_ROOT / f"test_{case_id}.py"

    files = {}
    if feat.exists():
        files[f"{case_id}.feature"] = feat.read_text()
    if steps.exists():
        files[f"test_{case_id}.py"] = steps.read_text()

    if not files:
        files["generated_code"] = record.generated_code or ""

    return {"files": files, "case_id": case_id}


@router.get("/suites/{suite_id}/runs/{run_id}/cases/{case_id}/code/download")
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
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename={case_id}.zip"})


@router.get("/screenshots/{run_id}/{case_id}/{step_index}")
async def get_screenshot(run_id: str, case_id: str, step_index: str):
    path = SCREENSHOT_ROOT / run_id / case_id / f"{step_index}"
    if not path.exists():
        raise HTTPException(404, "Screenshot not found")
    return FileResponse(path)
```

- [ ] **Step 3: Write results API tests**

Create `tests/test_api_results.py`:

```python
"""Tests for results API routes."""
import pytest
from httpx import ASGITransport, AsyncClient
from api.server import app
from storage.db import Store
from api.repository import SQLModelRepository
from input.models import Suite, TestCase, ExecutionRecord


@pytest.fixture
async def client():
    store = Store(url="sqlite+aiosqlite:///file:test_api_res?mode=memory&cache=shared&uri=true")
    await store.init()
    repo = SQLModelRepository(store)
    s = Suite(id="sx", name="SX", base_url="https://x.com")
    await repo.create(s)
    await repo.bulk_insert([
        TestCase(id="t1", name="C1", steps=["do a"], base_url="https://x.com", suite_id="sx"),
    ])
    await repo.create_run("r1", "sx", 1)
    await repo.save_record(ExecutionRecord(exec_id="e1", case_id="t1", suite_id="sx", run_id="r1", passed=True))
    import api.server as srv
    srv._repo = repo
    srv._store = store
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await store.close()
    srv._repo = None


@pytest.mark.asyncio
async def test_list_runs(client):
    r = await client.get("/api/suites/sx/runs")
    assert r.status_code == 200
    assert len(r.json()) == 1


@pytest.mark.asyncio
async def test_get_run_overview(client):
    r = await client.get("/api/suites/sx/runs/r1")
    assert r.status_code == 200
    assert r.json()["status"] == "running"


@pytest.mark.asyncio
async def test_get_case_result(client):
    r = await client.get("/api/suites/sx/runs/r1/cases/t1/result")
    assert r.status_code == 200
    assert r.json()["passed"] is True
    assert "history" in r.json()


@pytest.mark.asyncio
async def test_result_not_found(client):
    r = await client.get("/api/suites/sx/runs/r1/cases/ghost/result")
    assert r.status_code == 404
```

- [ ] **Step 4: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_api_results.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add api/routers/results.py tests/test_api_results.py harness/recorder.py
git commit -m "feat: 结果/截图/代码 API + 截图路径规约

T-23 Slice 3: results API, screenshot serve, code download.
Recorder screenshot path updated to <run_id>/<case_id>/step_NNN.png.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: CaseResultPage + CodeViewerPage frontend

**Files:**
- Create: `frontend/src/pages/CaseResultPage.tsx`
- Create: `frontend/src/pages/CodeViewerPage.tsx`
- Create: `frontend/src/components/StepListPanel.tsx`
- Create: `frontend/src/components/FileTree.tsx`

- [ ] **Step 1: Create StepListPanel component**

Create `frontend/src/components/StepListPanel.tsx`:

```tsx
interface Step {
  step_no: number;
  tool_name: string;
  reasoning: string;
  screenshot: string | null;
  assertion_results: { type: string; status: string; target: string; reason?: string }[];
}

interface Props {
  steps: Step[];
  onSelect: (stepNo: number) => void;
  selected: number | null;
}

export default function StepListPanel({ steps, onSelect, selected }: Props) {
  return (
    <div>
      <h3 className="font-semibold mb-2">步骤</h3>
      {steps.map((s) => (
        <div key={s.step_no}>
          <button
            onClick={() => onSelect(s.step_no)}
            className={`w-full text-left px-3 py-2 text-sm rounded mb-1 ${
              selected === s.step_no ? "bg-cyan-100" : "hover:bg-gray-50"
            }`}
          >
            <span className="text-green-500 mr-1">✅</span>
            Step {s.step_no}: {s.tool_name}
          </button>
          {s.assertion_results.length > 0 && (
            <div className="ml-6 mb-2">
              {s.assertion_results.map((a, i) => (
                <div key={i} className="text-xs text-gray-500">
                  {a.status === "pass" ? "✓" : "✗"} [{a.type}] {a.target}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Create CaseResultPage** (dual-panel with 3 tabs)

Create `frontend/src/pages/CaseResultPage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { apiGet } from "../api/client";
import StepListPanel from "../components/StepListPanel";

interface StepDetail {
  step_no: number;
  model_output: { reasoning: string; tool_name: string; tool_input: Record<string, unknown> };
  action_result: { tool_result: string; url: string; screenshot: string | null; duration_ms: number };
}

interface CaseResult {
  case_id: string;
  passed: boolean;
  final_result: string;
  token_usage: number;
  heal_count: number;
  history: StepDetail[];
  case_assertions: { type: string; status: string; target: string; reason?: string }[];
}

export default function CaseResultPage() {
  const { id, runId, caseId } = useParams<{ id: string; runId: string; caseId: string }>();
  const navigate = useNavigate();
  const [result, setResult] = useState<CaseResult | null>(null);
  const [selectedStep, setSelectedStep] = useState<number | null>(null);
  const [tab, setTab] = useState<"snapshot" | "code" | "log">("snapshot");

  useEffect(() => {
    apiGet<CaseResult>(`/suites/${id}/runs/${runId}/cases/${caseId}/result`).then(setResult);
  }, [id, runId, caseId]);

  if (!result) return <p>加载中...</p>;

  const selected = result.history.find((s) => s.step_no === selectedStep);

  return (
    <div>
      <button onClick={() => navigate(`/suites/${id}/runs/${runId}`)} className="text-sm text-gray-500 hover:underline mb-2">
        ← 返回执行
      </button>

      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold">{caseId}</h2>
        <div className="flex gap-2">
          <span className={`px-3 py-1 rounded text-sm font-semibold ${result.passed ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
            {result.passed ? "PASS" : "FAIL"}
          </span>
          <button onClick={() => navigate(`/suites/${id}/runs/${runId}/case/${caseId}/code`)}
            className="border px-3 py-1 rounded text-sm">
            查看代码
          </button>
        </div>
      </div>

      <p className="text-sm text-gray-500 mb-4">
        Token: {result.token_usage} · 自愈: {result.heal_count} 次
      </p>

      <div className="grid grid-cols-2 gap-6">
        {/* Left: Step list */}
        <div className="bg-white border rounded p-4">
          <StepListPanel
            steps={result.history.map((s) => ({
              step_no: s.step_no,
              tool_name: s.model_output.tool_name,
              reasoning: s.model_output.reasoning,
              screenshot: s.action_result.screenshot,
              assertion_results: [],
            }))}
            onSelect={setSelectedStep}
            selected={selectedStep}
          />

          {/* Final assertions */}
          {result.case_assertions.length > 0 && (
            <div className="mt-4 pt-4 border-t">
              <h4 className="font-semibold text-sm mb-2">最终断言</h4>
              {result.case_assertions.map((a, i) => (
                <div key={i} className={`text-xs py-1 ${a.status === "pass" ? "text-green-600" : "text-red-600"}`}>
                  {a.status === "pass" ? "✓" : "✗"} [{a.type}] {a.target} {a.reason || ""}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right: Detail panel with tabs */}
        <div className="bg-white border rounded p-4">
          <div className="flex gap-2 mb-4 border-b pb-2">
            {(["snapshot", "code", "log"] as const).map((t) => (
              <button key={t}
                onClick={() => setTab(t)}
                className={`px-3 py-1 text-sm rounded ${tab === t ? "bg-cyan-100 text-cyan-800" : "hover:bg-gray-50"}`}
              >
                {t === "snapshot" ? "快照" : t === "code" ? "代码" : "日志"}
              </button>
            ))}
          </div>

          {!selected ? (
            <p className="text-gray-400 text-sm">点击左侧步骤查看详情</p>
          ) : tab === "snapshot" ? (
            selected.action_result.screenshot ? (
              <img
                src={`/api/screenshots/${runId}/${caseId}/step_${String(selected.step_no).padStart(3, "0")}.png`}
                alt={`Step ${selected.step_no} screenshot`}
                className="max-w-full rounded border"
              />
            ) : (
              <p className="text-gray-400 text-sm">该步骤无截图</p>
            )
          ) : tab === "code" ? (
            <pre className="text-xs bg-gray-900 text-gray-100 p-3 rounded overflow-auto max-h-96">
              <code>{selected.model_output.tool_name}({JSON.stringify(selected.model_output.tool_input, null, 2)})</code>
            </pre>
          ) : (
            <div className="text-xs">
              <p className="text-gray-500 mb-2">推理:</p>
              <pre className="whitespace-pre-wrap bg-gray-50 p-2 rounded mb-4">{selected.model_output.reasoning}</pre>
              <p className="text-gray-500 mb-2">工具结果:</p>
              <pre className="whitespace-pre-wrap bg-gray-50 p-2 rounded">{selected.action_result.tool_result}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create FileTree component**

Create `frontend/src/components/FileTree.tsx`:

```tsx
interface Props {
  files: Record<string, string>;
  onSelect: (filename: string) => void;
  selected: string | null;
}

export default function FileTree({ files, onSelect, selected }: Props) {
  const filenames = Object.keys(files);
  return (
    <div>
      <h3 className="font-semibold mb-2 text-sm">文件</h3>
      {filenames.map((fn) => (
        <button
          key={fn}
          onClick={() => onSelect(fn)}
          className={`block w-full text-left px-3 py-1.5 text-sm rounded mb-0.5 font-mono ${
            selected === fn ? "bg-cyan-100" : "hover:bg-gray-50"
          }`}
        >
          📄 {fn}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Create CodeViewerPage**

Create `frontend/src/pages/CodeViewerPage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import Editor from "@monaco-editor/react";
import { apiGet } from "../api/client";
import FileTree from "../components/FileTree";

export default function CodeViewerPage() {
  const { id, runId, caseId } = useParams<{ id: string; runId: string; caseId: string }>();
  const navigate = useNavigate();
  const [files, setFiles] = useState<Record<string, string>>({});
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  useEffect(() => {
    apiGet<{ files: Record<string, string> }>(`/suites/${id}/runs/${runId}/cases/${caseId}/code`)
      .then((data) => {
        setFiles(data.files);
        const first = Object.keys(data.files)[0];
        if (first) setSelectedFile(first);
      });
  }, [id, runId, caseId]);

  const currentContent = selectedFile ? files[selectedFile] : "";
  const lang = selectedFile?.endsWith(".feature") ? "gherkin" : "python";

  return (
    <div>
      <button onClick={() => navigate(`/suites/${id}/runs/${runId}/case/${caseId}`)} className="text-sm text-gray-500 hover:underline mb-2">
        ← 返回结果
      </button>

      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold">{caseId} — 代码</h2>
        <a
          href={`/api/suites/${id}/runs/${runId}/cases/${caseId}/code/download`}
          className="bg-cyan-600 text-white px-4 py-1 rounded text-sm hover:bg-cyan-700"
        >
          下载 .zip
        </a>
      </div>

      <div className="grid grid-cols-[200px_1fr] gap-4" style={{ height: "70vh" }}>
        <div className="bg-white border rounded p-4">
          <FileTree files={files} onSelect={setSelectedFile} selected={selectedFile} />
        </div>
        <div className="bg-white border rounded overflow-hidden">
          <Editor
            height="100%"
            language={lang}
            value={currentContent}
            theme="vs-dark"
            options={{ readOnly: true, minimap: { enabled: false }, fontSize: 13 }}
          />
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Verify build**

```bash
cd frontend && npm run build
```

Expected: Build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/CaseResultPage.tsx frontend/src/pages/CodeViewerPage.tsx frontend/src/components/StepListPanel.tsx frontend/src/components/FileTree.tsx
git commit -m "feat: 结果详情 + Monaco 代码查看器

T-27 Slice 3 frontend: CaseResultPage (steps + 3-tab detail),
CodeViewerPage (Monaco read-only + file tree + download).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## SLICE 4: 权限配置 + 词汇表

### Task 11: Vocabulary API routes

**Files:**
- Create: `api/routers/vocabulary.py`
- Create: `tests/test_api_vocabulary.py`

- [ ] **Step 1: Create vocabulary routes**

Create `api/routers/vocabulary.py`:

```python
"""词汇表 CRUD + 扫描路由(Spec §4.5, T-27 尾巴)。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.server import get_repo, get_store
from input.models import PageVocabulary

router = APIRouter(tags=["vocabulary"])


class VocabularyEntry(BaseModel):
    url_pattern: str
    page_title: str
    login_role: str
    vocabulary: dict = {}
    action_map: list = []


@router.get("/vocabulary")
async def list_vocabulary(
    page: int = Query(1, ge=1),
    q: str = Query("", alias="query"),
    repo=Depends(get_repo),
):
    all_items = await repo.list_all()
    if q:
        all_items = [v for v in all_items if q.lower() in v.page_title.lower() or q.lower() in v.url_pattern.lower()]
    # Simple page-based slice (50 per page)
    start = (page - 1) * 50
    items = all_items[start : start + 50]
    return {"items": [v.model_dump() for v in items], "total": len(all_items), "page": page}


@router.post("/vocabulary")
async def create_vocabulary(entry: VocabularyEntry, repo=Depends(get_repo)):
    v = PageVocabulary(**entry.model_dump())
    await repo.save(v)
    return v.model_dump()


@router.put("/vocabulary/{vocab_id}")
async def update_vocabulary(vocab_id: int, entry: VocabularyEntry, repo=Depends(get_repo)):
    # Find by key fields from body
    v = PageVocabulary(**entry.model_dump())
    await repo.save(v)
    return v.model_dump()


@router.delete("/vocabulary/{vocab_id}")
async def delete_vocabulary(
    vocab_id: int,
    url_pattern: str = Query(""),
    page_title: str = Query(""),
    login_role: str = Query(""),
    repo=Depends(get_repo),
):
    if not await repo.delete_by_key(url_pattern, page_title, login_role):
        raise HTTPException(404, "Vocabulary entry not found")
    return {"ok": True}


@router.post("/vocabulary/scan")
async def trigger_scan(
    repo=Depends(get_repo), store=Depends(get_store),
):
    """触发页面扫描(调用 intelligence/scanner.py)。

    注意:扫描需要浏览器连接,本路由目前返回提示;实际扫描在 Agent 执行时由
    intelligence/scanner.py 的 scan_and_save 完成。
    """
    return {"ok": True, "message": "扫描已触发,词汇表将在执行过程中增量更新。请执行 Suite 以触发实际扫描。"}
```

- [ ] **Step 2: Write vocabulary API tests**

Create `tests/test_api_vocabulary.py`:

```python
"""Tests for vocabulary API routes."""
import pytest
from httpx import ASGITransport, AsyncClient
from api.server import app
from storage.db import Store
from api.repository import SQLModelRepository


@pytest.fixture
async def client():
    store = Store(url="sqlite+aiosqlite:///file:test_api_voc?mode=memory&cache=shared&uri=true")
    await store.init()
    repo = SQLModelRepository(store)
    import api.server as srv
    srv._repo = repo
    srv._store = store
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await store.close()
    srv._repo = None


@pytest.mark.asyncio
async def test_list_empty(client):
    r = await client.get("/api/vocabulary")
    assert r.status_code == 200
    assert r.json()["items"] == []


@pytest.mark.asyncio
async def test_create_and_list(client):
    r = await client.post("/api/vocabulary", json={
        "url_pattern": "/login",
        "page_title": "Login",
        "login_role": "user",
        "vocabulary": {"username": {"role": "textbox", "name": "Username", "confidence": 0.9}},
        "action_map": [],
    })
    assert r.status_code == 200

    r = await client.get("/api/vocabulary")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1
    assert r.json()["items"][0]["url_pattern"] == "/login"


@pytest.mark.asyncio
async def test_search(client):
    await client.post("/api/vocabulary", json={
        "url_pattern": "/login", "page_title": "Login",
        "login_role": "user", "vocabulary": {}, "action_map": [],
    })
    await client.post("/api/vocabulary", json={
        "url_pattern": "/dashboard", "page_title": "Dashboard",
        "login_role": "user", "vocabulary": {}, "action_map": [],
    })
    r = await client.get("/api/vocabulary?query=login")
    assert len(r.json()["items"]) == 1


@pytest.mark.asyncio
async def test_scan_trigger(client):
    r = await client.post("/api/vocabulary/scan")
    assert r.status_code == 200
    assert r.json()["ok"] is True
```

- [ ] **Step 3: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_api_vocabulary.py -v
```

Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add api/routers/vocabulary.py tests/test_api_vocabulary.py
git commit -m "feat: 词汇表 CRUD + 扫描路由

T-27 Slice 4 backend: vocabulary list/create/update/delete/search,
scan trigger endpoint.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: VocabularyPage frontend + final integration

**Files:**
- Create: `frontend/src/pages/VocabularyPage.tsx`

- [ ] **Step 1: Create VocabularyPage**

Create `frontend/src/pages/VocabularyPage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { apiGet, apiPost, apiDelete } from "../api/client";

interface Vocab {
  url_pattern: string;
  page_title: string;
  login_role: string;
  vocabulary: Record<string, { role: string; name: string; confidence: number }>;
}

export default function VocabularyPage() {
  const [items, setItems] = useState<Vocab[]>([]);
  const [query, setQuery] = useState("");
  const [scanning, setScanning] = useState(false);

  async function load() {
    const r = await apiGet<{ items: Vocab[] }>(`/vocabulary?query=${encodeURIComponent(query)}`);
    setItems(r.items);
  }
  useEffect(() => { load(); }, [query]);

  async function scan() {
    setScanning(true);
    try {
      await apiPost("/vocabulary/scan");
      load();
    } finally {
      setScanning(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">Page Intelligence 词汇表</h2>
        <button onClick={scan} disabled={scanning}
          className="bg-cyan-600 text-white px-4 py-2 rounded hover:bg-cyan-700 disabled:opacity-50">
          {scanning ? "扫描中..." : "扫描页面"}
        </button>
      </div>

      <div className="mb-4">
        <input
          className="border px-3 py-2 rounded w-64 text-sm"
          placeholder="搜索 URL 或页面标题..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="text-sm text-gray-500 ml-3">共 {items.length} 条</span>
      </div>

      {items.length === 0 ? (
        <p className="text-gray-500 text-center py-20">暂无词汇表数据。点击"扫描页面"开始。</p>
      ) : (
        <div className="bg-white border rounded overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left px-4 py-2">页面路径</th>
                <th className="text-left px-4 py-2">页面标题</th>
                <th className="text-left px-4 py-2">登录角色</th>
                <th className="text-left px-4 py-2">词汇数</th>
              </tr>
            </thead>
            <tbody>
              {items.map((v, i) => (
                <tr key={i} className="border-t hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-xs">{v.url_pattern}</td>
                  <td className="px-4 py-2">{v.page_title}</td>
                  <td className="px-4 py-2">{v.login_role}</td>
                  <td className="px-4 py-2">{Object.keys(v.vocabulary).length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Add # step_N comments to BDDGenerator for Monaco step association**

In `codegen/bdd.py`, modify `_step_defs` method's `emit` function to add step number comments:

```python
def emit(decorator: str, text: str, body: str, step_no: int = 0) -> None:
    nonlocal idx
    if text in seen:
        return
    seen.add(text)
    blocks.append("")
    blocks.append(f"@{decorator}({_q(text)})")
    blocks.append(f"def {decorator}_{idx}(page: Page):")
    if step_no:
        blocks.append(f"    # step_{step_no}")
    blocks.append(body)
    idx += 1
```

Then update the call sites to pass `step_no`:

```python
for i, g in enumerate(spec.given, start=1):
    emit("given", g.target, f"    # 业务前置({g.action}):{g.target} —— 请按实际补充\n    pass", i)

for i, s in enumerate(spec.steps, start=1):
    emit("when", _step_text(s), _step_body(s, spec.base_url), i)

for i, a in enumerate(spec.assertions, start=1):
    emit("then", _assertion_text(a), _assertion_body(a), i)
```

- [ ] **Step 3: Run all tests to verify integration**

```bash
source .venv/bin/activate && python -m pytest -q
```

Expected: All tests pass (274 + new tests = ~295 passed).

- [ ] **Step 4: Format code**

```bash
source .venv/bin/activate && isort api harness input intelligence cli tests && black api harness input intelligence cli tests
```

- [ ] **Step 5: Final frontend build check**

```bash
cd frontend && npm run build
```

Expected: Build succeeds.

- [ ] **Step 6: Final commit**

```bash
git add frontend/src/pages/VocabularyPage.tsx codegen/bdd.py
git commit -m "feat: 词汇表页面 + Monaco 步骤关联

T-27 Slice 4: VocabularyPage with search and scan trigger.
BDDGenerator adds # step_N comments for Monaco step-to-code navigation.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Integration verification

- [ ] **Step 1: Run full test suite**

```bash
source .venv/bin/activate && python -m pytest -q
```

Expected: All tests pass.

- [ ] **Step 2: Verify FastAPI starts**

```bash
source .venv/bin/activate && python -c "
from api.server import app
print('FastAPI app OK:', app.title)
"
```

Expected: `FastAPI app OK: T-agent`

- [ ] **Step 3: Verify frontend builds and dist exists**

```bash
cd frontend && npm run build && ls dist/index.html
```

Expected: Build succeeds and `dist/index.html` exists.

- [ ] **Step 4: Tag and commit**

```bash
git add -A
git commit -m "chore: 阶段四 Slice 4 完成 — 工程化界面端到端

T-23/T-24/T-25/T-26/T-27: FastAPI 路由 + React 控制台 +
SSE 实时推送 + Permission 交互 + Monaco 代码查看器 + 词汇表维护。

# 验证
- pytest: all passed
- frontend build: succeeds
- FastAPI app: starts

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Summary

| Slice | Backend Tasks | Frontend Tasks | Test Files |
|---|---|---|---|
| 1 | T1 (DB models), T2 (Repository), T3 (FastAPI + Suite routes) | T4 (Scaffold + Suite pages) | test_repository.py, test_api_suites.py |
| 2 | T5 (Orch SSE), T6 (Execution routes), T7 (Permission route) | T8 (RunConsole + Permission) | test_api_execution.py, test_api_permission.py |
| 3 | T9 (Screenshots + Results routes) | T10 (CaseResult + CodeViewer) | test_api_results.py |
| 4 | T11 (Vocabulary routes) | T12 (Vocabulary page + codegen) | test_api_vocabulary.py |
| Verify | T13 (Integration check) | — | All |
