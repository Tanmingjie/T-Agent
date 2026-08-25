## Purpose

用例可执行性质量闸门用于在 Midscene 执行前识别抽象、歧义、不可观察或缺少业务背景的旧文本用例，并给出可操作的改写建议，避免低质量用例直接进入长时间且不稳定的视觉执行。

## ADDED Requirements

### Requirement: Per-case executability assessment
The system SHALL evaluate every target test case before Midscene translation or execution and produce a per-case executability assessment with a numeric score from 0 to 100, a risk level, dimension scores, issue list, and rewrite suggestions.

#### Scenario: Assessment is produced before execution
- **WHEN** a user starts a run for one or more cases
- **THEN** the system produces an executability assessment for each target case before generating or reusing a TestSpec for that case

#### Scenario: Assessment contains actionable detail
- **WHEN** a case assessment is returned
- **THEN** it includes a total score, risk level, assessed dimensions, blocking issues if any, non-blocking warnings if any, and suggested rewrites for unclear steps or expected results

### Requirement: Assessment uses available business context
The system SHALL assess executability using the case text together with available project use-case guidance, selected execution Skills, and matching successful execution memory when present.

#### Scenario: Skill explains business terminology
- **WHEN** a case contains business terminology that is explained by selected Skill or project guidance
- **THEN** the assessment treats the terminology as grounded and reports the referenced context in its rationale

#### Scenario: Successful memory improves confidence
- **WHEN** a matching enabled successful execution memory exists for the unchanged case
- **THEN** the assessment accounts for the memory and may raise confidence while still reporting visible issues in the current case text

### Requirement: Assessment results are reused for unchanged inputs
The system SHALL reuse a previously successful executability assessment when the case content, base URL, assessment version, project guidance, selected Skills, and successful execution memory context are unchanged.

#### Scenario: Unchanged case reuses cached assessment
- **WHEN** the same target case is run again with unchanged assessment inputs
- **THEN** the system records a new run-specific assessment marked as a cache hit without calling the assessment LLM again

#### Scenario: Changed context invalidates cached assessment
- **WHEN** the case content, base URL, selected Skills, project guidance, successful memory context, or assessment version changes
- **THEN** the system performs a fresh executability assessment instead of reusing the previous one

#### Scenario: Unsafe assessment is not used as cache source
- **WHEN** a previous assessment failed, was manually overridden, or is itself a cache-hit copy
- **THEN** the system does not use that assessment as the reusable cache source

### Requirement: Quality gate controls execution
The system SHALL apply a configurable quality gate before execution with default bands: pass at score 80 or above, warn at score 60 through 79, and block below score 60.

#### Scenario: High score proceeds
- **WHEN** every target case scores 80 or above
- **THEN** the run proceeds without a quality warning gate

#### Scenario: Medium score warns but allows execution
- **WHEN** one or more target cases score from 60 through 79 and no target case is blocked
- **THEN** the system warns the user with risks and rewrite suggestions before execution proceeds

#### Scenario: Low score blocks by default
- **WHEN** any target case scores below 60
- **THEN** the system does not start Midscene execution for blocked cases by default and returns the blocking reasons and rewrite suggestions

### Requirement: User can explicitly override blocked cases
The system SHALL allow an explicit per-run override for blocked cases when the user supplies an override reason, and SHALL record that the run bypassed the quality gate.

#### Scenario: Blocked case forced with reason
- **WHEN** a user starts a run with blocked cases and provides a force-execution option plus a non-empty reason
- **THEN** the system allows those cases to execute and records the score, blocking issues, and override reason

#### Scenario: Blocked case forced without reason
- **WHEN** a user starts a run with blocked cases and provides a force-execution option without a reason
- **THEN** the system rejects the request and explains that an override reason is required

### Requirement: Rewrite suggestions are non-destructive
The system SHALL present rewrite suggestions and improved TestSpec-oriented drafts without modifying the original Excel-imported case content automatically.

#### Scenario: Suggestions do not change original case
- **WHEN** assessment generates a rewritten step or expected-result draft
- **THEN** the original case name, steps, preconditions, and expected results remain unchanged until a user explicitly edits the case through an existing edit flow

#### Scenario: Suggestions identify missing information
- **WHEN** a case cannot be safely rewritten because business context is missing
- **THEN** the suggestion identifies the missing information, such as entry path, allowed random choices, observable state, wait condition, or required Skill/SOP

### Requirement: Assessment is observable
The system SHALL make assessment results visible through execution events, case details, and run results.

#### Scenario: Run events include gate result
- **WHEN** assessment completes for a run
- **THEN** run events include each target case's score, risk level, gate decision, and whether execution was blocked or overridden
- **AND** include whether the assessment was reused from cache when applicable

#### Scenario: Case details show latest assessment
- **WHEN** a user opens a case detail view after assessment
- **THEN** the UI shows the latest assessment score, risk level, issues, rewrite suggestions, and timestamp

#### Scenario: Run result preserves assessment context
- **WHEN** a case executes after assessment
- **THEN** the case result includes the assessment score and gate decision used for that execution

### Requirement: Assessment failure is safe
The system SHALL fail closed for assessment service errors before execution, except when a user explicitly chooses force execution with a reason.

#### Scenario: Assessment service fails
- **WHEN** assessment cannot complete because of LLM, parsing, timeout, or internal errors
- **THEN** the system reports the assessment failure and does not execute affected cases by default

#### Scenario: Assessment failure is overridden
- **WHEN** assessment fails and the user provides force execution with a non-empty reason
- **THEN** the system may execute affected cases and records that the quality gate was bypassed due to assessment failure
