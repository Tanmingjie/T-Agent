## 1. Data Model and Storage

- [x] 1.1 Add `CaseExecutionMemory` domain model with fingerprint, stored TestSpec, experience summary, enabled/stale flags, source run metadata, and usage counters.
- [x] 1.2 Add `case_execution_memory` SQLModel row and Store CRUD methods for lookup, upsert, enable/disable, and usage outcome updates.
- [x] 1.3 Add Alembic migration for the new memory table and keep `Store.init` local fallback compatible.
- [x] 1.4 Add deterministic case fingerprint helper covering project, version, suite, case, effective base URL, name, preconditions, steps, and expected results.
- [x] 1.5 Add storage tests for memory round-trip, fingerprint mismatch, disabled memory, and stale status persistence.

## 2. Run-Time Reuse Flow

- [x] 2.1 Extend execution request options to allow per-run memory bypass/retranslation for selected cases.
- [x] 2.2 Load eligible memory before orchestration and prefer human approved TestSpecs over memory-backed TestSpecs.
- [x] 2.3 Reuse memory-backed TestSpecs through the existing approved-spec execution path and emit metadata that the spec came from memory.
- [x] 2.4 Inject only the current case's enabled experience summary into the Midscene execution context.
- [x] 2.5 Record memory hit/miss, bypass, stale, and source memory id in execution metrics or run events for observability.

## 3. Learning Flow

- [x] 3.1 Add successful execution learner that ignores failed, aborted, interrupted, or spec-missing records.
- [x] 3.2 Generate concise experience summaries from case fields, TestSpec, action summaries, phase assertions, timing signals, and final result.
- [x] 3.3 Upsert memory after PASS records without changing the original run result if summary generation fails.
- [x] 3.4 Update success/failure usage counters after runs that reused memory and mark memory stale after repeated reuse failures.
- [x] 3.5 Add learner tests for PASS learning, failure non-learning, summary failure best-effort behavior, and stale marking.

## 4. API and Frontend Controls

- [x] 4.1 Add API endpoints to get case memory, enable/disable memory, and request one-run retranslation bypass.
- [x] 4.2 Surface memory availability and status in case details or the execution confirmation flow.
- [x] 4.3 Add UI to view stored TestSpec, experience summary, source run, last successful update time, usage count, enabled status, and stale status.
- [x] 4.4 Add UI controls to disable/re-enable memory and bypass memory for the next execution.
- [x] 4.5 Add API/frontend tests for view, disable, re-enable, and bypass behavior.

## 5. Validation

- [x] 5.1 Add run executor tests for memory hit reuse, hash-change miss, human approved spec priority, and per-run retranslation bypass.
- [x] 5.2 Add Midscene agent or visual executor tests verifying case-scoped experience injection does not leak across cases.
- [x] 5.3 Add result/event tests showing memory source and hit/miss state are observable.
- [x] 5.4 Run targeted Python tests for storage, execution, API, learner, and Midscene agent paths.
- [x] 5.5 Run frontend validation for the memory controls when the UI changes are complete.
