# Priority 5A: Durable Policy and Exception Contracts

**Status:** Implemented release candidate
**Baseline:** MeshAgent commit `82c3eaf`
**Author:** Manus AI

## 1. Outcome and boundary

Priority 5A establishes the durable backend contract for CISO policy governance and time-bounded exceptions. It replaces the earlier single-row policy and minimal approval records with versioned aggregates, exact evidence bindings, immutable lifecycle events, and optimistic concurrency.

The relational control plane remains authoritative for mutable workflow state. A policy version is immutable after creation; lifecycle commands only change its state and effective interval. An exception is bound to one policy version and its SHA-256 content digest. This prevents a later policy edit from silently changing what the CISO approved.

Priority 5A does not yet implement maker-checker separation for policy activation, automatic expiry materialization, renewal or revocation commands, native HyperMesh projection, or the redesigned CISO workspace. Those controls belong to Stages 5B, 5C, and 5D respectively.

## 2. Aggregate contracts

### 2.1 Policy aggregate

A policy has a stable identifier, business name, scope, lifecycle status, active version pointer, and aggregate version counter. The aggregate version counter changes on every lifecycle mutation and is the optimistic-concurrency token presented by clients.

Each policy version contains the enforcement threshold, denied licenses, unknown-evidence behavior, rationale, creator identity, lifecycle state, effective interval, and canonical content digest. The digest is calculated over the policy identifier, version number, enforcement values, and rationale. It is not a signature and does not prove an external attestation; it detects substitution within the governed workflow.

New policies are created with version 1 active to preserve the current product contract. Every later version follows this state machine:

```text
draft --submit--> in_review --activate--> active --replace--> superseded
  |                    |
  +--withdraw----------+-------------------------------> withdrawn

active policy --retire--> retired
```

Only one active version may exist for a policy. A new draft cannot be created while another draft or version in review is unfinished. A CISO may withdraw an unfinished version without deleting its content or history. Activation closes the previous version's effective interval and moves it to `superseded` in the same database transaction. A retired policy cannot receive, withdraw, or activate another version.

### 2.2 Exception aggregate

An exception records the exact `policy_id`, `policy_version`, and policy content digest reviewed by the approver. It also records its scope, owner identity, rationale, compensating controls, expiry, supporting evidence identifiers, requester identity, decision identity, and aggregate version.

The exception request digest is SHA-256 over the complete decision payload: policy binding, scope, rationale, compensating controls, owner, expiry, and evidence identifiers. The linked approval stores the same digest, evidence identifiers, and exception version. Before a decision commits, the service verifies that the approval still matches the exception. A stale or substituted request is rejected.

An exception begins as `pending`. A CISO decision moves it to `approved` or `rejected`. Read models report a pending or approved record as `expired` once its expiry is in the past, even before Stage 5B materializes that transition. Existing separation of duties remains enforced: the requester cannot decide their own request.

### 2.3 Immutable lifecycle events

`policy_events` and `exception_events` are append-only event ledgers. Each event records the resource, actor subject, actor display name, actor role, action, prior state, resulting state, rationale, evidence identifiers, timestamp, and request correlation identifier.

The resource mutation and event append occur under the same `BEGIN IMMEDIATE` SQLite transaction. A caller therefore cannot observe a lifecycle state without its corresponding event. API audit entries remain a separate tamper-evident request log; governance events are the domain history used to reconstruct a policy or exception lifecycle.[1] [2]

## 3. Durable schema

| Table | Responsibility | Priority 5A additions |
| --- | --- | --- |
| `policies` | Stable policy aggregate | Aggregate `version` counter and active-version pointer |
| `policy_versions` | Immutable policy content | Lifecycle state, SHA-256 content digest, creator name, effective interval |
| `policy_events` | Ordered policy history | Actor, transition, rationale, correlation, and evidence fields |
| `exceptions` | Time-bounded policy deviation | Exact policy version and digest, owner/requester names, evidence, request digest, decision and revocation metadata |
| `exception_events` | Ordered exception history | Request and decision events with actor, rationale, correlation, and evidence |
| `approvals` | Decision work item | Bound exception version, request digest, evidence identifiers, and approver name |

A partial unique index guarantees at most one `active` row in `policy_versions` for each policy. Application checks prevent a second unexpired pending or approved exception for the same policy and scope. Foreign-key validation binds new exception rows to an existing policy version; the store also validates this relationship transactionally for upgraded databases.

## 4. Command semantics

Policy creation atomically writes the policy, active version 1, content digest, and `policy.created` event. Creating another version checks the aggregate version, rejects retired policies and unfinished drafts, inserts immutable draft content, increments the aggregate version, and appends `policy.version_created`.

Submission requires a draft and moves it to `in_review`. Withdrawal accepts a draft or version in review, preserves its immutable content, marks it `withdrawn`, and appends a lifecycle event. Activation requires a version in review. It supersedes the previous active version, activates the candidate, changes the active pointer, increments the aggregate version, and appends one activation event. Retirement is permitted only when no draft or version in review remains.

