## 1. Data Model and Storage

- [x] 1.1 Add `CaseExecutabilityAssessment`, dimension, issue, and rewrite suggestion domain models.
- [x] 1.2 Add SQLModel row and Store methods for saving latest/history assessment and querying by case or run.
- [x] 1.3 Add Alembic migration for assessment persistence and keep local `Store.init` fallback compatible.
- [x] 1.4 Attach assessment summary to `ExecutionRecord.metrics` for executed cases.
- [x] 1.5 Add storage tests for assessment round-trip, latest lookup, run lookup, and override metadata.
- [x] 1.6 Add reusable assessment cache metadata, lookup, migration columns, and cache-source filtering.

## 2. Assessment Engine

- [x] 2.1 Add deterministic precheck rules for empty fields, vague wording, missing operation targets, unobservable expectations, and unclear waits.
- [x] 2.2 Add LLM assessment prompt that returns structured JSON score, dimensions, issues, suggestions, draft steps, and draft expected results.
- [x] 2.3 Merge deterministic and LLM results conservatively, preserving blocking issues unless project guidance, selected Skill, or memory grounds them.
- [x] 2.4 Include project use-case guidance, selected Skills, and matching successful execution memory as assessment context.
- [x] 2.5 Add assessment tests for high-score, warning, blocked, grounded terminology, memory confidence, and LLM parse/timeout failure.
- [x] 2.6 Add assessment version and context hashing for project guidance, selected Skills, and successful memory context.

## 3. Execution Gate Integration

- [x] 3.1 Extend run options with `quality_gate_enabled`, `force_low_quality_cases`, and `quality_override_reason`.
- [x] 3.2 Run assessment after target cases and selected Skills are resolved, before TestSpec reuse, translation, or Midscene execution.
- [x] 3.3 Emit `case_quality` run events for pass, warn, block, error, and override decisions.
- [x] 3.4 Block low-score or errored cases by default and return actionable reasons without starting Midscene for those cases.
- [x] 3.5 Allow explicit override with non-empty reason and record the bypass in events and case metrics.
- [x] 3.6 Reuse unchanged assessments before calling the quality LLM and clone a run-specific cached result.

## 4. API Surface

- [x] 4.1 Add endpoint to preview assessment for selected suite cases without starting execution.
- [x] 4.2 Add endpoint to fetch the latest assessment for a case detail view.
- [x] 4.3 Update execution API validation for quality gate options and missing override reason.
- [x] 4.4 Ensure queue mode persists quality gate options and worker execution uses the same gate behavior.
- [x] 4.5 Add API tests for preview, latest assessment, blocked execution, warning execution, and override execution.

## 5. Frontend Experience

- [x] 5.1 Show quality assessment in the execution confirmation modal before starting the run.
- [x] 5.2 Highlight pass/warn/block states with score, blocking issues, warnings, and rewrite suggestions.
- [x] 5.3 Add force execution control requiring a reason when blocked cases are selected.
- [x] 5.4 Show latest assessment in the case drawer with copyable rewrite drafts and missing Skill/SOP suggestions.
- [x] 5.5 Add frontend validation/build coverage for the modal and drawer changes.
- [x] 5.6 Show cache-hit provenance in the case assessment drawer.

## 6. Validation and Rollout

- [x] 6.1 Add run executor tests proving blocked cases do not call translation or Midscene unless overridden.
- [x] 6.2 Add tests proving medium-score cases warn but still execute.
- [x] 6.3 Add tests proving assessment failure fails closed and override is audited.
- [x] 6.4 Run targeted Python tests for storage, assessment, API, executor, and queue worker paths.
- [x] 6.5 Run frontend build and document deployment notes, including the new migration and Node/runtime assumptions.
- [x] 6.6 Add regression coverage proving repeated unchanged runs reuse assessment cache.
