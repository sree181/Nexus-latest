#!/usr/bin/env python3
"""MeshAgent as MCP tools: where an agent volunteers the why.

The hooks record what happened. They cannot record why, because a hook sees
a file being written and never the reasoning behind it, so everything they
send arrives explicitly unexplained. That is honest, and it is also the
number on the Fleet screen that says two thirds of the estate is
unaccounted for.

This is the other half. An agent that wants to say why it did something has
somewhere to say it, and any MCP-capable editor -- Cursor, Claude Code,
Copilot -- can reach the same three verbs.

The ordering problem, and why this is not an inference engine
------------------------------------------------------------
A decision recorded here has to join up with code the hook records
milliseconds later, in a different process. The tempting shortcut is to
attach the most recent decision to the next file written. That is a guess,
and it would be a guess wearing a provenance record's clothing -- the worst
possible failure for this product, because the whole claim is that you can
read why a line of code exists.

So `record_decision` takes the modules it covers, named by the agent. The
hook attaches a decision only to files the agent explicitly claimed. An
agent that records a reason and names nothing leaves its code exactly as
unexplained as before, which is the correct outcome: it said something, but
not about anything in particular.

Voluntary, and therefore an upgrade rather than a foundation. An agent that
never calls this produces a perfect, empty record -- which is precisely why
the hooks exist underneath it and why coverage is measured at all.

Standard library only, and stdio JSON-RPC, so it runs anywhere the editor
can start a subprocess.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL = "2024-11-05"
NAME = "meshagent"
VERSION = "0.1.0"

TIMEOUT = float(os.environ.get("MESHAGENT_MCP_TIMEOUT", "8.0"))

# Where the hooks keep their state. Shared deliberately: a decision this
# server records and a file that server records have to end up on the same
# run, and the session file is what joins them.
def home() -> str:
    base = os.environ.get("MESHAGENT_HOOK_HOME") or os.path.expanduser(
        "~/.meshagent")
    os.makedirs(base, exist_ok=True)
    return base


def api() -> str:
    return os.environ.get("MESHAGENT_API", "http://localhost:8000").rstrip("/")


def token() -> str:
    try:
        with open(os.path.join(home(), "credentials.json")) as fh:
            return str(json.load(fh).get("token") or "").strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        return os.environ.get("MESHAGENT_TOKEN", "").strip()


def headers() -> dict[str, str]:
    out = {"Content-Type": "application/json"}
    if tok := token():
        out["Authorization"] = f"Bearer {tok}"
    elif user := os.environ.get("MESHAGENT_USER", "").strip():
        out["X-MeshAgent-User"] = user
        # Only meaningful when no identity provider is configured, where the
        # server believes what the client says. Sent because reading is
        # scoped by role -- without it an analyst cannot reach the runs
        # their own UI shows them.
        if role := os.environ.get("MESHAGENT_ROLE", "").strip():
            out["X-MeshAgent-Role"] = role
    return out


def call(path: str, body: dict) -> tuple[dict | None, str]:
    """POST, returning (result, problem). Never raises: a tool result that
    says what went wrong is more use to an agent than a stack trace."""
    req = urllib.request.Request(f"{api()}/api{path}",
                                 data=json.dumps(body).encode(),
                                 headers=headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return json.loads(res.read().decode()), ""
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode()).get("detail", "")
        except Exception:
            pass
        return None, f"MeshAgent refused this ({exc.code}): {detail or exc.reason}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, f"MeshAgent could not be reached: {exc}"


def get(path: str, params: dict | None = None) -> tuple[dict | None, str]:
    """GET, with the same contract as `call`: (result, problem), never raises.

    A separate helper rather than a flag on `call` because the two halves of
    this server are not symmetrical. Writing is the agent volunteering
    something; reading is the agent consulting memory it did not put there,
    and every one of those routes is behind `_may_read`, so a refusal is a
    normal answer and has to arrive as readable text rather than a traceback."""
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(f"{api()}/api{path}{query}",
                                 headers=headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return json.loads(res.read().decode()), ""
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode()).get("detail", "")
        except Exception:
            pass
        return None, f"MeshAgent refused this ({exc.code}): {detail or exc.reason}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, f"MeshAgent could not be reached: {exc}"


# -- finding the session the hooks are writing to ------------------------------

def active_session() -> tuple[str, dict] | tuple[None, None]:
    """The editor session currently being recorded, if there is one.

    Newest wins, because a developer with two editors open is recording two
    sessions and the one they just spoke to is the one they are working in.
    Stale files are ignored: attaching today's reasoning to last week's run
    would be worse than not attaching it."""
    best: tuple[float, str, dict] | None = None
    cutoff = time.time() - 12 * 3600
    try:
        names = os.listdir(home())
    except OSError:
        return None, None
    for name in names:
        if not name.startswith("session-") or not name.endswith(".json"):
            continue
        path = os.path.join(home(), name)
        try:
            when = os.path.getmtime(path)
            if when < cutoff:
                continue
            with open(path) as fh:
                state = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if not state.get("opened"):
            continue        # nothing to hang a decision off yet
        if best is None or when > best[0]:
            best = (when, name[len("session-"):-len(".json")], state)
    if best is None:
        return None, None
    return best[1], best[2]


def claim_modules(session_id: str, decision_id: str,
                  modules: list[str]) -> None:
    """Record that this decision explains these files.

    The hook reads this when it next writes one of them. Nothing is inferred
    from timing; a file is explained only because the agent named it."""
    path = os.path.join(home(), f"session-{session_id}.json")
    try:
        with open(path) as fh:
            state = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return
    claims = state.setdefault("claims", {})
    for module in modules:
        claims[module.strip()] = decision_id
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


# -- the tools -----------------------------------------------------------------

#: Every read tool takes the same optional run. Shared so the wording that
#: stops a model inventing a run id cannot drift between them.
RUN_ARG = {
    "type": "string",
    "description": "which run to read. Defaults to the one this editor "
                   "session is being recorded into, which is almost always "
                   "what you want. Do not guess a value.",
}

TOOLS = [
    {
        "name": "record_decision",
        "description":
            "State why you are about to write something, so the code can be "
            "explained later. Name the files this reasoning covers in "
            "`modules` -- a decision that names nothing is recorded but "
            "explains nothing, because MeshAgent will not guess which of "
            "your files you meant.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string",
                       "description": "short stable id, e.g. 'use-parquet'"},
                "statement": {"type": "string",
                              "description": "the reasoning, in a sentence"},
                "modules": {
                    "type": "array", "items": {"type": "string"},
                    "description":
                        "files this explains, as module paths like "
                        "'src/loader.py'. Optional, but without it nothing "
                        "becomes explained.",
                },
            },
            "required": ["id", "statement"],
        },
    },
    {
        "name": "check_package",
        "description":
            "Ask whether a dependency is allowed before installing it. "
            "Returns allow, warn, block or unknown. `unknown` means nobody "
            "checked -- the advisory feeds were unreachable, or you gave no "
            "version -- and must not be read as approval.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "package": {"type": "string"},
                "version": {"type": "string",
                            "description": "exact version; unpinned cannot "
                                           "be checked"},
            },
            "required": ["package"],
        },
    },
    {
        "name": "session_status",
        "description":
            "Whether MeshAgent is recording this session, and whether "
            "decisions you record will attach to the code being written. "
            "Worth calling once before relying on the other two.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "why",
        "description":
            "Read the recorded reason a piece of code exists: the evidence "
            "chain behind it, nearest first, with who authored each step and "
            "whether anyone verified it. Use this before changing unfamiliar "
            "code, and before trusting it. Every step is something that was "
            "actually recorded; nothing here is inferred.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {
                    "type": "string",
                    "description":
                        "what to explain, as a prefixed entity name such as "
                        "'class:ShardLoader', 'module:loader.py' or "
                        "'entry:open() at line 11'",
                },
                "run": RUN_ARG,
            },
            "required": ["node"],
        },
    },
    {
        "name": "rewind",
        "description":
            "Reconstruct what a run's memory held at a past moment. Call "
            "with no `at` to list the moments its memory changed, then call "
            "again with one of them. Memory deleted since is reported as "
            "deleted, never as absent and never with its content — a "
            "deletion is permanent and this cannot undo one.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "at": {
                    "type": "integer",
                    "description":
                        "the moment to reconstruct, in seconds since the "
                        "epoch. Omit to list the moments worth asking about.",
                },
                "run": RUN_ARG,
            },
        },
    },
    {
        "name": "findings",
        "description":
            "Dangerous call sites recorded for a run, with whether anything "
            "actually established that untrusted input reaches them. "
            "Reachability is three-valued: reachable, not-reachable, or not "
            "assessed. Not assessed means nobody looked and must not be read "
            "as safe.",
        "inputSchema": {
            "type": "object",
            "properties": {"run": RUN_ARG},
        },
    },
    {
        "name": "sbom",
        "description":
            "The dependencies a run recorded, with licences and any known "
            "advisories against them. Says when advisories come from curated "
            "sample data or were never checked, so an empty CVE list is not "
            "mistaken for a clean bill of health.",
        "inputSchema": {
            "type": "object",
            "properties": {"run": RUN_ARG},
        },
    },
]


def tool_record_decision(args: dict) -> str:
    ident = str(args.get("id") or "").strip()
    statement = str(args.get("statement") or "").strip()
    modules = [str(m) for m in (args.get("modules") or [])]
    if not ident or not statement:
        return "Both `id` and `statement` are required."

    session_id, state = active_session()
    if session_id is None:
        # Refusing beats opening a second run. A decision filed against a
        # run the developer's code is not in explains nothing and splits
        # one piece of work across two records.
        return ("No editor session is being recorded, so there is nothing "
                "for this decision to explain. Check that this repository "
                "opted in via .meshagent.json and that the hooks are "
                "installed.")

    body = {"agent": os.environ.get("MESHAGENT_MCP_AGENT", "mcp"),
            "session": session_id,
            "events": [{"type": "decision", "id": ident,
                        "statement": statement}]}
    got, problem = call("/recorder", body)
    if got is None:
        return problem
    if got.get("refused"):
        return "MeshAgent refused it: " + "; ".join(got["refused"])

    if not modules:
        return (f"Recorded '{ident}'. No files were named, so nothing became "
                f"explained by it — pass `modules` to attach it to the code "
                f"it covers.")
    claim_modules(session_id, ident, modules)
    return (f"Recorded '{ident}' against run {got.get('run_id')}. "
            f"{len(modules)} file(s) will be attributed to it as they are "
            f"written: {', '.join(modules)}.")


def tool_check_package(args: dict) -> str:
    package = str(args.get("package") or "").strip()
    version = str(args.get("version") or "").strip()
    if not package:
        return "`package` is required."

    got, problem = call("/gate/package", {"package": package,
                                          "version": version})
    if got is None:
        return problem

    verdict = got.get("verdict", "unknown")
    lines = [f"{verdict.upper()}: {package} {version or '(unpinned)'}"]
    lines += [f"  - {r}" for r in got.get("reasons", [])]
    if got.get("policy"):
        lines.append(f"  Policy: {got['policy']}")
    if verdict == "block":
        lines.append("  Do not install this. Choose another version or "
                     "another package.")
    elif verdict == "unknown":
        # Said in the tool result, not left to the model to infer from a
        # word it has not seen before.
        lines.append("  This was NOT checked. Do not treat it as approved.")
    if got.get("fleet_agents"):
        lines.append(f"  {got['fleet_agents']} agent(s) in this fleet "
                     f"already hold this version.")
    return "\n".join(lines)


def tool_session_status(_: dict) -> str:
    session_id, state = active_session()
    if session_id is None:
        return ("Not recording. No opted-in editor session is active, so "
                "record_decision has nothing to attach to.")
    claims = (state or {}).get("claims") or {}
    return (f"Recording session {session_id} as run "
            f"{(state or {}).get('run_id') or 'not yet assigned'}. "
            f"{len(claims)} file(s) already have a stated reason waiting to "
            f"be attached.")


# -- reading memory back -------------------------------------------------------

def run_for(args: dict) -> tuple[str, str]:
    """Which run to read, as (run_id, problem).

    Defaults to the run this editor session is being recorded into, because
    an agent asking "why does this exist" means the code in front of it. An
    explicit `run` is still allowed -- comparing against another run is a
    real question -- but it is never guessed at."""
    if explicit := str(args.get("run") or "").strip():
        return explicit, ""
    _, state = active_session()
    if state and (run_id := str(state.get("run_id") or "").strip()):
        return run_id, ""
    return "", ("No run to read. This editor session is not being recorded, "
                "so there is no memory of this work — pass `run` to ask "
                "about a different one.")


def _provenance(node: dict) -> str:
    """The two facts that decide how much weight a claim carries."""
    bits = [str(node.get("origin") or "?"), str(node.get("status") or "?")]
    if node.get("tombstoned"):
        bits.append("FORGOTTEN")
    return "/".join(bits)


def tool_why(args: dict) -> str:
    node = str(args.get("node") or "").strip()
    if not node:
        return "`node` is required, e.g. 'class:ShardLoader'."
    run_id, problem = run_for(args)
    if problem:
        return problem

    got, problem = get(f"/runs/{run_id}/why", {"node": node})
    if got is None:
        return problem
    chain = got.get("chain") or []
    if not chain:
        return f"Nothing in run {run_id} explains {node}."

    lines = [f"Why {node} exists, in run {run_id} "
             f"({_plural(len(chain), 'step', 'steps')}, nearest first):"]
    for i, link in enumerate(chain, 1):
        said = link.get("statement") or link.get("entity") or "(no statement)"
        lines.append(f"  {i}. [{_provenance(link)}] {said}")
        if via := link.get("via"):
            lines.append(f"     derived from the step below via {via}")
    if got.get("sample"):
        lines.append("This run is sample data, not a real recording.")
    # An EXTERNAL/UNVERIFIED root is the thing worth acting on, and an agent
    # skimming a list will not infer it from two words in a bracket.
    root = chain[-1]
    if root.get("origin") == "EXTERNAL" and root.get("status") == "UNVERIFIED":
        lines.append(f"WARNING: this chain bottoms out in unverified external "
                     f"input ({root.get('entity')}). Everything above it "
                     f"rests on something nobody checked.")
    return "\n".join(lines)


def tool_rewind(args: dict) -> str:
    run_id, problem = run_for(args)
    if problem:
        return problem

    raw = args.get("at")
    if raw in (None, ""):
        # Listing the stops beats letting a model invent an epoch second and
        # read the empty answer as "nothing was known then".
        got, problem = get(f"/runs/{run_id}/rewind", {"at": 0})
        if got is None:
            return problem
        stops = got.get("milestones") or []
        if not stops:
            return f"Run {run_id} holds no memory to rewind through."
        listed = "\n".join(f"  {s}  ({_utc(s)})" for s in stops)
        return (f"Run {run_id} changed at "
                f"{_plural(len(stops), 'moment', 'moments')}. Call rewind "
                f"again with `at` set to one of these:\n{listed}")
    try:
        at = int(raw)
    except (TypeError, ValueError):
        return "`at` must be a whole number of seconds since the epoch."

    got, problem = get(f"/runs/{run_id}/rewind", {"at": at})
    if got is None:
        return problem
    memories = got.get("memories") or []
    if not memories:
        return (f"Run {run_id} held nothing at {_utc(at)}. Its memory begins "
                f"later than that.")

    lines = [f"Run {run_id} at {_utc(at)}: "
             f"{_plural(got.get('held', 0), 'memory', 'memories')} held "
             f"then, {got.get('now')} held now."]
    for m in memories:
        if m.get("redacted"):
            # The single most important line in this file. "(deleted)" must
            # never be shortened to a blank, or an agent reports an erased
            # memory as one that never existed.
            lines.append(f"  [DELETED] {m.get('entity')} — this memory was "
                         f"held at that moment and has since been forgotten. "
                         f"Its content was destroyed and cannot be recovered.")
        else:
            said = m.get("statement") or m.get("entity") or "(no statement)"
            lines.append(f"  [{_provenance(m)}] {said}")
    if got.get("redacted"):
        lines.append(f"{got['redacted']} of these were deleted after this "
                     f"moment. Rewind proves they existed; it does not and "
                     f"must not reproduce what they said.")
    if got.get("sample"):
        lines.append("This run is sample data, not a real recording.")
    return "\n".join(lines)


def tool_findings(args: dict) -> str:
    run_id, problem = run_for(args)
    if problem:
        return problem

    got, problem = get(f"/runs/{run_id}/findings")
    if got is None:
        return problem
    if not got.get("scanned"):
        return (f"Run {run_id} has not been scanned — no code records were "
                f"read. This is not a clean result; nothing was examined.")
    findings = got.get("findings") or []
    if not findings:
        return (f"Run {run_id}: "
                f"{_plural(got['scanned'], 'code record', 'code records')} "
                f"scanned, no dangerous call sites found.")

    lines = [f"Run {run_id}: "
             f"{_plural(got.get('present', 0), 'dangerous call site', 'dangerous call sites')} "
             f"in {_plural(got['scanned'], 'scanned record', 'scanned records')} — "
             f"{got.get('reachable', 0)} reachable, "
             f"{got.get('not_reachable', 0)} not reachable, "
             f"{got.get('not_assessed', 0)} not assessed."]
    for f in findings:
        head = f"  {f.get('sink')}"
        if owner := f.get("owner"):
            head += f" in {owner}"
        if cwe := f.get("cwe"):
            head += f" ({cwe}{': ' + f['cwe_title'] if f.get('cwe_title') else ''})"
        lines.append(head)
        reach = f.get("reachability") or "not-assessed"
        by = f.get("asserted_by")
        if reach == "not-assessed":
            # Not the same as safe, and the phrasing has to close that door.
            lines.append("    reachability: NOT ASSESSED — no analyser has "
                         "decided whether input reaches this. Do not read "
                         "this as safe.")
        else:
            lines.append(f"    reachability: {reach}"
                         f"{f' (asserted by {by})' if by else ''}")
        if disputed := f.get("disputed_by"):
            lines.append(f"    DISPUTED: {disputed} reached the opposite "
                         f"conclusion. Both readings are kept.")
        if path := f.get("path"):
            lines.append(f"    path: {' -> '.join(path)}")
    if got.get("sample"):
        lines.append("This run is sample data, not a real recording.")
    return "\n".join(lines)


def tool_sbom(args: dict) -> str:
    run_id, problem = run_for(args)
    if problem:
        return problem

    got, problem = get(f"/runs/{run_id}/sbom")
    if got is None:
        return problem
    entries = got.get("entries") or []
    if not entries:
        return f"Run {run_id} records no dependencies."

    lines = [f"Run {run_id}: "
             f"{_plural(len(entries), 'dependency', 'dependencies')}."]
    for e in entries:
        line = f"  {e.get('package')}@{e.get('version')} — {e.get('license')}"
        if cves := e.get("cves"):
            line += (f" — {_plural(len(cves), 'advisory', 'advisories')} "
                     f"({', '.join(cves)})")
            if sev := e.get("severity"):
                line += f", highest {sev}"
        lines.append(line)
        # "no CVEs" from a feed that was never reached is not the same claim
        # as "no CVEs" from one that was.
        if e.get("feed") == "sample":
            lines.append("    advisories are curated sample data, not a live "
                         "feed")
        elif not e.get("feed"):
            lines.append("    NOT CHECKED against any advisory feed")
    if got.get("sample"):
        lines.append("This run is sample data, not a real recording.")
    return "\n".join(lines)


def _utc(epoch: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(epoch)))


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


HANDLERS = {
    "record_decision": tool_record_decision,
    "check_package": tool_check_package,
    "session_status": tool_session_status,
    "why": tool_why,
    "rewind": tool_rewind,
    "findings": tool_findings,
    "sbom": tool_sbom,
}


# -- JSON-RPC over stdio -------------------------------------------------------

def handle(message: dict) -> dict | None:
    """One request. None for notifications, which take no reply."""
    method = message.get("method")
    mid = message.get("id")

    if method == "initialize":
        return _ok(mid, {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": NAME, "version": VERSION},
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "tools/list":
        return _ok(mid, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        handler = HANDLERS.get(params.get("name") or "")
        if handler is None:
            return _err(mid, -32601, f"no tool named {params.get('name')!r}")
        try:
            text = handler(params.get("arguments") or {})
        except Exception as exc:        # a tool must not take the server down
            text = f"That failed: {type(exc).__name__}: {exc}"
        return _ok(mid, {"content": [{"type": "text", "text": text}]})
    if mid is None:
        return None
    return _err(mid, -32601, f"unknown method {method!r}")


def _ok(mid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code,
                                                   "message": message}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        reply = handle(message)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
