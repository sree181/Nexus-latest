#!/usr/bin/env python3
"""MeshAgent recorder for Claude Code.

One hook script, dispatching on the event Claude Code names in its payload.
It translates that payload into the recorder vocabulary and posts it to
/api/recorder. Nothing here knows what MeshAgent does with the result: the
adapter is a translator, and the whole point of the protocol is that it stays
one.

Three rules govern everything below, and they are worth more than the
translation itself.

*It never blocks the developer.* Every path exits 0. If MeshAgent is down,
unreachable, slow, or returns nonsense, the batch goes to a local queue and
the session carries on. A governance tool that stalls somebody's editor gets
uninstalled inside a week, and an uninstalled recorder records nothing.

*It never invents.* A hook sees a file being written; it does not see why.
Code therefore goes up with no rationale attached, and MeshAgent records it as
explicitly unexplained. A package install with no pinned version yields no
package event, because the version would be a guess. The reasoning arrives
later, from the MCP layer, where an agent volunteers it.

*It records nothing until the repository opts in.* Source code leaving a
developer's machine is a works-council conversation before it is an
engineering one, so this is off unless `.meshagent.json` says otherwise, and
that file carries the exclusions.

Standard library only, on purpose: it runs on whatever Python the developer
happens to have, in a hook with a short timeout.
"""

from __future__ import annotations

import fnmatch
import json
import os
import sys
import time
import urllib.error
import urllib.request

try:
    from meshagent_cli import state as local_state
    from meshagent_cli import protocol as session_protocol
except ImportError:  # source checkout, before `pip install meshagent-cli`
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "cli"))
    from meshagent_cli import state as local_state
    from meshagent_cli import protocol as session_protocol

AGENT = "claude-code"
CONFIG = ".meshagent.json"

# Short, because this runs inside somebody's editor. A recorder that adds a
# visible pause to every file write is one the developer turns off.
TIMEOUT = float(os.environ.get("MESHAGENT_HOOK_TIMEOUT", "2.0"))

# Above this a file is queued as-is but not sent inline; a generated bundle or
# a checked-in fixture is not the code anybody governs.
MAX_BYTES = int(os.environ.get("MESHAGENT_HOOK_MAX_BYTES", "200000"))

# Tool calls that mean "a file now says something different".
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# The gate runs *before* the command, so the developer is watching. Kept
# tight for that reason: a check that takes longer than the install is one
# nobody keeps switched on.
GATE_TIMEOUT = float(os.environ.get("MESHAGENT_GATE_TIMEOUT", "3.0"))

INSTALL_MARKERS = ("pip install", "pip3 install", "npm install", "npm i ",
                   "yarn add", "uv add", "poetry add")

# Subcommands and flags that appear between the tool and the package names.
INSTALL_WORDS = {"pip", "pip3", "npm", "yarn", "uv", "poetry", "python",
                 "python3", "install", "add", "i", "-m", "&&", "sudo"}


# -- where this adapter keeps its own state -----------------------------------

def home() -> str:
    """The adapter's state directory. Sessions and the failure queue live
    here, outside any repository, because they are about the developer's
    machine rather than the code."""
    return local_state.home()


def session_path(session_id: str, repository: str | None = None) -> str:
    return local_state.state_path(session_id, repository=repository, editor=AGENT)


def read_session(session_id: str, repository: str | None = None) -> dict:
    found = local_state.read_session(session_id, repository=repository, editor=AGENT)
    # Older callers used the helpers without a workspace.  Only use that
    # compatibility location when the properly namespaced state is absent.
    if not found and repository:
        found = local_state.read_session(session_id, editor=AGENT)
    if not found:
        legacy = os.path.join(home(), f"session-{session_id}.json")
        try:
            with open(legacy, encoding="utf-8") as handle:
                candidate = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            candidate = {}
        if isinstance(candidate, dict) and candidate:
            found = candidate
            write_session(session_id, found, repository=repository)
    return found


def write_session(session_id: str, state: dict, repository: str | None = None) -> None:
    try:
        local_state.write_session(session_id, state, repository=repository, editor=AGENT)
    except OSError:
        pass            # state is an optimisation, not a correctness condition


# -- opting in, and staying out of what was excluded --------------------------

