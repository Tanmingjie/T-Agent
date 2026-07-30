## 1. Suite configuration

- [x] 1.1 Add nullable `login_setup_case_id` to Suite settings storage and create the Alembic migration, preserving null defaults for existing suites.
- [x] 1.2 Extend settings repository/API contracts to read and write the field, validate that the referenced case belongs to the Suite, and cover configured, cleared, and invalid references with API/database tests.
- [x] 1.3 Add the login setup case dropdown with a “不使用” option to the existing Suite settings page and verify the frontend build.

## 2. Midscene authentication state exchange

- [x] 2.1 Extend `MidsceneCaseAgent` and `VisualExecutor` with optional run-scoped storage-state path and capture/load mode, without changing payloads for suites that do not configure login setup.
- [x] 2.2 Refactor the Node runner to create an explicit BrowserContext, load state for business cases, capture state only after a successful login setup case (including IndexedDB), and close context/browser cleanly.
- [x] 2.3 Add runner and VisualExecutor tests for state capture, state loading, capture failure, option propagation, and the unchanged no-state path.

## 3. Run orchestration

- [x] 3.1 Resolve and deduplicate the effective case set for full-suite and single-case runs so the configured login setup case executes once before requested business cases.
- [x] 3.2 Add a serial setup phase before the existing concurrent business phase; on setup/capture failure preserve the setup result and emit not-executed records for dependent cases.
- [x] 3.3 Make spec preview and approved-spec handling use the same effective case set so manual review includes the login setup case when required.
- [x] 3.4 Create the authentication state under a run-scoped system temporary directory and guarantee cleanup from `run_executor` on completed, failed, aborted, and exception paths.
- [x] 3.5 Add orchestration and execution-route tests for full Suite, single business case, setup-case-only, setup failure, concurrent business start after setup, and temporary-state cleanup.

## 4. Verification and documentation

- [x] 4.1 Document how to add the login setup row in Excel, remove repeated login steps from business cases, configure the Suite dropdown, and the sessionStorage limitation.
- [x] 4.2 Run formatters, focused backend/runner tests, the full pytest suite, and the frontend build.
- [x] 4.3 Run a real Midscene SauceDemo smoke with one login setup case and at least two business cases, confirm login executes once, both isolated cases start authenticated, reports remain separate, and the temporary state is removed after the Run.
