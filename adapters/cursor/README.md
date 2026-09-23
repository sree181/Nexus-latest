# MeshAgent recorder for Cursor

The second adapter, and therefore the one that turns "the engine never learns
which editor it is talking to" from a claim into something a test can check.
`test_cursor_adapter.py` asserts both adapters emit identical events for the
same developer behaviour.

## Install

Copy `hooks.json` into `.cursor/hooks.json`, then:

```bash
export MESHAGENT_API=https://meshagent.internal    # default http://localhost:8000
python cli/meshagent.py login --label "work laptop"
```

Opt the repository in with `.meshagent.json` at its root:

```json
{ "record": true, "exclude": ["secrets/*", "*.pem"], "gate": true }
```

Absent means absent. Source code leaving a developer's machine is a
works-council conversation before it is an engineering one.

## What it records

| Cursor event | Becomes |
| --- | --- |
| First `beforeSubmitPrompt` of a conversation | `session` — opens the run |
| `afterFileEdit` | `code` — the file as it now stands on disk |
| `afterShellExecution` | `tool`, plus `package` for any exactly-pinned install |
| `beforeShellExecution` | a gate check on every package the command installs |
| `sessionEnd` | `session` with `ends`, completing the run |

## Four things Cursor does differently

These are the reasons this is a translation rather than a copy of the Claude
Code adapter, and each one is a real bug avoided.

**Silence is not neutral.** Cursor treats unparseable or schema-invalid
output from a permission hook as a *denial*, even with `failClosed` off.
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
