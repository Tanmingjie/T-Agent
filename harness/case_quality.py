"""用例可执行性评估与执行前质量闸门。"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from harness.execution_memory import build_experience_context, case_fingerprint, effective_base_url
from harness.llm import LLMClient, loads_lenient
from input.models import (
    CaseExecutabilityAssessment,
    CaseExecutionMemory,
    CaseQualityDimension,
    CaseQualityIssue,
    CaseRewriteSuggestion,
    Suite,
    TestCase,
)

PASS_SCORE = 80
WARN_SCORE = 60
ASSESSMENT_VERSION = "case-quality-v2"

VAGUE_PATTERNS = (
    "任意",
    "随便",
    "适当",
    "相关",
    "正常",
    "异常",
    "进行操作",
    "处理一下",
    "确认无误",
    "按要求",
)
ACTION_HINTS = (
    "点击",
    "输入",
    "选择",
    "打开",
    "进入",
    "切换",
    "拖动",
    "等待",
    "观察",
    "勾选",
    "上传",
    "保存",
    "提交",
    "搜索",
    "登录",
    "刷新",
    "click",
    "tap",
    "enter",
    "type",
    "input",
    "select",
    "open",
    "navigate",
    "switch",
    "wait",
    "verify",
    "check",
    "observe",
    "submit",
    "search",
    "login",
)
OBSERVABLE_HINTS = (
    "显示",
    "出现",
    "页面",
    "按钮",
    "文案",
    "颜色",
    "状态",
    "数值",
    "列表",
    "表格",
    "提示",
    "在线",
    "online",
    "成功",
    "失败",
    "show",
    "shows",
    "display",
    "displays",
    "appear",
    "appears",
    "message",
    "error",
    "status",
    "text",
    "page",
)
UNOBSERVABLE_PATTERNS = (
    "后台",
    "数据库",
    "系统已处理",
    "流程正确",
    "功能正常",
    "状态正常",
    "无异常",
)


@dataclass
class QualityGateOptions:
    enabled: bool = True
    force: bool = False
    override_reason: str = ""


class QualityGateBlocked(Exception):
    """质量闸门阻断执行。"""

    def __init__(self, assessments: list[CaseExecutabilityAssessment]) -> None:
        super().__init__("用例可执行性评分过低，已阻断执行")
        self.assessments = assessments


def assessment_summary(assessment: CaseExecutabilityAssessment) -> dict[str, Any]:
    return {
        "id": assessment.id,
        "case_id": assessment.case_id,
        "run_id": assessment.run_id,
        "case_hash": assessment.case_hash,
        "assessment_version": assessment.assessment_version,
        "context_hash": assessment.context_hash,
        "cache_hit": assessment.cache_hit,
        "source_assessment_id": assessment.source_assessment_id,
        "score": assessment.score,
        "risk_level": assessment.risk_level,
        "gate_decision": assessment.gate_decision,
        "dimensions": [item.model_dump(mode="json") for item in assessment.dimensions],
        "issues": [issue.model_dump(mode="json") for issue in assessment.issues],
        "rewrite_suggestions": [
            item.model_dump(mode="json") for item in assessment.rewrite_suggestions
        ],
        "draft_steps": assessment.draft_steps,
        "draft_expected": assessment.draft_expected,
        "context_sources": assessment.context_sources,
        "override_reason": assessment.override_reason,
        "error": assessment.error,
        "created_at": assessment.created_at,
        "updated_at": assessment.updated_at,
    }


def assessment_context_hash(
    *,
    translation_knowledge: str = "",
    selected_skill_names: list[str] | None = None,
    memory: CaseExecutionMemory | None = None,
) -> str:
    payload = {
        "assessment_version": ASSESSMENT_VERSION,
        "translation_knowledge": translation_knowledge.strip(),
        "selected_skill_names": sorted(name for name in (selected_skill_names or []) if name),
        "memory": (
            {
                "id": memory.id,
                "case_hash": memory.case_hash,
                "enabled": memory.enabled,
                "stale": memory.stale,
                "updated_at": memory.updated_at,
            }
            if memory is not None
            else None
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def clone_cached_assessment(
    assessment: CaseExecutabilityAssessment,
    *,
    run_id: str,
    gate: QualityGateOptions | None = None,
) -> CaseExecutabilityAssessment:
    cached = assessment.model_copy(deep=True)
    cached.id = uuid.uuid4().hex
    cached.run_id = run_id
    cached.cache_hit = True
    cached.source_assessment_id = assessment.id
    cached.created_at = time.time()
    cached.updated_at = cached.created_at
    cached.override_reason = ""
    if "assessment_cache" not in cached.context_sources:
        cached.context_sources.append("assessment_cache")
    _apply_gate_options(cached, gate or QualityGateOptions())
    return cached


async def assess_case_executability(
    *,
    llm: LLMClient,
    suite: Suite,
    case: TestCase,
    run_id: str = "",
    translation_knowledge: str = "",
    selected_skill_names: list[str] | None = None,
    memory: CaseExecutionMemory | None = None,
    gate: QualityGateOptions | None = None,
) -> CaseExecutabilityAssessment:
    gate = gate or QualityGateOptions()
    selected_skill_names = selected_skill_names or []
    base_url = effective_base_url(case, suite)
    fingerprint = case_fingerprint(
        case,
        project_id=suite.project_id,
        version_id=suite.version_id,
        suite_id=suite.id,
        base_url=base_url,
    )
    context_hash = assessment_context_hash(
        translation_knowledge=translation_knowledge,
        selected_skill_names=selected_skill_names,
        memory=memory,
    )
    deterministic = _deterministic_assessment(
        suite=suite,
        case=case,
        run_id=run_id,
        case_hash=fingerprint,
        base_url=base_url,
        translation_knowledge=translation_knowledge,
        selected_skill_names=selected_skill_names,
        memory=memory,
    )
    try:
        llm_assessment = await _llm_assessment(
            llm=llm,
            suite=suite,
            case=case,
            run_id=run_id,
            case_hash=fingerprint,
            base_url=base_url,
            translation_knowledge=translation_knowledge,
            selected_skill_names=selected_skill_names,
            memory=memory,
        )
    except Exception as exc:  # noqa: BLE001
        assessment = deterministic.model_copy(deep=True)
        assessment.score = 0
        assessment.risk_level = "error"
        assessment.gate_decision = "block"
        assessment.error = str(exc) or exc.__class__.__name__
        assessment.issues.append(
            CaseQualityIssue(
                code="assessment_error",
                severity="blocking",
                message="用例可执行性评估失败",
                suggestion="检查文本模型配置，或填写强制执行原因后绕过本次质量闸门。",
            )
        )
    else:
        assessment = _merge_assessments(deterministic, llm_assessment, memory=memory)
    assessment.assessment_version = ASSESSMENT_VERSION
    assessment.context_hash = context_hash
    _apply_gate_options(assessment, gate)
    assessment.updated_at = time.time()
    return assessment


def _deterministic_assessment(
    *,
    suite: Suite,
    case: TestCase,
    run_id: str,
    case_hash: str,
    base_url: str,
    translation_knowledge: str,
    selected_skill_names: list[str],
    memory: CaseExecutionMemory | None,
) -> CaseExecutabilityAssessment:
    issues: list[CaseQualityIssue] = []
    suggestions: list[CaseRewriteSuggestion] = []
    score = 100
    text = "\n".join([case.name, *case.preconditions, *case.steps, *case.expected]).strip()
    grounded_context = "\n".join([translation_knowledge, *(selected_skill_names or [])])

    if not case.steps:
        issues.append(
            CaseQualityIssue(
                code="missing_steps",
                severity="blocking",
                message="用例缺少操作步骤",
                suggestion="补充可按顺序执行的页面操作步骤。",
            )
        )
        suggestions.append(
            CaseRewriteSuggestion(
                target="step",
                suggestion="补充具体入口、按钮/区域、输入值和操作顺序。",
                reason="Midscene 需要可观察页面上的可执行动作。",
            )
        )
        score -= 45
    if not case.expected:
        issues.append(
            CaseQualityIssue(
                code="missing_expected",
                severity="blocking",
                message="用例缺少预期结果",
                suggestion="补充页面可见的文案、状态、颜色、数值或列表变化。",
            )
        )
        score -= 35

    for idx, step in enumerate(case.steps, 1):
        step_text = step.strip()
        if len(step_text) < 4:
            issues.append(
                CaseQualityIssue(
                    code="step_too_short",
                    severity="warning",
                    message=f"第 {idx} 步描述过短",
                    suggestion="写清楚点击/输入/选择的对象和数据。",
                )
            )
            score -= 8
        if not _contains_any(step_text, ACTION_HINTS):
            issues.append(
                CaseQualityIssue(
                    code="missing_action",
                    severity="warning",
                    message=f"第 {idx} 步缺少明确操作动词",
                    suggestion="使用“点击/输入/选择/进入/等待”等可执行动作描述。",
                )
            )
            score -= 10
        if _contains_any(step_text, VAGUE_PATTERNS) and not _is_grounded(
            step_text, grounded_context
        ):
            issues.append(
                CaseQualityIssue(
                    code="vague_step",
                    severity="blocking" if "任意" in step_text else "warning",
                    message=f"第 {idx} 步存在模糊描述",
                    suggestion="补充候选范围、随机规则或业务术语解释到用例/Skill。",
                )
            )
            score -= 18 if "任意" in step_text else 8
            suggestions.append(
                CaseRewriteSuggestion(
                    target="step",
                    original=step_text,
                    suggestion=f"将“{step_text}”改写为具体页面入口、控件名称、候选范围和操作规则。",
                    reason="模糊操作会导致模型随机探索或误点。",
                )
            )

    for idx, expected in enumerate(case.expected, 1):
        expected_text = expected.strip()
        if _contains_any(expected_text, UNOBSERVABLE_PATTERNS) and not _contains_any(
            expected_text, OBSERVABLE_HINTS
        ):
            issues.append(
                CaseQualityIssue(
                    code="unobservable_expected",
                    severity="blocking",
                    message=f"第 {idx} 条预期结果不可直接观察",
                    suggestion="改为页面上能看到的文案、状态、颜色、数值或列表变化。",
                )
            )
            score -= 20
        if len(expected_text) < 4:
            issues.append(
                CaseQualityIssue(
                    code="expected_too_short",
                    severity="warning",
                    message=f"第 {idx} 条预期结果描述过短",
                    suggestion="写清楚具体可见结果。",
                )
            )
            score -= 6

    if memory is not None:
        score += 8
    score = _clamp(score)
    dimensions = [
        CaseQualityDimension(
            name="操作明确性",
            score=_dimension_score(
                score, issues, {"missing_steps", "missing_action", "vague_step"}
            ),
            reason="检查步骤是否包含明确动作、对象和顺序。",
        ),
        CaseQualityDimension(
            name="断言可观察性",
            score=_dimension_score(score, issues, {"missing_expected", "unobservable_expected"}),
            reason="检查预期是否能在页面上直接观察。",
        ),
        CaseQualityDimension(
            name="业务术语接地",
            score=85 if translation_knowledge or selected_skill_names or memory else 60,
            reason="根据项目规范、已选 Skill 和成功经验判断业务背景是否可用。",
        ),
        CaseQualityDimension(
            name="历史复用信心",
            score=90 if memory is not None else 50,
            reason="同一用例是否已有可用成功经验。",
        ),
    ]
    return _assessment(
        suite=suite,
        case=case,
        run_id=run_id,
        case_hash=case_hash,
        base_url=base_url,
        score=score,
        dimensions=dimensions,
        issues=issues,
        rewrite_suggestions=suggestions,
        draft_steps=_draft_steps(case),
        draft_expected=_draft_expected(case),
        context_sources=_context_sources(translation_knowledge, selected_skill_names, memory),
    )


async def _llm_assessment(
    *,
    llm: LLMClient,
    suite: Suite,
    case: TestCase,
    run_id: str,
    case_hash: str,
    base_url: str,
    translation_knowledge: str,
    selected_skill_names: list[str],
    memory: CaseExecutionMemory | None,
) -> CaseExecutabilityAssessment:
    context = build_experience_context(memory) if memory is not None else ""
    prompt = {
        "case": {
            "id": case.id,
            "name": case.name,
            "preconditions": case.preconditions,
            "steps": case.steps,
            "expected": case.expected,
            "base_url": base_url,
        },
        "project_knowledge": translation_knowledge,
        "selected_skills": selected_skill_names,
        "successful_memory": context,
    }
    resp = await llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "你是测试用例可执行性评估器。只输出 JSON 对象，不要 Markdown。"
                    "评分标准: 80-100 可直接执行; 60-79 有风险但可执行; <60 默认阻断。"
                    "只基于给定用例、项目知识、Skill、成功经验判断。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "评估以下用例是否适合交给 Midscene 视觉执行，并给出改写建议。\n"
                    "JSON 字段: score(int), dimensions([{name,score,reason}]), "
                    "issues([{code,severity,message,suggestion}]), "
                    "rewrite_suggestions([{target,original,suggestion,reason}]), "
                    "draft_steps(list[str]), draft_expected(list[str])。\n"
                    f"{json.dumps(prompt, ensure_ascii=False)}"
                ),
            },
        ]
    )
    data = loads_lenient(resp.content)
    dimensions = [
        CaseQualityDimension(**item)
        for item in _as_list(data.get("dimensions"))
        if isinstance(item, dict)
    ]
    issues = [
        CaseQualityIssue(**item) for item in _as_list(data.get("issues")) if isinstance(item, dict)
    ]
    suggestions = [
        CaseRewriteSuggestion(**item)
        for item in _as_list(data.get("rewrite_suggestions"))
        if isinstance(item, dict)
    ]
    return _assessment(
        suite=suite,
        case=case,
        run_id=run_id,
        case_hash=case_hash,
        base_url=base_url,
        score=_clamp(int(data.get("score", 0))),
        dimensions=dimensions,
        issues=issues,
        rewrite_suggestions=suggestions,
        draft_steps=[str(item) for item in _as_list(data.get("draft_steps"))],
        draft_expected=[str(item) for item in _as_list(data.get("draft_expected"))],
        context_sources=_context_sources(translation_knowledge, selected_skill_names, memory),
    )


def _merge_assessments(
    deterministic: CaseExecutabilityAssessment,
    llm_assessment: CaseExecutabilityAssessment,
    *,
    memory: CaseExecutionMemory | None,
) -> CaseExecutabilityAssessment:
    blocking = [issue for issue in deterministic.issues if issue.severity == "blocking"]
    dimension_score = _aggregate_dimension_score(llm_assessment.dimensions)
    llm_score = (
        min(llm_assessment.score, dimension_score)
        if dimension_score is not None
        else llm_assessment.score
    )
    score = min(deterministic.score, llm_score)
    issues = _dedupe_issues([*deterministic.issues, *llm_assessment.issues])
    suggestions = [*deterministic.rewrite_suggestions, *llm_assessment.rewrite_suggestions]
    merged = deterministic.model_copy(deep=True)
    merged.score = _clamp(score)
    merged.dimensions = llm_assessment.dimensions or deterministic.dimensions
    merged.issues = issues
    merged.rewrite_suggestions = suggestions
    merged.draft_steps = llm_assessment.draft_steps or deterministic.draft_steps
    merged.draft_expected = llm_assessment.draft_expected or deterministic.draft_expected
    _apply_gate(merged)
    return merged


def _assessment(
    *,
    suite: Suite,
    case: TestCase,
    run_id: str,
    case_hash: str,
    base_url: str,
    score: int,
    dimensions: list[CaseQualityDimension],
    issues: list[CaseQualityIssue],
    rewrite_suggestions: list[CaseRewriteSuggestion],
    draft_steps: list[str],
    draft_expected: list[str],
    context_sources: list[str],
) -> CaseExecutabilityAssessment:
    assessment = CaseExecutabilityAssessment(
        id=uuid.uuid4().hex,
        project_id=suite.project_id,
        version_id=suite.version_id,
        suite_id=suite.id,
        case_id=case.id,
        run_id=run_id,
        case_hash=case_hash,
        base_url=base_url,
        score=_clamp(score),
        dimensions=dimensions,
        issues=issues,
        rewrite_suggestions=rewrite_suggestions,
        draft_steps=draft_steps,
        draft_expected=draft_expected,
        context_sources=context_sources,
    )
    _apply_gate(assessment)
    return assessment


def _apply_gate(assessment: CaseExecutabilityAssessment) -> None:
    if assessment.risk_level == "error":
        assessment.gate_decision = "block"
        return
    if any(issue.severity == "blocking" for issue in assessment.issues):
        assessment.score = min(assessment.score, WARN_SCORE - 1)
        assessment.risk_level = "blocked"
        assessment.gate_decision = "block"
        return
    if assessment.score >= PASS_SCORE:
        assessment.risk_level = "pass"
        assessment.gate_decision = "allow"
    elif assessment.score >= WARN_SCORE:
        assessment.risk_level = "warn"
        assessment.gate_decision = "warn"
    else:
        assessment.risk_level = "blocked"
        assessment.gate_decision = "block"


def _apply_gate_options(
    assessment: CaseExecutabilityAssessment,
    gate: QualityGateOptions,
) -> None:
    if not gate.enabled:
        assessment.gate_decision = "allow"
        if "quality_gate_disabled" not in assessment.context_sources:
            assessment.context_sources.append("quality_gate_disabled")
    elif assessment.gate_decision == "block" and gate.force:
        assessment.gate_decision = "overridden"
        assessment.override_reason = gate.override_reason.strip()


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(needle.lower() in lower for needle in needles)


def _is_grounded(text: str, context: str) -> bool:
    if not context.strip():
        return False
    normalized_context = context.lower()
    normalized_text = text.strip().lower()
    if normalized_text and normalized_text in normalized_context:
        return True
    without_action = normalized_text
    for action in ACTION_HINTS:
        without_action = without_action.replace(action.lower(), "")
    if without_action.strip() and without_action.strip() in normalized_context:
        return True
    terms = [part for part in re.split(r"[\s,，。:：;；/|]+", normalized_text) if len(part) >= 2]
    return any(term and term in normalized_context for term in terms)


def _context_sources(
    translation_knowledge: str,
    selected_skill_names: list[str],
    memory: CaseExecutionMemory | None,
) -> list[str]:
    sources = []
    if translation_knowledge.strip():
        sources.append("project_knowledge")
    for name in selected_skill_names:
        sources.append(f"skill:{name}")
    if memory is not None:
        sources.append(f"memory:{memory.id}")
    return sources


def _dimension_score(base: int, issues: list[CaseQualityIssue], codes: set[str]) -> int:
    penalty = sum(
        18 if issue.severity == "blocking" else 8 for issue in issues if issue.code in codes
    )
    return _clamp(max(base, 70) - penalty)


def _aggregate_dimension_score(dimensions: list[CaseQualityDimension]) -> int | None:
    scores = [item.score for item in dimensions if item.score > 0]
    if not scores:
        return None
    return _clamp(round(sum(scores) / len(scores)))


def _draft_steps(case: TestCase) -> list[str]:
    if not case.steps:
        return ["补充具体页面入口、控件名称、输入值和操作顺序。"]
    return [step.strip() for step in case.steps if step.strip()]


def _draft_expected(case: TestCase) -> list[str]:
    if not case.expected:
        return ["补充页面可见的文案、状态、颜色、数值或列表变化。"]
    return [item.strip() for item in case.expected if item.strip()]


def _dedupe_issues(issues: list[CaseQualityIssue]) -> list[CaseQualityIssue]:
    seen = set()
    result = []
    for issue in issues:
        key = (issue.code, issue.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _clamp(value: int) -> int:
    return max(0, min(100, int(value)))
