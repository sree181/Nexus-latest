# MeshAgent Production Login-to-Outcome Gap Analysis and Implementation Specification

**Status:** Implemented production-v1 role journeys with residual post-v1 and deployment gates  
**Evidence baseline:** initial repository revision `10dd967`; implementation branch `manus/role-journeys-v1`  
**Audience:** product, security, frontend, API, platform, and test engineering teams  
**Author:** Manus AI

## Executive decision

MeshAgent should be delivered as a **production evidence and governance product**, not as a relabelled version of the existing analyst console. The current implementation has an effective OIDC validation boundary, developer run isolation, an engine-backed evidence path, and constrained recorder tokens. It does **not** yet have a CISO role, a durable case/remediation workflow, production-grade session lifecycle, reliable fleet-data provenance, independent audit anchoring, or the control plane required for high-impact approvals and compliance outcomes. The requested target therefore requires additive product and storage work; changing UI labels or mapping the existing `analyst` claim to `ciso` is unsafe.

The target has **two governance roles: Analyst and CISO**. The current `developer` identity remains a necessary baseline human access tier because the product already supports developer-owned runs, self-service devices, and non-enumerating ownership checks. It is **not a third governance role**. The closed human identity vocabulary is consequently `developer | analyst | ciso`; only Analyst and CISO receive governance capabilities. This preserves current developer safety guarantees while resolving the requested Analyst/CISO distinction.

> **Production invariant:** No screen, receipt, report, finding, recommendation, or audit record may represent seeded, sample, reference-build, unverified, or unattested input as live fleet evidence. A production deployment starts with an empty live fleet unless authenticated ingestion has created evidence.

The implementation should proceed in the order below. A release must not claim the CISO outcome before Priority 0 and Priority 1 are complete.

## Implemented outcome on this branch

The branch now delivers a cohesive login-to-outcome product path rather than a relabelled analyst console. The API owns OIDC discovery, PKCE transaction state, code exchange, and opaque browser sessions; access tokens are not stored in browser JavaScript storage. Sessions use HttpOnly, SameSite cookies, server-side expiry/revocation, exact-origin checks on cookie-authenticated mutations and WebSockets, and terminal `4401`/`4403` handling. Verified Bearer tokens remain supported for documented non-browser API clients.

The closed identity model is implemented as **Developer, Analyst, and CISO** with fixed server-side capabilities, denial of overlapping privileged group claims, and independent API enforcement. The frontend resolves `/api/me` before selecting the role landing page and uses capability guards as a usability layer rather than an authority boundary.

| Product path | Implemented production-v1 behavior |
| --- | --- |
| **Sign-in and landing** | Enterprise sign-in surface; API-owned Authorization Code + PKCE; opaque durable browser session; safe internal return path; server-authoritative role landing; session logout and expiry handling. |
| **Analyst** | Priority queue from eligible findings; case creation; assignment and SLA; investigation workspace; immutable case events; controlled transitions; source-origin labels; time-bounded policy-exception request linked to the case. |
| **CISO** | Coverage-qualified overview; versioned policy register; exception and approval review; rationale-required decisions; requester/approver separation; remediation ownership and due dates; live-source report manifests and digests. |
| **Shared evidence** | Existing run, graph, provenance, supply-chain, security, audit, device, and deletion surfaces remain available according to capabilities. Sample/reference evidence is labelled and cannot be presented as a live governance report. |
| **Durability and audit** | Cases, policies, exceptions, approvals, remediations, reports, posture snapshots, OIDC transactions, and browser sessions persist in SQLite under `MESHAGENT_DB_DIR`; workflow mutations emit strict audit records. |
| **Release quality** | Role/API tests, CSRF and session tests, terminal WebSocket tests, frontend capability/mutation tests, full TypeScript/lint/build gates, complete API suite, native engine tests, locked dependency audits, and container validation are release gates. |

The supported boundary remains a **single-tenant, customer-operated, single-writer v1**. The following are deliberately not claimed by this release: active-active or horizontally scaled control-plane writes, managed multi-tenant SaaS isolation, external audit notarization, scheduled report delivery, signed PDF evidence packs, refresh-token rotation, continuous IdP deprovision events, or deletion from backups and third-party copies. Real issuer configuration, TLS ingress, identity-group acceptance, monitoring, backup/restore, and promoted image digests remain deployment-owned go-live requirements.

| Priority | Release gate and outcome | Why it comes first |
| --- | --- | --- |
| **P0 — production trust boundary** | Closed Analyst/CISO entitlement mapping; API-audience OIDC; BFF session lifecycle; safe callback/logout; live-versus-demo provenance; inter-process durability; signed audit checkpoints; bounded ingestion. | Without these controls, a privileged UI, a persistent session, fleet evidence, and audit conclusions are not trustworthy. |
| **P1 — Analyst investigation to accountable case** | Durable findings, evidence, triage queue, case lifecycle, recommendation drafting, clear run/fleet navigation, and case-level audit. | This creates the first complete, auditable Analyst outcome rather than a collection of fleet and run views. |
| **P2 — CISO governance to executive outcome** | Policies, exceptions, approvals, remediation ownership/SLA, immutable exports, trends, and executive evidence packs. | These are the CISO-specific controls that turn investigation into risk acceptance, verified remediation, and executive reporting. |
| **P3 — scale and quality hardening** | Cursor pagination, API-client generation, observability/redaction, comprehensive browser tests, accessibility, and controlled background report delivery. | These make the product supportable at scale after the authority, evidence, and workflow semantics are correct. |

## Baseline gaps that drove this implementation

The current API verifies asymmetric OIDC JWTs, issuer, audience, expiry, and subject when an issuer is configured. It maps a configured analyst group to the only privileged role, `analyst`, and maps every other verified user to `developer`. The runtime type is explicitly `Literal["developer", "analyst"]`; there is no executable CISO entitlement, CISO capability, CISO route, or CISO UI type. In production, local asserted headers are rejected. Human routes reject device tokens, while recorder and package-gate routes retain their deliberately narrow device scope. [1] [2]

The current web client uses Authorization Code with S256 PKCE and stores an access token in `sessionStorage`. It validates callback state for successful code callbacks, but stores no token expiry/session state, has no refresh or reauthentication behavior, and treats a stored token as signed in. It also makes the default `/` route the developer-oriented task creation screen. The API properly rejects expired tokens, but the browser does not clear identity/query state and return safely to sign-in after a 401. [3] [4]

Engine mode has durable governed-memory operations and recovery support, but ordinary API startup defaults to `SampleGateway` unless `MESHAGENT_ENGINE=1`. The engine currently seeds a synthetic fleet and reference run. Sample responses and several fleet/structure contracts do not consistently expose a machine-readable sample or data-origin field. The existing JSONL audit chain detects many middle-file changes but is unsigned, can be rewritten wholesale, and currently skips malformed lines during read/verification. [5] [6] [7]

