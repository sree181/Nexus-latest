# Analyst Operations and Collaboration

**Status:** Implemented for the Priority 4 release candidate
**Audience:** Security analysts, CISO operators, API integrators, and deployment owners

## 1. Product boundary

Analyst Operations is the Security-office workflow that begins after a Developer asks for help or a finding becomes a tracked case. It is not an editor, a second vulnerability scanner, or a replacement for the native HyperMesh evidence layer. The relational control plane owns mutable work state such as assignment, due time, notes, and notification read status. HyperMesh remains authoritative for the sealed evidence and provenance used to understand and decide a Developer review.

Only authenticated principals with the Analyst operations capability can use the work queue, shared history, saved views, bulk triage, or team-note APIs. In the shipped role model, Analysts and CISOs have this capability; Developers do not. Developer review reads remain owner scoped and do not expose another Developer's session or review.

## 2. Analyst workflow

The `/analyst/queue` workspace combines Developer review requests and investigation cases in one ranked queue. Priority accounts for severity, state, linked code, age, ownership, and due time. Operators can search by package, project, developer, owner, or identifier; filter by work type, severity, project, or owner; switch quickly to their own or unassigned work; and save personal filter views.

Analysts can select multiple queue items and assign one owner and due time in a single action. Each item retains optimistic-concurrency protection. A bulk request does not roll back successful items when another item is stale or missing; the receipt identifies every success and failure so the operator can refresh only the rejected rows.

A review detail page exposes the submitted advisory facts and its role-scoped native HyperMesh evidence graph. Selecting a package, advisory, code item, contributor, or decision node supplies precise context for a new investigation case. Escalation creates the case and the review transition atomically, links the originating review and policy evaluation, appends an immutable review event, and queues the review episode for native HyperMesh projection.

Team notes are durable and remain attached to either a review or case. Optional email-address mentions produce recipient-specific notifications. The shared Work History screen searches review events, case events, and notes without merging or rewriting their source records. Notification read state is per person; one operator cannot mark another operator's notification as read.

## 3. API contract

| Method | Path | Purpose | Required authority |
| --- | --- | --- | --- |
| `GET` | `/api/operations/work` | Unified ranked and filtered review/case queue | Analyst operations |
| `POST` | `/api/operations/work/bulk-assign` | Assign up to 100 reviews/cases with item-level optimistic concurrency | Analyst operations |
| `GET` | `/api/operations/activity` | Search review, case, and team-note history | Analyst operations |
| `GET` | `/api/operations/{kind}/{id}/comments` | Read durable notes for an existing review or case | Analyst operations |
| `POST` | `/api/operations/{kind}/{id}/comments` | Add a note and optional mentions | Analyst operations |
| `GET` | `/api/operations/views` | List the caller's saved queue views | Analyst operations |
| `POST` | `/api/operations/views` | Create or replace a named personal view | Analyst operations |
| `DELETE` | `/api/operations/views/{view_id}` | Delete a view owned by the caller | Analyst operations |
| `POST` | `/api/reviews/{request_id}/assign` | Assign one review and optional due time | Review write |
| `POST` | `/api/reviews/{request_id}/escalate` | Atomically create a case from a review | Case write |
| `GET` | `/api/notifications` | List notifications visible to the caller | Authenticated caller |
| `POST` | `/api/notifications/{notification_id}/read` | Mark one visible notification read for the caller | Authenticated caller |

Queue filters are `kind`, `state`, `assignee`, `repository`, `severity`, `q`, and `limit`. The reserved assignee value `__unassigned__` selects work without an owner. Assignment mutations require the current review `version_counter` or case `version`. Stale versions return `412`; missing resources return `404`; unauthorized Developer access to operations routes returns `403`.

## 4. Persistence and event behavior

Priority 4 adds assignment, display-name, due-time, and escalated-case fields to existing review records through upgrade-safe additive initialization. `work_comments`, `saved_work_views`, `work_notifications`, and `work_notification_reads` are durable tables in `control-plane.sqlite3`. This file must be backed up, restored, and migrated with the rest of `MESHAGENT_DB_DIR`.

Review assignment, review-to-case escalation, decisions, automatic verification, case assignment, case due-time changes, and mentions schedule contextual notifications. Assignment and escalation also append immutable review events. Review event projection uses the existing transactional outbox: the relational transaction commits before a reconciler acknowledges the corresponding native HyperMesh ULID.

The current v1 queue implementation intentionally reads at most 100 recent cases and 500 recent review requests before applying combined filters and the response limit. The response limit is capped at 500. This is a bounded single-tenant implementation, not cursor-based enterprise pagination. Deployments approaching those working-set limits should add server-side keyset pagination and database-level combined filtering before asserting complete inventory coverage.

## 5. Operational checks

After an upgrade, verify that `/api/health` reports `EngineGateway` and durable state, then sign in as an Analyst and a CISO. Confirm both can open `/analyst/queue`, while a Developer receives `403` from `/api/operations/work`. Assign a nonterminal test review with its current version, set a future due time, add a note, save a personal view, and confirm the queue filter, notification, and Work History search all return the new records. Open the review, select a native evidence node, and verify the case rationale reflects that exact node without creating a case unless the test plan requires one.

For bulk triage, include one current item and one deliberately stale or missing item in a non-production test. The receipt should report one success and one failure, and the successful assignment must remain committed. Confirm saved views and notification read state are isolated by person. On a narrow viewport, the semantic queue table must remain inside its horizontal scroll container rather than widening the page.

Monitor failed `review_projection_outbox` rows, repeated assignment conflicts, overdue work, notification-delivery growth, control-plane database size, and queue cardinality. The current notification center is in-product workflow state; it does not send external email, page on-call personnel, or guarantee delivery outside MeshAgent.

## 6. Validation boundary

Priority 4 is covered by the complete API suite, frontend typecheck/lint/Vitest/production build, native HyperMesh suites, live EngineGateway migration and workflow smoke tests, desktop Analyst/CISO browser checks, and a 390 × 844 responsive queue inspection. Container rebuilding was skipped for this release candidate in the constrained sandbox; the deployment pipeline must still build, scan, and smoke the promoted images before go-live.

The implementation does not claim multi-tenant partitioning, distributed work queues, external notification delivery, cursor-based queue pagination, or active-active API writers. Those remain later platform capabilities or deployment-owned integrations.
