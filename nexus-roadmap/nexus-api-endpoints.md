# Nexus Coordinate — Proposed API v1

**Base path:** `/api/v1`  
**Primary style:** Versioned JSON REST commands/queries plus authenticated Server-Sent Events for operational updates.  
**Contract format:** OpenAPI 3.1, which provides a language-agnostic HTTP API description for human and machine consumers. [1]

## 1. Cross-Cutting Contract

| Concern | API rule |
|---|---|
| **Authentication** | Human routes require OIDC/OAuth2 bearer tokens. Machine connectors use a separate client identity and scoped credentials. |
| **Authorization** | Every request is evaluated against agency, role, scope, incident assignment, operational mode, and object sensitivity. |
| **Operational mode** | `X-Nexus-Mode: live|training|replay` is required for consequential writes and must match every referenced object. |
| **Idempotency** | `Idempotency-Key` is required on incident creation, decisions, commitment commands, and execution requests. Replays return the original response. |
| **Optimistic concurrency** | Mutations require `If-Match` or a request `expectedVersion`. Mismatch returns `409 Conflict` or `412 Precondition Failed`. |
| **Request tracing** | `X-Request-ID` is accepted/generated and returned. Every audit event records it. |
| **Problem responses** | Errors use `application/problem+json` with stable `type`, `title`, `status`, `code`, `detail`, and object context. |
| **Pagination** | Collection reads use opaque `cursor` and bounded `limit`; responses return `nextCursor`. |
| **Time** | RFC 3339 UTC timestamps at the API boundary; the UI renders Auburn local time. |
| **Deletion** | Operational records are not physically deleted through the public API. Authorized users may close, supersede, or redact according to policy. |

## 2. Identity, Context, and Readiness

| Method | Endpoint | Purpose | Minimum scope |
|---|---|---|---|
| `GET` | `/me` | Current user, agency, roles, scopes, active assignments, and mode access. | authenticated |
| `GET` | `/capabilities` | Allowed operations and action templates for the current context. | authenticated |
| `GET` | `/operational-events` | List authorized game days or other operational events. | `events:read` |
| `POST` | `/operational-events` | Create a planned operational event from an approved template. | `events:write` |
| `GET` | `/operational-events/{eventId}` | Read event plan, phase, command owner, participants, and version. | `events:read` |
| `PATCH` | `/operational-events/{eventId}` | Update event phase/readiness with concurrency control. | `events:write` |
| `GET` | `/operational-events/{eventId}/readiness` | Return agency roles, required checks, source health, unresolved blockers. | `readiness:read` |
| `POST` | `/operational-events/{eventId}/readiness/{checkId}/decisions` | Confirm, fail, waive, or reassign a readiness check. | `readiness:decide` |

## 3. Source Health and Evidence

| Method | Endpoint | Purpose | Minimum scope |
|---|---|---|---|
| `GET` | `/sources` | List authorized sources, owners, cadence, stale threshold, permitted use, and health. | `sources:read` |
| `GET` | `/sources/{sourceId}` | Read source configuration metadata and current health without exposing credentials. | `sources:read` |
| `GET` | `/sources/{sourceId}/health` | Current status, last success, last event, lag, error category, and affected recommendations. | `sources:read` |
| `GET` | `/operational-events/{eventId}/evidence` | Query canonical evidence by time, source, geography, incident, or quality. | `evidence:read` |
| `GET` | `/evidence/{evidenceId}` | Read normalized evidence, lineage, quality flags, and authorized raw reference. | `evidence:read` |
| `POST` | `/integrations/{connectorId}/events` | Signed inbound event endpoint for approved connector identities. | connector scope |
| `POST` | `/integrations/{connectorId}/replay` | Administrator-controlled replay into training only. | `connectors:replay` |

The inbound connector endpoint requires source signature verification, a source event ID, observed time, schema version, and replay-protection timestamp/nonce. It never accepts an arbitrary `incidentId` or approval state from the source.

## 4. Incidents and Operational State

| Method | Endpoint | Purpose | Minimum scope |
|---|---|---|---|
| `GET` | `/operational-events/{eventId}/incidents` | List incidents filtered by state, severity, agency, owner, or geography. | `incidents:read` |
| `POST` | `/operational-events/{eventId}/incidents` | Create a human-reported incident from authorized evidence or operator observation. | `incidents:write` |
| `GET` | `/incidents/{incidentId}` | Read incident brief, command owner, evidence summary, constraints, and version. | `incidents:read` |
| `PATCH` | `/incidents/{incidentId}` | Update title, severity, owner, status, or affected services with concurrency control. | `incidents:write` |
| `POST` | `/incidents/{incidentId}/assignments` | Assign or transfer command/agency ownership. | `incidents:assign` |
| `POST` | `/incidents/{incidentId}/close` | Close incident with outcome and verification evidence. | `incidents:close` |
| `GET` | `/incidents/{incidentId}/timeline` | Ordered evidence, recommendation, decision, task, execution, and audit projection. | `incidents:read` |

