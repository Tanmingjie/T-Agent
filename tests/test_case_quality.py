from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from api.repository import SQLModelRepository
from api.run_executor import execute_run
from api.server import app
from harness.case_quality import QualityGateOptions, assess_case_executability
from harness.llm import LLMResponse
from input.models import (
    CaseExecutabilityAssessment,
    CaseExecutionMemory,
    CaseQualityDimension,
    CaseQualityIssue,
    Suite,
    TestCase,
    TestSpec,
)
from storage.db import Store


class FakeLLM:
    def __init__(
        self,
        *,
        score: int = 95,
        issues: list[dict] | None = None,
        dimensions: list[dict] | None = None,
    ) -> None:
        self.score = score
        self.issues = issues or []
        self.dimensions = dimensions or [
            {
                "name": "操作明确性",
                "score": self.score,
                "reason": "LLM 评估结果",
            }
        ]

    async def chat(self, messages, **kwargs):
        return LLMResponse(
            content=json.dumps(
                {
                    "score": self.score,
                    "dimensions": self.dimensions,
                    "issues": self.issues,
                    "rewrite_suggestions": [],
                    "draft_steps": ["点击明确按钮"],
                    "draft_expected": ["页面显示明确结果"],
                },
                ensure_ascii=False,
            )
        )


class FailingLLM:
    async def chat(self, messages, **kwargs):
        raise TimeoutError("assessment timeout")


class CountingLLM(FakeLLM):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return await super().chat(messages, **kwargs)


@pytest.mark.asyncio
async def test_quality_gate_blocks_vague_case_even_when_llm_scores_high():
    suite = Suite(id="sx", name="SX", base_url="https://x.com")
    case = TestCase(
        id="t1",
        name="抽象用例",
        steps=["任意操作一下"],
        expected=["功能正常"],
        base_url="https://x.com",
        suite_id="sx",
    )

    assessment = await assess_case_executability(
        llm=FakeLLM(),
        suite=suite,
        case=case,
    )

    assert assessment.gate_decision == "block"
    assert assessment.risk_level == "blocked"
    assert assessment.score < 60
    assert {issue.code for issue in assessment.issues} >= {
        "vague_step",
        "unobservable_expected",
    }


@pytest.mark.asyncio
async def test_quality_gate_override_requires_reason_and_records_override():
    suite = Suite(id="sx", name="SX", base_url="https://x.com")
    case = TestCase(
        id="t1",
        name="抽象用例",
        steps=["任意操作一下"],
        expected=["状态正常"],
        base_url="https://x.com",
        suite_id="sx",
    )

    assessment = await assess_case_executability(
        llm=FakeLLM(),
        suite=suite,
        case=case,
        gate=QualityGateOptions(force=True, override_reason="试点验证，需要保留样本"),
    )

    assert assessment.gate_decision == "overridden"
    assert assessment.override_reason == "试点验证，需要保留样本"


@pytest.mark.asyncio
async def test_quality_gate_warns_but_allows_clear_case():
    suite = Suite(id="sx", name="SX", base_url="https://x.com")
    case = TestCase(
        id="t1",
        name="登录",
        steps=["点击登录按钮"],
        expected=["页面显示首页"],
        base_url="https://x.com",
        suite_id="sx",
    )

    assessment = await assess_case_executability(
        llm=FakeLLM(
            score=70,
            issues=[
                {
                    "code": "weak_expected",
                    "severity": "warning",
                    "message": "预期可以更具体",
                    "suggestion": "补充首页可见文案。",
                }
            ],
        ),
        suite=suite,
        case=case,
    )

    assert assessment.risk_level == "warn"
    assert assessment.gate_decision == "warn"


@pytest.mark.asyncio
async def test_quality_gate_allows_high_score_clear_case():
    suite = Suite(id="sx", name="SX", base_url="https://x.com")
    case = TestCase(
        id="t1",
        name="登录",
        steps=["点击登录按钮"],
        expected=["页面显示首页"],
        base_url="https://x.com",
        suite_id="sx",
    )

    assessment = await assess_case_executability(
        llm=FakeLLM(score=95),
        suite=suite,
        case=case,
    )

    assert assessment.risk_level == "pass"
    assert assessment.gate_decision == "allow"


