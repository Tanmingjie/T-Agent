"""SQLModel 持久化(规格 §4 注记 / §0 原则 2、4 / §7,T-21)。

数据层抽象:业务代码只与领域模型(``input.models`` 的 pydantic 结构)打交道,
**不直接写 SQL**;``Store`` 内部在领域模型 ↔ SQLModel 表行之间转换。SQLite +
aiosqlite;换 PostgreSQL 只改连接串。

核心表预留同步字段 ``updated_at`` / ``owner`` / ``external_id``(实现原则 4)。
嵌套/列表字段(steps、assertions、vocabulary、hooks…)以 JSON 列存储,保持单表简洁、
不引入 ORM 关系,契合"换库只改连接串"。

表行类用 ``*Row`` 命名以与领域模型区分;``PageVocabulary`` 无自然主键,用自增 id +
(url_pattern, page_title, login_role) 作为缓存键(规格 §5.5)。
"""

from __future__ import annotations

import time
from typing import Type, TypeVar

from sqlalchemy import JSON, Column
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from input.models import (
    ExecutionRecord,
    PageVocabulary,
    SessionProfile,
    Suite,
    TestCase,
)

# ── 表行定义(table=True) ──────────────────────────────────────


class SuiteRow(SQLModel, table=True):
    __tablename__ = "suite"
    id: str = Field(primary_key=True)
    name: str = ""
    base_url: str = ""
    session_profile: str | None = None
    page_intelligence_id: str | None = None
    code_generator: str = "BDDGenerator"
    custom_prompt: str = ""
    hooks: dict = Field(default_factory=dict, sa_column=Column(JSON))
    external_id: str | None = None
    owner: str | None = None
    updated_at: float = 0.0


class TestCaseRow(SQLModel, table=True):
    __tablename__ = "test_case"
    id: str = Field(primary_key=True)
    name: str = ""
    preconditions: list = Field(default_factory=list, sa_column=Column(JSON))
    steps: list = Field(default_factory=list, sa_column=Column(JSON))
    expected: list = Field(default_factory=list, sa_column=Column(JSON))
    base_url: str = ""
    suite_id: str | None = Field(default=None, index=True)
    external_id: str | None = None
    owner: str | None = None
    updated_at: float = 0.0


class SessionProfileRow(SQLModel, table=True):
    __tablename__ = "session_profile"
    name: str = Field(primary_key=True)
    login_aw: str = ""
    cookie_store: str = ""
    valid_until: float | None = None
    base_url: str = ""
    owner: str | None = None
    updated_at: float = 0.0


class ExecutionRecordRow(SQLModel, table=True):
    __tablename__ = "execution_record"
    exec_id: str = Field(primary_key=True)
    case_id: str = Field(default="", index=True)
    suite_id: str | None = None
    run_id: str | None = Field(default=None, index=True)  # 关联 RunRecord(Phase 4)
    steps: list = Field(default_factory=list, sa_column=Column(JSON))
    passed: bool = False
    case_assertions: list = Field(default_factory=list, sa_column=Column(JSON))
    final_result: str = ""
    generated_code: str = ""
    token_usage: int = 0
    heal_count: int = 0
    start_time: float = 0.0
    end_time: float | None = None
    external_id: str | None = None
    owner: str | None = None
    updated_at: float = 0.0


class PageVocabularyRow(SQLModel, table=True):
    __tablename__ = "page_vocabulary"
    id: int | None = Field(default=None, primary_key=True)
    url_pattern: str = Field(default="", index=True)
    page_title: str = ""
    login_role: str = ""
    vocabulary: dict = Field(default_factory=dict, sa_column=Column(JSON))
    action_map: list = Field(default_factory=list, sa_column=Column(JSON))
    stale: bool = False
    scanned_at: float = 0.0
    updated_at: float = 0.0


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


_T = TypeVar("_T", bound=SQLModel)


