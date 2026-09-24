# MeshAgent Production v1 Release Validation Report

**Release candidate:** `manus/developer-sessions-ui-v1`
**Baseline version:** `0.1.0`  
**Validation date:** 2026-09-23  
**Code status:** Production-ready single-tenant release candidate. Customer-environment acceptance remains required before go-live.

## Executive result

MeshAgent now has a coherent production baseline across its React web application, API-owned OIDC session boundary, Analyst casework, CISO governance, FastAPI control plane, editor/MCP adapters, governed-memory engine, native HyperMesh core, durable operations, security controls, backup and recovery tooling, and release pipeline. The implementation fails closed when production identity, role mappings, storage, audit, engine, or browser-origin requirements are absent.

The release was validated from locked dependencies and rebuilt native code. Both production images were built and run as non-root users. The browser-facing image successfully proxied the API, returned the required browser security headers, and showed the OIDC sign-in boundary rather than the local identity switcher.

## Implemented production controls

| Area                       | Production-v1 outcome                                                                                                                                                                                                                                                                                |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Identity and authorization | API-owned OIDC Authorization Code + PKCE; access tokens excluded from browser storage; opaque HttpOnly sessions; expiry/revocation; exact-origin CSRF and WebSocket checks; closed Developer/Analyst/CISO mapping; device-token scope separation; production rejection of local asserted identities. |
| Analyst journey            | Server-prioritized case queue, evidence-linked case creation, assignment/SLA, immutable events, controlled transitions, source labels, and case-linked time-bounded exception requests.                                                                                                              |
| CISO journey               | Coverage-qualified overview, versioned policy register, exception approvals with rationale and separation of duties, remediation ownership, and live-source report manifests/digests.                                                                                                                |
| Destructive actions        | Preview/execute separation, optimistic graph-version check, durable idempotency journal, conflict detection, restart recovery, deletion certificates, actor attribution.                                                                                                                             |
| Governed memory integrity  | Graph-to-content hash verification, fail-closed corruption handling, valid interrupted-redaction recovery, fsync-safe registries and device state.                                                                                                                                                   |
| RAG safety                 | Citation allowlist enforcement for streaming and non-streaming output, mixed valid/invented citation rejection, honest abstention support.                                                                                                                                                           |
| Agent integrations         | Cursor and Claude Code hooks, MCP tools, namespaced locked local state, bounded offline queues, retry commands, recording-only device credentials.                                                                                                                                                   |
| Developer sessions         | Owner-scoped connected-agent sessions, transactional ordered event ingestion, replay conflicts, projection health, package-policy history, restart reconciliation, and exact activity-to-HyperMesh correlation.                                                                                      |
| Product UI                 | Enterprise sign-in boundary, Developer-first Sessions/Activity/Security workspace, authoritative role landing, capability-scoped navigation and route guards, complete Analyst and CISO workspaces, terminal WebSocket session states, accessible graph table alternative, responsive layouts, and recoverable loading/error states. |
| Runtime hardening          | Non-root containers, read-only filesystems, dropped capabilities, internal-only API network, exact CORS configuration, CSP and related browser headers, production health checks.                                                                                                                    |
| Operations                 | Quiesced backup, checksums, encryption hooks, clean restore, state/audit verification, retention and legal hold, DR drill, upgrade preflight, rollback handoff.                                                                                                                                      |
| Release engineering        | Hash-locked Python dependencies, frozen pnpm lockfile, pinned container bases, CI tests/builds, SBOM, OSV and Trivy gates, image checksums.                                                                                                                                                          |

## Validation evidence