Exception creation validates that the target policy is active and the requested version is its active version. If an older client omits `policy_version`, the service binds the request to the active version inside the transaction. The response always contains the exact version and digests. The request and its approval are created atomically with `exception.requested`.

Approval decisions validate the approval version, pending state, decision deadline, requester/approver separation, exception version, exception state, exception expiry, and matching request digest. The approval, exception, and `exception.approved` or `exception.rejected` event commit together.

## 5. API contract

| Method | Path | Result | Authority |
| --- | --- | --- | --- |
| `GET` | `/api/policies` | Policy aggregates with versions and event history | `policy.read` |
| `POST` | `/api/policies` | Active policy version 1 | `policy.write` |
| `GET` | `/api/policies/{policy_id}` | One complete policy aggregate | `policy.read` |
| `POST` | `/api/policies/{policy_id}/versions` | New immutable draft | `policy.write` |
| `POST` | `/api/policies/{policy_id}/versions/{version}/submit` | Draft moved to review | `policy.write` |
| `POST` | `/api/policies/{policy_id}/versions/{version}/activate` | Candidate activated and predecessor superseded | `policy.write` |
| `POST` | `/api/policies/{policy_id}/versions/{version}/withdraw` | Unfinished candidate withdrawn without deletion | `policy.write` |
| `POST` | `/api/policies/{policy_id}/retire` | Active policy retired | `policy.write` |
| `GET` | `/api/exceptions` | Exception register | `exception.read` |
| `POST` | `/api/exceptions` | Exception plus linked approval | `exception.request` |
| `GET` | `/api/exceptions/{exception_id}` | Exact exception and immutable events | `exception.read` |
| `GET` | `/api/approvals` | Decision queue | `exception.approve` |
| `GET` | `/api/approvals/{approval_id}` | Bound approval contract | `exception.approve` |
| `POST` | `/api/approvals/{approval_id}/decision` | Atomic approval and exception decision | `exception.approve` |

Every lifecycle mutation accepts an expected aggregate version. Stale policy, exception, or approval state returns HTTP `412`. Invalid state transitions, duplicate active exceptions, digest mismatches, and separation-of-duties conflicts return HTTP `409`. Missing resources return HTTP `404`. Role and capability denial returns HTTP `403`.[2] [3]

## 6. Migration and compatibility

Startup performs additive column upgrades and creates the two event ledgers without deleting existing data. Historical policy versions receive deterministic content digests and effective intervals. Historical exceptions receive exact policy-version bindings, request digests, names, and evidence arrays. The complete upgrade and backfill runs in one immediate transaction, and each required legacy event is repaired independently by action. A process stop therefore cannot commit a partial lifecycle, while deterministic event identifiers keep repeated startups idempotent.

The original policy-create and exception-request endpoints remain valid. Existing exception clients may omit `policy_version`; new clients send it explicitly so the server can reject a stale form that references a superseded version. Responses are additive, preserving existing fields while exposing the stronger contract.

The complete pre-5A collaborative state was backed up offline and verified at `/home/ubuntu/meshagent-backups/20260924T175731Z-pre-priority5a` before migration work began.[4]

## 7. Validation requirements

The focused contract suite verifies policy digest creation, draft/review/activation/withdrawal sequencing, single active-version behavior, optimistic concurrency, role denial, retirement, exact exception binding, evidence de-duplication, duplicate-scope prevention, approval linkage, immutable event correlation, and stale-decision rejection. A multi-version legacy-schema fixture verifies additive migration, repeated-startup idempotency, and repair of an interrupted terminal-event backfill without data loss.[5]

Priority 5A is release-ready only after the complete API suite, frontend typecheck/lint/tests/build, native HyperMesh suites, source-hygiene checks, and a live migration smoke against a copy of the preserved collaborative state pass. The live service should not be pointed at the authoritative collaborative state until these gates succeed.

## 8. Deferred controls

Stage 5B will add policy maker-checker separation, exception renewal and revocation, materialized expiry transitions, deterministic expiry reconciliation, and notification rules. Stage 5C will project policy and exception events through a transactional outbox into native HyperMesh and connect Analyst review/case evidence. Stage 5D will expose the lifecycle in the professional CISO workspace. Stage 5E will perform full release, recovery, role, and container validation.

## References

[1]: ../services/api/app/control_plane.py "MeshAgent durable control-plane store"
[2]: ../services/api/app/main.py "MeshAgent governance API routes"
[3]: ../services/api/app/workflow_models.py "MeshAgent governance wire models"
[4]: ./PRODUCTION_OPERATIONS.md "MeshAgent production backup and recovery runbook"
[5]: ../services/api/tests/test_governance_contracts.py "Priority 5A governance contract regression suite"