The existing analyst experience is a governed-run/fleet console. It includes fleet snapshot/query/recommendations, action-log, run evidence, SARIF ingestion, SBOM views, and a destructive forget workflow. It has no case model, triage queue, assignment, resolution state, recommendation authoring/approval, remediation verification, policy register, exception register, report export, or executive trend model. These must be built; they must not be inferred from the current endpoints.

## Decisions that resolve conflicting audit recommendations

The audits use `analyst`, `security office`, and `ciso` inconsistently because the code currently implements only `analyst`. This specification resolves the conflicts as follows.

| Decision | Target rule | Rationale and rejected alternative |
| --- | --- | --- |
| **Role vocabulary** | `developer`, `analyst`, and `ciso` are the only valid human primary roles. Analyst and CISO are the two governance roles. | Removing `developer` would break the existing owner-isolated workflow. Treating a display label such as “Security office” as an entitlement is rejected. |
| **Overlapping governance groups** | Membership in both configured Analyst and CISO groups is an **authentication/entitlement error**. The API returns 401 with `ambiguous_role_claim`; it never silently upgrades to CISO or Analyst. | A precedence rule can hide an IdP administration error and accidentally grant power. |
| **No governance group** | A valid signed token with no governance membership is `developer`; malformed membership types, untrusted issuer/audience/signature, missing subject, or ambiguous governance membership are 401. | This preserves ordinary developer use while denying malformed privilege claims. |
| **CISO authority** | CISO has broader governance access but does not bypass separation-of-duties rules. Critical action requires a separate approved request, and the requester cannot approve their own request. | “CISO may do everything alone” conflicts with the audit’s approval and evidence requirements. |
| **Recommendation application** | A non-cut recommendation is called **Record plan** until external remediation is verified. A cut/deletion action remains a separate destructive workflow. Neither closes a case by itself. | The existing “Apply to N agents” wording materially overclaims what the API changes. |
| **Sample/reference mode** | Demo/sample mode is operationally isolated and cannot mutate live cases, create production approvals, issue production certificates, or generate compliance exports. Every response and receipt carries its origin. | A banner alone is inadequate when static data can be mistaken for fleet evidence. |
| **Structure page scope** | `/structure` becomes a selected-run structure screen, or `/fleet/structure` exclusively uses fleet decomposition. This specification adopts both explicit routes. | The present Fleet-labelled route actually reads a latest run. |
| **SARIF negative evidence** | A SARIF upload may add an evidence artifact and coverage observation, but may set a finding to `not_reachable` only when trusted successful scanner/rule/revision/location coverage proves the negative result. | Merely naming a module must not clear reachable risk. |
| **Audit assurance** | A local hash chain is retained for fast verification, but an external signed/witnessed checkpoint is required for an integrity claim. Until then the UI says “local chain only,” not “untruncated.” | The current chain cannot detect valid final-record rollback or whole-file rewriting. |

## Target information architecture

### Product shell and route model

The application has one same-origin browser shell with a server-mediated OIDC session. The shell does not decide authorization from decoded token claims. It requests `/api/me`, receives authoritative capabilities, and uses them for navigation and user experience. The API independently enforces the same capabilities on every REST and WebSocket endpoint.

| Area | Route(s) | Intended users | Purpose and critical behavior |
| --- | --- | --- | --- |
| Authentication | `/sign-in`, `/auth/callback`, `/signed-out` | Unauthenticated human | Starts/finishes the BFF OIDC flow, validates state for code **and error** callbacks, announces recoverable errors, and never renders raw provider text. |
| Role landing | `/` | All human users | Resolves authoritative identity before selecting a landing: Developer `/work`; Analyst `/triage`; CISO `/governance/overview`. A valid authorized deep link wins over the default. |
| Developer work | `/work`, `/runs/new`, `/runs/:runId/*` | Developer; Analyst/CISO where capability allows | Creates and investigates owned runs. Run pages use a consistent 401/403/404 boundary; another developer’s run remains 404. |
| Analyst triage | `/triage`, `/cases`, `/cases/:caseId` | Analyst, CISO | Shows a priority queue and a durable case detail view containing finding, provenance, code location, scans, recommendations, owner, SLA, and immutable case history. |
| Fleet investigation | `/fleet/overview`, `/fleet/query`, `/fleet/structure` | Analyst, CISO | Shows live, source-labelled posture and drill-downs only. Every tile/node/hit includes stable IDs that link to cases or findings. |
| Evidence investigation | `/runs/:runId/code`, `/provenance`, `/security`, `/supply`, `/rewind`, `/structure` | Run owner; Analysts/CISOs under capability rules | Displays selected run ID, owner, origin, source timestamp, evidence identifiers, and redaction state. `/runs/:runId/structure` is explicitly run-scoped. |
| Remediation | `/recommendations`, `/recommendations/:id`, `/remediation/:id` | Analyst, CISO | Lets Analysts draft evidence-linked recommendations and lets authorized CISO workflows approve/execute/verify them. |
| CISO governance | `/governance/overview`, `/policies`, `/exceptions`, `/approvals`, `/remediation`, `/reports` | CISO; Analysts get read-only views only where shown in the permission matrix | Controls policies and exceptions; reviews approval work; manages remediation posture, trends, and evidence packs. |
| Assurance | `/audit`, `/evidence/:evidenceId`, `/operations/:operationId`, `/certificates/:certificateId` | Analyst read where permitted; CISO broader export/control access | Provides authoritative, durable evidence and receipt deep links rather than transient mutation toast content. |
| Devices and setup | `/devices`, `/setup` | Human users according to capability | Keeps self-device actions for developers and fleet device administration for CISO. Setup is informative and never asserts production readiness from a mere HTTP success. |

The navigation must render only capabilities that the current identity has, but it must retain a route-level error boundary. A failed `/api/me` query is an authentication/service error with retry, not a “not an Analyst” message. The navigation must include a skip link, route-specific document title, focus movement to the page heading, keyboard reachable destructive confirmations, a text alternative for graph content, and responsive table overflow behavior.

### Landing and deep-link algorithm

1. Preserve only an internal, normalized `pathname + search + hash` that passes a server-shared allowlist and length limit. Reject protocol-relative, external, malformed, expired, and unauthorized targets.
2. On callback success, establish a session and fetch `/api/me` before navigating.
3. If the preserved target is authorized under the authoritative capability set, navigate to it. Do not prefetch protected data before this check.
4. Otherwise choose `/work` for Developer, `/triage` for Analyst, and `/governance/overview` for CISO.
5. If identity retrieval fails, show a retryable sign-in/session error. Do not render a role denial and do not loop.

The target permits an Analyst or CISO to open a developer-owned run only through an explicitly granted evidence capability. A developer opening another developer’s run remains a 404 to preserve the current non-enumeration contract.

### Analyst outcome flow

