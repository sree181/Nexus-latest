# MeshAgent Production v1 Package Manifest

This archive contains the complete portable source for the customer-operated MeshAgent v1 control plane and its Developer, Analyst, and CISO workflows. It is produced from a Git commit, not from a working-directory copy.

## Included source

| Path | Contents |
| --- | --- |
| `apps/web/` | React, TypeScript, Vite, TanStack Query/Router, role workspaces, responsive design, and Nginx production image |
| `services/api/` | FastAPI control plane, identity boundary, durable workflows, migrations, adapters, projection workers, and tests |
| `services/engine/` | MeshAgent engine bindings, native HyperMesh core, tests, and native evidence writers |
| `packages/` | Shared graph and UI contracts |
| `adapters/` | Cursor, Claude Code, and Model Context Protocol integration assets |
| `cli/` and `pyproject.toml` | Installable MeshAgent recorder CLI and shared protocol/state modules |
| `scripts/ops/` | Backup, verification, restore, retention, recovery drill, upgrade preflight, and rollback tools |
| `scripts/release/` | Authoritative release validation |
| `scripts/demo/` | Read-only live-demo preflight |
| `docs/` | Architecture, workflow, production, Priority 5, and client-demo guides |
| Root release files | README, demo entry point, changelog, security/support policies, license, notices, Compose files, lockfiles, package manifest, and validation report |

## Deliberately excluded

The archive excludes Git metadata, dependency directories, build output, caches, Python bytecode, local environment files, credentials, tokens, browser state, logs, Docker volumes, HyperMesh runtime state, SQLite databases, backups, screenshots containing local data, and other machine-specific content.

## Restore and build

The source archive preserves executable bits and file paths from the release commit. Dependencies are restored from `pnpm-lock.yaml` and `services/api/requirements.lock`. The simplest local demonstration build is:

```bash
docker compose up --build -d
scripts/demo/verify-live-demo.sh http://localhost:8080
```

For non-container development, follow `README.md`. For a real Cursor and GitHub demonstration, start with `DEMO_README.md` and follow `docs/demo/MAC_LIVE_DEMO_SETUP.md` plus `docs/demo/LIVE_CLIENT_DEMO_RUNBOOK.md`.

## Verification

The matching `.sha256` file verifies the archive. The release report records frontend, API, native engine, dependency, Compose, production-image, migration, recovery, browser, and role-isolation evidence. A client deployment must still complete its own identity, storage, backup, network, and acceptance gates.

## References

[1]: ./README.md "MeshAgent production v1 architecture"
[2]: ./DEMO_README.md "End-to-end client demo package"
[3]: ./RELEASE_VALIDATION_REPORT.md "MeshAgent release validation report"
[4]: ./docs/PRODUCTION_OPERATIONS.md "Production operations runbook"