@pytest.mark.asyncio
async def test_quality_score_uses_dimension_average_when_llm_score_is_higher():
    suite = Suite(id="sx", name="SX", base_url="https://x.com")
    case = TestCase(
        id="t1",
        name="登录",
        steps=["输入用户名和密码后点击登录"],
        expected=["页面显示错误提示"],
        base_url="https://x.com",
        suite_id="sx",
    )

    assessment = await assess_case_executability(
        llm=FakeLLM(
            score=100,
            dimensions=[
                {"name": "clarity", "score": 95, "reason": "目标明确"},
                {"name": "feasibility", "score": 98, "reason": "可执行"},
                {"name": "determinism", "score": 90, "reason": "断言略泛"},
                {"name": "context_sufficiency", "score": 95, "reason": "上下文足够"},
                {"name": "risk_control", "score": 90, "reason": "风险可控"},
            ],
        ),
        suite=suite,
        case=case,
    )

    assert assessment.score == 94
    assert assessment.gate_decision == "allow"


@pytest.mark.asyncio
async def test_quality_precheck_recognizes_english_actions_and_observable_expected():
    suite = Suite(id="sx", name="SX", base_url="file:///tmp/login.html")
    case = TestCase(
        id="t1",
        name="Wrong password login",
        steps=[
            "Enter standard_user in the Username input field",
            "Enter wrong_password in the Password input field",
            "Click the Login button",
        ],
        expected=[
            "The page shows the error message: Login failed: Username and password do not match any user in this service"
        ],
        base_url="file:///tmp/login.html",
        suite_id="sx",
    )

    assessment = await assess_case_executability(
        llm=FakeLLM(score=90),
        suite=suite,
        case=case,
    )

    assert assessment.gate_decision == "allow"
    assert "missing_action" not in {issue.code for issue in assessment.issues}


@pytest.mark.asyncio
async def test_quality_gate_keeps_grounded_business_terms_allowed():
    suite = Suite(id="sx", name="SX", base_url="https://x.com")
    case = TestCase(
        id="t1",
        name="Stick 流气",
        steps=["点击任意Stick流气按钮"],
        expected=["页面显示 Stick 流气状态为开启"],
        base_url="https://x.com",
        suite_id="sx",
    )

    assessment = await assess_case_executability(
        llm=FakeLLM(score=90),
        suite=suite,
        case=case,
        translation_knowledge="任意Stick流气按钮: 工艺图中任一标识为 Stick 流气的可点击按钮。",
    )

    assert assessment.gate_decision == "allow"
    assert "vague_step" not in {issue.code for issue in assessment.issues}


@pytest.mark.asyncio
async def test_quality_gate_uses_success_memory_as_confidence_context():
    suite = Suite(id="sx", name="SX", base_url="https://x.com")
    case = TestCase(
        id="t1",
        name="登录",
        steps=["点击登录按钮"],
        expected=["页面显示首页"],
        base_url="https://x.com",
        suite_id="sx",
    )
    memory = CaseExecutionMemory(
        id="mem1",
        suite_id="sx",
        case_id="t1",
        base_url="https://x.com",
        case_hash="hash",
        spec=TestSpec(case_id="t1", name="登录", base_url="https://x.com"),
        experience="- 登录按钮点击后等待首页加载",
    )

    assessment = await assess_case_executability(
        llm=FakeLLM(score=82),
        suite=suite,
        case=case,
        memory=memory,
    )

    assert assessment.gate_decision == "allow"
    assert "memory:mem1" in assessment.context_sources


@pytest.mark.asyncio
async def test_quality_gate_fails_closed_when_assessment_errors():
    suite = Suite(id="sx", name="SX", base_url="https://x.com")
    case = TestCase(
        id="t1",
        name="登录",
        steps=["点击登录按钮"],
        expected=["页面显示首页"],
        base_url="https://x.com",
        suite_id="sx",
    )

    assessment = await assess_case_executability(
        llm=FailingLLM(),
        suite=suite,
        case=case,
    )

    assert assessment.risk_level == "error"
    assert assessment.gate_decision == "block"
    assert "assessment timeout" in assessment.error