class Store:
    """异步仓储。CRUD 方法收发领域模型,内部转换为表行。"""

    def __init__(self, url: str = "sqlite+aiosqlite:///storage/ai_test.db") -> None:
        self.engine = create_async_engine(url, future=True)
        self._sf = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    # —— 通用 upsert(按主键 merge)——

    async def _upsert(self, row_cls: Type[_T], domain) -> None:
        data = domain.model_dump()
        data["updated_at"] = time.time()
        async with self._sf() as s:
            await s.merge(row_cls(**data))
            await s.commit()

    # —— TestCase ——

    async def save_case(self, tc: TestCase) -> None:
        await self._upsert(TestCaseRow, tc)

    async def get_case(self, case_id: str) -> TestCase | None:
        async with self._sf() as s:
            row = await s.get(TestCaseRow, case_id)
            return TestCase(**row.model_dump()) if row else None

    async def list_cases(self, suite_id: str | None = None) -> list[TestCase]:
        stmt = select(TestCaseRow)
        if suite_id is not None:
            stmt = stmt.where(TestCaseRow.suite_id == suite_id)
        async with self._sf() as s:
            rows = (await s.exec(stmt)).all()
            return [TestCase(**r.model_dump()) for r in rows]

    # —— ExecutionRecord ——

    async def save_record(self, rec: ExecutionRecord) -> None:
        await self._upsert(ExecutionRecordRow, rec)

    async def get_record(self, exec_id: str) -> ExecutionRecord | None:
        async with self._sf() as s:
            row = await s.get(ExecutionRecordRow, exec_id)
            return ExecutionRecord(**row.model_dump()) if row else None

    async def list_records(self, case_id: str | None = None) -> list[ExecutionRecord]:
        stmt = select(ExecutionRecordRow)
        if case_id is not None:
            stmt = stmt.where(ExecutionRecordRow.case_id == case_id)
        async with self._sf() as s:
            rows = (await s.exec(stmt)).all()
            return [ExecutionRecord(**r.model_dump()) for r in rows]

    # —— Suite ——

    async def save_suite(self, suite: Suite) -> None:
        await self._upsert(SuiteRow, suite)

    async def get_suite(self, suite_id: str) -> Suite | None:
        async with self._sf() as s:
            row = await s.get(SuiteRow, suite_id)
            return Suite(**row.model_dump()) if row else None

    async def list_suites(self) -> list[Suite]:
        async with self._sf() as s:
            rows = (await s.exec(select(SuiteRow))).all()
            return [Suite(**r.model_dump()) for r in rows]

    # —— SessionProfile ——

    async def save_session_profile(self, p: SessionProfile) -> None:
        await self._upsert(SessionProfileRow, p)

    async def get_session_profile(self, name: str) -> SessionProfile | None:
        async with self._sf() as s:
            row = await s.get(SessionProfileRow, name)
            return SessionProfile(**row.model_dump()) if row else None

    # —— PageVocabulary(按缓存键 upsert)——

    async def save_vocabulary(self, v: PageVocabulary) -> None:
        data = v.model_dump()
        data["updated_at"] = time.time()
        async with self._sf() as s:
            stmt = select(PageVocabularyRow).where(
                PageVocabularyRow.url_pattern == v.url_pattern,
                PageVocabularyRow.page_title == v.page_title,
                PageVocabularyRow.login_role == v.login_role,
            )
            existing = (await s.exec(stmt)).first()
            if existing is not None:
                for k, val in data.items():
                    setattr(existing, k, val)
                s.add(existing)
            else:
                s.add(PageVocabularyRow(**data))
            await s.commit()

    async def get_vocabulary(
        self, url_pattern: str, page_title: str, login_role: str
    ) -> PageVocabulary | None:
        async with self._sf() as s:
            stmt = select(PageVocabularyRow).where(
                PageVocabularyRow.url_pattern == url_pattern,
                PageVocabularyRow.page_title == page_title,
                PageVocabularyRow.login_role == login_role,
            )
            row = (await s.exec(stmt)).first()
            if row is None:
                return None
            data = row.model_dump()
            data.pop("id", None)  # 领域模型无 id 字段
            return PageVocabulary(**data)

    async def list_vocabularies(self) -> list[PageVocabulary]:
        async with self._sf() as s:
            rows = (await s.exec(select(PageVocabularyRow))).all()
            out = []
            for r in rows:
                data = r.model_dump()
                data.pop("id", None)
                out.append(PageVocabulary(**data))
            return out
