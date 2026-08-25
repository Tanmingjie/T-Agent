"""执行路由: /run, /stream SSE(Spec §4.2)。

执行不在 API 事件循环上跑:每次 run 起一个守护线程 + 独立事件循环 + 独立 Store
(见 ``api/execution_worker.py``),API 循环只管 HTTP/SSE,结构上永不被执行阻塞。
并发由 Orchestrator 的 ``parallelism`` 控制(各用例独立 MCP/浏览器)。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth import require_suite_access
from api.execution_worker import spawn_run
from api.repository import get_suite_settings, resolve_effective_cases, set_suite_settings
from input.models import TestSpec
from storage.auth_state import (
    delete_suite_auth_state,
    has_suite_auth_state,
    save_suite_auth_state,
    suite_auth_state_meta,
)

router = APIRouter(tags=["execution"])


# get_repo/get_store 用惰性包装(不在模块加载期 import api.server),打断
# execution→server→(router 块)→execution 的循环导入(execution 被直接 import 时尤甚)。
def get_repo():
    from api.server import get_repo as _g

    return _g()


def get_store():
    from api.server import get_store as _g

    return _g()


# suite 维度鉴权(单机/无 project_id 放行)。SSE /stream 因 EventSource 无法带 header,
# 单机隐式 admin 放行;平台 SSE 鉴权随 T-P09 双进程改造引入 token。
_suite_guard = [Depends(require_suite_access)]

# embedded 模式下「本进程内有活 worker 线程」的 run 集合,纯作**僵尸检测**用
# (DB 为 running 但不在此集合 = 上次进程崩溃遗留 → 自动收尾)。SSE 投递不再走内存,
# 统一经 run_event 表(execute_run 落表,/stream 从 seq 0 重放+尾随),故退出再进可看全程。
_live_runs: set[str] = set()
# 权限审批结果回传:执行在 worker 线程/loop,审批在 API loop,跨线程用 threading.Event set。
_permission_events: dict[str, threading.Event] = {}
_permission_results: dict[str, dict] = {}

logger = logging.getLogger(__name__)


class RunOptions(BaseModel):
    # null/未提供=全量；非空列表=部分执行；显式空列表由路由拒绝。
    case_ids: list[str] | None = None
    # 本次执行强制加载的项目 skill 名(一次性,随本次 run;空=全走渐进披露)。
    skill_names: list[str] = Field(default_factory=list)
    # 人工审核后的规格。空表示沿用执行期即时翻译。
    approved_specs: dict[str, TestSpec] = Field(default_factory=dict)
    # 本次执行强制重新翻译这些用例,绕过成功执行记忆。
    retranslate_case_ids: list[str] = Field(default_factory=list)
    # 执行前用例可执行性质量闸门。默认开启；低分强制执行需填写原因。
    quality_gate_enabled: bool = True
    force_low_quality_cases: bool = False
    quality_override_reason: str = ""


class SpecPreviewOptions(BaseModel):
    # case_id 保留兼容旧前端；新调用统一使用 case_ids。
    case_id: str | None = None
    case_ids: list[str] | None = None
    skill_names: list[str] = Field(default_factory=list)


class QualityPreviewOptions(BaseModel):
    case_id: str | None = None
    case_ids: list[str] | None = None
    skill_names: list[str] = Field(default_factory=list)
    force_low_quality_cases: bool = False
    quality_override_reason: str = ""


class MemoryUpdate(BaseModel):
    enabled: bool


class AuthStateUpload(BaseModel):
    storage_state: dict = Field(default_factory=dict)


def _normalize_requested_case_ids(
    *, legacy_case_id: str | None, case_ids: list[str] | None
) -> list[str] | None:
    if legacy_case_id is not None and case_ids is not None:
        raise HTTPException(400, "case_id 与 case_ids 不能同时提供")
    return [legacy_case_id] if legacy_case_id is not None else case_ids


async def _translation_context(store, suite, skill_names: list[str]) -> tuple[object, str]:
    llm_config = await store.get_llm_config(suite.project_id) if suite.project_id else None
    knowledge = ""
    if suite.project_id:
        project = await store.get_project(suite.project_id)
        if project is not None:
            knowledge = project.translation_knowledge or ""
        selected = {name for name in skill_names if name}
        for skill in await store.list_skills(suite.project_id):
            if skill.name in selected and skill.content.strip():
                knowledge += (
                    f"\n\n[执行 Skill:{skill.name}]\n"
                    f"{(skill.description or '').strip()}\n{skill.content.strip()}"
                )
    return llm_config, knowledge


async def _suite_case(repo, suite_id: str, case_id: str):
    suite = await repo.get_suite(suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")
    case = await repo.get_case(case_id)
    if case is None or case.suite_id != suite_id:
        raise HTTPException(404, "Case not found")
    return suite, case


async def _assess_cases(
    *,
    suite,
    cases,
    skill_names: list[str],
    store,
    run_id: str = "",
    force_low_quality_cases: bool = False,
    quality_override_reason: str = "",
):
    from harness.case_quality import (
        ASSESSMENT_VERSION,
        QualityGateOptions,
        assess_case_executability,
        assessment_context_hash,
        assessment_summary,
        clone_cached_assessment,
    )
    from harness.execution_memory import case_fingerprint, effective_base_url
    from harness.llm import build_llm_client

    if force_low_quality_cases and not quality_override_reason.strip():
        raise HTTPException(400, "强制执行低质量用例必须填写原因")
    llm_config, knowledge = await _translation_context(store, suite, skill_names)
    llm = build_llm_client(llm_config)
    assessments = []
    selected = {name for name in skill_names if name}
    for case in cases:
        base_url = effective_base_url(case, suite)
        fingerprint = case_fingerprint(
            case,
            project_id=suite.project_id,
            version_id=suite.version_id,
            suite_id=suite.id,
            base_url=base_url,
        )
        memory = await store.find_case_memory(
            project_id=suite.project_id,
            version_id=suite.version_id,
            suite_id=suite.id,
            case_id=case.id,
            base_url=base_url,
            case_hash=fingerprint,
        )
        gate = QualityGateOptions(
            force=force_low_quality_cases,
            override_reason=quality_override_reason,
        )
        context_hash = assessment_context_hash(
            translation_knowledge=knowledge,
            selected_skill_names=list(selected),
            memory=memory,
        )
        cached = await store.find_reusable_case_assessment(
            project_id=suite.project_id,
            version_id=suite.version_id,
            suite_id=suite.id,
            case_id=case.id,
            base_url=base_url,
            case_hash=fingerprint,
            assessment_version=ASSESSMENT_VERSION,
            context_hash=context_hash,
        )
        if cached is not None:
            assessment = clone_cached_assessment(cached, run_id=run_id, gate=gate)
        else:
            assessment = await assess_case_executability(
                llm=llm,
                suite=suite,
                case=case,
                run_id=run_id,
                translation_knowledge=knowledge,
                selected_skill_names=list(selected),
                memory=memory,
                gate=gate,
            )
        await store.save_case_assessment(assessment)
        assessments.append(assessment)
    return [assessment_summary(item) for item in assessments]


@router.get("/suites/{suite_id}/cases/{case_id}/memory", dependencies=_suite_guard)
async def get_case_memory(
    suite_id: str,
    case_id: str,
    repo=Depends(get_repo),
    store=Depends(get_store),
):
    from harness.execution_memory import case_fingerprint, effective_base_url

    suite, case = await _suite_case(repo, suite_id, case_id)
    base_url = effective_base_url(case, suite)
    fingerprint = case_fingerprint(
        case,
        project_id=suite.project_id,
        version_id=suite.version_id,
        suite_id=suite.id,
        base_url=base_url,
    )
    current = await store.find_case_memory(
        project_id=suite.project_id,
        version_id=suite.version_id,
        suite_id=suite.id,
        case_id=case.id,
        base_url=base_url,
        case_hash=fingerprint,
        enabled_only=False,
        include_stale=True,
    )
    latest = await store.latest_case_memory(
        project_id=suite.project_id,
        version_id=suite.version_id,
        suite_id=suite.id,
        case_id=case.id,
    )
    return {
        "case_id": case.id,
        "case_hash": fingerprint,
        "current": current.model_dump(mode="json") if current else None,
        "latest": latest.model_dump(mode="json") if latest else None,
        "eligible": bool(current and current.enabled and not current.stale),
    }


@router.patch("/suites/{suite_id}/cases/{case_id}/memory/{memory_id}", dependencies=_suite_guard)
async def update_case_memory(
    suite_id: str,
    case_id: str,
    memory_id: str,
    body: MemoryUpdate,
    repo=Depends(get_repo),
    store=Depends(get_store),
):
    suite, case = await _suite_case(repo, suite_id, case_id)
    memory = await store.get_case_memory(memory_id)
    if (
        memory is None
        or memory.project_id != suite.project_id
        or memory.version_id != suite.version_id
        or memory.suite_id != suite.id
        or memory.case_id != case.id
    ):
        raise HTTPException(404, "Memory not found")
    ok = await store.set_case_memory_enabled(memory_id, body.enabled)
    if not ok:
        raise HTTPException(404, "Memory not found")
    updated = await store.get_case_memory(memory_id)
    return updated.model_dump(mode="json") if updated else {"ok": True}


@router.get("/suites/{suite_id}/cases/{case_id}/quality", dependencies=_suite_guard)
async def get_case_quality(
    suite_id: str,
    case_id: str,
    repo=Depends(get_repo),
    store=Depends(get_store),
):
    suite, case = await _suite_case(repo, suite_id, case_id)
    latest = await store.latest_case_assessment(
        project_id=suite.project_id,
        version_id=suite.version_id,
        suite_id=suite.id,
        case_id=case.id,
    )
    return {
        "case_id": case.id,
        "latest": latest.model_dump(mode="json") if latest else None,
    }


@router.post("/suites/{suite_id}/quality-preview", dependencies=_suite_guard)
async def preview_case_quality(
    suite_id: str,
    options: QualityPreviewOptions,
    repo=Depends(get_repo),
    store=Depends(get_store),
):
    """执行前质量评估预览。只评分和给建议，不创建 run、不启动 Midscene。"""
    suite = await repo.get_suite(suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")
    all_cases = await repo.list_by_suite(suite_id)
    if not all_cases:
        raise HTTPException(400, "Suite 没有用例，请先上传 Excel")
    requested_case_ids = _normalize_requested_case_ids(
        legacy_case_id=options.case_id,
        case_ids=options.case_ids,
    )
    settings = await get_suite_settings(store, suite_id)
    uploaded_auth_state = has_suite_auth_state(suite_id)
    try:
        cases, _ = resolve_effective_cases(
            all_cases,
            requested_case_ids=requested_case_ids,
            login_setup_case_id=settings.get("login_setup_case_id"),
            skip_login_setup=uploaded_auth_state,
        )
    except ValueError as exc:
        status_code = (
            404 if options.case_id and options.case_id not in {c.id for c in all_cases} else 400
        )
        raise HTTPException(status_code, str(exc)) from exc
    assessments = await _assess_cases(
        suite=suite,
        cases=cases,
        skill_names=options.skill_names,
        force_low_quality_cases=options.force_low_quality_cases,
        quality_override_reason=options.quality_override_reason,
        store=store,
    )
    return {"assessments": assessments}


@router.post("/suites/{suite_id}/spec-preview", dependencies=_suite_guard)
async def preview_specs(
    suite_id: str,
    options: SpecPreviewOptions,
    repo=Depends(get_repo),
    store=Depends(get_store),
):
    """执行前翻译预览。只生成 TestSpec，不创建 run、不启动 Midscene。"""
    from harness.llm import build_llm_client
    from intelligence.pre_analysis import SpecGenerator

    suite = await repo.get_suite(suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")
    all_cases = await repo.list_by_suite(suite_id)
    if not all_cases:
        raise HTTPException(400, "Suite 没有用例，请先上传 Excel")
    requested_case_ids = _normalize_requested_case_ids(
        legacy_case_id=options.case_id,
        case_ids=options.case_ids,
    )
    settings = await get_suite_settings(store, suite_id)
    uploaded_auth_state = has_suite_auth_state(suite_id)
    try:
        cases, _ = resolve_effective_cases(
            all_cases,
            requested_case_ids=requested_case_ids,
            login_setup_case_id=settings.get("login_setup_case_id"),
            skip_login_setup=uploaded_auth_state,
        )
    except ValueError as exc:
        status_code = (
            404 if options.case_id and options.case_id not in {c.id for c in all_cases} else 400
        )
        raise HTTPException(status_code, str(exc)) from exc

    llm_config, knowledge = await _translation_context(store, suite, options.skill_names)
    generator = SpecGenerator(build_llm_client(llm_config))
    specs = []
    for case in cases:
        spec = await generator.generate(case, knowledge=knowledge)
        specs.append(spec.model_dump(mode="json"))
    return {"specs": specs}


@router.post("/suites/{suite_id}/run", dependencies=_suite_guard)
async def trigger_run(
    suite_id: str,
    case_id: str | None = None,
    options: RunOptions | None = None,
    repo=Depends(get_repo),
    store=Depends(get_store),
):
    """触发执行。``case_ids`` 选择部分用例；旧 ``case_id`` 继续兼容单条执行。

    ``options.skill_names``:执行前勾选的项目 skill → 本次强制加载(详见 execute_run)。
    """
    skill_names = options.skill_names if options is not None else []
    approved_specs = options.approved_specs if options is not None else {}
    retranslate_case_ids = options.retranslate_case_ids if options is not None else []
    quality_gate_enabled = options.quality_gate_enabled if options is not None else True
    force_low_quality_cases = options.force_low_quality_cases if options is not None else False
    quality_override_reason = options.quality_override_reason if options is not None else ""
    requested_case_ids = _normalize_requested_case_ids(
        legacy_case_id=case_id,
        case_ids=options.case_ids if options is not None else None,
    )
    suite = await repo.get_suite(suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")

    all_cases = await repo.list_by_suite(suite_id)
    if not all_cases:
        raise HTTPException(400, "Suite 没有用例，请先上传 Excel")
    settings = await get_suite_settings(store, suite_id)
    uploaded_auth_state = has_suite_auth_state(suite_id)
    try:
        cases, _ = resolve_effective_cases(
            all_cases,
            requested_case_ids=requested_case_ids,
            login_setup_case_id=settings.get("login_setup_case_id"),
            skip_login_setup=uploaded_auth_state,
        )
    except ValueError as exc:
        status_code = 404 if case_id and case_id not in {c.id for c in all_cases} else 400
        raise HTTPException(status_code, str(exc)) from exc

    target_ids = {case.id for case in cases}
    if set(approved_specs) - target_ids:
        raise HTTPException(400, "人工确认规格包含非本次执行用例")
    if set(retranslate_case_ids) - target_ids:
        raise HTTPException(400, "重新翻译用例包含非本次执行用例")
    if quality_gate_enabled and force_low_quality_cases and not quality_override_reason.strip():
        raise HTTPException(400, "强制执行低质量用例必须填写原因")
    for approved_case_id, spec in approved_specs.items():
        if spec.case_id != approved_case_id:
            raise HTTPException(400, f"人工确认规格 case_id 不匹配: {approved_case_id}")

    # Check if already running。注意:_sse_queues 是内存态,进程重启后必为空,
    # 故 DB 里仍为 running 但不在队列中的 run 是上次崩溃/重启遗留的僵尸 → 自动收尾,
    # 不再 409 卡住用户(否则每次崩溃都要手动改库)。
    runs = await repo.list_runs_by_suite(suite_id)
    active_run = next((r for r in runs if r["status"] == "running"), None)
    if active_run is not None:
        if active_run["id"] in _live_runs:
            raise HTTPException(409, "已有执行在进行中")
        await repo.update_run(active_run["id"], status="failed", finished_at=time.time())

    run_id = uuid.uuid4().hex[:12]
    await repo.create_run(run_id, suite_id, len(cases), suite.project_id, suite.version_id)
    if approved_specs:
        await store.append_run_event(
            run_id,
            "specs_approved",
            {
                "specs": {
                    case_key: spec.model_dump(mode="json")
                    for case_key, spec in approved_specs.items()
                }
            },
        )
    await store.append_audit(
        "system", "run.trigger", project_id=suite.project_id, target=suite_id, detail=run_id
    )

    # 双进程模式(RUN_MODE=queue):API 只入队,独立 worker(scripts/worker.py)领取执行。
    # 默认 embedded:进程内守护线程执行(单机)。两模式进度都落 run_event 表,/stream 统一
    # 从表重放+尾随 → 退出执行页再进来可看全程。
    if os.getenv("RUN_MODE") == "queue":
        await store.enqueue_run(
            run_id,
            suite_id,
            suite.project_id,
            case_ids=requested_case_ids,
            skill_names=skill_names,
            retranslate_case_ids=retranslate_case_ids,
            quality_gate_enabled=quality_gate_enabled,
            force_low_quality_cases=force_low_quality_cases,
            quality_override_reason=quality_override_reason,
        )
        return {"run_id": run_id, "status": "queued"}

    api_loop = asyncio.get_running_loop()
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///storage/ai_test.db")

    def _perm_approver_factory(emit):
        """API 进程内审批:Reason 后 Act 前经 emit 推审批请求(落表可重放),
        threading.Event 跨线程等结果(执行在 worker 线程,审批 POST 在 API loop)。"""
        from harness.permission import threading_event_approver

        async def _perm_approver(req):
            event_id = uuid.uuid4().hex[:8]
            ev = threading.Event()
            _permission_events[event_id] = ev
            _permission_results[event_id] = {"approved": False}
            await emit(
                "permission",
                {
                    "event_id": event_id,
                    "case_id": "current",
                    "action": req.tool_name,
                    "reason": req.reason,
                },
            )
            try:
                return await threading_event_approver(ev, _permission_results[event_id])(req)
            finally:
                _permission_events.pop(event_id, None)
                _permission_results.pop(event_id, None)

        return _perm_approver

    _live_runs.add(run_id)  # 僵尸检测标记:本进程有活 worker 线程

    async def _worker_main() -> None:
        # 共享执行核(api/run_executor.py):自带独立 Store/loop;事件由 execute_run 落 run_event
        # 表(sse_cb=None,不再走内存队列)。/stream 从表重放+尾随。
        from api.run_executor import execute_run

        try:
            await execute_run(
                db_url=db_url,
                run_id=run_id,
                suite_id=suite_id,
                case_ids=requested_case_ids,
                sse_cb=None,
                perm_approver_factory=_perm_approver_factory,
                force_skill_names=skill_names,
                retranslate_case_ids=retranslate_case_ids,
                quality_gate_enabled=quality_gate_enabled,
                force_low_quality_cases=force_low_quality_cases,
                quality_override_reason=quality_override_reason,
            )
        finally:
            api_loop.call_soon_threadsafe(_live_runs.discard, run_id)

    spawn_run(run_id, _worker_main)
    return {"run_id": run_id, "status": "started"}


@router.post("/suites/{suite_id}/runs/{run_id}/stop", dependencies=_suite_guard)
async def stop_run(suite_id: str, run_id: str, repo=Depends(get_repo), store=Depends(get_store)):
    """请求停止一个正在执行的 run(协作式优雅停)。

    置 run_record.cancel_requested 标志;执行链(orchestrator 每用例前 / Midscene 启动前)轮询到
    后,正在飞的那步 MCP/LLM 调用跑完即优雅退出,未开跑的用例补「已中止」占位,run 终态记
    aborted。embedded / queue 两模式统一(都各自有 Store 读同一标志)。幂等:已结束/不存在
    返回 ok=false。"""
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    if run["status"] != "running":
        return {"ok": False, "status": run["status"], "detail": "run 已结束,无需停止"}
    flagged = await repo.request_cancel(run_id)
    if flagged:
        # 落 run_event 表,让在场/重连的 /stream 订阅者即时看到「停止中」。
        await store.append_run_event(run_id, "aborting", {"run_id": run_id})
    return {"ok": flagged, "status": "running" if flagged else run["status"]}


@router.get("/suites/{suite_id}/stream")
async def stream_events(
    suite_id: str, run_id: str, store=Depends(get_store), repo=Depends(get_repo)
):
    # 统一:从 run_event 表**重放(seq 0 起)+ 尾随**——embedded/queue 同一逻辑。在场或晚到
    # (退出执行页再进来)订阅者都能拿到完整进度,suite_done/error 收尾。run 不存在则 404。
    from api.execution_worker import format_sse

    run = await repo.get_run(run_id)
    if run is None and await store.get_queued_run(run_id) is None:
        raise HTTPException(404, "Run not found")

    async def _generate():
        yield ": keepalive\n\n"
        last_seq = 0
        idle = 0
        while True:
            events = await store.list_run_events(run_id, after_seq=last_seq)
            if events:
                idle = 0
                for ev in events:
                    last_seq = ev.seq
                    yield format_sse(ev.event_type, ev.data)
                    if ev.event_type in ("suite_done", "error"):
                        return
            else:
                idle += 1
                yield ": keepalive\n\n"
                # 兜底:run 已落终态且无新事件 → 收尾(防 worker 没发 suite_done)
                if idle >= 4:
                    cur = await repo.get_run(run_id)
                    if cur and cur["status"] in ("completed", "failed", "aborted"):
                        yield format_sse("suite_done", {"run_id": run_id, "sentinel": True})
                        return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/suites/{suite_id}/settings", dependencies=_suite_guard)
async def get_settings(suite_id: str, store=Depends(get_store)):
    settings = await get_suite_settings(store, suite_id)
    settings["auth_state"] = suite_auth_state_meta(suite_id)
    return settings


@router.post("/suites/{suite_id}/auth-state", dependencies=_suite_guard)
async def upload_auth_state(
    suite_id: str,
    body: AuthStateUpload,
    repo=Depends(get_repo),
):
    suite = await repo.get_suite(suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")
    try:
        meta = save_suite_auth_state(suite_id, body.storage_state)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "auth_state": meta}


@router.delete("/suites/{suite_id}/auth-state", dependencies=_suite_guard)
async def clear_auth_state(suite_id: str, repo=Depends(get_repo)):
    suite = await repo.get_suite(suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")
    deleted = delete_suite_auth_state(suite_id)
    return {"ok": True, "deleted": deleted, "auth_state": suite_auth_state_meta(suite_id)}


class SettingsUpdate(BaseModel):
    permission_mode: str  # "trust" | "approve"
    parallelism: int = 1  # 并发执行用例数(1=串行)
    login_setup_case_id: str | None = None


@router.put("/suites/{suite_id}/settings", dependencies=_suite_guard)
async def update_settings(
    suite_id: str,
    body: SettingsUpdate,
    store=Depends(get_store),
    repo=Depends(get_repo),
):
    login_setup_case_id = body.login_setup_case_id or None
    if login_setup_case_id:
        setup_case = await repo.get_case(login_setup_case_id)
        if setup_case is None or setup_case.suite_id != suite_id:
            raise HTTPException(400, "登录准备用例必须属于当前 Suite")
    await set_suite_settings(
        store,
        suite_id,
        body.permission_mode,
        body.parallelism,
        login_setup_case_id,
    )
    return {"ok": True}