## 5. Agent Findings and Recommendations

| Method | Endpoint | Purpose | Minimum scope |
|---|---|---|---|
| `GET` | `/incidents/{incidentId}/findings` | Read role-filtered findings from the seven agents and exposed conflicts. | `findings:read` |
| `POST` | `/incidents/{incidentId}/recommendations:generate` | Request a recommendation against the current immutable incident snapshot. | `recommendations:generate` |
| `GET` | `/incidents/{incidentId}/recommendations` | List current and superseded recommendation versions. | `recommendations:read` |
| `GET` | `/recommendations/{recommendationId}` | Read the exact version, evidence set, expected effect, constraints, alternatives, approvers, and expiry. | `recommendations:read` |
| `POST` | `/recommendations/{recommendationId}/revision-requests` | Request revision with reason, missing evidence, or bounded change. | `recommendations:review` |
| `POST` | `/recommendations/{recommendationId}/acknowledgements` | Acknowledge receipt without approving the action. | `recommendations:acknowledge` |

Generation returns `202 Accepted` with an operation ID when analysis is asynchronous. Re-requesting against an unchanged snapshot returns the current recommendation version rather than producing duplicates.

## 6. Human Decisions and Approval State

| Method | Endpoint | Purpose | Minimum scope |
|---|---|---|---|
| `GET` | `/decision-queue` | List decisions assigned or visible to the current user, ordered by safety, expiry, and severity. | `decisions:read` |
| `GET` | `/recommendations/{recommendationId}/approval-requirements` | Read required roles, quorum, ordering, delegation, and current satisfaction state. | `decisions:read` |
| `POST` | `/recommendations/{recommendationId}/decisions` | Approve, reject, request revision, delegate, or escalate the exact recommendation version. | action-specific decision scope |
| `GET` | `/decisions/{decisionId}` | Read actor, role, authorization context, reason, timestamp, and resulting commitments. | `decisions:read` |
| `POST` | `/decisions/{decisionId}/withdrawals` | Withdraw an approval only if policy allows and execution has not crossed the configured boundary. | `decisions:withdraw` |

The decision command requires:

| Field/header | Reason |
|---|---|
| `Idempotency-Key` | Prevents duplicate approval caused by touch retry or network retry. |
| `X-Nexus-Mode` | Prevents a training decision from reaching live records. |
| `recommendationVersion` | Binds the decision to exact action text, evidence, and constraints. |
| `expectedState` | Prevents approving an expired, revised, or already decided card. |
| `evidenceSnapshotVersion` | Detects material evidence change during review. |
| `action` | `approve`, `reject`, `request_revision`, `delegate`, or `escalate`. |
| `reasonCode` and `comment` | Produces an accountable decision record. |
| `confirmationTextHash` | Proves the client confirmed the exact bounded action summary displayed. |

`approve` may create commitments; it does not invoke an agency connector. If evidence changed materially, the API returns `412` and the UI must reload the card before another decision.

## 7. Commitments and Agency Tasks

| Method | Endpoint | Purpose | Minimum scope |
|---|---|---|---|
| `GET` | `/incidents/{incidentId}/commitments` | List cross-agency commitments and current state. | `commitments:read` |
| `GET` | `/commitments/{commitmentId}` | Read owner, assignee, deadline, required outcome, blocker, and verification rule. | `commitments:read` |
| `POST` | `/commitments/{commitmentId}/acknowledgements` | Receiving agency acknowledges the request. | `commitments:acknowledge` |
| `POST` | `/commitments/{commitmentId}/assignments` | Assign or reassign to an eligible operator/team. | `commitments:assign` |
| `POST` | `/commitments/{commitmentId}/status-transitions` | Move through allowed states with expected version and evidence. | state-specific commitment scope |
| `POST` | `/commitments/{commitmentId}/blockers` | Add or resolve a blocker. | `commitments:update` |
| `POST` | `/commitments/{commitmentId}/verifications` | Verify the outcome with source/operator evidence. | `commitments:verify` |

The server owns the allowed state graph. Clients cannot write arbitrary status strings. A transition to `verified` requires the configured verification evidence.

## 8. Execution Requests and Agency Connectors

