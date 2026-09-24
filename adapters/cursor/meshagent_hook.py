#!/usr/bin/env python3
"""MeshAgent recorder for Cursor.

The second adapter, and therefore the one that proves the claim: the engine
never learns which editor it is talking to. Everything below translates
Cursor's hook payloads into the same five recorder events Claude Code's
adapter produces -- session, decision, code, package, tool -- and the API
cannot tell them apart afterwards except by the `agent` field.

The same three rules hold as in the Claude Code adapter. It never blocks the
developer except where the gate says to; it never invents a reason for code;
it records nothing until the repository opts in.

What is genuinely different about Cursor, and why this is not a copy
-------------------------------------------------------------------
*Silence is not neutral.* Claude Code treats a hook that prints nothing as
"carry on". Cursor treats invalid or unparseable output from a permission
hook as a refusal, even with failClosed off. So `beforeShellExecution` here
always prints an explicit decision, including when it is allowing. A
recorder that accidentally blocked every shell command because it had
nothing to say would be removed the same morning.

*The session id is `conversation_id`, not `session_id`.* `session_id` is
documented only on sessionStart/sessionEnd. `conversation_id` is stable
across turns, which is what the run needs to stay one run.

*`cwd` is not always there.* It is documented for the shell and tool hooks
and not for `afterFileEdit`, so module paths fall back to
`workspace_roots[0]`. Guessing the process's own cwd would file a file
under whatever directory Cursor happened to launch from.

*An edit reports a diff, not a file.* Same as Claude Code, so the file is
read back off disk: the governed record should be what the developer
actually has, not this adapter's idea of what applying the edit produced.

Standard library only, and a single file on purpose -- a hook people copy
into their own repository should not come with an import path to get right.
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

AGENT = "cursor"
CONFIG = ".meshagent.json"

TIMEOUT = float(os.environ.get("MESHAGENT_HOOK_TIMEOUT", "2.0"))
GATE_TIMEOUT = float(os.environ.get("MESHAGENT_GATE_TIMEOUT", "3.0"))
MAX_BYTES = int(os.environ.get("MESHAGENT_HOOK_MAX_BYTES", "200000"))

INSTALL_MARKERS = ("pip install", "pip3 install", "npm install", "npm i ",
                   "yarn add", "uv add", "poetry add")
INSTALL_WORDS = {"pip", "pip3", "npm", "yarn", "uv", "poetry", "python",
                 "python3", "install", "add", "i", "-m", "&&", "sudo"}


# -- state, shared with every other MeshAgent adapter on this machine ---------

def home() -> str:
    return local_state.home()


def session_path(session_id: str, repository: str | None = None) -> str:
    return local_state.state_path(session_id, repository=repository, editor=AGENT)


def read_session(session_id: str, repository: str | None = None) -> dict:
    found = local_state.read_session(session_id, repository=repository, editor=AGENT)
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
        pass            # losing session state is not worth failing a hook for


def queue_path() -> str:
    return local_state.queue_path("default", editor=AGENT)


def enqueue(batch: dict, repository: str | None = None) -> None:
    try:
        local_state.enqueue(batch, repository=repository, editor=AGENT)
    except OSError:
        pass


def drain(repository: str | None = None) -> list[dict]:
    """Everything waiting, read and cleared together so a second hook firing
    at the same moment cannot send it twice."""
    return local_state.drain_matching(repository=repository, editor=AGENT)


# -- opting in ----------------------------------------------------------------

def workspace(payload: dict) -> str:
    """The directory this repository lives in.

    `cwd` where Cursor sends it, the first workspace root otherwise. Never
    this process's own cwd: that would file a module under whatever
    directory the editor happened to be launched from."""
    if cwd := (payload.get("cwd") or "").strip():
        return cwd
    roots = payload.get("workspace_roots") or []
    return str(roots[0]) if roots else os.getcwd()


def config(cwd: str) -> dict | None:
    """The repository's opt-in, or None.

    Absent means absent. Source code leaving a developer's machine is a
    works-council conversation before it is an engineering one."""
    try:
        with open(os.path.join(cwd, CONFIG)) as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return cfg if isinstance(cfg, dict) and cfg.get("record") is True else None


def excluded(module: str, cfg: dict) -> bool:
    patterns = cfg.get("exclude") or []
    name = os.path.basename(module)
    return any(fnmatch.fnmatch(module, p) or fnmatch.fnmatch(name, p)
               for p in patterns)


def module_name(file_path: str, cwd: str) -> str:
    """The path as the repository sees it, with separators normalised so a
    Windows developer's files land under the same names as everyone else's."""
    path = file_path.replace("\\", "/")
    root = (cwd or "").replace("\\", "/").rstrip("/")
    if root and path.startswith(root + "/"):
        path = path[len(root) + 1:]
    return path.lstrip("/")


# -- talking to MeshAgent -----------------------------------------------------

def token() -> str:
    """The credential `meshagent login` left. Read every time rather than
    cached, so `meshagent logout` takes effect on the next write."""
    return local_state.device_token()


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if tok := token():
        headers["Authorization"] = f"Bearer {tok}"
    elif user := os.environ.get("MESHAGENT_USER", "").strip():
        headers["X-MeshAgent-User"] = user
    return headers


def post(batch: dict) -> dict | None:
    return session_protocol.post(batch, headers=_headers(), timeout=TIMEOUT)


def send(session_id: str, events: list[dict], repository: str | None = None) -> dict | None:
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


def ask_gate(package: str, version: str, session_id: str) -> dict | None:
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


# -- translating Cursor's events into the recorder vocabulary ------------------

def on_prompt(payload: dict, cfg: dict) -> list[dict]:
    """`beforeSubmitPrompt`. The first one opens the run with what the
    developer asked for; later ones are not decisions and are not recorded
    as such."""
    del cfg
    session_id = conversation(payload)
    if read_session(session_id, workspace(payload)).get("opened"):
        return []
    task = (payload.get("prompt") or "").strip()
    if not task:
        return []
    return [{"type": "session", "agent": AGENT, "task": task}]


def on_file_edit(payload: dict, cfg: dict) -> list[dict]:
    """`afterFileEdit`. Cursor sends the diff; the record gets the file."""
    path = payload.get("file_path") or ""
    if not path:
        return []
    module = module_name(path, workspace(payload))
    if excluded(module, cfg):
        return []
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return []
        with open(path, encoding="utf-8") as fh:
            code = fh.read()
    except (OSError, UnicodeDecodeError):
        return []

    event = {"type": "code", "module": module, "code": code}
    # A reason only where the agent volunteered one about this exact file,
    # through the MCP server. Never inferred from timing.
    if because := claimed_decision(conversation(payload), module, workspace(payload)):
        event["because"] = because
    return [event]


def claimed_decision(session_id: str, module: str,
                     repository: str | None = None) -> str | None:
    if not session_id:
        return None
    claims = read_session(session_id, repository).get("claims") or {}
    found = claims.get(module)
    return str(found) if found else None


def on_shell_done(payload: dict, cfg: dict) -> list[dict]:
    """`afterShellExecution`. The command, plus anything it pinned.

    An unpinned install yields no package event: a version this adapter
    guessed would be indistinguishable in the graph from one it read."""
    del cfg
    command = (payload.get("command") or "").strip()
    if not command:
        return []
    events: list[dict] = [{"type": "tool", "name": "Shell", "detail": command}]
    events += [{"type": "package", "package": name, "version": version}
               for name, version in pinned_packages(command)]
    return events


def pinned_packages(command: str) -> list[tuple[str, str]]:
    """Dependencies the command names with an exact version."""
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
    return out


def installs(command: str) -> list[tuple[str, str]]:
    """Every dependency the command would add, pinned or not. Wider than
    `pinned_packages`, because whether an unpinned install is acceptable is
    the deployment's call and it cannot answer a question never asked."""
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
            if word and word.replace("-", "").replace("_", "").replace(
                    ".", "").isalnum():
                out.append((word, ""))
    return out


