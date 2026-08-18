## Context

The current Midscene execution path already persists the TestSpec used by each case in `ExecutionRecord.spec`, maps phase assertions into `ExecutionRecord.case_assertions`, and passes selected project Skill content through the shared execution context. Execution can also bypass translation through `approved_specs`, which makes successful TestSpec reuse a small extension of an existing contract rather than a new execution backend.

The change should keep Midscene as the only product execution kernel and preserve the current human approval flow. Execution memory is an optimization and guidance layer; it must never hide failures or change a failed result into a pass.

## Goals / Non-Goals

**Goals:**

- Reduce translation drift by reusing a previously successful TestSpec for unchanged cases.
- Improve repeat execution accuracy by injecting case-level successful experience into the existing Midscene context.
- Make reuse visible and controllable so users can inspect, disable, or bypass memory when it looks wrong.
- Keep learning best-effort: failed memory generation must not affect the original run result.

**Non-Goals:**

- No coordinate replay, Playwright action replay, or deterministic browser script generation.
- No change to the Midscene model provider or low-level model inference performance.
- No automatic learning from failed, aborted, or partial executions.
- No cross-project or cross-version memory sharing in this phase.

## Decisions

### Store execution memory as a first-class case asset

Add a project-scoped `CaseExecutionMemory` domain model and SQL row rather than overloading `ProjectSkill` or `ExecutionRecord.metrics`.

Rationale: memory needs status, fingerprint, source run, usage counters, stale flags, and a stored TestSpec. Those are lifecycle fields, not free-form Skill content. Keeping it separate also lets the UI show and disable memory without mutating user-authored project Skills.

Alternative considered: write generated experience back into `ProjectSkill`. This would be easy to inject but would mix generated per-case facts with curated project knowledge and make stale/disable semantics awkward.

### Use a deterministic case fingerprint for validity

Compute a stable hash from normalized case identity inputs: project id, version id, suite id, case id, effective base URL, case name, preconditions, steps, and expected results. Store the hash with memory and only reuse on exact match.

Rationale: exact matching is conservative and avoids leaking outdated behavior into changed cases. It also gives users a predictable answer for why memory did or did not apply.

Alternative considered: semantic similarity matching. That may increase reuse, but it risks applying stale business guidance to subtly different test intent and is too risky for phase one.

### Reuse TestSpec through the existing approved-spec path

At run startup, load eligible memory for target cases. Human approved specs remain highest priority. Memory-backed specs populate the same map used by `approved_specs`, with metadata marking the source as memory.

Rationale: `MidsceneCaseAgent` already checks `approved_specs` before translation. Reusing that path minimizes new execution behavior and keeps `spec_ready` event semantics intact.

Alternative considered: add a separate `cached_specs` parameter to the agent. That makes source tracking explicit but duplicates an already working bypass mechanism.

### Inject experience as case-scoped execution context

Build the agent with the existing project and selected Skill context, then append case-specific memory only when running that case. The injected block should be clearly labeled and concise, for example `[成功经验:<case_id>]`.

Rationale: current `translation_knowledge` is shared across all cases in a run. Memory is per-case, so the implementation should avoid leaking one case's chamber names, wait hints, or state rules into another case.

Alternative considered: append all selected case memories to one global run context. This is simpler but creates context pollution and token growth for multi-case runs.

### Generate experience after record persistence succeeds

After a record is saved and known to be PASS, run a best-effort summarizer using the existing LLM client. The summarizer receives only non-sensitive internal execution artifacts already persisted for the run: case fields, TestSpec, action summaries, phase assertions, timings, and final result. It returns concise Markdown guidance.

Rationale: learning should not interfere with the user-visible run result. Running after record construction also gives a complete view of what actually happened.

Alternative considered: summarize during runner execution. That could include richer live context but would add latency to the critical execution path and complicate failure handling.

### Keep user control at execution and case levels

Support two controls: persistent enable/disable on the memory asset, and one-run bypass for retranslation. The persistent flag prevents both TestSpec reuse and experience injection. The one-run bypass ignores memory for that run without deleting it.

Rationale: users need to recover quickly from bad memory while still preserving an audit trail and allowing future reuse after review.

Alternative considered: delete memory on user bypass. This is destructive and makes experimentation harder.

## Risks / Trade-offs

- Stale memory can make repeated runs fail in the same way -> use exact fingerprints, stale marking after repeated reuse failures, and visible disable/retranslate controls.
- Experience summaries can overfit to incidental page state -> prompt the summarizer to write only reusable operational guidance and require PASS evidence before learning.
- Memory reuse can hide translation improvements after prompt updates -> provide retranslate bypass and refresh memory from later successful runs.
- Per-case context injection can increase tokens for large suites -> inject only the current case's memory and keep summaries concise.
- Human-approved specs and memory may conflict -> always prefer human-approved specs for the current run.

## Migration Plan

- Add the new table through Alembic and keep local `Store.init` fallback compatible with SQLite.
- Existing projects start with no memory; behavior remains unchanged until cases pass and memory is created.
- Rollback can disable reuse globally in code or by marking memory disabled; stored records remain historical data.

## Open Questions

None.
