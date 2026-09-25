# Changelog

All notable production-v1 operational and repository changes should be recorded here. Entries describe the repository release; they do not prove that a deployment has adopted the change. Follow semantic versioning where the project release process has adopted it, and include image digests or immutable build references in deployment change records.

## [Unreleased]

### Added

- Production operations runbook covering secure configuration, application-consistent backups, manifest verification, encryption hooks, clean-directory restore, state verification, retention, legal holds, disaster-recovery drills, upgrade preflight, rollback handoff, observability, incident response, deletion policy, and compatibility boundaries.
- Executable operations scripts in `scripts/ops` for backup, verification, restore, retention, recovery drill, upgrade preflight, rollback handoff, and optional age encryption/decryption hooks.
- Security disclosure policy, support boundary, authoritative architecture and operating-mode documentation, and archival notices for legacy documents.
- Durable destructive-operation journal with restart-safe idempotency, conflict detection, and recovery across tombstone, redaction, certificate, and commit phases.
- Production OIDC authorization-code flow with PKCE, signed API-token validation, analyst-group mapping, and a sign-in gate that removes local identity switching from production builds.
- Thread-safe namespaced CLI and adapter state, bounded offline event queues, retry commands, recording-only device credentials, and legacy state migration.
- Frontend error boundaries, accessible graph relationship tables, resilient WebSocket recovery, device-management states, responsive overflow handling, and automated Vitest coverage.
- Hash-locked Python dependencies, pinned container bases, CI release gates, SBOM and vulnerability scanning, and non-root production images.
- Closed Developer, Analyst, and CISO roles with server-owned capabilities, authoritative role landing, and capability-scoped navigation and route guards.
- Durable Analyst casework: prioritized queue, case assignment and SLA, controlled transitions, immutable events, source labels, and policy-exception requests.
- Durable CISO governance: executive overview, versioned policy register, separated exception approvals, remediation ownership, and live-source report manifests.
- API-owned Authorization Code + PKCE, opaque HttpOnly browser sessions, one-time login state, bounded expiry and revocation, exact-origin CSRF checks, and terminal WebSocket session handling.
- Durable owner-scoped Developer sessions with ordered activity events, projection state, package-policy history, strict sequencing, replay conflict detection, and startup reconciliation.
- Versioned Cursor and Claude Code session adapters with collision-safe event identities, acknowledgement-based `.inflight` queues, bounded offline replay, and synchronous policy-result capture.
- Lifespan-managed Developer activity projection reconciliation with durable retry timestamps and capped exponential backoff.
- Developer-first Sessions, Activity, and Security screens backed by the owner-scoped connected-agent APIs, including live status refresh, ordered event evidence, package-policy history, projection-health diagnostics, deep links to governed runs, responsive states, and capability guards.
- Visual-first Developer shell with icon navigation, repository-derived Project scope, compact session lists, session Overview and Evidence views, dense event inspection, package lifecycle visualization, contextual technical drawers, and a unified Connections workspace for Editors, Repositories, Devices, privacy settings, and verification. The interface uses scoped implementation tokens, plain-language status, responsive infographics, keyboard-accessible disclosure, and no editor-like chrome.
- Developer **Attention** with ecosystem-aware CVE cards, published fixed releases, code-to-package impact, and plain-language next actions; durable Developer-to-Analyst review requests with immutable evidence snapshots, prioritized Analyst queue, separation of duties, optimistic decisions, automatic clean-check verification, and role-scoped contributor/code/package/advisory hypergraphs.
- Native HyperMesh evidence for module imports, function-level package API calls, OSV advisory sources and released fixes, policy evaluations, and append-only review lifecycle events. Review creation freezes native entity IDs and a canonical digest; a transactional outbox projects requests, decisions, and verification with retry-safe ULID acknowledgements, and review graph APIs now return only the sealed native evidence ancestry.
- Priority 4 Analyst Operations with one ranked queue for Developer reviews and investigation cases; search, filters, personal saved views, assignment and due times, overdue state, item-level optimistic bulk triage, review-to-case escalation, durable team notes and mentions, per-person notifications, searchable work history, and native evidence-node selection for case context. Analyst/CISO authority is enforced by the API and Developers remain blocked from cross-team operations.
- Priority 5A durable governance contracts with immutable policy versions, draft/review/activation/supersession/retirement states, effective intervals, canonical SHA-256 content digests, aggregate optimistic concurrency, append-only policy and exception events, exact policy-version exception binding, evidence-aware request digests, atomic approval decisions, and migration-safe deterministic backfill.
- Priority 5B governance enforcement with production maker-checker policy activation, server-side requester separation for exception approval/renewal/revocation, lineage-preserving renewals, terminal revocation and pending-renewal cancellation, materialized approval and exception expiry, a service-owned idempotent reconciler, durable role-scoped governance notifications, and CISO lifecycle health/recovery endpoints.
- Priority 5C native HyperMesh governance evidence with a transactional policy/exception projection outbox, canonical payload and source-digest verification, retry-safe native ULID acknowledgement, activation and supersession relations, exception request/decision/expiry/revocation relations, review/case native-boundary links, scoped policy and exception evidence APIs, and Developer access denial.
- Priority 5D CISO operating workspace with an actionable command center, policy studio and immutable version detail, full-context decision desk, exception register and lifecycle records, evidence-backed remediation portfolio, measured health/coverage drill-downs, native HyperMesh decision timelines, simplified governance navigation, and responsive desktop/mobile layouts. Remediation now has optimistic lifecycle transitions, required terminal evidence, and immutable events.