An Analyst lands on `/triage`. The queue is live and source-labelled. It prioritizes reachable, unassessed, newly observed, aging, overdue, and unassigned cases using an explainable server-produced priority score and reason list. It must not surface seeded/demo records as active cases. Each row has stable case, finding, run, and evidence IDs.

The Analyst opens a case and sees the exact finding/call site, finding state, scanner/taint evidence, source revision and hash, provenance chain, case history, recommendation links, and current remediation/exception status. The Analyst may create a case from an eligible finding, assign it within the allowed team scope, add evidence, add a disposition/rationale, upload qualified scanner evidence, draft a recommendation, and request a transition. A resolution requires a disposition, actor, timestamp, rationale, and verified evidence criteria. Reopen adds a new event rather than rewriting history. Forget is a separately authorized deletion operation and never resolves a security case implicitly.

### CISO outcome flow

A CISO lands on `/governance/overview`, which provides only live, coverage-qualified posture: exposure trends, unassessed coverage, unknown gate rate, policy feed freshness, exception aging, remediation SLA, evidence freshness, and report/anchor health. Every aggregate provides a denominator and drill-down to its source cases/evidence.

The CISO controls versioned policies and exceptions, reviews approval requests, assigns remediation owners and due dates, decides risk acceptance within policy, and creates authorized evidence packs. A CISO cannot approve a request they initiated. The system records the requester, approver(s), execution actor, policy version, evidence set, and verification result separately. A report shows `pending`, `accepted`, `in_progress`, `overdue`, `verified_remediated`, and `exception_covered` risk; it must never translate a memory mutation into “remediated.”

## Target authorization model

### Principles

Authorization is capability based. A primary role maps to a fixed server-side capability set; a request never supplies its own role or capability. The server is the enforcement point. Frontend guards, hidden navigation, and client-side route redirects are usability aids only.

Device credentials are separate from human roles. A recorder token can only use `POST /api/recorder` and `POST /api/gate/package`, subject to the device’s fixed scope and immediate revocation. It can never call `/api/me`, a human route, a case API, a fleet endpoint, an approval endpoint, or a WebSocket. This existing design remains unchanged. [2]

### Capability matrix

| Capability domain | Developer baseline | Analyst | CISO | Notes |
| --- | --- | --- | --- | --- |
| `identity.read` | Yes, self | Yes, self | Yes, self | `/api/me` returns only authoritative identity and capabilities. |
| `run.create.self`, `run.read.owned`, `run.mutate.owned` | Yes | Yes | Yes | Existing owner attribution remains. |
| `run.read.seeded_reference` | Read-only, visibly reference | Read-only | Read-only | Excluded from live metrics and reports. |
| `run.read.fleet_evidence` | No | Yes | Yes | Analysts/CISOs may investigate all runs. |
| `run.delete.owned` | Request only; never resolves cases | Request only | Approve/execute only under approval policy | Preserve preview/version/idempotency and require legal-hold/exception evaluation. |
| `finding.read.owned` | Yes | Yes | Yes | Developer only sees own/seeded run evidence. |
| `finding.read.fleet`, `case.read`, `case.triage` | No | Yes | Yes | Analysts receive the investigative read and case work capability. |
| `case.create`, `case.assign`, `case.transition.request`, `recommendation.draft` | No | Yes | Yes | Analyst cannot unilaterally approve critical resolution/remediation. |
| `evidence.ingest.scan` | Own runs only | Fleet evidence scope | Fleet evidence scope | Ingestion does not imply trusted coverage or case closure. |
| `recommendation.request_execute` | No | Yes | Yes | Creates an approval-gated remediation request. |
| `approval.review`, `approval.approve`, `remediation.execute` | No | No | Yes, with requester/approver separation | CISO still cannot self-approve and approval tiers can require quorum. |
| `policy.read`, `exception.read` | No | Read-only | Yes | Exception data may be redacted based on sensitive fields. |
| `policy.admin`, `exception.request`, `exception.approve` | No | No / request if delegated by policy | Yes, with separation | Start without delegation unless a policy explicitly allows it. |
| `fleet.posture.read`, `trend.read` | No | Yes | Yes | Always source/coverage labelled. |
| `audit.read` | No | Read selected investigation events | Read and export eligible events | Sensitive reads are auditable under a privacy-reviewed event taxonomy. |
| `report.generate`, `report.download`, `report.schedule` | No | No / draft preview if granted | Yes | Production exports refuse sample/unverified sources. |
| `device.read.owned`, `device.revoke.owned` | Yes | Yes | Yes | Existing self-device behavior. |
| `device.read.fleet`, `device.revoke.fleet` | No | No | Yes | Removes the current overloaded Analyst device authority. |

The initial capability values are static constants in API code and tested as a complete matrix. Do **not** accept a free-form `capabilities` token claim in the first release. If a later policy-driven entitlement system is required, it must be separately versioned, audited, and deny by default.

### OIDC entitlement and session contract

Production configuration must require all of the following at startup: issuer, client ID, API audience or resource/scope configuration, exact HTTPS browser origin, exact HTTPS CORS origin, Analyst group mapping, CISO group mapping, durable engine/control-plane storage, strict audit configuration, and a non-demo seed setting. The browser’s OIDC request must request the documented API resource/scope. A deployment is invalid if the browser cannot obtain an access token accepted by the API audience check.

The configured role claim must be an explicit `MESHAGENT_ROLE_CLAIM` setting, defaulting only when deliberately configured to `groups`. Its valid value is a string or an array of strings. The target maps normalized exact group membership as follows:

| Valid signed claim condition | Result |
| --- | --- |
| In configured Analyst group only | `analyst` with Analyst capabilities |
| In configured CISO group only | `ciso` with CISO capabilities |
| In neither governance group | `developer` with baseline capabilities |
| In both governance groups | 401 `ambiguous_role_claim`; no session and no `/api/me` role |
| Claim type malformed, issuer/audience/signature/expiry invalid, no subject, or unsupported token type | 401; no session and no implicit Developer fallback |

The browser implementation now uses a **same-origin BFF session**. The browser does not retain an access or refresh token in JavaScript storage. The API owns discovery, authorization-code exchange, PKCE transaction state, token audience verification, bounded session expiry, and server-side revocation. This release deliberately requires reauthentication at expiry; refresh-token rotation and provider-wide logout are post-v1 integrations because their semantics differ by customer identity provider.

