# Priority 5 Project Plan: CISO Governance and Policy Lifecycle

**Baseline:** Commit `82c3eaf`
**Planning horizon:** 12 working days
**Author:** Manus AI

## 1. Delivery objective

Priority 5 turns the existing CISO screens into a governed decision system. It will add durable policy versioning, controlled exceptions, evidence-linked approvals, verified remediation, and executive reporting without weakening the Developer and Analyst boundaries completed in earlier stages.

The stages are sequential because each one creates the contract required by the next. Priority 5B must not enforce transitions against an unstable data model. Priority 5C must not project events until those transitions are final. Priority 5D must consume the stable API rather than invent client-side workflow. Priority 5E validates the integrated result.

## 2. Timeline

The following is an engineering forecast, not a production deployment commitment. It assumes one implementation stream, prompt review of stage outcomes, and no new scope. Calendar dates use the current start date of 24 September 2026 and exclude weekends.

| Stage | Working days | Forecast window | Completion checkpoint |
| --- | ---: | --- | --- |
| **5A — Durable contracts** | 2 | 24–25 September | Versioned policy and exception aggregates, immutable events, migrations, APIs, tests, documentation, release commit |
| **5B — Governance enforcement** | 3 | 28–30 September | Maker-checker separation, renewal/revocation, materialized expiry, deterministic reconciliation, notification rules |
| **5C — HyperMesh evidence integration** | 2 | 1–2 October | Transactional projection outbox, native policy/exception relations, review/case linkage, scoped retrieval |
| **5D — CISO operating experience** | 3 | 5–7 October | Policy studio, approval workspace, exception register, remediation portfolio, executive drill-down |
| **5E — Release qualification** | 2 | 8–9 October | Full regression, migration/recovery and role tests, container build, live walkthrough, commit and package |

The nominal total is **12 working days**. A two-day contingency should be reserved for identity-provider testing, container capacity, migration rehearsal, or user-requested CISO workflow changes. With contingency, the planning range is **12–14 working days**.

## 3. Stage plans

### 3.1 Stage 5A — Durable policy and exception contracts

Stage 5A establishes the database and API foundation. Policies gain immutable versions, effective intervals, content digests, aggregate version counters, and event history. Exceptions bind to an exact policy version, request digest, owner, expiry, compensating controls, and evidence identifiers. Approval records carry the same version and digest so a decision cannot be applied to substituted content.

The stage includes additive migration and deterministic backfill for existing rows. It preserves current policy and exception endpoints while adding detail and lifecycle endpoints. Completion requires focused contract tests, the complete release gate, live migration against a copy of collaborative state, and an independently reviewable design.[1]

### 3.2 Stage 5B — Governance enforcement

Stage 5B adds the controls that decide who may move each lifecycle and when. Policy authors cannot activate their own submitted version when maker-checker mode is enabled. Exception requesters cannot approve, renew, or revoke their own request. Renewal creates a new approval-bound exception version rather than rewriting the original decision.

A deterministic service-owned reconciler will materialize expired approval and exception states, append events, and create in-product notifications. This is product logic and does not require an AI task or an external scheduler. In the current single-writer deployment it will use the API service lifecycle and the existing durable store. Horizontal deployments will require one elected worker or a database-backed work claim before this process is enabled on multiple replicas.

Acceptance tests will cover every permitted and forbidden transition, clock boundaries, retry idempotency, restart recovery, notification de-duplication, and role capability mapping.

### 3.3 Stage 5C — Native HyperMesh evidence integration

Stage 5C will make policy and exception provenance available through native HyperMesh records. The relational mutation and a projection-outbox row will commit together. The reconciler will write idempotent native episodes and acknowledge only the returned ULID, following the review-evidence pattern completed in Stage 1.

A policy activation will relate the policy, exact version, control values, author, approver, effective interval, and superseded version. An exception decision will relate its exact policy digest, scope, owner, compensating controls, expiry, evidence nodes, requester, and approver. Analyst reviews and cases will reference these native identifiers rather than copied labels.

Completion requires retry and startup reconciliation tests, native-only scoped graph retrieval, digest verification, and proof that unrelated tenant or Developer evidence cannot enter a decision graph.

### 3.4 Stage 5D — CISO operating experience

Stage 5D will replace the current create-and-list governance pages with a cohesive CISO workspace. The policy view will show active, draft, in-review, superseded, and retired versions with effective dates and plain-language differences. The approval view will present the exact policy version, submitted evidence, owner, expiry, controls, and separation-of-duties state before a decision is possible.

The exception register will support active, expiring, expired, revoked, and rejected views. The remediation portfolio will connect governance decisions to owned work and verification evidence. Executive metrics will drill down to the underlying records and will label coverage gaps rather than claiming unsupported compliance.

Completion requires desktop and mobile accessibility checks, keyboard and screen-reader review of decision forms, loading and stale-state recovery, Analyst-to-CISO and CISO-to-Analyst return paths, and browser validation with both Analyst and CISO identities.

### 3.5 Stage 5E — Release qualification

Stage 5E validates the combined product. It includes the complete API, frontend, native engine, dependency, source-hygiene, migration, backup, restore, role-isolation, and public-proxy gates. It must build production containers as non-root, scan them under the existing release policy, and run an OIDC configuration smoke in a production-mode environment.

The release rehearsal will begin from a Stage 4 database backup, migrate forward, exercise policy creation through exception decision and remediation verification, restart the services, and prove the history and native evidence remain intact. The release will then be committed, published to the authorized repository, packaged with a checksum, and accompanied by a validation report and operating guide.

## 4. Dependencies and decision points

Stage 5B needs one product decision: whether all policy activation must require a second CISO or whether maker-checker is configurable by deployment. The safe default is a second identity in production and same-user activation only in local development.

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
[2]: ./ANALYST_OPERATIONS.md "Priority 4 Analyst Operations contract"
[3]: ./PRODUCTION_OPERATIONS.md "MeshAgent production operations and recovery guide"
[4]: ../RELEASE_VALIDATION_REPORT.md "MeshAgent release validation report"