@pytest.mark.asyncio
async def test_quality_storage_round_trip_latest_and_run_lookup(tmp_path):
    store = Store(url=f"sqlite+aiosqlite:///{tmp_path}/quality-store.db")
    await store.init()
    older = CaseExecutabilityAssessment(
        id="a1",
        project_id="p1",
        version_id="v1",
        suite_id="sx",
        case_id="t1",
        run_id="r1",
        score=70,
        risk_level="warn",
        gate_decision="warn",
        dimensions=[CaseQualityDimension(name="操作明确性", score=70, reason="偏弱")],
        issues=[
            CaseQualityIssue(
                code="weak_expected",
                severity="warning",
                message="预期可以更具体",
            )
        ],
    )
    newer = older.model_copy(
        update={
            "id": "a2",
            "run_id": "r2",
            "assessment_version": "case-quality-v2",
            "context_hash": "ctx",
            "score": 40,
            "risk_level": "blocked",
            "gate_decision": "overridden",
            "override_reason": "试点验证",
        },
        deep=True,
    )
    await store.save_case_assessment(older)
    await store.save_case_assessment(newer)

    loaded = await store.get_case_assessment("a2")
    latest = await store.latest_case_assessment(
        project_id="p1",
        version_id="v1",
        suite_id="sx",
        case_id="t1",
    )
    run_items = await store.list_run_case_assessments("r2")
    await store.close()

    assert loaded is not None and loaded.override_reason == "试点验证"
    assert latest is not None and latest.id == "a2"
    assert [item.id for item in run_items] == ["a2"]


