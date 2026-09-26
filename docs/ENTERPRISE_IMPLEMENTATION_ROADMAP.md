# meshAgent enterprise implementation roadmap — Stage 1A through Stage 5

## Product boundary

This roadmap evolves the current customer-operated meshAgent control plane into an enterprise-managed product without replacing its authoritative FastAPI workflows, non-composable roles, optimistic concurrency, native HyperMesh evidence, or recording-only device permissions.

Status labels are literal:

- **Implemented** means code and tests exist in the current Stage 1A branch.
- **Next** means the architecture is specified but production code is not yet shipped.
- **Later** means dependent scope that must not appear as a working product feature until its acceptance gate passes.

## Program sequence

| Stage | Status | Outcome | Client-demonstration value |
|---|---|---|---|
| 1A — Native recorder foundation | **Implemented** | Go hook shim, per-user daemon, protected IPC, encrypted queue, ordered replay, real package gate and session API | Demonstrates the real technical path without a Python hook process per event |
| 1B — Device trust and enrollment | **Next** | One corporate sign-in, non-exportable device key, DPoP-bound short-lived token, signed configuration | Demonstrates enterprise identity and immediate revoke/quarantine |
| 1C — Signed platform packages | **Next** | Notarized macOS package, Authenticode MSI, signed DEB/RPM, SBOM and provenance | Demonstrates an IT-installable product rather than a developer script |
| 1D — Administrator pilot | **Next** | Platform Administrator onboarding, fleet health, cohorts, rollout receipts and rollback rehearsal | Demonstrates controlled deployment to a pilot group |
| 1E — Stable recorder release | **Next** | Operations runbook, support bundle, migration, real-OS acceptance matrix and stable channel | Makes Stage 1 supportable for customer production use |
| 2 — Managed Cursor integration | **Later** | Team/MDM hook distribution, central capture policy, repository eligibility and coverage proof | Removes normal repository edits and terminal setup |
| 3 — GitHub organization integration | **Later** | Organization-installed GitHub App, repository registry, signed webhook ingestion and commit/PR linkage | Proves repository authorization and connects agent evidence to normal delivery workflow |
| 4 — Enterprise policy and evidence operations | **Later** | Scalable policy distribution, retention/legal hold, evidence export, service integrations and operational SLOs | Demonstrates governed operation across teams without broadening role access |
| 5 — Enterprise GA and assurance | **Later** | Upgrade/rollback certification, disaster recovery, security assurance, deployment profiles and release governance | Provides a procurement-ready, auditable release posture |

---

## Stage 1A — Native recorder foundation

### Backend and native runtime

- Two Go executables: `meshagent-hook` and `meshagent-recorder`.
- Existing `meshagent.session.v1` and package-gate APIs remain unchanged.
- AES-256-GCM encrypted SQLite queue with opener-first ordered replay.
- Atomic sequence reservation, acknowledgement-driven deletion, lease recovery and bounded backpressure.
- Same-user Unix socket or current-user Windows named pipe.
- Dynamic device-credential reload; human read tokens are never used.
- Repository opt-in, nested exclusions, traversal and symlink escape protection.
- Explicit package-gate fail-open boundary: only server `block` denies.
- Linux, macOS and Windows build targets with development service installers.

### Frontend

No new role or screen is introduced. Native sessions appear in the existing Developer Sessions, Attention, Analyst, CISO and HyperMesh evidence workflows because the backend contract is preserved.

### Acceptance gate

- Race, vet, cross-platform build, Python compatibility and vulnerability checks pass.
- Live Cursor-shaped traffic reaches the real API and HyperMesh projection.
- Offline encrypted records replay with no sequence gap.
- Native migration refuses to orphan a non-empty Python queue.

---

## Stage 1B — Device trust and enrollment

### Backend

- Add durable recorder enrollment, device-key, token and configuration aggregates.
- Add `/api/v2/recorders/enrollments`, `/token`, `/config` and `/heartbeat` resources.
- Bind verified OIDC subject, deployment, device public key and platform facts.
- Validate RFC 9449-style DPoP proof, target URI, method, timestamp, proof ID and access-token hash.
- Mint ten-minute recording-only tokens; check device trust state on every write.
- Add immediate `active → quarantined → revoked` enforcement.
- Sign versioned effective configuration and retain immutable administrative receipts.
- Migrate existing bearer devices as visibly marked legacy credentials with a retirement deadline.