| Browser-facing auth contract | Required behavior |
| --- | --- |
| `GET /auth/login?return_to=<safe-path>` | Creates cryptographically fresh state, nonce, and S256 PKCE verifier server side; stores one-time transaction state in an encrypted/HttpOnly same-site cookie or server session; redirects to discovery-derived authorization endpoint. Rejects invalid return targets with 400. |
| `GET /auth/callback?code=&state=` | Validates exact unused state before token exchange. Exchanges at configured token endpoint with the stored verifier and exact redirect URI. On success, establishes `__Host-meshagent_session` (`Secure; HttpOnly; Path=/; SameSite=Lax`) and consumes artifacts exactly once. |
| `GET /auth/callback?error=&state=` | Validates exact unused state **before** clearing it. Mismatches render a generic safe error. Valid errors consume the transaction and present a bounded, escaped, locally mapped error; raw `error_description` is not rendered. |
| `GET /auth/session` | Returns only `{authenticated, expires_at, reauth_required}` for browser UX; no bearer/refresh/ID token. Returns 401 when absent/expired. |
| `POST /auth/logout` | Clears BFF session, identity query cache, active socket registry, and local app state before redirecting to the provider end-session endpoint when discovery advertises one. Uses only an allowlisted configured post-logout URI. If no end-session endpoint exists, response/UI state says **Signed out of MeshAgent** rather than implying IdP global logout. |

`GET /api/me` accepts the BFF session for same-origin browser calls and a verified human Bearer token for supported non-browser API clients. It does not accept local assertion headers in production. A 401 from REST or an authenticated WebSocket close clears all auth-scoped query state and transitions to sign-in with a validated return path. A 403 is a permission response and does not sign the user out.

Active WebSockets are bound to the authenticated session/token expiry and an entitlement version. At session expiry, session revocation, or deprovisioning, the server closes the connection with documented terminal codes: `4401` for reauthentication required and `4403` for access revoked. The browser must not reconnect automatically on either terminal close. For valid transient closures, it retains the existing bounded retry behavior.

## Exact API contract changes

### Conventions that apply to all new APIs

All new API identifiers are ULIDs. Timestamps are RFC 3339 UTC strings. Every mutation receives a server-generated `correlation_id`, emits an audit event, and returns it in the response header/body. All APIs use JSON unless an endpoint explicitly accepts an evidence upload. Collection endpoints use opaque cursors and a bounded `page_size` from 1 to 100; no `limit=0` “return everything” behavior exists. Results are ordered deterministically by documented `(sort_key, id)` order.

All errors use:

```json
{
  "code": "approval_required",
  "detail": "A distinct CISO approval is required before execution.",
  "correlation_id": "01J..."
}
```

Status semantics are fixed: 400 malformed parameters, 401 unauthenticated/invalid entitlement, 403 known identity lacks capability, 404 resource not visible, 409 state or idempotency conflict, 412 stale `If-Match`, 413 oversize upload, 422 schema/domain validation, 428 missing required precondition, and 429 rate limited. Existing developer cross-owner run reads remain 404.

Every new evidence-bearing response includes the following origin envelope:

```json
{
  "data_origin": "live | demo | sample | reference_build",
  "source_time": "2026-09-23T00:00:00Z",
  "seeded_count": 0,
  "coverage": {"known": 0, "unknown": 0, "basis": "..."}
}
```

Production responses may only use `live` or, for specifically opened historical reference data, `reference_build`. `demo` and `sample` are rejected for production report/case/remediation mutation paths. The exact current engine/sample data has no such comprehensive contract; this is an additive target requirement.

### Identity and existing API changes

| Endpoint | Change | Request | Success response and enforcement | Compatibility |
| --- | --- | --- | --- | --- |
| `GET /api/me` | **Changed** | None | `{subject,name,email,primary_role,capabilities,verified,expires_at,entitlement_version}`. `primary_role` is one of the closed vocabulary. Never returns raw claims. | Retain legacy `role` as an alias of `primary_role` for one release and mark it deprecated. Web switches to `primary_role`/capabilities first. |
| `GET /api/health` | **Changed** | None | Preserve public liveness facts, but return only coarse configured/healthy booleans in public mode. Add authenticated `/api/health/details` for exact mode, storage, anchor, and ingestion health. | Existing ModeBanner can use public `mode` during migration. Do not expose credentials, issuer internals, or operational topology. |
| `GET /api/audit` | **Changed** | `cursor`, `page_size`, optional `from`, `to`, `action`, `actor`, `case_id`, `correlation_id` | `{items,next_cursor,integrity:{local_chain,external_checkpoint,checkpoint_at},origin}`. Requires `audit.read`; export needs CISO capability. | Support `limit` for one release as an adapter to first page, with server max 100 and `Deprecation`/`Sunset` headers. |
| `POST /api/recorder` | **Changed** | Current `RecorderBatch` plus required `Idempotency-Key`; optional event keys remain validated if retained | Stores bounded owner/device/session idempotency key, normalized payload digest, and receipt atomically before acknowledgment. Same key+same owner+same digest replays original receipt; changed owner/payload returns 409. | During rollout accept missing key only from explicitly versioned legacy adapters, emit warning/audit, then require it. |
| `POST /api/runs/{runId}/scan` | **Changed** | `multipart/form-data` evidence artifact or `application/sarif+json`; `Idempotency-Key`; declared source revision and module digest map where available | 201 artifact receipt with `evidence_id`, digest, scanner identity/version/ruleset/invocation state, trust level, coverage outcome, and rejected-count. Enforce configurable production limits of **25 MiB**, **10,000 results**, **2,000 artifacts**, **100 locations/result**, **20 flow steps**, and **30 JSON nesting levels** before durable mutation. | Existing JSON input remains accepted for one release but produces `trust=unbound` unless it supplies the required provenance. |
| `GET /api/runs/{runId}/findings` | **Changed** | Optional cursor/filter | Uses stable `finding_id` and returns `classes_detected`, `externally_scanned_modules`, `externally_scanned_classes`, `unassessed`, and evidence links. It does not call detected code “scanned.” | Keep deprecated `scanned` only as a clearly documented alias during a release window; UI must stop using it immediately. |
| `GET /api/runs/{runId}/findings/{findingId}` | **New; replaces ambiguous sink lookup** | Stable `findingId` | Full finding/evidence detail. Legacy `/findings/{sink}` rejects ambiguity with 409 and is sunset. | Deterministic backfill IDs make legacy links resolvable where exactly one finding exists. |
| `GET /api/runs/{runId}/structure` | **New explicit run scope** | None | `{scope:"run",run_id,owner,origin,decomposition,scales}`. | Existing decomposition/scales endpoints remain, are labelled legacy, and redirect client use to this contract. |
| `GET /api/fleet/decomposition` | **Changed** | Cursor/filter as required | `{scope:"fleet",origin,decomposition,coverage}`; requires `fleet.posture.read`. | `/structure` frontend must never substitute run decomposition for this response. |
| `GET /api/cve/{cve}/impact` | **Changed** | CVE identifier | Returns aggregate per-run/version/class/decision evidence across all matches with cursor support; developer requests are not permitted. | The developer Supply screen omits fleet impact and explains that blast radius is Analyst/CISO-only. |

### Finding, case, and evidence APIs

The target does not claim that current HyperMesh records already provide a case system. These are new control-plane APIs backed by the storage model below.

