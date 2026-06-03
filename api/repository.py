"""Repository 抽象层(规格 §4 注记, phase 4)。

路由依赖这些接口而非直接依赖 storage/db.py,便于测试和换存储。
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod

from sqlmodel import delete as sql_delete
from sqlmodel import select as sql_select

from input.models import (
    ExecutionRecord,
    PageVocabulary,
    Suite,
    TestCase,
)
from storage.db import RunRecordRow, Store, SuiteSettingsRow


class SuiteRepository(ABC):
    @abstractmethod
    async def create(self, suite: Suite) -> Suite: ...

    @abstractmethod
    async def get_suite(self, suite_id: str) -> Suite | None: ...

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
    async def get_case(self, case_id: str) -> TestCase | None: ...

    @abstractmethod
    async def update_precondition(
        self, case_id: str, precondition_index: int, confirmed: bool
    ) -> bool: ...


class ExecutionRepository(ABC):
    @abstractmethod
    async def create_run(self, run_id: str, suite_id: str, total_cases: int) -> None: ...

    @abstractmethod
    async def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        passed_cases: int | None = None,
        failed_cases: int | None = None,
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
    async def list_vocabularies(
        self, page: int = 1, query: str | None = None
    ) -> list[PageVocabulary]: ...

    @abstractmethod
    async def get_vocabulary(
        self, url_pattern: str, page_title: str, login_role: str
    ) -> PageVocabulary | None: ...

    @abstractmethod
    async def save(self, vocab: PageVocabulary) -> None: ...

    @abstractmethod
    async def bulk_upsert(self, entries: list[PageVocabulary]) -> int: ...

    @abstractmethod
    async def delete_by_key(self, url_pattern: str, page_title: str, login_role: str) -> bool: ...


class SQLModelRepository(
    SuiteRepository, TestCaseRepository, ExecutionRepository, VocabularyRepository
):
    """基于 storage/db.Store 的 SQLModel 实现。"""

    def __init__(self, store: Store) -> None:
        self._store = store

    # ── Suite ──

    async def create(self, suite: Suite) -> Suite:
        await self._store.save_suite(suite)
        return suite

    async def get_suite(self, suite_id: str) -> Suite | None:
        return await self._store.get_suite(suite_id)

    async def list_all(self) -> list[Suite]:
        return await self._store.list_suites()

    async def delete(self, suite_id: str) -> bool:
        suite = await self._store.get_suite(suite_id)
        if suite is None:
            return False
        async with self._store._sf() as s:
            from storage.db import (
                ExecutionRecordRow,
                RunRecordRow,
                SuiteRow,
                SuiteSettingsRow,
                TestCaseRow,
            )

            await s.exec(
                sql_delete(ExecutionRecordRow).where(ExecutionRecordRow.suite_id == suite_id)
            )
            await s.exec(sql_delete(RunRecordRow).where(RunRecordRow.suite_id == suite_id))
            await s.exec(sql_delete(TestCaseRow).where(TestCaseRow.suite_id == suite_id))
            await s.exec(sql_delete(SuiteSettingsRow).where(SuiteSettingsRow.suite_id == suite_id))
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

    async def get_case(self, case_id: str) -> TestCase | None:
        return await self._store.get_case(case_id)

    async def update_precondition(
        self, case_id: str, precondition_index: int, confirmed: bool
    ) -> bool:
        tc = await self._store.get_case(case_id)
        if tc is None:
            return False
        confirmed_list = list(tc.precondition_confirmed)
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
            id=run_id,
            suite_id=suite_id,
            status="running",
            total_cases=total_cases,
            started_at=time.time(),
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
                "id": row.id,
                "suite_id": row.suite_id,
                "status": row.status,
                "total_cases": row.total_cases,
                "passed_cases": row.passed_cases,
                "failed_cases": row.failed_cases,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
            }

    async def list_runs_by_suite(self, suite_id: str) -> list[dict]:
        async with self._store._sf() as s:
            stmt = (
                sql_select(RunRecordRow)
                .where(RunRecordRow.suite_id == suite_id)
                .order_by(RunRecordRow.started_at.desc())
            )
            rows = (await s.exec(stmt)).all()
            return [
                {
                    "id": r.id,
                    "suite_id": r.suite_id,
                    "status": r.status,
                    "total_cases": r.total_cases,
                    "passed_cases": r.passed_cases,
                    "failed_cases": r.failed_cases,
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                }
                for r in rows
            ]

    async def save_record(self, record: ExecutionRecord) -> None:
        await self._store.save_record(record)

    async def get_record(self, exec_id: str) -> ExecutionRecord | None:
        return await self._store.get_record(exec_id)

    async def list_records_by_run(self, run_id: str) -> list[ExecutionRecord]:
        from storage.db import ExecutionRecordRow

        async with self._store._sf() as s:
            stmt = sql_select(ExecutionRecordRow).where(ExecutionRecordRow.run_id == run_id)
            rows = (await s.exec(stmt)).all()
            return [ExecutionRecord(**r.model_dump()) for r in rows]

    async def list_records_by_suite(self, suite_id: str) -> list[ExecutionRecord]:
        return await self._store.list_records(suite_id=suite_id)

    # ── Vocabulary ──

    async def list_vocabularies(
        self, page: int = 1, query: str | None = None
    ) -> list[PageVocabulary]:
        return await self._store.list_vocabularies()

    async def get_vocabulary(
        self, url_pattern: str, page_title: str, login_role: str
    ) -> PageVocabulary | None:
        return await self._store.get_vocabulary(url_pattern, page_title, login_role)

    async def save(self, vocab: PageVocabulary) -> None:
        await self._store.save_vocabulary(vocab)

    async def bulk_upsert(self, entries: list[PageVocabulary]) -> int:
        for v in entries:
            await self._store.save_vocabulary(v)
        return len(entries)

    async def delete_by_key(self, url_pattern: str, page_title: str, login_role: str) -> bool:
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
        suite_id=suite_id,
        permission_mode=permission_mode,
        updated_at=time.time(),
    )
    async with store._sf() as s:
        await s.merge(row)
        await s.commit()