### Native runtime

- Generate non-exportable P-256 device keys in macOS Keychain/Secure Enclave, Windows CNG/TPM, and Linux Secret Service/TPM2.
- Wrap the queue data key with the OS keystore; refuse plaintext fallback in enterprise mode.
- Perform browser-based corporate enrollment with no copied token or terminal command.
- Refresh DPoP-bound tokens and configuration automatically.
- Preserve accepted encrypted data during quarantine; stop new delivery immediately.

### Frontend

Introduce a fourth non-composable server role, `platform_admin`, with no automatic access to Developer content, Analyst cases, or CISO decisions.

- `/admin/onboarding/recorder`: Identity → trust → configuration readiness.
- `/admin/recorders/:deviceId`: content-free identity, key, version, configuration and administrative history.
- Focused quarantine, re-enroll and revoke actions with expected versions and immutable receipts.

### Acceptance gate

Enrollment replay, key mismatch, token theft, DPoP replay, expired configuration, quarantine, revoke and rotation tests must pass. A revoked device must fail its next write.

---

## Stage 1C — Signed platform packages

### Backend and release service

- Store signed release manifests, artifact digests, SBOM digests, provenance and channel eligibility.
- Require immutable artifact identity across Pilot → Stable promotion.
- Refuse unapproved downgrade; model rollback as a new versioned rollout.

### Packaging

- macOS arm64/x86_64 PKG: Developer ID, hardened runtime, notarization and stapling.
- Windows x86_64 MSI: Authenticode signature, silent install, repair and uninstall.
- Linux amd64/arm64 DEB and RPM: signed package and repository metadata.
- Signed canonical release manifest and per-artifact CycloneDX/SPDX SBOM.
- Installer applies managed bootstrap configuration but contains no customer secret.

### Frontend

- `/admin/recorder-releases`: signatures, provenance, SBOM, supported OS, channel and digest.
- Package selection shows only verified artifacts and clearly distinguishes build, signed and promoted states.

### Acceptance gate

Fresh install, upgrade, repair, uninstall, sleep/wake, user logout/login, proxy, private CA and signature verification pass on real OS virtual machines.

---

## Stage 1D — Administrator pilot

### Backend

- Add recorder fleet snapshots, rollout aggregates and versioned cohort rules.
- Content-free heartbeat only: device/version/platform/config/adapter state/queue counters/delivery class.
- Rollout state machine: `draft → running → paused | completed | cancelled`.
- Idempotency keys for rollout start, quarantine and revoke; `expected_version` for every mutation.
- Automatic pause may stop expansion but never silently roll devices back.

### Frontend

- `/admin/onboarding/recorder`: Identity → Package → Configuration → Pilot → Release.
- `/admin/recorders`: action-required fleet queue rather than a dense dashboard.
- `/admin/recorder-rollouts/:rolloutId`: cohort, progress, failures, pause and rollback receipt.
- Mobile reflow, keyboard operation, non-color states and equivalent tables.

### Acceptance gate

A 25-device pilot completes with tested pause, quarantine, re-enrollment and explicit rollback. Fleet telemetry contains no prompt, source path, command, package or repository content.

---

## Stage 1E — Stable recorder release

### Operations

- Final SLOs and alerts for enrollment, gate latency, queue age, delivery failure and configuration staleness.
- Backup and disaster-recovery coverage for control plane, device registry, audit, Developer ledger and HyperMesh state.
- Redacted support bundle and diagnostic-code catalog.
- Legacy Python retirement and documented rollback window.
- Security threat model, incident runbook and release approval evidence.

### Acceptance gate

No unresolved trust-boundary blocker, migration and rollback rehearsal complete, all supported-OS acceptance suites green, recovery objectives verified, and signed Stable artifacts promoted from identical Pilot digests.

---

## Stage 2 — Managed Cursor integration

### Backend

- Signed central hook policy and source precedence.
- Approved repository patterns and short-lived device/deployment-bound eligibility grants.
- Coverage evidence for **Team policy → Cursor sync → Local recorder → Session accepted**.
- Unknown repositories default to no content capture.

