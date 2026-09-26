# meshAgent native recorder — Stage 1A

Stage 1A moves Cursor observation delivery out of short-lived Python hooks and into a native per-user service. The current FastAPI and HyperMesh backend remains authoritative and unchanged.

## Components

- `meshagent-hook`: a small Cursor hook process. It reads one JSON payload from stdin, forwards it over private local IPC, and exits. For `beforeShellExecution` it always emits valid JSON and fails open if the daemon is unavailable.
- `meshagent-recorder`: the per-user daemon. It enforces repository opt-in/exclusions, translates Cursor events, calls the existing package gate, persists encrypted batches, and replays them in sequence.
- `internal/cryptoqueue`: SQLite WAL plus AES-256-GCM per-record authenticated encryption. Sequence reservation and queue insertion share one transaction. A record is deleted only after an exact v1 acknowledgement.
- `internal/ipc`: length-prefixed JSON over a mode-`0600` Unix socket with same-UID peer checks on Linux/macOS, or a current-user ACL named pipe on Windows.

## Existing contracts preserved

The daemon posts the existing `meshagent.session.v1` bodies to:

- `POST /api/v1/developer/sessions`
- `POST /api/v1/developer/sessions/{id}/events`
- `POST /api/gate/package`

The API continues to own authenticated identity, owner scoping, ordering, idempotency, projection, policy, and all downstream Developer → Analyst → CISO workflow state.

## Build and test

Go 1.25.13 or newer is required so released binaries include current standard-library security fixes.

```bash
cd recorder
go test -race ./...
go vet ./...
GOTOOLCHAIN=go1.25.13 go run golang.org/x/vuln/cmd/govulncheck@v1.6.0 ./...
go build ./cmd/meshagent-recorder ./cmd/meshagent-hook
```

The parser corpus in `testdata/package_commands.json` is consumed by both Go and Python tests to prevent language drift.

## Development install

When upgrading from the Python hook, fully quit Cursor and drain its existing queue first:

```bash
meshagent replay --json
```

Proceed only when the result reports `retained: 0`. The installers refuse to continue while a non-empty legacy JSONL queue exists; the native daemon does not silently import or discard that queue.

macOS or Linux:

```bash
scripts/recorder/install-dev.sh
```

Windows PowerShell:

```powershell
.\scripts\recorder\Install-Dev.ps1 -Repository C:\path\to\your-repository
```

The Windows installer builds both `.exe` files, registers and starts the least-privilege logon task, waits for a real daemon health response, writes a native `.cmd` Cursor wrapper, safely merges the hook configuration, and smoke-tests the selected repository. It does not require the binaries to be on `PATH`.

These are explicitly **development** installers. Stage 1B must replace the file-protected development queue key with OS-keystore binding and must code-sign/notarize the binaries before managed enterprise rollout.

On macOS or Linux, configure a repository after the daemon is running:

```bash
meshagent-hook --health
scripts/demo/configure_cursor_hooks.py /path/to/repository \
  --native-hook "$HOME/.local/bin/meshagent-hook" \
  --local-exclude
```

The configurator first requires a successful daemon health response, then preserves existing Cursor hooks, backs up modified configuration, refuses to override a repository opt-out, creates a privacy-first `.meshagent.json`, and smoke-tests an explicit permission response. A fail-open permission response alone never counts as successful setup.

## Configuration and state

The native service reads the same `~/.meshagent/config.json` and `credentials.json` used by the CLI. Only a `mesh_...` device token is accepted as a recorder credential. Human read/OIDC tokens are never used for recording.

Development state:

- database: `~/.meshagent/recorder.db`
- queue key: `~/.meshagent/recorder.queue-key` (`0600`, development only)
- Unix socket: `~/.meshagent/run/recorder.sock`

Environment overrides retain the existing names: `MESHAGENT_API`, `MESHAGENT_TOKEN`, `MESHAGENT_USER`, `MESHAGENT_HOOK_HOME`, `MESHAGENT_QUEUE_MAX_BATCHES`, `MESHAGENT_QUEUE_MAX_BYTES`, and `MESHAGENT_HOOK_MAX_BYTES`.

## Diagnostics

```bash
meshagent-recorder status
meshagent-recorder replay
```

Status reports paths, endpoint source, credential presence, queue counts, queue bytes, validation-blocked batches, and backpressure counters. It never prints tokens, queue keys, prompts, source, commands, package names, or policy details. A semantic API validation failure remains encrypted and visible instead of being deleted or hot-retried; after correcting the cause, `meshagent-recorder replay` explicitly retries retained blocked batches.

## Security and failure behavior

- No repository recording occurs without `.meshagent.json` containing `"record": true`.
- Excluded files and files outside the repository are never read into queue payloads.
- Ordinary telemetry is fail-open for developer work; unavailable delivery remains queued.
- Package installation is denied only for an explicit server `block` verdict. Timeout, connection failure, malformed response, or unknown verdict permits execution.
- The gate deadline is 4.5 seconds, bounded below Cursor's six-second permission-hook timeout.
- Queue bounds preserve the oldest causal prefix. A rejected observation does not consume a sequence number and is counted as backpressure.
- Queue leases survive daemon termination and are eligible for replay after expiry.

## Stage 1B boundary

Stage 1A does **not** claim enterprise distribution or signing. Stage 1B adds Apple Developer ID signing/notarization, Windows Authenticode, managed deployment artifacts, OS keystore-backed queue keys, enrollment certificates, policy bundles, rotation, and administrator fleet telemetry.
