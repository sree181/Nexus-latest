# MeshAgent Support Boundary

MeshAgent production v1 is a repository-delivered, customer-operated single-tenant application. This document defines what information a maintainer or deployment operator needs to triage an issue and what remains the responsibility of the customer operating the environment. It does not create a service-level agreement, managed-service commitment, or 24/7 support obligation.

## Request routing

| Need | Route | Include | Do not include |
|---|---|---|---|
| Defect, documentation correction, or feature discussion | Repository issue or approved engineering tracker | Release/commit, mode, minimal reproduction, expected/actual behavior, sanitized logs | Tokens, passwords, private keys, recovery identities, customer data, raw prompts, or full source bodies |
| Deployment assistance | Customer's platform or operations channel | Hosting platform, ingress pattern, state-storage class, sanitized `/api/health`, error class, time window | Credentials, state archives, decrypted backups, or configuration secrets |
| Backup, restore, recovery, or rollback question | Operations owner using the [production runbook](docs/PRODUCTION_OPERATIONS.md) | Backup ID, manifest/verification result, release, restore target class, drill result | Encryption identities, plaintext backup payloads, or legal-hold details in a public tracker |
| Security concern | **Private path only** | Follow [SECURITY.md](SECURITY.md) | Any public issue or discussion post |
| Data deletion or legal hold | Customer privacy, legal, records, and platform owners | Request authority, scope, hold status, approved case reference | Public disclosure of data subject or evidence details |

## Supported repository scope

The current v1 repository scope includes the FastAPI API, shipped web application, bundled engine integration, current Cursor/Claude Code/MCP adapters, documented operations scripts in `scripts/ops`, and authoritative documentation. Triage can help reproduce behavior in the published modes and clarify intended repository behavior.

The following are deployment responsibilities unless a separate written agreement says otherwise: TLS termination and ingress firewalling; cloud or host security; OIDC tenant, claim, group, and lifecycle configuration; endpoint security; container orchestration; volume durability and encryption; backups, recovery keys, retention, legal holds, and external replication; monitoring, alert routing, and on-call staffing; vulnerability remediation of the host and dependencies; network egress; external model/provider accounts; and regulatory, contractual, privacy, or records decisions.

The included `docker compose` configuration is designed for local convenience. It is not a complete production reference architecture, HA solution, network policy, secret-management system, or compliance baseline. The application is supported in its documented production **single-tenant EngineGateway mode** only after the deployment owner implements the production controls in [docs/PRODUCTION_OPERATIONS.md](docs/PRODUCTION_OPERATIONS.md).

## Triage information

For non-security defects, provide the following sanitized information:

1. MeshAgent release or commit, image digest if applicable, and whether the active gateway is `EngineGateway` or `SampleGateway`.
2. Deployment mode, operating system/runtime, browser or adapter version, and whether the issue is reproducible in an isolated test state.
3. The relevant time window with timezone, request route or operation name, status code, and correlation ID if the deployment provides one.
4. Sanitized error messages and steps to reproduce. Replace identifiers and content that identify people, source code, governed-memory facts, credentials, or customer infrastructure.
5. What changed immediately before the problem: release, identity configuration, storage change, dependency update, backup/restore, or network change.
6. For state concerns, the output category—not the full contents—of `scripts/ops/verify-state.sh` and `scripts/ops/verify-backup.sh`.

Before opening an issue, consult the current [README](README.md), the [production operations runbook](docs/PRODUCTION_OPERATIONS.md), and the [change log](CHANGELOG.md). Material under `docs/archive/` is historical and cannot be used as a current support contract.

## Escalation expectations

A support contact should acknowledge ownership and establish the next action, but response and resolution timing depend on severity, evidence, maintenance capacity, and the deployment owner's ability to provide a safe reproduction. For availability or data-risk events, the customer incident commander remains responsible for containment, business decisions, and communications. For suspected vulnerabilities, use the private process in [SECURITY.md](SECURITY.md).