### Native runtime

- Consume Cursor Enterprise team hooks or MDM system hooks.
- Remove normal `.cursor/hooks.json`, copied scripts and per-repository terminal setup.
- Local repository policy may disable or narrow capture, never broaden it.

### Frontend

- `/admin/integrations/cursor`: choose Team Hooks or MDM distribution.
- `/admin/integrations/cursor/policy`: event set, exclusions and fail-open disclosure.
- `/admin/integrations/cursor/pilot`: cohort and verification.
- `/admin/integrations/cursor/coverage`: exact coverage chain and uncovered devices.

### Acceptance gate

An administrator distributes a managed hook to a pilot without repository edits; an approved repository creates a real session; an unknown repository emits no source content; coverage is based on verified chain facts, not enrollment alone.

---

## Stage 3 — GitHub organization integration

### Backend

- Organization-installed GitHub App with selected-repository scope and least-privilege permissions.
- Installation/repository registry and tenant-safe repository identity mapping.
- Signed, replay-protected webhook ingestion for installation, repository, push, branch and pull-request events.
- Durable webhook inbox/outbox, idempotency, retry and dead-letter operations.
- Link observed Cursor sessions to repository, branch, commit and pull request without inventing causality.
- Repository access removal immediately disables new capture eligibility.

### Frontend

- `/admin/integrations/github`: installation, organization, selected repositories and permission review.
- `/admin/repositories`: eligible, awaiting sync, removed and error states.
- Repository detail shows GitHub authorization, Cursor coverage, recorder coverage and last accepted session as separate facts.

### Acceptance gate

Selected-repository changes propagate safely; invalid signatures and replays are rejected; removed repositories stop new recording; Developer ownership and Analyst/CISO boundaries remain server-enforced.

---

## Stage 4 — Enterprise policy and evidence operations

### Backend

- Database-level pagination and indexing for fleet-scale sessions, reviews, cases and approvals.
- Versioned capture/policy profiles by approved organizational scope.
- Retention, legal hold, deletion certificates and export manifests across the Developer ledger and HyperMesh.
- Enterprise event export through signed webhooks or customer-owned streaming destinations.
- Private CA/proxy profiles, regional deployment settings and health/SLO APIs.
- No additive browser role composition; Platform Administrator, Developer, Analyst and CISO remain distinct.

### Frontend

- Policy assignment and exception-focused administration, not raw source browsing.
- Retention/legal-hold workflows with impact preview and durable receipts.
- Integration delivery health and replay controls.
- Evidence export manifests with exact scope, digest, time and authorization.

### Acceptance gate

Load, tenant/isolation, retention, legal-hold, export, pagination, failover and authorization suites pass at target enterprise scale. Every destructive or high-impact operation is versioned and auditable.

---

## Stage 5 — Enterprise GA and assurance

### Product and release

- Supported deployment profiles, upgrade matrix and rollback certification.
- External penetration test and remediation closure.
- Software supply-chain attestations, signed SBOMs and provenance verification.
- Disaster-recovery exercise and customer-operable restore procedure.
- Data-flow, subprocess, privacy and security documentation required for procurement.
- Operational ownership, escalation, maintenance windows and end-of-life policy.

### Frontend

- Deployment readiness checklist grounded in verified configuration.
- Assurance evidence packages for CISO and procurement review.
- Release health, maintenance and compatibility notices without exposing tenant content.

### Acceptance gate

A clean customer environment can install signed packages, enroll through corporate identity, receive managed Cursor and GitHub policy, execute the full Developer → Analyst → CISO journey, survive recorder/API outage, revoke a device, restore protected state, and produce an evidence-backed assurance package.

---

## Dependency order

```text
1A native transport
  → 1B device trust
    → 1C signed packages
      → 1D administrator pilot
        → 1E stable recorder
          → 2 managed Cursor
            → 3 GitHub organization integration
              → 4 enterprise policy/evidence operations
                → 5 enterprise GA and assurance
```

Stages are intentionally sequential. A polished Administrator mockup or demo may preview a later stage, but the product must label it as preview until its backend, authorization, failure states and acceptance suite are implemented.