def on_session_end(payload: dict, cfg: dict) -> list[dict]:
    del cfg
    session_id = conversation(payload)
    state = read_session(session_id, workspace(payload))
    if not state.get("opened"):
        return []
    return [{"type": "session", "agent": AGENT,
             "task": state.get("task", ""), "ends": True}]


def conversation(payload: dict) -> str:
    """The id that keeps one piece of work as one run.

    `conversation_id` is stable across turns; `session_id` appears only on
    sessionStart and sessionEnd. Preferring the stable one means a session
    that spans an API restart still finds its run."""
    return str(payload.get("conversation_id")
               or payload.get("session_id") or "")


HANDLERS = {
    "beforeSubmitPrompt": on_prompt,
    "afterFileEdit": on_file_edit,
    "afterShellExecution": on_shell_done,
    "sessionEnd": on_session_end,
}


# -- the permission hook, which is the only thing here that can refuse --------

def on_before_shell(payload: dict, cfg: dict) -> dict:
    """`beforeShellExecution`. Always returns an explicit decision.

    Cursor treats unparseable or schema-invalid output from a permission
    hook as a refusal even with failClosed off, so staying quiet is not the
    safe option here that it is under Claude Code. Allowing out loud is.

    It denies on exactly one condition: MeshAgent answered, and answered
    `block`. Not on a timeout, not on a connection error, not on a verdict
    it does not recognise."""
    allow = {"permission": "allow"}
    if not cfg.get("gate", True):
        return allow
    command = (payload.get("command") or "").strip()
    if not command:
        return allow

    session_id = conversation(payload)
    for package, version in installs(command):
        got = ask_gate(package, version, session_id)
        state = read_session(session_id, workspace(payload))
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
                workspace(payload),
            )
        if got is None or got.get("verdict") != "block":
            continue
        reasons = " ".join(got.get("reasons") or ["no reason given"])
        told = (f"MeshAgent refused {package} {version or '(unpinned)'}. "
                + reasons
                + (f" Policy: {got['policy']}." if got.get("policy") else ""))
        return {
            "permission": "deny",
            # Both audiences, deliberately. The developer needs to know why
            # their command did not run; the agent needs enough to choose a
            # different version rather than retry the same one.
            "user_message": told,
            "agent_message": told + " Pick a different version or package.",
        }
    return allow


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Nothing was understood, so nothing can be judged. Say allow out
        # loud rather than leaving Cursor to read silence as a refusal.
        json.dump({"permission": "allow"}, sys.stdout)
        return 0

    event = payload.get("hook_event_name") or ""
    cwd = workspace(payload)
    cfg = config(cwd)

    if event == "beforeShellExecution":
        json.dump(on_before_shell(payload, cfg or {}) if cfg
                  else {"permission": "allow"}, sys.stdout)
        return 0

    handler = HANDLERS.get(event)
    if handler is None or cfg is None:
        return 0

    session_id = conversation(payload)
    if not session_id:
        return 0

    events = handler(payload, cfg)
    if not events:
        return 0

    receipt = send(session_id, events, cwd)
    state = read_session(session_id, cwd)
    if any(e["type"] == "session" and not e.get("ends") for e in events):
        state["opened"] = True
        state["task"] = next(e["task"] for e in events
                             if e["type"] == "session")
    if receipt:
        state["run_id"] = receipt.get("run_id")
        state["last_ok"] = int(time.time())
    write_session(session_id, state, cwd)
    return 0            # always. See the module docstring.


if __name__ == "__main__":
    raise SystemExit(main())