| Gate                                   | Result                                                                                                                                                                                                                                       |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frontend TypeScript check              | Passed                                                                                                                                                                                                                                       |
| Frontend deterministic lint            | Passed                                                                                                                                                                                                                                       |
| Frontend Vitest                        | **8 files, 21 tests passed**                                                                                                                                                                                                                 |
| Frontend production build              | Passed; largest generated chunk approximately **479 kB** before gzip                                                                                                                                                                         |
| Complete Python/API suite | **486 passed, 1 skipped** |
| Focused Developer session suites       | **102 passed** across the ledger, projection, shared protocol, Cursor adapter, Claude Code adapter, and recorder                                                                                                                             |
| Native HyperMesh suites                | **20 + 119 + 28 + 37 + WAL recovery + 18** checks passed                                                                                                                                                                                     |
| JavaScript production dependency audit | **No known vulnerabilities found**                                                                                                                                                                                                           |
| Python locked dependency audit         | **No known vulnerabilities found** after upgrading FastAPI and PyJWT                                                                                                                                                                         |
| Production Compose rendering           | Passed; only the web service publishes a host port                                                                                                                                                                                           |
| API production image                   | Built successfully; non-root; healthy in engine mode with durable workflow/session state and OIDC enabled                                                                                                                                    |
| Web production image                   | Rebuilt from the Developer UI branch; ran as UID/GID `101:101`; root, API proxy, CSP, X-Frame-Options, and X-Content-Type-Options smoke passed                                                                                                  |
| Browser security headers               | CSP, Permissions-Policy, Referrer-Policy, X-Content-Type-Options, and X-Frame-Options present                                                                                                                                                |
| Browser identity boundary              | OIDC-configured image showed the company sign-in gate and no local identity picker                                                                                                                                                           |
| Developer frontend live browser        | Developer root redirected to `/developer/sessions`; owner-scoped live sessions, ordered Activity, policy Security, projection state, and governed-run links rendered against the hosted API with no console errors. A 390 × 844 responsive pass verified scroll behavior without clipped cards. |
| Live model execution                   | `gpt-5-mini` completed “Build a small language model training pipeline in Python” as run `e639`: 250-line `main.py`, 4 classes, 9 governed memories, resolved `torch@2.14.0`, and 0 scanner findings.                                        |
| Model failure handling                 | Empty or unsupported provider responses are rejected explicitly; the failed state and redacted reason persist across reloads and render as an actionable run banner.                                                                         |
| Live Cursor adapter                    | Final hosted lifecycle opened session `ses_8424dfb14d95dcd23acda2628607c422`, committed and projected six ordered activities, persisted an `httpx@0.27.2` policy evaluation, completed run `1030`, and projected the edited `app.py` module into HyperMesh. |
| Developer session API image smoke      | Rebuilt API image ran as `meshagent:meshagent`, opened real-engine run `1cdb`, accepted ordered sequence 2, acknowledged through 2, and exposed final retry metadata with projected state.                                                     |

Validated local image identifiers:

- `meshagent-api:developer-sessions`: `sha256:52c3d15bcd2ea05bfa1fa82c4679aa9ec3b56590cb2d6ef2a37319aff79641c7`
- `meshagent-web:developer-sessions-ui`: `sha256:38570a9a5a540503e8b130ab38049aa4b66842e71367e2e910746c36f1542dac`

These local identifiers are build evidence, not registry release references. CI or the deployment pipeline must record immutable registry digests for the promoted images.

## Deployment-owned go-live gates

Code completion cannot substitute for customer-environment acceptance. Before production traffic, the deployment owner must complete the following:

1. Configure a real OIDC issuer, API audience, browser public client ID, callback URI, scopes, role claim, and distinct Analyst/CISO groups.
2. Terminate TLS at an approved ingress and restrict network paths, egress destinations, and exact browser origins.
3. Mount a customer-controlled durable state volume and validate ownership, capacity, monitoring, and backup access.
4. Configure secret management for model credentials, backup encryption, recovery identities, and any external feeds.
5. Complete role-based acceptance with real Developer, Analyst, and CISO accounts, including safe return paths, expiry/logout, negative authorization, non-self-approval, and WebSocket revocation tests.
6. Pair and revoke a test recorder device and verify denied use after revocation.
7. Create, verify, restore, and application-test an encrypted backup in an isolated recovery environment.
8. Connect logs and health metrics to owned alerts and execute the documented rollback decision path.
9. Record promoted image digests, SBOM, vulnerability scan evidence, approvals, and any accepted exceptions.

## Supported v1 boundary

This release supports a **single-tenant, customer-operated deployment** with one writer process per state directory. It does not claim a managed multi-tenant SaaS control plane, active-active high availability, external audit notarization, scheduled report delivery, signed PDF evidence packs, managed KMS, refresh-token rotation, continuous IdP deprovision events, or deletion from downstream copies and backups. Those are explicit post-v1 platform capabilities or deployment responsibilities.

For installation and operation, use [README.md](README.md), [Production Operations](docs/PRODUCTION_OPERATIONS.md), [Security Policy](SECURITY.md), and [Support Policy](SUPPORT.md).
