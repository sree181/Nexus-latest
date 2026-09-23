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

### Changed

- The root README now defines the authoritative production-v1 architecture and supported modes.
- Production startup now fails closed unless engine mode, durable non-temporary state, strict audit, exact HTTPS origins, OIDC issuer/audience, and analyst groups are configured.
- Governed-memory reads verify graph-to-sidecar content hashes and recover only valid interrupted redactions.
- RAG streaming and non-streaming paths now share the same citation firewall and reject mixed valid/invented citation tags.
- FastAPI and PyJWT were upgraded to current advisory-free versions and all dependency lock hashes regenerated.
- Browser requests now use same-origin opaque sessions; OIDC access tokens no longer enter JavaScript or browser storage. Verified Bearer access remains supported for documented non-browser clients.
- Production startup now requires distinct Analyst and CISO groups plus the public OIDC client ID used by the API-owned code flow.
- Model-backed runs now default to the supported `gpt-5-mini` model, accept both standard OpenAI base-URL environment names, and receive optional model settings through local and production Compose.

### Fixed

- Corrected native test fixtures and parser expectations so every bundled C engine test is hermetic and repeatable.
- Corrected production container path discovery, native-library placement, health checks, OIDC web-build configuration, and Nginx security-header inheritance.
- Corrected MCP session discovery and module claims to use the locked namespaced state store.
- Corrected unsupported or malformed model responses so the run persists a redacted failure reason and the UI shows an actionable error instead of stopping after the task statement.

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
