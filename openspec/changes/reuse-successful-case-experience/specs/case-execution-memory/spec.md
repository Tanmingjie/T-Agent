## Purpose

This capability lets the platform learn from successful case executions by reusing a validated TestSpec and injecting concise case-level execution experience when the same case is executed again.

## ADDED Requirements

### Requirement: Successful TestSpec memory
The system SHALL persist the TestSpec used by a successful case execution as reusable execution memory when the case passes all phase assertions.

#### Scenario: Persist memory after passing execution
- **WHEN** a case execution completes with PASS and has a TestSpec
- **THEN** the system stores the TestSpec as reusable memory for that case identity and input fingerprint

#### Scenario: Do not learn from failing execution
- **WHEN** a case execution completes with FAIL, is aborted, or is interrupted
- **THEN** the system MUST NOT replace an existing successful TestSpec memory with that failed execution

### Requirement: Case fingerprint controls memory validity
Reusable case execution memory SHALL be keyed by the project, version, suite, case, effective base URL, and a fingerprint of the case name, preconditions, steps, and expected results.

#### Scenario: Same case content matches memory
- **WHEN** a later execution uses the same project, version, suite, case, effective base URL, and case content fingerprint
- **THEN** the system treats the stored memory as eligible for reuse

#### Scenario: Changed case content does not match memory
- **WHEN** the case steps, expected results, preconditions, name, effective base URL, or project version changes
- **THEN** the system MUST NOT reuse memory generated for the previous fingerprint

### Requirement: Reuse successful TestSpec before translation
When eligible memory exists and memory reuse is enabled for the execution, the system SHALL reuse the stored successful TestSpec instead of generating a new TestSpec with the translation model.

#### Scenario: Reuse avoids translation
- **WHEN** a case execution starts and eligible successful TestSpec memory exists
- **THEN** the system emits and executes the stored TestSpec without calling the translation model for that case

#### Scenario: Human approved TestSpec has priority
- **WHEN** the execution request includes a human approved TestSpec for the case
- **THEN** the system uses the human approved TestSpec instead of the stored memory

#### Scenario: Forced retranslation bypasses memory
- **WHEN** the user chooses to retranslate or disables memory reuse for a case execution
- **THEN** the system generates a new TestSpec and MUST NOT execute the stored memory TestSpec for that run

### Requirement: Successful experience summary
The system SHALL generate and store a concise case-level experience summary after a successful execution, using the successful TestSpec, execution steps, assertions, timing signals, and final result as source material.

#### Scenario: Generate experience from successful execution
- **WHEN** a case passes and enough execution evidence exists
- **THEN** the system stores an experience summary containing business terminology, stable operation hints, wait guidance, assertion guidance, and known avoid-repeat notes where applicable

#### Scenario: Experience generation failure does not fail run
- **WHEN** the experience summary cannot be generated because the model or source evidence is unavailable
- **THEN** the original case execution result remains unchanged and the system records that no new experience summary was produced

### Requirement: Inject experience on matching execution
When eligible memory contains an enabled experience summary, the system SHALL inject that summary into the execution context for the matching case.

#### Scenario: Inject enabled experience
- **WHEN** a case execution starts with eligible enabled experience memory
- **THEN** the Midscene execution context includes the case-level experience summary for that case

#### Scenario: Disabled experience is not injected
- **WHEN** the user disables memory reuse for a case or for an execution request
- **THEN** the system MUST NOT inject the stored experience summary for that execution

### Requirement: Memory observability and user control
The system SHALL expose whether a case has reusable memory, whether a run used it, and controls to view, disable, and bypass that memory.

#### Scenario: User views memory
- **WHEN** a user opens a case with stored execution memory
- **THEN** the user can view the stored TestSpec, experience summary, source run, last successful update time, usage count, and current enabled or stale status

#### Scenario: User disables memory
- **WHEN** a user disables execution memory for a case
- **THEN** later executions of that case MUST NOT reuse the stored TestSpec or inject the experience summary until memory is re-enabled

#### Scenario: User requests retranslation
- **WHEN** a user starts an execution and chooses to retranslate the case
- **THEN** the system bypasses stored TestSpec memory for that run while leaving the stored memory available for future runs

### Requirement: Memory health tracking
The system SHALL track memory reuse outcomes and mark memory as possibly stale when reuse correlates with repeated failures.

#### Scenario: Successful reuse updates usage signals
- **WHEN** a case passes after reusing execution memory
- **THEN** the system increments successful usage signals and may refresh the stored TestSpec or experience summary from that passing run

#### Scenario: Repeated failures mark memory stale
- **WHEN** a case repeatedly fails after reusing the same execution memory
- **THEN** the system marks that memory as possibly stale and surfaces the stale status to users
