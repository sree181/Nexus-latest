# Priority 5D: CISO Operating Workspace

**Status:** Implemented and validated on 24 September 2026
**Baseline:** Priority 5C commit `56886fa`

## 1. Outcome

Priority 5D turns the prior create-and-list screens into one connected CISO operating flow. The interface remains a governance control plane rather than a policy code editor. It leads from executive attention, to the exact policy or exception record, to a separated decision, to owned remediation, to an evidence-qualified report.

The API remains the authority for capabilities, maker-checker separation, requester separation, lifecycle transitions, optimistic concurrency, evidence requirements, and native graph scope. Route guards and disabled buttons provide usability, not security.

## 2. Information architecture

| Workspace | Purpose | Primary next action |
| --- | --- | --- |
| **Command center** | Show waiting decisions, overdue security cases, expiring exceptions, open fixes, measured control-plane health, and evidence coverage | Open the source queue or governed record |
| **Policy control** | Create a policy, review every immutable version, compare proposed controls with the active version, and inspect native policy history | Submit, activate, withdraw, or create a new version |
| **Decision desk** | Present the exact policy version, request digest, owner, expiry, compensating controls, evidence identifiers, and requester before a decision | Approve or reject with a required rationale |
| **Exception record** | Show the full decision, policy binding, lifecycle, lineage, controls, submitted evidence, and native HyperMesh timeline | Decide, renew, revoke, or follow evidence |
| **Remediation portfolio** | Connect an Analyst case to accountable work, a target revision, due date, terminal outcome, and verification evidence | Start work or record a verified, failed, or exception-covered outcome |
| **Assurance reports** | Generate live-data-only manifests with explicit coverage and SHA-256 digests | Inspect and retain the report manifest |

The CISO navigation uses these operating concepts directly: **Command center**, **Policy control**, **Decision desk**, **Remediation portfolio**, and **Assurance reports**. Cross-role return paths remain available as **Security work**, **Fleet evidence**, and **Action history**.

## 3. Policy and exception decisions

Policy detail shows all active, draft, in-review, superseded, withdrawn, and retired versions. Each version states who created, submitted, activated, or withdrew it; the effective interval; the content digest; and a plain-language difference from the active controls. Production activation still requires another CISO identity.

The decision desk joins approval, exception, and policy resources in the browser only after each resource has passed its own server-side capability check. It does not infer a policy or substitute current policy content. The page shows the exact stored policy digest and immutable request digest that the API will decide.

A pending exception directs the CISO to the decision desk. An approved exception can be renewed or revoked by an independently authorized identity. Expired, rejected, revoked, and superseded exceptions remain visible but cannot be reopened.

## 4. Native HyperMesh evidence

Policy and exception detail pages call their capability-protected Priority 5C evidence endpoints. The evidence panel reports acknowledged projection count, entity count, native relation count, ULIDs, payload SHA-256 values, and a chronological relationship list. It never constructs a relationship from frontend fields.

Policy evidence is strictly policy-only. Exception evidence may follow its recorded policy ancestry. A browser QA finding showed that same-second legacy migration order could place an older exception episode in a later policy episode's native parent set. Priority 5D closes that condition in the gateway and adds a reverse-order regression; policy graphs now filter both roots and ancestry to policy relation kinds.

## 5. Remediation lifecycle

Priority 5D adds one additive table, `remediation_events`, and two routes:

- `GET /api/remediations/{remediation_id}`
- `POST /api/remediations/{remediation_id}/transition`

The durable states are `accepted`, `in_progress`, `verified_remediated`, `failed`, and `exception_covered`; `overdue` is calculated from a nonterminal row and its due time. Every mutation appends an immutable event with actor, display name, source and target state, rationale, evidence identifiers, timestamp, and correlation ID.

Commands require the aggregate version. Stale commands return HTTP `412`. Terminal outcomes cannot be reopened. `verified_remediated` and `exception_covered` require at least one evidence identifier. The CISO UI preserves and displays the complete event history.

## 6. Data and coverage honesty

The command center distinguishes deployment health from business posture. It shows whether durable storage, HyperMesh, the audit chain, identity verification, and lifecycle reconciliation are actually available. It does not combine them into an invented score.

Fleet evidence keeps reachable, unknown, and unscanned counts separate. The local demo identity is visibly labeled as unverified. Reports continue to exclude seeded and sample runs and fail instead of producing an empty live-evidence report.

## 7. Accessibility and responsive behavior

All primary navigation and actions are native links, buttons, selects, form fields, or disclosure elements with focus treatment and labels. Decision and lifecycle forms require a rationale. Destructive or terminal choices use distinct styling and a second form step.

Desktop browser walkthrough covered Command center, Policy control, Policy detail, Decision desk, Exception detail, and Remediation portfolio. The browser console showed no application runtime errors. A 390 × 844 Chromium check confirmed both Command center and Policy detail retain a 390-pixel body width with no horizontal page overflow; the compact sidebar remains independently scrollable.

## 8. Validation evidence

The focused CISO/governance suite passes **18 tests**. The complete API suite passes **520 tests with 1 skipped**. Frontend type checking, deterministic lint, **8 test files / 25 tests**, and the production Vite build pass. Live migration against a copy of the collaborative EngineGateway state created a remediation, blocked a terminal outcome without evidence, moved work to `in_progress`, verified it with case/run evidence, denied Analyst access, and preserved three immutable events.

The final Stage 5E gate repeats the full repository validation and adds production-container qualification, recovery rehearsal, end-to-end connected-agent demonstration, package creation, and release publication.

## References

[1]: ./PRIORITY5C_NATIVE_GOVERNANCE_EVIDENCE.md "Priority 5C native governance evidence"
[2]: ./PRIORITY5B_GOVERNANCE_ENFORCEMENT.md "Priority 5B lifecycle enforcement"
[3]: ./PRODUCTION_OPERATIONS.md "Production operations runbook"
[4]: ../services/api/tests/test_ciso_workspace.py "CISO remediation regression suite"
