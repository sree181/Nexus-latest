# Priority 5E: Release Qualification and Demo Packaging

**Status:** Completed on 24 September 2026
**Scope:** Integrated Priority 5A–5D release, production images, recovery, real-data demo path, documentation, and distribution package

## 1. Release result

Priority 5E qualifies the combined Developer, Analyst, and CISO product as a **controlled client-demo release** and as a **production-ready single-tenant software candidate**. Customer-environment acceptance remains mandatory before go-live.

The final source passed the repository release gate, fresh API and web image builds, non-root runtime checks, EngineGateway health, durable-state validation, web-to-API proxying, Developer denial on a CISO lifecycle endpoint, checksum-verified backup, clean-target restore, and application health from restored state.

## 2. Container evidence

The final Dockerfiles built these local images from the release source:

| Image | Local digest | Runtime user | Result |
| --- | --- | --- | --- |
| `meshagent-api:p5e` | `sha256:7bc639bee5ae27a9112cc7e334f0175d9a6cf0c84561969e735a910c7e43bd71` | `meshagent:meshagent` | Healthy in Engine mode with durable state |
| `meshagent-web:p5e` | `sha256:6768efc3d7157a99981c0e27c17c89159b8548a3c82815d63e0a2583bd586be9` | `101:101` | SPA and `/api` proxy passed |

The sandbox kernel lacked the `iptables` raw table required by the Docker bridge endpoint. The final images were therefore built and run with Docker host networking for qualification. This is a build-host limitation, not a product fallback. The Compose configuration still renders under the release gate; the client Mac path uses Docker Desktop networking and must be rehearsed before the meeting.

## 3. Recovery evidence

The running release containers were stopped before backup. State from the qualified API volume was copied to an isolated host directory, backed up with operator-attested offline consistency, verified by checksum and state inspection, restored into a new empty directory, and mounted into the final API image. The restored API returned healthy `EngineGateway` and durable-state status.

Backup evidence: `20260925T013608Z-priority5e`. This sandbox rehearsal was unencrypted and contained fresh qualification state; customer production acceptance must exercise the configured encryption/decryption hooks and recovery identities.

## 4. Real-data demo package

The release includes:

- Mac setup for Docker, CLI pairing, and portable Cursor project hooks;
- a read-only live-demo preflight;
- a 25-minute real Cursor and GitHub runbook;
- a presenter checklist and honest contingency path;
- a light, editable enterprise PowerPoint using current product screenshots;
- an archive manifest, validation report, source checksum, and commit-based package.

The demo uses a real local Git clone, Git remote, branch, commit, normal Cursor activity, and optional normal Git push. Version 1 does not claim GitHub pull-request or issue webhook ingestion.

## 5. Acceptance boundary

The release is ready for a controlled client demonstration. Production deployment still requires customer OpenID Connect, TLS ingress, network controls, customer-controlled durable storage, encrypted backups, monitoring, incident ownership, retention approval, and customer acceptance testing. The supported v1 topology remains single-tenant with one writer per state directory.
