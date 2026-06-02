"""Tests for api/repository.py using SQLite in-memory."""

import pytest

from api.repository import SQLModelRepository
from input.models import ExecutionRecord, Suite, TestCase
from storage.db import Store


@pytest.fixture
async def repo():
    store = Store(url="sqlite+aiosqlite://")
    await store.init()
    r = SQLModelRepository(store)
    yield r
    await store.close()


@pytest.mark.asyncio
async def test_suite_crud(repo):
    s = Suite(id="s1", name="Test Suite", base_url="https://example.com")
    await repo.create(s)
    assert (await repo.get_suite("s1")).name == "Test Suite"
    assert len(await repo.list_all()) == 1
    assert await repo.delete("s1") is True
    assert await repo.get_suite("s1") is None


@pytest.mark.asyncio
async def test_case_bulk_insert_and_list(repo):
    cases = [
        TestCase(
            id="tc1",
            name="Case 1",
            steps=["step a"],
            base_url="https://x.com",
            suite_id="s1",
        ),
        TestCase(
            id="tc2",
            name="Case 2",
            steps=["step b"],
            base_url="https://x.com",
            suite_id="s1",
        ),
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
