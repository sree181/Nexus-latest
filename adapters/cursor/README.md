# meshAgent recorder for Cursor

The second adapter, and therefore the one that turns "the engine never learns
which editor it is talking to" from a claim into something a test can check.
`test_cursor_adapter.py` asserts both adapters emit identical events for the
same developer behaviour.

## Install

### Stage 1A native recorder (recommended)

Build and install the native per-user service, then point this repository's
Cursor hooks at the small IPC client:

```bash
scripts/recorder/install-dev.sh
scripts/demo/configure_cursor_hooks.py /path/to/repository \
  --native-hook "$HOME/.local/bin/meshagent-hook" \
  --local-exclude
```

Windows PowerShell performs installation, task registration, health verification, and repository configuration in one command:

```powershell
.\scripts\recorder\Install-Dev.ps1 -Repository C:\path\to\your-repository
```

The safe configurator writes a `.cmd` wrapper and Windows-native hook commands; `adapters/cursor/hooks.native.windows.json` is the corresponding reference shape.

The native service uses the same endpoint and paired device credential as the
CLI. It persists ordered batches in an AES-256-GCM encrypted SQLite queue and
removes a batch only after the existing v1 API explicitly acknowledges its
final sequence. The installer is for Stage 1A development: signed/notarized
packages and OS-keystore queue keys belong to Stage 1B.

### Python compatibility mode

Install the CLI once, configure the deployment origin, and pair this machine:

```bash
python3 -m pip install /abs/path/to/meshagent-production-v1
meshagent config set https://meshagent.internal
meshagent login --label "work laptop"
meshagent doctor
```

Then merge `hooks.json` into `.cursor/hooks.json`. The compatibility hook resolves
the endpoint and recording credential from `~/.meshagent`; an explicit
`MESHAGENT_API` remains available as a validated CI override. Do not place a
human OIDC token in `MESHAGENT_TOKEN`: only `mesh_...` recording tokens are
accepted there.

Opt the repository in with `.meshagent.json` at its root:

```json
{ "record": true, "exclude": ["secrets/*", "*.pem"], "gate": true }
```

Absent means absent. Source code leaving a developer's machine is a
works-council conversation before it is an engineering one.

## What it records

| Cursor event                                 | Becomes                                               |
| -------------------------------------------- | ----------------------------------------------------- |
| First `beforeSubmitPrompt` of a conversation | `session` — opens the run                             |
| Later `beforeSubmitPrompt` events             | `prompt` — records each developer-authored turn       |
| `afterFileEdit`                              | `code` — the file as it now stands on disk            |
| `afterShellExecution`                        | `tool`, plus `package` for any exactly-pinned install |
| `beforeShellExecution`                       | a gate check on every package the command installs    |
| `sessionEnd`                                 | `session` with `ends`, completing the run             |

These editor events are translated into the versioned Developer session API:

- `POST /api/v1/developer/sessions` opens the authenticated session.
- `POST /api/v1/developer/sessions/{id}/events` appends strictly ordered activity.
- Package-gate answers are mirrored as `policy.evaluated` events before the
  corresponding shell completion is recorded.

The server commits activity before projecting it into HyperMesh. Delivery and
projection state therefore remain separately observable.

## Four things Cursor does differently

These are the reasons this is a translation rather than a copy of the Claude
Code adapter, and each one is a real bug avoided.

**Silence is not neutral.** Cursor treats unparseable or schema-invalid
output from a permission hook as a _denial_, even with `failClosed` off.
Claude Code treats a silent hook as "carry on". So `beforeShellExecution`
here always prints an explicit decision, including when allowing — and it
prints one even when the JSON on stdin could not be parsed at all. An adapter
that stayed quiet would block every shell command in the editor.

**The run is keyed on `conversation_id`.** `session_id` is documented only on
`sessionStart` and `sessionEnd`; `conversation_id` is stable across turns.
Keying on the wrong one splits a single piece of work across several runs.

**`cwd` is not always present.** It is documented for the shell and tool
hooks but not for `afterFileEdit`, so module paths fall back to
`workspace_roots[0]`. Using the process's own cwd would file code under
whatever directory Cursor happened to launch from.

**An edit is a diff.** `afterFileEdit` carries `old_string`/`new_string`
pairs. The file is read back off disk instead, because the governed record
should be what the developer actually has, not this adapter's idea of what
applying the edit produced.

## What it refuses

Identical to the Claude Code adapter, deliberately. It denies a shell command
only when MeshAgent answers `block` — never on a timeout, a connection error,
or a verdict it does not recognise. The policy lives in the deployment and
travels with the refusal. Set `"gate": false` to keep the recorder and drop
the refusals.

## Pair it with the MCP server

The hooks record what happened and never why. `adapters/mcp` lets the agent
volunteer the reasoning, and a file becomes explained only because the agent
named it — never because of when it was written.

## Offline recovery

In native mode, recorder batches are kept in the encrypted
`~/.meshagent/recorder.db` queue and replayed by the per-user daemon. In Python
compatibility mode they remain in bounded JSONL queues under
`~/.meshagent/queues/<repository>/<editor>/`. Both implementations retain the
oldest undelivered causal prefix, including the session opener; refuse new
observations rather than create a sequence gap when full; and remove data only
after an exact server acknowledgement. Semantic API validation failures remain
encrypted and visible as `blocked_batches` rather than being hot-retried or
deleted. Operators can inspect and explicitly retry them after correcting the
cause without exposing credentials:

```bash
meshagent-recorder status  # native mode
meshagent-recorder replay  # native mode
meshagent status           # Python compatibility mode
meshagent replay           # Python compatibility mode
```
