## 1. Execution Scope Contract

- [x] 1.1 Extend the effective-case resolver to accept nullable case ID lists, reject empty/duplicate/foreign IDs, preserve Suite order, and insert the configured login setup once.
- [x] 1.2 Add focused repository tests for full, single, multi-select, invalid selection, ordering, and login-setup combinations.
- [x] 1.3 Add a nullable JSON `case_ids` column to the run queue model and create the Alembic migration while retaining the legacy `case_id` column.
- [x] 1.4 Update queue storage methods and tests to persist selected IDs and read legacy single-case records safely.

## 2. API And Execution Handoff

- [x] 2.1 Extend run and spec-preview request models with nullable `case_ids`, normalize legacy `case_id`, and reject requests that provide both forms.
- [x] 2.2 Make spec preview translate only the effective selected cases, including an automatically inserted login setup case, and add API coverage for selection validation.
- [x] 2.3 Make Run creation validate the selected scope, calculate `total_cases` from the effective cases, and validate approved specs against that same scope.
- [x] 2.4 Pass the selected case list through embedded `execute_run`, queue enqueue/claim, and `scripts/worker.py`, with fallback support for legacy queue rows.
- [x] 2.5 Add run-executor, API, queue-worker, login-setup, and parallelism tests proving that selected scope is never expanded to the full Suite.

## 3. Frontend Multi-Select Experience

- [x] 3.1 Add per-row and header checkboxes to the Suite case table, keep selections across search filtering, and prevent checkbox clicks from opening the case drawer.
- [x] 3.2 Update the primary execution action and confirmation dialog to distinguish “执行全部” from “执行所选 N 条”, while preserving the drawer single-case action.
- [x] 3.3 Send the same selected ID list through direct execution and manual TestSpec preview/review, and disable selection changes while a Run is active.
- [x] 3.4 Extend `useSuiteRun` to send `case_ids`, track the effective total from `suite_start`, and calculate partial-run progress without using the Suite's full case count.

## 4. Verification And Documentation

- [x] 4.1 Update user-facing execution documentation to describe list selection, filtered select-all behavior, login setup inclusion, and actual Run totals.
- [x] 4.2 Run focused backend tests for repository, execution API, run executor, orchestrator, storage, and worker behavior.
- [x] 4.3 Run the frontend production build and manually verify full, single, multi-select, filtered selection, manual spec review, and login-setup flows.
- [x] 4.4 Run the full Python test suite and perform one real Midscene smoke run containing a selected subset; record any unavailable environment dependency explicitly.

Smoke run `3ee76b4d6c04` selected only business case `TCB`; the effective Run contained login setup `TCA` plus `TCB` and reported `total_cases=2`. Login reached `inventory.html` and displayed `Products`, but the configured visual model returned a false-negative `aiAssert` because it declined to use the injected current URL, so the dependent business case was skipped by the existing login-setup failure rule.