| Endpoint | Capability | Request contract | Success contract | Critical rules |
| --- | --- | --- | --- | --- |
| `GET /api/triage` | `case.triage` | `cursor`, `page_size`, `state`, `severity`, `owner`, `aging`, `source`, `sort=priority|updated|sla` | `{items:[{case_id,finding_id,run_id,priority,priority_reasons,severity,state,assignee,sla_due_at,origin}],next_cursor,origin}` | Only live/eligible cases appear by default. Priority is explainable and returned, not reconstructed by the browser. |
| `POST /api/cases` | `case.create` | `{finding_id,run_id?,title?,severity,initial_rationale}` and `Idempotency-Key` | 201 `{case_id,version,state:"open",event_id}` | Finding/run linkage and cited evidence are mandatory; same open finding cannot produce duplicate live cases unless a reopened/new-occurrence rule permits it. |
| `GET /api/cases` | `case.read` | Cursor/filter fields including `finding_id`, `state`, `assignee`, `sla`, `source` | Paginated case summaries | Developer has no fleet-case access. |
| `GET /api/cases/{caseId}` | `case.read` | None | Full case, evidence links, findings, recommendations, remediation/exception links, immutable ordered event history, and ETag/version | Redacts evidence fields according to capability; no hidden developer discovery. |
| `PATCH /api/cases/{caseId}` | `case.assign` or `case.transition.request` | `If-Match`, `{assignee?,severity?,sla_due_at?,title?}` | Updated summary and case event | State transition itself uses the explicit endpoint below. |
| `POST /api/cases/{caseId}/transitions` | `case.transition.request` | `If-Match`, `Idempotency-Key`, `{to_state,disposition?,rationale,evidence_ids[]}` | 201 `{event_id,case_id,state,version}` or 202 approval requirement | `resolved` requires disposition, rationale, and verified criteria. `reopened` preserves all earlier events. A deletion certificate alone is never resolution proof. |
| `POST /api/cases/{caseId}/evidence` | `case.create` | `Idempotency-Key`, `{evidence_id,relation:"supports|contradicts|remediates"}` | 201 event/link | Evidence source must be visible and content-hash protected. |
| `GET /api/evidence/{evidenceId}` | Evidence read applicable to parent | None | Immutable envelope: source, actor/device, received/observed times, content hash, parent IDs, trust, redaction state, artifact link if permitted | After forget, only permitted retained identity/hash appears. |
| `GET /api/operations/{operationId}` and `GET /api/certificates/{certificateId}` | Parent visibility / audit read | None | Persisted operation/certificate with request event, completion event, checkpoint reference, retained hashes, and redaction-safe details | Replaces transient React mutation-state receipts. |

### Recommendation, remediation, approval, policy, exception, and report APIs

| Endpoint | Capability | Request and state | Required semantics |
| --- | --- | --- | --- |
| `POST /api/recommendations` | `recommendation.draft` | `{case_ids[],finding_ids[],kind,title,proposed_action,rationale,evidence_ids[],affected_targets[]}`, idempotency key | Creates `draft`. Every draft cites evidence; no automatic case closure. |
| `GET /api/recommendations` / `GET /api/recommendations/{id}` | Read capability | Cursor/filter and detail | Returns `draft | requested | approved | executing | recorded_plan | verification_pending | verified | failed | cancelled`, origin, and remediation links. |
| `POST /api/recommendations/{id}/requests` | `recommendation.request_execute` | `{action:"record_plan|execute_remediation|execute_cut", target_scope, justification}`, idempotency key | Creates an approval request. `record_plan` does not claim external change; destructive/cut execution carries legal-hold and preview references. |
| `POST /api/approvals/{approvalId}/decisions` | `approval.approve` | `If-Match`, `{decision:"approve|reject",rationale}` | Enforces distinct requester/approver, policy tier/quorum, expiry, and immutable approval evidence. 409 if stale/expired/not eligible. |
| `POST /api/remediations` / `PATCH /api/remediations/{id}` | CISO create/execute; Analyst read/request as matrix allows | Create with case/recommendation linkage, service/technical owner, due date, SLA, target revision; patch status via ETag | Status is `accepted | in_progress | blocked | overdue | verification_pending | verified_remediated | failed | exception_covered`. Verified remediation requires target-revision SBOM/scan/evidence. |
| `GET/POST/PATCH /api/policies` and `/api/policies/{id}/versions` | `policy.read` / `policy.admin` | New policy/version contains scope, gate rules, unknown handling, denied licenses, severity thresholds, effective time | Versions are immutable after activation. High-assurance profiles block unknown/unpinned input unless a valid exception applies. |
| `GET/POST/PATCH /api/exceptions` and `POST /api/exceptions/{id}/requests` | Read / CISO or delegated request | Scope, risk rationale, compensating controls, owner, expiry, review cadence, evidence, affected policy/version | Approval is separate, time-bounded, revocable, and linked to gates/cases/remediations. Expired exceptions do not authorize actions. |
| `POST /api/reports` / `GET /api/reports/{id}` / `GET /api/reports/{id}/download` | `report.generate`, `report.download` | Period, filters, template/schema version, redaction profile, requested delivery; asynchronous job return 202 | Produces signed JSON/CSV/PDF evidence bundle only if every included source is live, authorized, retained, and checkpoint-verifiable. Includes manifest digest, generation actor/time, policy/exception/remediation/coverage metadata. |
| `POST /api/report-schedules` / `PATCH` / `DELETE` | `report.schedule` | Named schedule, period rule, approved recipients/channel reference, redaction profile, enable/disable | The scheduler is a durable product worker, not a browser timer. Delivery failure creates an auditable alert/event. This capability is P2, after report authorization and immutable manifests are live. |

The current `GET /api/recommendations` and `POST /api/recommendations/{rec_id}/apply` remain read/execute APIs with insufficient semantics. Migration is therefore explicit: the old list can be served as a projection with `Deprecation` and `Sunset` headers; the old `apply` endpoint remains available only during the documented compatibility period for non-production legacy/demo clients, then returns 410 in production. New production UI uses the request/approval/remediation endpoints. This prevents a hidden downgrade to single-Analyst execution while preserving a controlled migration path.

### WebSocket contract

`WS /api/runs/{runId}/stream` remains for outcome streaming but changes its terminal contract. Browser sessions authenticate through the same-origin BFF cookie; supported non-browser clients retain the documented Bearer subprotocol. The server authorizes before acceptance, sends only the documented event types `memory | notice | done | error`, and includes `correlation_id`/`run_id` in every frame. It closes pre-accept auth failures consistently with HTTP-equivalent reason codes where the runtime permits, and accepted sockets use 4401/4403 for terminal reauthentication/revocation.

The client treats 4401/4403 as terminal, clears auth state on 4401, displays an access message on 4403, and does not reconnect. It may retry boundedly for recoverable network failures only. The server must re-check active session/entitlement state at a bounded interval and at each outbound frame boundary so a deprovisioned CISO cannot retain a governance stream after entitlement removal.

