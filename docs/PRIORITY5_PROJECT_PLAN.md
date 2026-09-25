# Priority 5 Project Plan: CISO Governance and Policy Lifecycle

**Baseline:** Commit `82c3eaf`
**Planning horizon:** 12 working days
**Author:** Manus AI

**Progress:** Stages 5A through 5D completed on 24 September 2026; Stage 5E release qualification remains in ordered delivery.

## 1. Delivery objective

Priority 5 turns the existing CISO screens into a governed decision system. It will add durable policy versioning, controlled exceptions, evidence-linked approvals, verified remediation, and executive reporting without weakening the Developer and Analyst boundaries completed in earlier stages.

The stages are sequential because each one creates the contract required by the next. Priority 5B must not enforce transitions against an unstable data model. Priority 5C must not project events until those transitions are final. Priority 5D must consume the stable API rather than invent client-side workflow. Priority 5E validates the integrated result.

## 2. Timeline

The following is an engineering forecast, not a production deployment commitment. It assumes one implementation stream, prompt review of stage outcomes, and no new scope. Calendar dates use the current start date of 24 September 2026 and exclude weekends.

| Stage | Working days | Forecast window | Status | Completion checkpoint |
| --- | ---: | --- | --- | --- |
| **5A — Durable contracts** | 2 | 24–25 September | **Completed 24 September** | Versioned policy and exception aggregates, immutable events, migrations, APIs, tests, documentation, release commit |
| **5B — Governance enforcement** | 3 | 28–30 September | **Completed 24 September** | Maker-checker separation, renewal/revocation, materialized expiry, deterministic reconciliation, notification rules |
| **5C — HyperMesh evidence integration** | 2 | 1–2 October | **Completed 24 September** | Transactional projection outbox, native policy/exception relations, review/case linkage, scoped retrieval |
| **5D — CISO operating experience** | 3 | 5–7 October | **Completed 24 September** | Policy studio, approval workspace, exception register, remediation portfolio, executive drill-down |
| **5E — Release qualification** | 2 | 8–9 October | Planned | Full regression, migration/recovery and role tests, container build, live walkthrough, commit and package |

The nominal total is **12 working days**. A two-day contingency should be reserved for identity-provider testing, container capacity, migration rehearsal, or user-requested CISO workflow changes. With contingency, the planning range is **12–14 working days**.

## 3. Stage plans

### 3.1 Stage 5A — Durable policy and exception contracts

Stage 5A establishes the database and API foundation. Policies gain immutable versions, effective intervals, content digests, aggregate version counters, and event history. Exceptions bind to an exact policy version, request digest, owner, expiry, compensating controls, and evidence identifiers. Approval records carry the same version and digest so a decision cannot be applied to substituted content.

The stage includes additive migration and deterministic backfill for existing rows. It preserves current policy and exception endpoints while adding detail and lifecycle endpoints. Completion requires focused contract tests, the complete release gate, live migration against a copy of collaborative state, and an independently reviewable design.[1]

### 3.2 Stage 5B — Governance enforcement

Stage 5B added the controls that decide who may move each lifecycle and when. Policy authors cannot activate their own submitted version in production. Exception requesters cannot approve, renew, or revoke their own request. Renewal creates a new approval-bound exception record rather than rewriting the original decision.

A deterministic service-owned reconciler now materializes expired approval and exception states, appends events, and creates in-product notifications. This is product logic and does not require an AI task or an external scheduler. In the current single-writer deployment it uses the API service lifecycle and the existing durable store. Horizontal deployments will require one elected worker or a database-backed work claim before this process is enabled on multiple replicas.

Acceptance tests cover every permitted and forbidden transition, exact clock boundaries, retry idempotency, restart recovery, notification de-duplication, and role capability mapping. The detailed shipped contract is documented separately.[2]

### 3.3 Stage 5C — Native HyperMesh evidence integration

Stage 5C makes policy and exception provenance available through native HyperMesh records. The relational mutation and a projection-outbox row commit together. The reconciler writes idempotent native episodes and acknowledges only the returned ULID, following the review-evidence pattern completed in Stage 1.

A policy activation relates the policy, exact version, control values, author, approver, effective interval, and superseded version. An exception decision relates its exact policy digest, scope, owner, compensating controls, expiry, evidence nodes, requester, and approver. Analyst reviews and cases reference stored native boundaries rather than copied labels.

Completion included retry and startup reconciliation tests, native-only scoped graph retrieval, digest verification, Developer access denial, and restart proof that native ULIDs and scoped relation counts remain stable.[6]

### 3.4 Stage 5D — CISO operating experience

