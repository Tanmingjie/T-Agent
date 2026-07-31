from types import SimpleNamespace

import pytest

from scripts.worker import _run_one


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_ids", "legacy_case_id", "expected"),
    [(["B", "A"], None, ["B", "A"]), (None, "A", ["A"])],
)
async def test_worker_passes_selected_scope_and_supports_legacy_rows(
    monkeypatch, case_ids, legacy_case_id, expected
):
    captured = {}

    class _Store:
        def __init__(self, url):
            pass

        async def init(self):
            pass

        async def heartbeat_run(self, run_id):
            pass

        async def complete_queued_run(self, run_id, status):
            captured["queue_status"] = status

        async def close(self):
            pass

    async def _execute_run(**kwargs):
        captured["case_ids"] = kwargs["case_ids"]

    monkeypatch.setattr("storage.db.Store", _Store)
    monkeypatch.setattr("api.run_executor.execute_run", _execute_run)
    claimed = SimpleNamespace(
        run_id="r1",
        suite_id="s1",
        case_ids=case_ids,
        case_id=legacy_case_id,
        skill_names=[],
    )

    await _run_one("sqlite+aiosqlite://", claimed)

    assert captured["case_ids"] == expected
    assert captured["queue_status"] == "done"