## Backend storage and integrity changes

### Target storage boundary

Current durable files and HyperMesh state provide useful evidence storage but are not a multi-process transactional control plane. P0 introduces a transactional durable control-plane store, specified here as PostgreSQL, for sessions, entitlement snapshots, cases, policies, approvals, operations, receipts, idempotency, audit event index/checkpoints, report jobs, and fleet snapshots. Existing HyperMesh governed-memory/evidence records remain the evidence graph of record until deliberately migrated; they are referenced by immutable evidence IDs and content hashes rather than copied into mutable workflow fields.

The deployment must use either a single-writer lease around all remaining file-backed engine state or migrate each such mutation to transactional storage before horizontal API scaling. The release gate chooses one of those approaches explicitly. Two API processes may not share a state directory without a verified exclusive lease or transactional coordinator.

| Storage entity | Essential fields | Integrity and retention rule |
| --- | --- | --- |
| `identity_session` | opaque session ID hash, subject, primary role, capability/entitlement version, issued/expiry/revocation times, provider session metadata | Server-only; token values are never stored in browser or application logs. |
| `evidence_artifact` | ULID, type, object location, content hash, source/revision/module hashes, scanner/tool/ruleset/version, invocation outcome, trust, received/observed times, parent IDs | Immutable content-addressed object with retention/legal-hold reference; only authorized content is downloadable. |
| `finding` | stable ULID, run/evidence/linkage, owner/module/location/sink fingerprint, reachability, severity, coverage state, first/last observed | Identity remains after case closure; updates create observation history rather than erase provenance. |
| `finding_case` and `case_event` | case ID, finding links, state/version, assignee, SLA, priority/reasons, disposition; immutable event ID/actor/timestamp/rationale/evidence links | Append-only events and optimistic versioning. |
| `recommendation` and `remediation` | workflow state, linked cases/findings/evidence, target scope/revision, owner, due date, verification evidence | A plan, action, and verification are distinct records. |
| `approval_request` and `approval_decision` | requester, policy tier, required quorum, expiry, approver identity, decision/rationale, execution link | Enforces non-self-approval and preserves decision evidence. |
| `policy`, `policy_version`, `exception` | policy scopes/rules/version/effective time; exception scope/controls/owner/expiry/review/approval | Activated policy versions immutable; exception revocation/expiry checked at authorization/gate time. |
| `operation_receipt` and `deletion_certificate` | operation/idempotency payload digest, request/completion audit IDs, outcome, retained hashes, certificate ID | Readable by deep link after refresh; tied to external audit checkpoint. |
| `recorder_idempotency` | principal/device/session scope, key, payload digest, receipt ID, expiry | Unique `(scope,key)`; changed digest is conflict, not duplicate ingestion. |
| `audit_event` and `audit_checkpoint` | monotonically ordered event, previous digest/digest, correlated actor/action/resource/outcome; external signature/witness URI/key ID/head/count | A malformed/truncated local log fails verification. Checkpoints detect valid-tail rollback and external verifier validates independently. |
| `fleet_snapshot` and `report_job` | source census/version/coverage/time, posture metrics; report manifest, filters, schema, digest, signer, delivery state | Snapshot denominators must be retained; reports point to exact snapshot/evidence set. |

Raw source, prompt, token, Authorization header, raw SARIF body, and sensitive code content must never appear in normal application telemetry. Structured logs and traces use correlation IDs, route, status, duration, safe size/count metrics, and redacted identifiers only.

### Evidence and scanner storage rules

A scan artifact is stored before interpretation. A trusted scan result requires a successful invocation, known tool/ruleset/version, bound source revision/module hash, applicable rule coverage, source locator, and artifact digest. Unbound or user-supplied SARIF may be retained as `unverified` evidence but cannot clear a finding. A failed/partial invocation, unsupported rule coverage, unmatched module digest, or unmatched revision leaves the finding `not_assessed` for that scope.

Package commands and editor hooks produce **unverified observations**, not verified SBOM facts. Verified SBOM entries require a lockfile, package-manager manifest/SBOM, or build attestation linked to repository revision/build ID. The target SBOM model includes PURL/ecosystem/version, artifact hash, dependency path, license source, source revision, observation time, advisory feed timestamp, and unresolved/ambiguous coverage. Feed outage and unknown coverage become measurable posture data; regulated profiles either block them or require an active approved exception.

### Audit checkpoint design

Every mutation produces a requested and a completion/failure audit event. Sensitive fleet/run/finding/case reads and exports produce privacy-reviewed read events. The local sequence has a durable monotonic count and hash chain. At a defined interval and after high-impact operations, a checkpoint service signs the `{sequence,count,head_digest,time}` using a key unavailable to the API writer and stores/verifies it through an immutable/WORM or independent evidence destination. The UI calls the state `anchored` only when external checkpoint verification succeeds; otherwise it says `local chain only` or `verification failed`.

A deletion certificate contains the operation ID, request and completion audit event IDs/digests, case impact (if any), closure count, retained hashes, checkpoint reference, actor, time, and redaction-safe receipt. A case cannot be resolved merely because a certificate exists.

## Migration and compatibility plan

### Migration principles

The migration is additive, reversible at each data step, and does not grant new governance authority by default. Backfills must preserve existing IDs/ownership where valid, distinguish seeded/sample/reference records, and produce an operator review report rather than silently converting synthetic state into production evidence. No data migration deletes governed-memory content, audit lines, or current deletion certificates.

| Phase | Change | Compatibility and rollback condition |
| --- | --- | --- |
| **M0: inventory and freeze** | Capture production configuration, data directory, schema/version, seeded records, active devices, OIDC claim samples, current audit head, and API client usage. | No behavior change. Produce a signed pre-migration inventory/checkpoint and backup restore test. |
| **M1: provenance and demo isolation** | Add `data_origin`, seed flags, source times, reference-build and coverage envelopes. Disable demo seeding in production; start live fleet empty. | Existing seed IDs remain accessible only under explicit reference/demo context. If data-origin migration fails, production launch fails rather than mislabels data. |
| **M2: entitlement expansion** | Add CISO config/group mapping and new `/api/me` fields. Deploy capability code with CISO disabled until IdP groups and matrix tests are validated. | Preserve legacy `role`; users in both governance groups fail closed. Rollback removes CISO assignment but never maps it to Analyst implicitly. |
| **M3: BFF/session rollout** | Introduce BFF routes/cookies and API dual authentication. Migrate browser UI to session calls; retain verified Bearer only for documented non-browser clients. | Feature flag by origin/client version. Roll back UI to old auth only before production cutover; after cutover do not silently re-enable JS token storage. |
| **M4: control-plane database** | Create tables/indexes/outbox/checkpoint records and dual-write non-destructively from current operations/audit indexes. Validate row counts, hashes, and restart recovery. | Read path stays on current source until reconciliation is clean. Any mismatch blocks cutover; current data directory remains restorable. |
| **M5: stable finding/evidence backfill** | Generate deterministic finding IDs from canonical run/source/location/sink fingerprint; store legacy aliases and evidence provenance gaps. | Ambiguous legacy sink routes return 409, not an arbitrary first match. Unsupported old data is marked `unverified`/`unknown`, never cleared. |
| **M6: cases and workflow** | Backfill no cases automatically except explicitly reviewed imported cases. Create case APIs and UI; map current recommendations to `legacy_derived` read-only projections. | Current recommendation list remains readable. New workflow owns all new production changes. |
| **M7: deprecate unsafe apply** | Announce headers and telemetry for old `/apply`; train clients on request/approval/remediation APIs; disable unsafe production path after adoption window. | Legacy demo/nonproduction path may remain isolated. Production endpoint returns 410 after date; do not emulate an approval. |
| **M8: reporting and scheduled delivery** | Add report manifests, external audit checkpoints, retention/hold controls, then delivery workers/schedules. | Report generation remains disabled if checkpoint/data origin/coverage validation is incomplete. |

