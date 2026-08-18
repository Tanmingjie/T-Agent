"""Successful case execution memory helpers."""

from __future__ import annotations

import hashlib
import json
import re

from harness.llm import LLMClient
from input.models import CaseExecutionMemory, ExecutionRecord, Suite, TestCase, TestSpec


def effective_base_url(case: TestCase, suite: Suite) -> str:
    return (case.base_url or suite.base_url or "").strip()


def case_fingerprint(
    case: TestCase,
    *,
    project_id: str = "",
    version_id: str = "",
    suite_id: str = "",
    base_url: str = "",
) -> str:
    payload = {
        "project_id": project_id or "",
        "version_id": version_id or "",
        "suite_id": suite_id or case.suite_id or "",
        "case_id": case.id,
        "base_url": base_url or case.base_url or "",
        "name": _norm(case.name),
        "preconditions": [_norm(v) for v in case.preconditions],
        "steps": [_norm(v) for v in case.steps],
        "expected": [_norm(v) for v in case.expected],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_experience_context(memory: CaseExecutionMemory) -> str:
    if not memory.experience.strip():
        return ""
    return (
        f"[成功经验:{memory.case_id}]\n"
        "以下经验来自该用例此前 PASS 的执行记录。用于帮助理解业务术语、等待策略和断言时机；"
        "如果当前页面证据与经验冲突,以当前页面为准。\n"
        f"{memory.experience.strip()}"
    )


async def summarize_successful_experience(
    *,
    llm: LLMClient,
    case: TestCase,
    record: ExecutionRecord,
) -> str:
    if not record.passed or record.spec is None:
        return ""
    response = await llm.chat(_summary_messages(case, record))
    return _clean_summary(response.content)


async def learn_from_success(
    *,
    store,
    llm: LLMClient,
    suite: Suite,
    case: TestCase,
    record: ExecutionRecord,
) -> CaseExecutionMemory | None:
    if not record.passed or record.spec is None:
        return None
    base_url = effective_base_url(case, suite)
    fingerprint = case_fingerprint(
        case,
        project_id=suite.project_id,
        version_id=suite.version_id,
        suite_id=suite.id,
        base_url=base_url,
    )
    try:
        experience = await summarize_successful_experience(llm=llm, case=case, record=record)
    except Exception:  # noqa: BLE001
        experience = ""
    memory = CaseExecutionMemory(
        id=f"{suite.project_id}:{suite.version_id}:{suite.id}:{case.id}:{fingerprint[:16]}",
        project_id=suite.project_id,
        version_id=suite.version_id,
        suite_id=suite.id,
        case_id=case.id,
        base_url=base_url,
        case_hash=fingerprint,
        spec=record.spec,
        experience=experience,
        source_run_id=record.run_id or "",
        source_exec_id=record.exec_id,
    )
    await store.save_case_memory(memory)
    return memory


def _summary_messages(case: TestCase, record: ExecutionRecord) -> list[dict]:
    actions = [
        {
            "intent": step.intent,
            "result": step.tool_result,
            "duration_ms": step.duration_ms,
        }
        for step in record.steps[:30]
    ]
    payload = {
        "case": {
            "id": case.id,
            "name": case.name,
            "preconditions": case.preconditions,
            "steps": case.steps,
            "expected": case.expected,
        },
        "spec": record.spec.model_dump(mode="json") if record.spec else None,
        "actions": actions,
        "assertions": record.case_assertions,
        "metrics": record.metrics,
        "final_result": record.final_result,
    }
    return [
        {
            "role": "system",
            "content": (
                "你是测试执行经验总结器。只总结可复用的业务操作经验,不要编造选择器、坐标或未出现证据。"
                "输出 3-8 条简洁 Markdown bullet,覆盖业务术语、稳定操作、等待策略、断言时机和避免重复操作。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        },
    ]


def _clean_summary(text: str | None) -> str:
    value = (text or "").strip()
    value = re.sub(r"^```(?:markdown)?\s*", "", value)
    value = re.sub(r"\s*```$", "", value).strip()
    return value[:4000]


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
