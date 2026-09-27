# Stage 1A — Native enterprise recorder foundation

## Status

Stage 1A implements the native local recording path for Cursor while preserving the existing FastAPI, server-owned identity and authorization, Developer session state machine, and HyperMesh projection semantics.

The implementation intentionally does **not** claim corporate enrollment, DPoP device trust, OS-keystore queue keys, production code signing, notarization, or MDM distribution. Enrollment and OS-backed key trust belong to Stage 1B; signed packages and managed distribution begin in Stage 1C.

## Runtime architecture

```text
Cursor hook event
    |
    v
meshagent-hook (short-lived, fail-open)
    |
    | private local IPC
    v
meshagent-recorder (one per OS user)
    |              |
    |              +-- package gate request (bounded deadline)
    |
    +-- AES-256-GCM encrypted SQLite queue
            |
            | ordered, acknowledgement-driven replay
            v
existing FastAPI Developer session endpoints
            |
            v
existing ordered HyperMesh projection and governed workflows
```

## Shipped components

| Component | Responsibility |
|---|---|
| `cmd/meshagent-hook` | Bounded stdin parsing, private IPC request, valid Cursor permission JSON, explicit health probe |
| `cmd/meshagent-recorder` | Per-user daemon, Cursor translation, queue replay, redacted status, single-writer ownership |
| `internal/ipc` | Length-prefixed protocol; same-UID Unix socket or current-user Windows named-pipe ACL |
| `internal/cryptoqueue` | SQLite WAL, atomic sequence allocation, AES-256-GCM envelopes, leases, acknowledgements, queue bounds |
| `internal/protocol` | Existing `meshagent.session.v1` payloads, Python-compatible idempotency keys, strict acknowledgement checks |
| `internal/cursorhook` | Repository opt-in, exclusions, symlink boundary, package evidence, gate decisions, bounded fields |
| `internal/installparse` | Non-executing, conservative package command parser shared with Python through a golden corpus |
| `internal/service` | Opener-first ordered replay with bounded retry, retained validation blocks, and redacted diagnostics |
| `internal/singleton` | Exclusive per-user writer ownership across daemon starts |

## Existing backend contracts

No fake endpoint or parallel authorization model was introduced. The recorder uses only:

- `POST /api/v1/developer/sessions`
- `POST /api/v1/developer/sessions/{session_id}/events`
- `POST /api/gate/package`

Device credentials are recording-only server credentials. Local `X-MeshAgent-User` remains a development-only adapter. Credential files are re-read for each request so device rotation or logout does not require a daemon restart.

## Ordering and durability invariants

1. The encrypted session opener is committed before any activity batch.
2. Sequence reservation and encrypted batch insertion occur in one SQLite transaction.
3. Concurrent hooks receive unique contiguous sequence ranges.
4. The absolute oldest causal prefix is leased first; a retry delay never allows a newer sequence to bypass it.
5. A batch is deleted only after an acknowledgement covers its final sequence.
6. Expired leases return to the queue after process termination.
7. Queue limits are global per OS user. Backpressure rejects the new observation, rolls back its sequence reservation, and increments a durable session or recorder-level counter.
8. Session close is atomic with its final event; duplicate close and post-close observations cannot consume a sequence.
9. Semantic API validation failures remain encrypted as visible `blocked_batches`; an explicit replay can retry them after remediation.
10. Sender failures never remove queued evidence.
11. SQLite uses WAL, foreign keys, a busy timeout, and `synchronous=FULL`.

## Privacy and security invariants

- Recording requires repository-local `.meshagent.json` with `record: true`.
- Exclusion patterns match nested paths with Python `fnmatch` semantics.
- Canonical-path checks reject traversal and symlinks outside the opted-in repository; platform file handles refuse final-component symlinks/reparse points while enforcing the capture-size bound.
- Prompt, source, command, package, policy, and session-open bodies are stored only as authenticated ciphertext in the queue.
- AES-GCM nonces are generated independently for every record and the record identity is bound as additional authenticated data.
- Unix IPC checks the peer UID; Windows IPC uses a current-user/system/administrators DACL.
- IPC frames are length-bounded before allocation, strictly decoded, time-bounded before the first byte, and capped at 64 concurrent connections.
- A single-writer lock prevents a second daemon from replacing the active socket or writing the queue.
- Status and logs never print tokens, queue keys, prompts, source code, commands, package names, or server response bodies.
- Human OIDC/read tokens are refused as recording credentials.

## Cursor failure semantics

- Ordinary event recording is fail-open: the editor is not interrupted if the daemon or API is unavailable.
- `beforeShellExecution` always returns valid permission JSON.
- A package command is denied only for an explicit server `block` verdict.
- Gate timeout, transport failure, malformed response, or unknown verdict permits execution and does not create a false block.
- Native onboarding separately calls `meshagent-hook --health`; a fail-open permission response cannot falsely certify daemon connectivity.

## Platform integration

- macOS development installation: per-user LaunchAgent.
- Linux development installation: hardened per-user systemd unit.
- Windows development installation: least-privilege logon scheduled task, protected named pipe, protected current-user file ACLs, daemon health verification, `.cmd` hook wrapper, and repository configuration.
- CI cross-compiles the recorder and hook for Linux amd64/arm64, macOS amd64/arm64, and Windows amd64.

## Validation

The committed gates cover:

- Go race detector, unit/integration tests, vet, and cross-platform compilation;
- Go 1.25.13 minimum enforcement and a zero-finding `govulncheck` call-graph scan;
- a shared Go/Python package-parser corpus;
- exact Python-compatible idempotency key;
- encrypted-at-rest and tamper rejection tests;
- concurrent queue admission, causal retry ordering, lease recovery, global backpressure, durable close, retained validation failure, and partial-ack rejection;
- IPC framing and Unix same-user path;
- Cursor opt-in, nested exclusions, symlink escape, fail-open and explicit deny behavior;
- configurator preservation, stale-entry normalization, backup, idempotency, opt-out refusal, symlink refusal, daemon-health enforcement, and platform-specific wrapper generation;
- the complete existing frontend, API, and native HyperMesh release suite.

A live Stage 1A acceptance test additionally exercises Cursor-shaped events through native IPC, the encrypted queue, the real FastAPI API, and projected HyperMesh records, including a real OSV-backed `requests==2.19.0` block and encrypted offline replay.

## Enterprise controls after Stage 1A

- macOS Keychain, Windows DPAPI/CNG, and Linux secret-service/TPM queue-key binding;
- corporate device enrollment, proof-bound short-lived credentials, signed device policy, credential rotation telemetry, and administrator trust controls (Stage 1B);
- Apple Developer ID/notarization, Windows Authenticode, signed Linux packages, release provenance, MDM deployment, and uninstall packages (Stage 1C).