Schema migrations require forward and backward compatibility for at least one application release. API responses can add fields freely; renamed/remove fields use documented deprecation headers and a published sunset date. The hand-maintained TypeScript client must be replaced or checked against generated OpenAPI types in CI before an API schema migration is released.

## Frontend implementation requirements

### Shared application services

The frontend needs a single authenticated transport service that recognizes `401`, `403`, offline/network, timeout, cancellation, validation, and server errors. It must accept React Query abort signals, classify recoverable errors, never retry destructive mutations automatically, and clear auth-scoped QueryClient state/sockets on verified 401. A standard error component must be used for identity, fleet, structure, and composite Setup queries so failed queries do not remain visually “loading.”

The identity provider discovery/session layer must catch and surface sign-in initiation errors. A rejected discovery promise must be cleared before retry. Callback and provider errors use `role="alert"`, local safe messages, and a retry option. The app sets a route-specific title, moves focus to the main heading on navigation, and includes a visible-on-focus skip link.

### Screen-level acceptance behavior

| Screen | Required frontend behavior |
| --- | --- |
| Sign in / callback | No bearer token in session/local storage. Exact state required on success and error. Show provider unavailable, safe callback failure, and retry states distinctly. |
| Role landing | Wait for `/api/me`; preserve only allowed deep link; send Analyst to triage and CISO to governance overview; no default task-create landing for CISO. |
| Triage | URL-backed filters/sort/cursor. Shows priority reasons, source/origin, loading/empty/error/retry states, and links by stable IDs. |
| Case detail | Shows an event timeline, cited evidence, explicit resolution criteria, relationships to recommendation/remediation/exception, and a read-only historic record after closure. |
| Run pages | Run header always identifies run, owner, origin, source time, reference/demo state, and access outcome. Unknown/404 is a non-enumerating unavailable state. |
| Security/Supply | Uses externally scanned coverage labels accurately. Developers see scoped own evidence only and do not call fleet CVE impact; UI explains access boundary. |
| Fleet structure | Uses fleet endpoint and visible `scope=fleet`; run structure is separately named/linked. Graph always has text/table alternative and evidence drill-down. |
| Recommendations | Buttons state their real action before confirmation: Draft, Request approval, Record plan, Execute approved remediation, or Verify. A non-cut action never says “Apply to N agents.” |
| Approval/remediation | Shows requester, required policy/quorum, expiry, approver eligibility, execution result, target revision, and verification proof. |
| Certificates/receipts | Uses durable URL/deep link and download/print-safe representation. Refresh restores the same authorized document. |
| Audit/reports | Shows local versus externally anchored integrity precisely. Export/report controls show scope, redaction profile, data origin, coverage, manifest checksum, and delivery status. |

## End-to-end acceptance test specification

The release suite must run against a disposable engine-backed, production-configured deployment with a stub OIDC provider and a durable control-plane store. It must also run distinct demo/sample fixtures to prove that demo data cannot leak into production outcomes. Local asserted headers are permitted only in explicitly nonproduction unit fixtures and are rejected in the production suite.

### Authentication, session, and entitlement tests

| ID | Test | Expected assertion |
| --- | --- | --- |
| AUTH-01 | Start sign-in with a test IdP. | Browser sends authorization code + S256 PKCE with fresh state/verifier/nonce for each attempt, uses configured API resource/audience, and exchanges only at discovery token endpoint. |
| AUTH-02 | Return code success, code error, and provider error callbacks with matching/mismatching/replayed state. | Both success and error validate exact state before consuming artifacts; mismatch is generic safe failure; each transaction is single-use. |
| AUTH-03 | Submit signed tokens for Developer, Analyst, CISO, no group, both groups, malformed claim, expired token, wrong issuer/audience/signature. | `/api/me` emits the exact closed role/capability set or 401. Both governance groups never receive an implicit grant. |
| AUTH-04 | Open authorized, missing, expired, external, and unauthorized deep links before sign-in. | Authorized path is restored; others land at `/work`, `/triage`, or `/governance/overview` according to authoritative role; CISO never defaults to task creation. |
| AUTH-05 | Force 401 on REST and 4401/4403 on an active socket. | 401 clears session/query/sockets and enters safe sign-in; 403 remains a permission state; 4401 does not reconnect and reauthenticates; 4403 does not reconnect and reports revoked access. |
| AUTH-06 | Sign out with and without provider end-session discovery metadata. | Local app state and sockets clear first; configured post-logout URI is used; UI accurately distinguishes MeshAgent-only from global IdP logout. |
| AUTH-07 | Remove Analyst/CISO group during a live session. | Role removal takes effect within the configured SLA; protected REST and existing sockets cannot retain governance access. |

### Authorization matrix tests

Use a table-driven test fixture to execute every REST endpoint and the run WebSocket for Developer self, Developer cross-owner, Analyst, CISO, no-role valid user, expired human token, and device token. Assert the capability matrix above exactly. The suite specifically proves: developers cannot read colleague data or fleet/case/audit data; Analysts can investigate fleet/cases but cannot administer fleet devices, policies, approvals, exceptions, reports, or cross-owner destructive execution; CISO is subject to approval separation; and recorder device tokens remain recorder/gate only even if approved by a CISO.

### Analyst case journey tests