@pytest.mark.asyncio
async def test_execute_run_reuses_cached_quality_assessment(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/quality-cache.db"
    store = Store(url=db_url)
    await store.init()
    repo = SQLModelRepository(store)
    await repo.create(Suite(id="sx", name="SX", base_url="https://x.com"))
    await repo.bulk_insert(
        [
            TestCase(
                id="t1",
                name="登录",
                steps=["点击登录按钮"],
                expected=["页面显示首页"],
                base_url="https://x.com",
                suite_id="sx",
            )
        ]
    )
    await repo.create_run("quality-cache-run-1", "sx", 1, None, None)
    await repo.create_run("quality-cache-run-2", "sx", 1, None, None)

    import harness.llm as llm_mod
    import harness.orchestrator as orch_mod

    llm = CountingLLM(score=90)

    class _NoopOrch:
        def __init__(self, *args, **kwargs):
            pass

        async def run_suite(self, cases, **kwargs):
            class _R:
                passed_count = 1
                failed_count = 0

            return _R()

    monkeypatch.setattr(llm_mod, "build_llm_client", lambda config=None: llm)
    monkeypatch.setattr(orch_mod, "Orchestrator", _NoopOrch)

    await execute_run(db_url=db_url, run_id="quality-cache-run-1", suite_id="sx")
    await execute_run(db_url=db_url, run_id="quality-cache-run-2", suite_id="sx")

    first = (await store.list_run_case_assessments("quality-cache-run-1"))[0]
    second = (await store.list_run_case_assessments("quality-cache-run-2"))[0]
    await store.close()

    assert llm.calls == 1
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.source_assessment_id == first.id
    assert second.run_id == "quality-cache-run-2"


@pytest.mark.asyncio
async def test_execute_run_quality_block_saves_failed_record(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/quality-block.db"
    store = Store(url=db_url)
    await store.init()
    repo = SQLModelRepository(store)
    await repo.create(Suite(id="sx", name="SX", base_url="https://x.com"))
    await repo.bulk_insert(
        [
            TestCase(
                id="t1",
                name="抽象用例",
                steps=["任意操作一下"],
                expected=["状态正常"],
                base_url="https://x.com",
                suite_id="sx",
            )
        ]
    )
    run_id = "quality-run"
    await repo.create_run(run_id, "sx", 1, None, None)

    import harness.llm as llm_mod
    import harness.orchestrator as orch_mod

    async def _should_not_run(*args, **kwargs):
        raise AssertionError("quality gate should block before orchestrator")

    monkeypatch.setattr(llm_mod, "build_llm_client", lambda config=None: FakeLLM())
    monkeypatch.setattr(orch_mod.Orchestrator, "run_suite", _should_not_run)

    await execute_run(db_url=db_url, run_id=run_id, suite_id="sx")

    run = await repo.get_run(run_id)
    records = await repo.list_records_by_run(run_id)
    assert run is not None and run["status"] == "failed"
    assert records[0].passed is False
    assert "质量闸门阻断执行" in records[0].final_result
    assert records[0].metrics["case_quality"]["gate_decision"] == "block"


@pytest.mark.asyncio
async def test_execute_run_quality_warn_still_executes(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/quality-warn.db"
    store = Store(url=db_url)
    await store.init()
    repo = SQLModelRepository(store)
    await repo.create(Suite(id="sx", name="SX", base_url="https://x.com"))
    await repo.bulk_insert(
        [
            TestCase(
                id="t1",
                name="登录",
                steps=["点击登录按钮"],
                expected=["页面显示首页"],
                base_url="https://x.com",
                suite_id="sx",
            )
        ]
    )
    run_id = "quality-warn-run"
    await repo.create_run(run_id, "sx", 1, None, None)

    import harness.llm as llm_mod
    import harness.orchestrator as orch_mod

    captured = {}

    class _WarnOrch:
        def __init__(self, *args, **kwargs):
            pass

        async def run_suite(self, cases, **kwargs):
            captured["case_ids"] = [case.id for case in cases]

            class _R:
                passed_count = 1
                failed_count = 0

            return _R()

    monkeypatch.setattr(llm_mod, "build_llm_client", lambda config=None: FakeLLM(score=70))
    monkeypatch.setattr(orch_mod, "Orchestrator", _WarnOrch)

    await execute_run(db_url=db_url, run_id=run_id, suite_id="sx")

    run = await repo.get_run(run_id)
    assessment = (await store.list_run_case_assessments(run_id))[0]
    assert captured["case_ids"] == ["t1"]
    assert run is not None and run["status"] == "completed"
    assert assessment.gate_decision == "warn"


@pytest.mark.asyncio
async def test_execute_run_quality_override_executes_and_audits_reason(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/quality-override.db"
    store = Store(url=db_url)
    await store.init()
    repo = SQLModelRepository(store)
    await repo.create(Suite(id="sx", name="SX", base_url="https://x.com"))
    await repo.bulk_insert(
        [
            TestCase(
                id="t1",
                name="抽象用例",
                steps=["任意操作一下"],
                expected=["状态正常"],
                base_url="https://x.com",
                suite_id="sx",
            )
        ]
    )
    run_id = "quality-override-run"
    await repo.create_run(run_id, "sx", 1, None, None)

    import harness.llm as llm_mod
    import harness.orchestrator as orch_mod

    captured = {}

    class _OverrideOrch:
        def __init__(self, *args, **kwargs):
            pass

        async def run_suite(self, cases, **kwargs):
            captured["case_ids"] = [case.id for case in cases]

            class _R:
                passed_count = 1
                failed_count = 0

            return _R()

    monkeypatch.setattr(llm_mod, "build_llm_client", lambda config=None: FakeLLM())
    monkeypatch.setattr(orch_mod, "Orchestrator", _OverrideOrch)

    await execute_run(
        db_url=db_url,
        run_id=run_id,
        suite_id="sx",
        force_low_quality_cases=True,
        quality_override_reason="试点保留样本",
    )

    assessment = (await store.list_run_case_assessments(run_id))[0]
    assert captured["case_ids"] == ["t1"]
    assert assessment.gate_decision == "overridden"
    assert assessment.override_reason == "试点保留样本"


@pytest.mark.asyncio
async def test_quality_preview_persists_latest_assessment(monkeypatch):
    store = Store(url="sqlite+aiosqlite://")
    await store.init()
    repo = SQLModelRepository(store)
    await repo.create(Suite(id="sx", name="SX", base_url="https://x.com"))
    await repo.bulk_insert(
        [
            TestCase(
                id="t1",
                name="抽象用例",
                steps=["任意操作一下"],
                expected=["状态正常"],
                base_url="https://x.com",
                suite_id="sx",
            )
        ]
    )

    import api.server as srv
    import harness.llm as llm_mod

    monkeypatch.setattr(llm_mod, "build_llm_client", lambda config=None: FakeLLM())
    srv._repo = repo
    srv._store = store
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/suites/sx/quality-preview", json={"case_ids": ["t1"]})
        latest = await client.get("/api/suites/sx/cases/t1/quality")
    await store.close()
    srv._repo = None
    srv._store = None

    assert response.status_code == 200
    assert response.json()["assessments"][0]["gate_decision"] == "block"
    assert latest.status_code == 200
    assert latest.json()["latest"]["gate_decision"] == "block"