def config(cwd: str) -> dict | None:
    """The repository's recorder settings, or None when it has not opted in.

    Absent means absent: no recording, no error, no nagging. A developer who
    has not agreed to ship their source anywhere should not have to discover
    that they are doing it."""
    try:
        with open(os.path.join(cwd, CONFIG)) as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return cfg if cfg.get("record") is True else None


def excluded(path: str, cfg: dict) -> bool:
    """Whether the repository asked for this path to be left alone.

    Matched against the repo-relative path and the bare filename, so both
    `secrets/*` and `*.pem` behave the way somebody writing that list
    expects."""
    patterns = cfg.get("exclude") or []
    name = os.path.basename(path)
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p)
               for p in patterns)


def module_name(file_path: str, cwd: str) -> str:
    """The repo-relative path, as the module name MeshAgent files it under.

    Kept as a path rather than a dotted module, because the recorder takes
    files in any language and `src/api/handlers.go` is what the developer
    would search for."""
    path = file_path.replace("\\", "/")     # Windows hooks deliver backslashes
    root = cwd.replace("\\", "/").rstrip("/")
    if root and path.startswith(root + "/"):
        path = path[len(root) + 1:]
    return path.lstrip("/")


# -- posting, and what to do when that fails ----------------------------------

def queue_path() -> str:
    # Compatibility helper for callers that previously displayed this path.
    return local_state.queue_path("default", editor=AGENT)


def enqueue(batch: dict, repository: str | None = None) -> None:
    """Hold a batch that could not be delivered.

    The alternative to a queue is dropping the events, which would make the
    recorder quietly lossy in exactly the conditions -- a restarting API, a
    laptop on a train -- where a developer is most likely to be working."""
    try:
        local_state.enqueue(batch, repository=repository, editor=AGENT)
    except OSError:
        pass


def drain(repository: str | None = None) -> list[dict]:
    """Take everything queued, leaving the queue empty.

    Read-and-truncate rather than read-then-delete-on-success: a second hook
    firing while this one is posting would otherwise send the same batches
    again."""
    return local_state.drain_matching(repository=repository, editor=AGENT)


def post(batch: dict) -> dict | None:
    """Send one batch. None means it did not land, and the caller queues it.

    Every failure mode is the same failure mode here -- the developer's agent
    carries on either way -- so they are deliberately not distinguished."""
    return session_protocol.post(batch, headers=_headers(), timeout=TIMEOUT)


def device_token() -> str:
    """The token `meshagent login` left for us, if there is one.

    Read on every post rather than cached, so `meshagent logout` takes effect
    on the next file write instead of whenever the editor happens to
    restart."""
    return local_state.device_token()


def _headers() -> dict[str, str]:
    """Identity for the post, best first.

    The device token is preferred over everything because it is the only one
    of these the server can actually check. The header fallback is kept for
    a developer who has not run `meshagent login` yet -- their writes are
    still recorded, but the API marks the identity unverified and the
    coverage table says so rather than naming them as though it knew."""
    headers = {"Content-Type": "application/json"}
    if token := device_token():
        headers["Authorization"] = f"Bearer {token}"
    elif user := os.environ.get("MESHAGENT_USER", "").strip():
        headers["X-MeshAgent-User"] = user
    return headers


def send(session_id: str, events: list[dict], repository: str | None = None) -> dict | None:
    """Deliver these events, oldest queued batches first.

    Order matters more than throughput: a code event that arrives before the
    session event that opens its run would be refused."""
    try:
        return session_protocol.send(
            agent=AGENT,
            native_session=session_id,
            events=events,
            repository=repository or os.getcwd(),
            headers=_headers(),
            timeout=TIMEOUT,
            post_fn=post,
        )
    except Exception:
        return None


# -- translating what Claude Code did into what MeshAgent records -------------

def on_prompt(payload: dict, cfg: dict) -> list[dict]:
    """The developer said what they want. The first one opens the session.

    Later prompts remain developer-authored source activity. They are recorded
    as prompts, never as agent decisions."""
    del cfg
    state = read_session(payload.get("session_id", ""), payload.get("cwd"))
    prompt = (payload.get("prompt") or "").strip()[:4096]
    if not prompt:
        return []
    if state.get("opened"):
        return [{
            "type": "prompt",
            "prompt": prompt,
            "turn_id": str(
                payload.get("prompt_id") or payload.get("message_id") or ""
            ) or None,
        }]
    return [{"type": "session", "agent": AGENT, "task": prompt}]