| Method | Endpoint | Purpose | Minimum scope |
|---|---|---|---|
| `GET` | `/action-templates` | List allowlisted actions available to the current agency, mode, connector, and incident type. | `execution:read` |
| `POST` | `/commitments/{commitmentId}/execution-requests:validate` | Dry-run policy, permissions, parameters, connector state, and approval references. | `execution:validate` |
| `POST` | `/commitments/{commitmentId}/execution-requests` | Create a bounded request after successful validation. Stage 1 returns a manual/deep-link handoff. | `execution:request` |
| `GET` | `/execution-requests/{executionRequestId}` | Read status, connector receipt, confirmation, timeout, and rollback reference. | `execution:read` |
| `POST` | `/execution-requests/{executionRequestId}/confirmations` | Record authoritative-system confirmation or accountable manual confirmation. | `execution:confirm` |
| `POST` | `/execution-requests/{executionRequestId}/cancellations` | Cancel if connector and action state permit it. | `execution:cancel` |
| `POST` | `/connectors/{connectorId}/disable` | Emergency connector kill switch. | `connectors:disable` |

There is intentionally no `/execute-command` endpoint. Every execution request references one approved commitment, one recommendation version, one allowlisted action template, exact validated parameters, and the required decision records.

## 9. Notifications and Communications

| Method | Endpoint | Purpose | Minimum scope |
|---|---|---|---|
| `GET` | `/incidents/{incidentId}/message-drafts` | Read source-backed communication drafts. | `messages:read` |
| `POST` | `/incidents/{incidentId}/message-drafts` | Create a draft from approved facts/templates. | `messages:write` |
| `POST` | `/message-drafts/{messageId}/decisions` | Approve, reject, or revise the exact message version. | `messages:approve` |
| `POST` | `/message-drafts/{messageId}/publications` | Publish through an authorized channel connector after approval. | `messages:publish` |
| `GET` | `/message-drafts/{messageId}/publications/{publicationId}` | Read channel receipt and status. | `messages:read` |

## 10. Realtime Events

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/stream?operationalEventId={id}` | Authenticated SSE stream for the wall display and operator clients. |
| `GET` | `/incidents/{incidentId}/stream` | Incident-scoped SSE stream. |

Clients resume with `Last-Event-ID`. The server rechecks authorization on connect and when role/assignment changes. Event types include:

| Event type | Payload summary |
|---|---|
| `source.health.changed` | Source ID, old/new health, freshness, affected objects |
| `evidence.created` | Evidence metadata and authorized summary |
| `incident.created|updated|closed` | Incident ID, version, changed fields |
| `recommendation.created|revised|expired|superseded` | Recommendation ID/version/state and decision owner |
| `decision.recorded` | Decision ID, action, actor role, resulting state |
| `commitment.created|updated|verified` | Commitment ID, owner, state, deadline |
| `execution.updated` | Execution request state and receipt summary |
| `audit.appended` | Redacted audit metadata for authorized operational views |

SSE payloads contain identifiers and display-safe summaries. Clients retrieve complete current resources through REST after an event; stream events are not authoritative state replacements.

## 11. Audit, Reports, and System Health

| Method | Endpoint | Purpose | Minimum scope |
|---|---|---|---|
| `GET` | `/incidents/{incidentId}/audit` | Read the role-filtered immutable audit timeline. | `audit:read` |
| `GET` | `/operational-events/{eventId}/after-action` | Read the after-action projection and metric definitions. | `reports:read` |
| `POST` | `/operational-events/{eventId}/after-action/annotations` | Add a review annotation without changing the historical record. | `reports:annotate` |
| `POST` | `/operational-events/{eventId}/after-action/exports` | Generate an authorized report artifact asynchronously. | `reports:export` |
| `GET` | `/operations/{operationId}` | Read asynchronous operation status. | operation owner or relevant scope |
| `GET` | `/health/live` | Process liveness, with no dependency details. | public/internal policy |
| `GET` | `/health/ready` | Dependency readiness for orchestration/load balancer. | internal |
| `GET` | `/system/status` | Authorized operational status for UI source-health rail. | `system:read` |

## 12. Problem Codes

| HTTP status | Stable code | Meaning |
|---|---|---|
| `400` | `invalid_request` | Schema or parameter error |
| `401` | `unauthenticated` | Missing/invalid identity |
| `403` | `not_authorized` | Role/scope/assignment/mode does not permit operation |
| `404` | `not_found` | Object not visible or does not exist |
| `409` | `version_conflict` | Object changed since the client snapshot |
| `409` | `state_transition_not_allowed` | Command is invalid from current state |
| `412` | `evidence_changed` | Material evidence or recommendation version changed during review |
| `422` | `policy_not_satisfied` | Required evidence, approver, constraint, or parameter rule failed |
| `423` | `mode_locked` | Training/replay object attempted a live-only action |
| `429` | `rate_limited` | Client/connector exceeded policy |
| `503` | `connector_unavailable` | Required external connector unavailable; no execution claim is made |

## References

[1] [OpenAPI Initiative, “OpenAPI Specification v3.1.0.”](https://spec.openapis.org/oas/v3.1.0.html)