| ID | Test | Expected assertion |
| --- | --- | --- |
| CASE-01 | Sign in as Analyst. | Landing is `/triage`; queue includes live reachable, unassessed, newly observed, aging, and unassigned test cases with deterministic priority reasons. No seeded/sample record appears as a live case. |
| CASE-02 | Open a queue item. | Case detail resolves stable case/run/finding/evidence IDs and displays exact module/call site, reachability, scanner trust/coverage, taint path, provenance, source hash, and events without manual URL assembly. |
| CASE-03 | Ingest qualified and unqualified SARIF. | Qualified exact-source scan changes only covered findings; traced evidence remains reachable. Unbound/partial/mismatched/unsupported scan leaves finding `not_assessed`; rejected limits yield 413/422 and zero partial writes. |
| CASE-04 | Create/assign/transition/reopen a case. | Every state change includes ETag/idempotency, actor/time/rationale; resolution requires disposition and verification evidence; reopening preserves prior events. |
| CASE-05 | Draft a recommendation from a case and request a plan/remediation. | Evidence is mandatory, a non-cut plan is not remediation, and neither action closes the case. |
| CASE-06 | Attempt forget after case creation. | Forget follows a distinct approval/deletion workflow. Certificate links to audit/checkpoint and does not silently resolve/close the security case. |

### CISO governance and reporting tests

| ID | Test | Expected assertion |
| --- | --- | --- |
| CISO-01 | Sign in as CISO. | Landing is governance overview with source-qualified posture, denominators, trend/snapshot time, exception aging, feed health, remediation/SLA, and drill-downs. |
| CISO-02 | Create policy and exception flows. | Policies version immutably; exceptions require owner, justification, controls, expiry/review, distinct approval, and affect only their declared scope. Expired/revoked exception fails authorization. |
| CISO-03 | Request and approve critical remediation/deletion. | Requester cannot approve own request; quorum/tier/expiry/stale rules work; operation persists/replays safely through restart; legal hold/exception checks occur; completion certificate and audit linkage are immutable. |
| CISO-04 | Verify external remediation. | A repository/action claim remains `verification_pending` until a target-revision SBOM/scan/evidence validates it; repeated non-cut plan request is idempotent and never claims remediation. |
| CISO-05 | Create a period report. | Export filters, authorization, redaction, schema version, source identifiers, coverage/unknown data, exceptions, posture, SBOM, audit/certificates, manifest hash/signature, retention/hold status, and independent checkpoint verification are present. Sample/unverified sources refuse production export. |
| CISO-06 | Schedule and fail delivery. | Durable scheduled report run respects authorized recipient/channel configuration; failure is observed, auditable, and surfaced without leaking report content. |

### Durability, evidence, and scale tests

| ID | Test | Expected assertion |
| --- | --- | --- |
| DATA-01 | Start production with no ingestion and with demo seed flag. | Empty fleet returns `data_origin=live`, zero seeded count, and no synthetic CVEs/recommendations; production startup fails if demo seeding is enabled. |
| DATA-02 | Restart after runs, scans, cases, approvals, report generation, and deletion preparation. | Evidence, cases, events, operation receipts, audit history, and idempotent replay state persist with no duplicate mutation. |
| DATA-03 | Start two API processes on a shared state target. | Second process fails exclusive lease or all writes serialize through a verified transactional coordinator; no pairing/recorder/forget loss occurs. |
| DATA-04 | Alter a middle audit record, remove a final valid record, append malformed tail, rewrite local chain, and verify externally. | Local malformed data fails; external signed/witnessed head detects tail rollback/rewrites; UI reports precise state. |
| DATA-05 | Re-send recorder batch after lost response/restart. | Same scoped key/payload returns original receipt and no new memory/audit ingestion; changed payload/key scope returns 409. |
| DATA-06 | Page audit/cases/findings while concurrent writes occur. | Bounded pages and opaque cursors return each eligible item exactly once under documented snapshot/cursor behavior. |
| DATA-07 | Create same sink in different classes and CVE in multiple runs/versions. | Stable finding lookup is unambiguous; CVE impact and recommendation blast radius aggregate every relevant source and retain per-run provenance. |

### Browser quality and contract tests

The browser suite must cover sign-in discovery outage then retry, session expiry, deep-link restore, role guards, create-to-stream-to-outcome, offline/cancellation/retry behavior, stale/idempotent forget and receipt refresh, developer Supply experience, URL state for triage/fleet/rewind, engine versus sample/reference labels, and report/certificate deep links. It must run keyboard and responsive checks at 320 px, 768 px, and desktop: skip link, focus order, focus visibility, route announcements, graph alternative, upload labels, table overflow, confirmation cancel paths, and no horizontal clipping.

CI must run backend API tests in engine and production-configuration modes; typecheck/build; generated OpenAPI-versus-TypeScript compatibility; component tests using mocked transport; browser E2E with stub IdP; migration/restart tests; and external audit-checkpoint verification. A release fails if production health reports sample/demo origin, non-durable storage, no identity provider, no verified audit anchor where the release claims an anchored report, or a local asserted identity.

## Delivery sequence and definition of done

**P0 is done** only when production has closed Analyst/CISO mapping, explicit API audience acquisition, BFF logout/session/expiry handling, terminal socket behavior, demo-seed exclusion, provenance envelopes, idempotent recorder ingestion, bounded SARIF intake, single-writer/transactional control plane, and independently verifiable audit checkpoints. The release evidence includes the authentication, authorization, and integrity acceptance tests above.

**P1 is done** only when a signed-in Analyst can land at triage, find a real live case, inspect stable evidence, ingest qualified scanner evidence, create/assign/transition/reopen the case, draft an evidence-linked recommendation, and revisit every case/receipt after refresh. No action may present deletion or a memory policy fact as remediation or case closure.

**P2 is done** only when a CISO can manage versioned policy/exception controls, perform separated approvals, track a remediation to target-revision verification, view trend/coverage data, and generate an authorized, signed, source-qualified evidence pack. Background delivery is enabled only after the report itself is trustworthy and the durable worker/delivery failure test passes.

**P3 is done** only when all list APIs have bounded stable pagination, API client compatibility is generated/validated, logs/metrics are demonstrably redacted, web routes use consistent recoverable error patterns, and the full browser/accessibility/regression suite is a release gate.

## Evidence references

The findings in this specification are grounded in the supplied audit and the following authoritative repository files at revision `10dd967`. These references describe the **current** implementation, not features claimed as already delivered by this target specification.

[1]: ../services/api/app/auth.py "Current role mapping and OIDC verifier"
[2]: ../services/api/app/main.py "Current FastAPI authorization, device, audit, run, and WebSocket routes"
[3]: ../apps/web/src/lib/auth.ts "Current browser PKCE, token storage, and sign-out implementation"
[4]: ../apps/web/src/router.tsx "Current frontend routes and Analyst route guards"
[5]: ../services/api/app/gateway.py "Gateway mode selection and sample gateway behavior"
[6]: ../services/api/app/engine_gateway.py "Current engine persistence, seeded fleet, recorder, recommendation, and CVE behavior"
[7]: ../services/api/app/audit.py "Current local hash-chain audit implementation"
[8]: ../services/api/app/models.py "Current Pydantic API models"
[9]: ../services/api/app/operations.py "Current durable deletion operation journal"
[10]: ../docker-compose.production.yml "Current production Compose engine configuration"