def on_tool(payload: dict, cfg: dict) -> list[dict]:
    """A tool finished. Files become code, commands become tool events."""
    tool = payload.get("tool_name") or ""
    args = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()

    if tool in WRITE_TOOLS:
        return _code_events(args, cwd, cfg,
                            payload.get("session_id") or "")
    if tool in ("Bash", "PowerShell"):
        return _command_events(args)
    return []


def claimed_decision(session_id: str, module: str,
                     repository: str | None = None) -> str | None:
    """The decision an agent said explains this file, if it said so.

    Written by the MCP server when the agent calls `record_decision` and
    names the files it covers. Looked up by exact module path and nothing
    else: attaching the most recent decision to the next file written would
    be a guess wearing a provenance record's clothing, which is the worst
    failure available to this product."""
    if not session_id:
        return None
    claims = read_session(session_id, repository).get("claims") or {}
    found = claims.get(module)
    return str(found) if found else None


def _code_events(args: dict, cwd: str, cfg: dict,
                 session_id: str = "") -> list[dict]:
    """The file as it now stands on disk.

    Read back rather than reconstructed from the tool arguments: `Edit` sends
    a diff, and the governed record should be the file the developer actually
    has, not this hook's idea of what applying that diff produced."""
    path = args.get("file_path") or args.get("notebook_path") or ""
    if not path:
        return []
    module = module_name(path, cwd)
    if excluded(module, cfg):
        return []
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return []
        with open(path, encoding="utf-8") as fh:
            code = fh.read()
    except (OSError, UnicodeDecodeError):
        return []           # binary, deleted, or unreadable: say nothing

    event = {"type": "code", "module": module, "code": code}
    # A reason only if the agent volunteered one about *this file*, through
    # the MCP server. Otherwise none: the hook saw the write and not the
    # reason for it, and MeshAgent records that absence rather than this
    # adapter inventing one.
    if because := claimed_decision(session_id, module, cwd):
        event["because"] = because
    return [event]


def _command_events(args: dict) -> list[dict]:
    """A shell command, plus any dependency it pinned.

    An unpinned install yields no package event. `pip install requests`
    resolves to whatever the index serves that minute, and a version this
    adapter guessed would be indistinguishable in the graph from one it
    read."""
    command = (args.get("command") or "").strip()
    if not command:
        return []
    events: list[dict] = [{"type": "tool", "name": "Bash", "detail": command}]
    events += [{"type": "package", "package": name, "version": version}
               for name, version in pinned_packages(command)]
    return events


def pinned_packages(command: str) -> list[tuple[str, str]]:
    """Dependencies the command names with an exact version."""
    if not any(k in command for k in ("pip install", "npm install", "pip3 install")):
        return []
    out: list[tuple[str, str]] = []
    for word in command.split():
        if word.startswith("-"):
            continue
        for sep in ("==", "@"):
            # npm scoped names start with @, which is not a version separator
            name, found, version = word.partition(sep)
            if found and name and version and not word.startswith("@"):
                out.append((name, version))
                break
    return out


def on_session_end(payload: dict, cfg: dict) -> list[dict]:
    """The developer closed the session, so the run is no longer recording."""
    del cfg
    state = read_session(payload.get("session_id", ""), payload.get("cwd"))
    if not state.get("opened"):
        return []       # nothing was ever opened; nothing to close
    return [{"type": "session", "agent": AGENT,
             "task": state.get("task", ""), "ends": True}]


HANDLERS = {
    "UserPromptSubmit": on_prompt,
    "PostToolUse": on_tool,
    "SessionEnd": on_session_end,
}


# -- the one place this adapter refuses ---------------------------------------

