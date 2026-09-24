# Priority 5B: Governance Enforcement and Lifecycle Operations

**Status:** Implemented release candidate
**Baseline:** MeshAgent commit `2e39f7c`
**Author:** Manus AI

## 1. Outcome and boundary

Priority 5B turns the durable policy and exception records from Priority 5A into enforced governance workflows. The API now controls who may activate policy changes, renew or revoke exceptions, and decide approvals. A service-owned reconciler materializes time-based transitions without requiring a user to open a page.

The relational control plane remains authoritative for mutable lifecycle state. Every policy, exception, approval, and notification mutation commits in SQLite before the API returns. Priority 5C will project these finalized lifecycle events into native HyperMesh evidence; Priority 5B does not create synthetic graph records.

## 2. Server-enforced authority

| Action | Required capability | Separation rule |
| --- | --- | --- |
| Create or submit a policy version | `policy.write` | CISO role only |
| Activate a submitted policy version | `policy.activate` | In production, the activator must differ from both the author and submitter |
| Request an exception | `exception.request` | Analyst or CISO |
| Renew an approved exception | `exception.renew` | The original requester cannot renew the same request |
| Decide an exception approval | `exception.approve` | The requester cannot approve the request |
| Revoke an approved exception | `exception.revoke` | The requester cannot revoke the request |
| Inspect or force lifecycle reconciliation | `exception.approve` | CISO role only |

Production maker-checker enforcement is fail closed. A new policy is created as `pending` with version 1 already `in_review`; a second CISO must activate it before exceptions can target it. The author or submitter receives HTTP `409` when attempting that activation. Every later submitted revision follows the same rule. Local development retains immediate active creation and permits same-user activation so one-person evaluation remains possible.

The policy version records the author, submitter, activator, and withdrawer identities and timestamps. These fields are server-owned. A client cannot nominate an approver or rewrite attribution.

## 3. Exception renewal and revocation

Renewal never extends or rewrites an approved exception. It creates a new pending exception with its own identifier, request digest, evidence list, expiry, approval, and immutable event history. The new request records `predecessor_exception_id` and an incremented `renewal_number`. It binds the policy version that is active when renewal is requested, so a policy change cannot silently carry an old exception forward.

The predecessor remains approved while the renewal is pending. Approval of the successor atomically moves the predecessor to `superseded`, links `superseded_by_exception_id`, activates the successor, updates the approval, and appends both event histories. Rejection leaves the predecessor unchanged. A unique partial index prevents two pending or approved successors for one predecessor, while a rejected or expired attempt remains in history and permits a corrected renewal with the next `renewal_number`.

Revocation is terminal. It records the independent revoker, time, rationale, evidence, version increment, and `exception.revoked` event. If a renewal is still pending, the same transaction revokes that successor, rejects its approval, and appends `exception.renewal_cancelled`. A stale version returns HTTP `412`; an invalid lifecycle or separation violation returns HTTP `409`.

## 4. Materialized expiry

The API service owns a deterministic reconciliation loop. It runs once during startup and then at a bounded interval controlled by `MESHAGENT_GOVERNANCE_RECONCILE_SECONDS`, which defaults to 60 seconds and is constrained to 0.1–3,600 seconds.

Each scan runs under one immediate transaction. It performs two ordered operations:

1. Pending approvals at or past `expires_at` become `expired`, receive `expired_at`, and increment their version. Their pending exception becomes `expired` with an `exception.approval_expired` system event.
2. Approved exceptions at or past `expires_at` become `expired` with an `exception.expired` system event and a version increment.

The queries select only currently pending or approved rows, and notification inserts use unique deduplication keys. Repeating a scan or restarting the service therefore produces no duplicate transition or notification. Read models report the stored state; they no longer simulate expiry only while rendering a response.

`governance_reconciler_state` stores the last start, last successful completion, last error, and transition counts. CISO operators can inspect `GET /api/governance/lifecycle/status` and can invoke `POST /api/governance/lifecycle/reconcile` for controlled recovery. The resident loop logs failures and retries on the next interval.

> The supported v1 deployment remains one API writer per state directory. A horizontally scaled deployment must elect one lifecycle worker or claim due rows through a shared database before enabling the loop on multiple replicas.

## 5. Notifications

