# MeshAgent Production v1 Release Validation Report

**Release candidate:** `manus/production-v1`  
**Baseline version:** `0.1.0`  
**Validation date:** 2026-09-23  
**Code status:** Production-ready single-tenant release candidate. Customer-environment acceptance remains required before go-live.

## Executive result

MeshAgent now has a coherent production baseline across its React web application, FastAPI control plane, editor/MCP adapters, governed-memory engine, native HyperMesh core, durable operations, security controls, backup and recovery tooling, and release pipeline. The implementation fails closed when production identity, storage, audit, engine, or browser-origin requirements are absent.

The release was validated from locked dependencies and rebuilt native code. Both production images were built and run as non-root users. The browser-facing image successfully proxied the API, returned the required browser security headers, and showed the OIDC sign-in boundary rather than the local identity switcher.

## Implemented production controls

| Area | Production-v1 outcome |
|---|---|
| Identity and authorization | OIDC JWT verification, PKCE browser flow, analyst group mapping, device-token scope separation, developer data isolation, production rejection of local asserted identities. |
| Destructive actions | Preview/execute separation, optimistic graph-version check, durable idempotency journal, conflict detection, restart recovery, deletion certificates, actor attribution. |
| Governed memory integrity | Graph-to-content hash verification, fail-closed corruption handling, valid interrupted-redaction recovery, fsync-safe registries and device state. |
| RAG safety | Citation allowlist enforcement for streaming and non-streaming output, mixed valid/invented citation rejection, honest abstention support. |
| Agent integrations | Cursor and Claude Code hooks, MCP tools, namespaced locked local state, bounded offline queues, retry commands, recording-only device credentials. |
| Product UI | OIDC sign-in gate, route-level error boundaries, reconnecting WebSocket state, accessible graph table alternative, responsive tables, device-management error/loading states. |
| Runtime hardening | Non-root containers, read-only filesystems, dropped capabilities, internal-only API network, exact CORS configuration, CSP and related browser headers, production health checks. |
| Operations | Quiesced backup, checksums, encryption hooks, clean restore, state/audit verification, retention and legal hold, DR drill, upgrade preflight, rollback handoff. |
| Release engineering | Hash-locked Python dependencies, frozen pnpm lockfile, pinned container bases, CI tests/builds, SBOM, OSV and Trivy gates, image checksums. |

## Validation evidence

| Gate | Result |
|---|---|
| Frontend TypeScript check | Passed |
| Frontend deterministic lint | Passed |
| Frontend Vitest | **4 files, 7 tests passed** |
| Frontend production build | Passed; largest generated chunk approximately **479 kB** before gzip |
| Complete Python/API suite | **436 passed, 1 skipped** |
| Native HyperMesh suites | **20 + 119 + 28 + 37 + WAL recovery + 18** checks passed |
| JavaScript production dependency audit | **No known vulnerabilities found** |
| Python locked dependency audit | **No known vulnerabilities found** after upgrading FastAPI and PyJWT |
| Production Compose rendering | Passed; only the web service publishes a host port |
| API production image | Built successfully; non-root; healthy in engine mode with durable state and OIDC enabled |
| Web production image | Built successfully; non-root; healthy; API proxy passed |
| Browser security headers | CSP, Permissions-Policy, Referrer-Policy, X-Content-Type-Options, and X-Frame-Options present |
| Browser identity boundary | OIDC-configured image showed the company sign-in gate and no local identity picker |

Validated local image identifiers:

- `meshagent-api:v1`: `sha256:b9c7b541e46b3cca03ccbb7723415925550ea53c633eab772368c7a9ec623316`
- `meshagent-web:v1`: `sha256:b857bd78c663e49dbe7f1f5cf66317d32292767b35f6e13aa4f3998228c256e1`

These local identifiers are build evidence, not registry release references. CI or the deployment pipeline must record immutable registry digests for the promoted images.

## Deployment-owned go-live gates

Code completion cannot substitute for customer-environment acceptance. Before production traffic, the deployment owner must complete the following:

1. Configure a real OIDC issuer, API audience, browser public client ID, callback URI, role claim, and analyst groups.
2. Terminate TLS at an approved ingress and restrict network paths, egress destinations, and exact browser origins.
3. Mount a customer-controlled durable state volume and validate ownership, capacity, monitoring, and backup access.
4. Configure secret management for model credentials, backup encryption, recovery identities, and any external feeds.
5. Complete role-based acceptance with real developer and analyst accounts, including negative authorization tests.
6. Pair and revoke a test recorder device and verify denied use after revocation.
7. Create, verify, restore, and application-test an encrypted backup in an isolated recovery environment.
8. Connect logs and health metrics to owned alerts and execute the documented rollback decision path.
9. Record promoted image digests, SBOM, vulnerability scan evidence, approvals, and any accepted exceptions.

## Supported v1 boundary

This release supports a **single-tenant, customer-operated deployment** with one writer process per state directory. It does not claim a managed multi-tenant SaaS control plane, active-active high availability, external audit notarization, managed KMS, or deletion from downstream copies and backups. Those are explicit post-v1 platform capabilities or deployment responsibilities.

For installation and operation, use [README.md](README.md), [Production Operations](docs/PRODUCTION_OPERATIONS.md), [Security Policy](SECURITY.md), and [Support Policy](SUPPORT.md).