def ask_gate(package: str, version: str, session_id: str) -> dict | None:
    """Ask MeshAgent whether this install is allowed. None if it cannot say.

    Deliberately not merged with `post`: that one queues on failure so
    nothing is lost, and queuing a question whose answer arrives after the
    install would be worse than not asking."""
    api = local_state.endpoint()
    req = urllib.request.Request(
        f"{api}/api/gate/package",
        data=json.dumps({"package": package, "version": version,
                         "session": session_id}).encode(),
        headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=GATE_TIMEOUT) as res:
            return json.loads(res.read().decode())
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def on_pre_tool(payload: dict, cfg: dict) -> dict | None:
    """Before a command runs. The only hook here that can say no.

    It blocks on exactly one condition: MeshAgent answered, and answered
    `block`. Not on a timeout, not on a connection error, not on a verdict
    it does not recognise. The policy itself -- including what to do when
    the advisory feeds are down -- lives in the deployment and arrives in
    the verdict, so this file never decides to refuse on its own.

    That asymmetry is deliberate. A recorder that goes quiet during an
    outage loses some memory. A gate that starts refusing during an outage
    stops the company working, and gets removed by lunchtime."""
    if not cfg.get("gate", True):
        return None
    if (payload.get("tool_name") or "") not in ("Bash", "PowerShell"):
        return None
    command = ((payload.get("tool_input") or {}).get("command") or "").strip()
    if not command:
        return None

    session_id = payload.get("session_id") or ""
    for package, version in installs(command):
        got = ask_gate(package, version, session_id)
        repository = payload.get("cwd") or os.getcwd()
        state = read_session(session_id, repository)
        if got is not None and state.get("opened"):
            send(
                session_id,
                [{
                    "type": "policy", "package": package, "version": version,
                    "verdict": got.get("verdict", "unknown"),
                    "reasons": got.get("reasons") or [],
                    "policy": got.get("policy") or "",
                    "worst": got.get("worst"),
                    "unavailable": got.get("unavailable"),
                    "advisories": got.get("advisories") or [],
                }],
                repository,
            )
        if got is None or got.get("verdict") != "block":
            continue
        reasons = got.get("reasons") or ["no reason given"]
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                # The policy travels with the refusal. Being blocked by a
                # rule you cannot read is how a governance tool becomes the
                # thing everyone routes around.
                "permissionDecisionReason": (
                    f"MeshAgent refused {package} {version or '(unpinned)'}. "
                    + " ".join(reasons)
                    + (f" Policy: {got['policy']}." if got.get("policy") else "")
                ),
            }
        }
    return None


def installs(command: str) -> list[tuple[str, str]]:
    """Every dependency the command tries to add, pinned or not.

    Wider than `pinned_packages`, which records what landed and so must not
    guess a version. The gate has to see the unpinned ones too: whether an
    unpinned install is acceptable is a question for the deployment's
    policy, and it cannot answer a question it was never asked."""
    if not any(k in command for k in INSTALL_MARKERS):
        return []
    out: list[tuple[str, str]] = []
    for word in command.split():
        if word.startswith("-") or word in INSTALL_WORDS:
            continue
        for sep in ("==", "@"):
            name, found, version = word.partition(sep)
            if found and name and version and not word.startswith("@"):
                out.append((name, version))
                break
        else:
            # a bare name: unpinned, and still the deployment's call
            if word and word.replace("-", "").replace("_", "").replace(
                    ".", "").isalnum():
                out.append((word, ""))
    return out


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0        # not ours to interpret, and never ours to fail on

    event = payload.get("hook_event_name") or ""
    handler = HANDLERS.get(event)
    if handler is None and event != "PreToolUse":
        return 0

    cfg = config(payload.get("cwd") or os.getcwd())
    if cfg is None:
        return 0        # this repository has not opted in

    if event == "PreToolUse":
        # The one path that can stop the agent. It still exits 0: Claude Code
        # reads the decision from stdout, and a non-zero exit here would mean
        # "the hook broke", which is not what happened.
        if (decision := on_pre_tool(payload, cfg)) is not None:
            json.dump(decision, sys.stdout)
        return 0

    session_id = payload.get("session_id") or ""
    if not session_id:
        return 0

    events = handler(payload, cfg)
    if not events:
        return 0

    repository = payload.get("cwd") or os.getcwd()
    receipt = send(session_id, events, repository)
    state = read_session(session_id, repository)
    if (
        state.get("opening_enqueued")
        and any(e["type"] == "session" and not e.get("ends") for e in events)
    ):
        state["opened"] = True
        state["task"] = (state.get("opening_request") or {}).get("task", "")
    if receipt:
        state["run_id"] = receipt.get("run_id")
        state["last_ok"] = int(time.time())
    write_session(session_id, state, repository)
    return 0            # always. See the module docstring.


if __name__ == "__main__":
    sys.exit(main())