Governance notifications are durable and separate from the existing review/case notification table because the earlier table intentionally restricts resource kinds. The new ledger supports policy, exception, and approval destinations while preserving per-person read state.

The service creates notifications for policy review requests, independent activation, new exception approvals, renewal approvals, approval decisions, seven-day and one-day expiry warnings, expiry, and revocation. The same `/api/notifications` endpoint merges work and governance notifications, applies recipient-subject or recipient-role isolation, removes routing metadata not intended for the client, and sorts one bounded feed.

Routes respect the recipient's authority. CISO recipients are sent to Policies or Approvals. Analyst recipients are returned to the Analyst queue rather than an inaccessible CISO page. Mark-read checks visibility in the owning notification ledger before writing a read receipt.

The exception `owner` field remains a responsibility label, not a verified identity. The service never converts that free-text value into a notification recipient. Decisions and expiry alerts go only to the authenticated requester and the authorized CISO role until a governed directory-backed ownership contract is introduced.

## 6. Durable schema additions

| Store | Priority 5B additions |
| --- | --- |
| `policy_versions` | Submitter, activator, withdrawer identities and timestamps |
| `exceptions` | Renewal predecessor, renewal number, successor link, supersession time, revoker name, and revocation rationale |
| `approvals` | Materialized `expired_at` |
| `governance_notifications` | Role- or subject-scoped notification records with unique deduplication keys |
| `governance_notification_reads` | Per-subject read receipts |
| `governance_reconciler_state` | Last run, completion, error, and transition counts |

Startup adds columns and tables transactionally. Legacy rows retain their identifiers and populated digests; backfill computes a digest only when the stored field is empty. Reviewer metadata is backfilled without changing policy content. Migration treats `exception.renewal_requested` as a request event, `exception.approval_expired` as terminal expiry, and `exception.renewal_cancelled` as terminal revocation. Repeated startup therefore does not rewrite a Priority 5B request digest or append a synthetic duplicate event.

## 7. API additions

| Method | Path | Result |
| --- | --- | --- |
| `POST` | `/api/exceptions/{exception_id}/renew` | New pending exception and approval; response is the bound approval |
| `POST` | `/api/exceptions/{exception_id}/revoke` | Terminal revoked exception |
| `GET` | `/api/governance/lifecycle/status` | Last reconciliation state and counts |
| `POST` | `/api/governance/lifecycle/reconcile` | Immediate deterministic scan and resulting status |

Existing policy version, exception, approval, and notification endpoints remain additive. Frontend wire types now include reviewer attribution, renewal lineage, revocation metadata, materialized approval expiry, and lifecycle status. The professional CISO workspace that exposes these controls visually remains Stage 5D.

## 8. Operational acceptance

Before upgrading, stop all writers and verify a complete state backup. Start the candidate on a copy of production state first. Confirm that the lifecycle status has no error, existing queue totals are unchanged, policy digests remain stable, and repeated startup does not add events.

Exercise one policy revision with two CISO identities in production-mode identity testing. Exercise an exception request, independent decision, colleague renewal, independent renewal decision, early revocation, pending-approval expiry, and approved-exception expiry. Confirm Developer denial, requester separation, stale-version rejection, recipient notification isolation, mark-read isolation, and exact-boundary behavior.

The Priority 5B focused suite covers all permitted and forbidden transitions, exact expiry boundaries, retry idempotency, restart recovery, notification deduplication, cancellation of pending renewals, and role capability mapping. The release remains conditional on the complete API, frontend, native engine, dependency, migration, and source-hygiene gates.[1] [2] [3]

## 9. Deferred work

Priority 5C will commit governance projection-outbox entries with relational transitions and project native policy and exception relations into HyperMesh. Priority 5D will provide the full CISO policy, approval, exception, remediation, and executive workflow. Priority 5E will complete integrated container, production-identity, recovery, and release qualification.

## References

[1]: ../services/api/app/control_plane.py "MeshAgent durable governance store and reconciler"
[2]: ../services/api/app/main.py "MeshAgent governance APIs and service lifecycle"
[3]: ../services/api/tests/test_governance_enforcement.py "Priority 5B enforcement regression suite"
[4]: ./PRIORITY5A_GOVERNANCE_CONTRACTS.md "Priority 5A durable governance contract"
[5]: ./PRODUCTION_OPERATIONS.md "MeshAgent production operations runbook"
