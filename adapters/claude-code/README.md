# The Claude Code recorder

Makes an agent your developers actually use write into governed memory.

MeshAgent's own build loop records perfect provenance and almost nobody runs
it. This hook records what Claude Code really does, so the graph describes the
estate rather than the slice this product happens to drive itself. Memory that
arrives this way is indistinguishable downstream from memory the built-in loop
wrote — the same security walk, the same SBOM, the same `why` chain — because
both go through the same recorder.

## Install

Copy the hook configuration into the repository you want recorded:

```bash
mkdir -p .claude
# merge adapters/claude-code/settings.json into .claude/settings.json
```

Then opt the repository in. Nothing is recorded until this file exists:

```json
{
  "record": true,
  "exclude": ["*.env", "secrets/*", "*.pem", "fixtures/*"]
}
```

Save it as `.meshagent.json` at the repository root. Patterns are matched
against the repo-relative path and against the bare filename, so both
`secrets/*` and `*.pem` do what you would expect.

Point the hook at your deployment and register the machine:

```bash
python3 -m pip install /abs/path/to/meshagent-production-v1
meshagent config set-endpoint https://meshagent.internal
meshagent login --label "work laptop"
meshagent doctor
```

`login` prints a short code. Open MeshAgent's **Devices** screen in a browser
you are already signed into, enter the code, and approve it. The hook then
records as you, and picks the token up from `~/.meshagent/credentials.json`
on its own — there is nothing further to export.

The token is scoped to recording. It cannot read a run, see the fleet, or
delete anything, which is what makes it safe to leave in a file on a laptop.
`meshagent logout` revokes it, and revocation takes effect on the next write.

Without a login the hook still records, using `MESHAGENT_USER` as an asserted
name. That works, but the API marks the identity unverified and the coverage
table labels the row **name asserted** rather than presenting it as a person
the security office can act on.

## What it records

| Claude Code event | Becomes |
| --- | --- |
| First `UserPromptSubmit` of a session | `session` — opens the run, with the developer's request as its task |
| `PostToolUse` on `Write`/`Edit`/`MultiEdit`/`NotebookEdit` | `code` — the file as it now stands on disk |
| `PostToolUse` on `Bash` | `tool`, plus `package` for any exactly-pinned install |
| `SessionEnd` | `session` with `ends`, completing the run |
| `PreToolUse` on `Bash` | a gate check on every package the command installs |

## What it refuses

`PreToolUse` is the one hook here that can stop the agent. It asks
`/api/gate/package` about each dependency the command would add, and denies
the tool call only when MeshAgent answers `block` — never on a timeout, a
connection error, or a verdict it does not recognise.

The policy lives in the deployment, not in the hook:

```bash
MESHAGENT_GATE_THRESHOLD=high      # advisories at or above this refuse
MESHAGENT_DENIED_LICENSES=AGPL     # empty by default; a legal question
MESHAGENT_GATE_ON_UNKNOWN=0        # what an unreachable feed means
```

That last one is the honest part. When OSV cannot be reached the verdict is
`unknown`, never `allow`, and by default the install proceeds and is recorded
in the action log as **Install not checked**. Set it to `1` to fail closed
instead. Either way the number of ungated installs is countable rather than
invisible.

Set `"gate": false` in `.meshagent.json` to keep the recorder and drop the
refusals — observing and refusing are different asks, and a team may agree to
the first without the second.

The file is read back from disk rather than reconstructed from the tool
arguments, because `Edit` sends a diff and the governed record should be the
file the developer actually has.

## What it deliberately does not do

**It does not explain the code.** A hook sees a file being written; it does not
see why. Code goes up with no rationale, and MeshAgent records it as explicitly
unexplained rather than inventing a plausible reason. That is what makes "how
much of what our agents write can anyone explain" a number instead of a guess.
Volunteered reasoning is the next layer (MCP), where the agent states it.

**It does not guess a version.** `pip install requests` resolves to whatever
the index serves that minute, so it produces a tool event and no package event.
Only `requests==2.31.0` produces a package event.

**It does not block anything on its own authority.** Every recording path
exits 0. If MeshAgent is down the batch goes to a queue under `~/.meshagent/`
and is delivered, in order, with the next successful post. The one exception is
the `PreToolUse` gate above, and it refuses only what the deployment's policy
refuses — a hook deciding for itself which installs to stop would be the exact
overreach this product argues against.

The queue is bounded by count and bytes and stored under a repository/editor
namespace with process locks. Inspect or replay it without printing secrets:

```bash
meshagent status
meshagent replay
```

**It does not record reads.** Only what changed. The action log says the same
thing on the wire, so its silence is not mistaken for evidence that nobody
looked.

## Tests

The translation logic is unit tested with the payload shapes from the Claude
Code hooks reference:

```bash
cd services/api && PYTHONPATH=../engine .venv/bin/python -m pytest tests/test_claude_code_adapter.py -q
```