### Changed

- The root README now defines the authoritative production-v1 architecture and supported modes.
- Production startup now fails closed unless engine mode, durable non-temporary state, strict audit, exact HTTPS origins, OIDC issuer/audience, and analyst groups are configured.
- Governed-memory reads verify graph-to-sidecar content hashes and recover only valid interrupted redactions.
- RAG streaming and non-streaming paths now share the same citation firewall and reject mixed valid/invented citation tags.
- FastAPI and PyJWT were upgraded to current advisory-free versions and all dependency lock hashes regenerated.
- Browser requests now use same-origin opaque sessions; OIDC access tokens no longer enter JavaScript or browser storage. Verified Bearer access remains supported for documented non-browser clients.
- Production startup now requires distinct Analyst and CISO groups plus the public OIDC client ID used by the API-owned code flow.
- Model-backed runs now default to the supported `gpt-5-mini` model, accept both standard OpenAI base-URL environment names, and receive optional model settings through local and production Compose.
- Offline adapter queue limits now apply backpressure to new observations without truncating accepted ordered records; CLI diagnostics expose any rejected observation count.
- Cursor and Claude Code package gates now recognize only supported install-command grammars, preserve PyPI/npm ecosystem identity, and allow enough bounded time for a cold advisory lookup while remaining fail-open on an unavailable control plane.

### Fixed

- Corrected native test fixtures and parser expectations so every bundled C engine test is hermetic and repeatable.
- Corrected production container path discovery, native-library placement, health checks, OIDC web-build configuration, and Nginx security-header inheritance.
- Corrected MCP session discovery and module claims to use the locked namespaced state store.
- Corrected unsupported or malformed model responses so the run persists a redacted failure reason and the UI shows an actionable error instead of stopping after the task statement.
- Corrected session-opener crash replay, cross-repository engine correlation, replay identity checks, repository-relative path validation, and paginated session totals.
- Device pairing now opens the Programmer UI's Connections → Devices tab directly instead of the legacy standalone device route.
- Cursor and Claude Code now retain subsequent developer prompts as ordered `prompt.submitted` activity instead of showing only the session's opening request.
- Shell navigation and inspection commands such as `cd`, `head`, `tail`, `git show`, `pip show`, and `npm view` no longer appear as packages. Duplicate OSV records for the same CVE are merged, and visible remediation guidance excludes raw commit hashes.

## [0.1.0] — production v1 baseline

Initial repository baseline. Deployments should record their own acceptance date, image references, configuration approval, backup verification, and disaster-recovery drill result rather than infer production acceptance from this entry.

## Release checklist

The release manager records completion, responsible owner, evidence location, and exceptions for every item. A skipped item requires an approved risk decision. This checklist does not replace customer change control.

### Scope and supply chain

- [ ] Release scope, issue references, version, commit SHA, and build/image digests are recorded.
- [ ] Changes are reviewed by appropriate code, operations, and security owners.
- [ ] Dependency changes, lockfile changes, advisories, licenses, and generated artifacts are reviewed according to the organization’s software-supply-chain process.
- [ ] No credentials, private keys, decrypted backups, recovery identities, customer data, raw governed-memory content, or environment files are included in the release material.
- [ ] Compatibility impact on API clients, adapters, state format, backup scripts, and rollback paths is documented.

### Validation

- [ ] Relevant unit, integration, and web validation pass in a clean environment.
- [ ] Engine mode is tested with isolated durable `MESHAGENT_DB_DIR` state.
- [ ] OIDC authentication, developer isolation, analyst access, device recording scope, and device revocation are tested.
- [ ] `/api/health` is validated for expected version, gateway, durability, and identity-provider reporting.
- [ ] Backup creation, checksum verification, encrypted decryption path, clean-directory restore, and `verify-state.sh --require-audit` pass against representative state.
- [ ] An isolated disaster-recovery drill meets the approved recovery objective or has an approved exception.
- [ ] Upgrade preflight is run against the candidate and a known-good rollback release and backup are identified.

### Production readiness and change execution

- [ ] Configuration matrix requirements are met: TLS ingress, exact CORS origins, OIDC, durable storage, least privilege, backups, key custody, retention, legal-hold register, and alerting.
- [ ] Observability dashboards and alerts have owners and were tested without exposing sensitive payloads.
- [ ] Support, incident command, privacy/records, and customer communication paths are current.
- [ ] Maintenance window, expected impact, abort thresholds, and rollback decision authority are approved.
- [ ] A verified pre-change backup ID and manifest result are recorded.
- [ ] During rollout, API health, latency, error rate, recorder behavior, state storage, authorization failures, and audit verification are observed.
- [ ] Post-release acceptance validates functional access, audit integrity, backup scheduling, monitoring, and release version; evidence is attached to the change record.

### Rollback trigger examples

Initiate the approved rollback plan for an authorization-boundary regression, audit-chain failure, corruption or unexpected state write, sustained availability or latency breach beyond the approved threshold, failed post-deployment health check, or any condition the incident commander judges unsafe. Preserve evidence and the failed release/state copy before destructive cleanup.