Stage 5D replaced the create-and-list governance pages with a cohesive CISO workspace. The policy view shows active, draft, in-review, superseded, withdrawn, and retired versions with effective dates and plain-language differences. The decision view presents the exact policy version, submitted evidence, owner, expiry, controls, and separation-of-duties state before a decision is possible.

The exception register supports pending, active, expiring, expired, revoked, rejected, and superseded views. The remediation portfolio connects cases to owned work, due dates, target revisions, terminal outcomes, verification evidence, and immutable history. Executive metrics drill down to the underlying records and label coverage gaps rather than claiming unsupported compliance.

Completion included desktop browser validation across all CISO workspaces, 390 × 844 responsive checks with no horizontal page overflow, semantic native controls, loading/error paths, API stale-state rejection, Analyst/CISO return paths, and a clean application console. The detailed shipped design is documented separately.[7]

### 3.5 Stage 5E — Release qualification

Stage 5E validates the combined product. It includes the complete API, frontend, native engine, dependency, source-hygiene, migration, backup, restore, role-isolation, and public-proxy gates. It must build production containers as non-root, scan them under the existing release policy, and run an OIDC configuration smoke in a production-mode environment.

The release rehearsal will begin from a Stage 4 database backup, migrate forward, exercise policy creation through exception decision and remediation verification, restart the services, and prove the history and native evidence remain intact. The release will then be committed, published to the authorized repository, packaged with a checksum, and accompanied by a validation report and operating guide.

## 4. Dependencies and decision points

The Stage 5B maker-checker decision is resolved: production requires a second CISO identity, while local development permits same-user activation for one-person evaluation. This is server-enforced and not a client preference.

Stage 5C depends on a stable native relation vocabulary. The recommended relation kinds are `policy_version`, `policy_activation`, `policy_supersession`, `exception_request`, `exception_decision`, `exception_expiry`, and `exception_revocation`. These names should be finalized before persistence because native relation kinds become long-lived evidence contracts.

Stage 5D needs approval of the CISO information architecture after the backend flow is stable. It should remain a governance workspace, not a policy code editor. Complex rule authoring or a general-purpose expression language is outside Priority 5.

Stage 5E depends on adequate Docker inode and disk capacity. If the current sandbox remains constrained, container qualification must run in CI or another clean build host; source-only validation cannot substitute for the production-image gate.

## 5. Risks and controls

| Risk | Control |
| --- | --- |
| Existing policy or exception data cannot migrate cleanly | Additive schema changes, deterministic idempotent backfill, verified pre-change backup, and migration fixture |
| A decision applies to changed content | Exact policy-version binding, policy digest, exception request digest, and approval resource version |
| Concurrent operators overwrite decisions | Aggregate optimistic-concurrency counters and HTTP `412` on stale commands |
| One person requests and approves a sensitive change | Server-side separation of duties in 5B; client controls remain secondary |
| Expiry depends on someone opening a page | Service-owned deterministic reconciler with restart recovery and de-duplicated events in 5B |
| Evidence graph becomes decorative or synthetic | Transactional outbox and native HyperMesh ULID acknowledgement in 5C |
| Executive metrics overstate coverage | Every metric drills to source records and labels unknown or excluded evidence |
| Queue or event volume outgrows SQLite | Maintain the single-writer boundary for v1; move to PostgreSQL and claimed background work before horizontal scale |

## 6. Stage governance

Each stage ends with a written completion confirmation that includes scope, commit, test totals, live validation, known limitations, and downloadable artifacts. The next stage starts only after that checkpoint, preserving the agreed order.

A change that alters role authority, evidence visibility, lifecycle states, or production persistence is a scope decision and must be resolved before implementation continues. Styling refinements, field labels, and other reversible interface choices do not block backend work.

## References

[1]: ./PRIORITY5A_GOVERNANCE_CONTRACTS.md "Priority 5A durable policy and exception technical design"
[2]: ./PRIORITY5B_GOVERNANCE_ENFORCEMENT.md "Priority 5B governance enforcement and lifecycle operations"
[3]: ./ANALYST_OPERATIONS.md "Priority 4 Analyst Operations contract"
[4]: ./PRODUCTION_OPERATIONS.md "MeshAgent production operations and recovery guide"
[5]: ../RELEASE_VALIDATION_REPORT.md "MeshAgent release validation report"
[6]: ./PRIORITY5C_NATIVE_GOVERNANCE_EVIDENCE.md "Priority 5C native HyperMesh governance evidence design"
[7]: ./PRIORITY5D_CISO_OPERATING_WORKSPACE.md "Priority 5D CISO operating workspace"
