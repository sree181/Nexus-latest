"""
Project:     MeshAgent
File:        meshagent/codegraph.py
Description: The code-provenance capture layer. When MeshAgent writes code,
             this records WHAT it produced (classes, the packages they
             import) and WHY (the design decision behind each, linked to the
             source that informed it) as hyperedges in the same governed
             memory. The what is extracted deterministically from the code
             with Python's AST; the why is the agent's logged reasoning. The
             result is a provenance DAG — source -> decision -> class ->
             package — that `why` traces and `forget` prunes, now over the
             user's own model code rather than abstract beliefs.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

import ast
import time
from dataclasses import dataclass, field
from typing import Any

from hypermeshdb.agentmem import (
    Kind,
    MemoryStore,
    Origin,
    Rel,
    Status,
    Verbs,
)

# entity-name prefixes for the four node kinds in a code graph
P_SOURCE = "source:"
P_DECISION = "decision:"
P_MODULE = "module:"
P_CLASS = "class:"
P_PACKAGE = "pkg:"
P_SINK = "sink:"
P_CWE = "cwe:"
P_CAP = "cap:"
P_VERSION = "version:"
P_LICENSE = "license:"
P_CVE = "cve:"
P_ENTRY = "entry:"
P_TOOL = "tool:"
P_ACTIVITY = "activity:"


# Dangerous call sites, keyed by the resolved dotted call name. Each maps to
# the weakness class it introduces and the capability it grants. These are
# accurate, feed-free facts (a pickle.load on untrusted bytes IS CWE-502);
# reachability — whether untrusted data actually reaches the sink — is a
# separate taint question this layer deliberately does not claim.
_SINKS: dict[str, tuple[str, str, str]] = {
    # call name : (cwe id, cwe title, capability)
    "pickle.load": ("CWE-502", "Deserialization of untrusted data", "deserialization"),
    "pickle.loads": ("CWE-502", "Deserialization of untrusted data", "deserialization"),
    "marshal.load": ("CWE-502", "Deserialization of untrusted data", "deserialization"),
    "marshal.loads": ("CWE-502", "Deserialization of untrusted data", "deserialization"),
    "yaml.load": ("CWE-502", "Deserialization of untrusted data", "deserialization"),
    "eval": ("CWE-95", "Eval injection", "code-exec"),
    "exec": ("CWE-95", "Eval injection", "code-exec"),
    "os.system": ("CWE-78", "OS command injection", "subprocess-spawn"),
    "subprocess.call": ("CWE-78", "OS command injection", "subprocess-spawn"),
    "subprocess.run": ("CWE-78", "OS command injection", "subprocess-spawn"),
    "subprocess.Popen": ("CWE-78", "OS command injection", "subprocess-spawn"),
    "hashlib.md5": ("CWE-327", "Use of a broken cryptographic algorithm", "weak-crypto"),
    "hashlib.sha1": ("CWE-327", "Use of a broken cryptographic algorithm", "weak-crypto"),
    "tempfile.mktemp": ("CWE-377", "Insecure temporary file", "filesystem-write"),
    "requests.get": ("", "", "network-egress"),
    "requests.post": ("", "", "network-egress"),
    "urllib.request.urlopen": ("", "", "network-egress"),
    "httpx.get": ("", "", "network-egress"),
    "httpx.post": ("", "", "network-egress"),
}


@dataclass
class SinkFinding:
    """A dangerous call site found inside a class."""

    call: str           # resolved dotted name, e.g. "pickle.load"
    cwe: str            # "" when it is a capability marker, not a weakness
    cwe_title: str
    capability: str
    shell_true: bool = False


@dataclass
class ExtractedClass:
    """One class parsed from source, with the packages it references and the
    dangerous sinks it calls."""

    name: str
    packages: list[str]
    bases: list[str]
    sinks: list[SinkFinding] = field(default_factory=list)
    taints: list["TaintPath"] = field(default_factory=list)


def _call_alias_map(tree: ast.AST) -> dict[str, str]:
    """Map each imported name to its FULLY-QUALIFIED callable/module path, so
    a call through any alias resolves to the sink table. `import pickle as
    pkl` -> pkl->pickle; `from pickle import loads as _l` -> _l->pickle.loads;
    `from subprocess import run` -> run->subprocess.run."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _call_name(node: ast.Call, call_aliases: dict[str, str]) -> str | None:
    """Resolve a Call's fully-qualified dotted name via the call-alias map."""
    func = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    else:
        return None
    parts.reverse()
    head = parts[0]
    real = call_aliases.get(head)
    if real:
        parts = real.split(".") + parts[1:]
    return ".".join(parts)


def _detect_sinks(
    node: ast.ClassDef, call_aliases: dict[str, str]
) -> list[SinkFinding]:
    found: list[SinkFinding] = []
    seen: set[str] = set()
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        name = _call_name(n, call_aliases)
        if name is None or name not in _SINKS:
            continue
        shell_true = any(
            isinstance(kw, ast.keyword) and kw.arg == "shell"
            and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in n.keywords
        )
        if name not in seen:
            seen.add(name)
            cwe, title, cap = _SINKS[name]
            found.append(SinkFinding(name, cwe, title, cap, shell_true))
    return found


# Where a value can enter the process from outside it. Reaching a sink from
# one of these is what separates "present" from "exploitable".
#
# A method parameter is deliberately NOT here. A parameter is supplied by the
# caller, and the caller is usually the program itself; treating one as
# untrusted would mark almost every sink exploitable and the distinction this
# whole screen rests on would mean nothing. Only data that actually crosses
# the process boundary counts.
_ENTRY_CALLS = {
    "input": "standard input",
    "open": "a file",
    "requests.get": "the network",
    "requests.post": "the network",
    "httpx.get": "the network",
    "httpx.post": "the network",
    "urllib.request.urlopen": "the network",
    "os.getenv": "the environment",
}

# attribute/subscript reads that are external data
_ENTRY_ATTRS = {
    "sys.argv": "the command line",
    "os.environ": "the environment",
}

# Weaknesses where untrusted input reaching the call is what makes it
# exploitable. An md5 digest is weak whoever supplies the bytes, and a
# predictable temp file is predictable regardless -- calling those
# "exploitable" because data reached them would say nothing true, so
# reachability leaves them at "present".
_INPUT_DRIVEN = {"deserialization", "code-exec", "subprocess-spawn"}

@dataclass
class TaintPath:
    """A value that entered the process from outside and reached a sink.

    `entry` names the exact place it came in, with a line number, so the
    claim can be checked against the source rather than believed."""

    sink: str
    owner: str
    entry: str
    entry_kind: str
    flow: list[str]
    cwe: str
    cwe_title: str


def _attr_path(node: ast.AST, call_aliases: dict[str, str]) -> str | None:
    """The dotted path of an attribute chain, resolved through imports."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    parts.reverse()
    real = call_aliases.get(parts[0])
    if real:
        parts = real.split(".") + parts[1:]
    return ".".join(parts)


def _detect_taint(
    cls: ast.ClassDef, call_aliases: dict[str, str]
) -> list[TaintPath]:
    """Find values that enter from outside the process and reach a sink.

    Deliberately modest: it follows local names inside a single function, in
    statement order, and does not reason across calls. That means it misses
    real paths -- but everything it does report is one it can point at, which
    is the only way `exploitable` is worth more than `present`."""
    out: list[TaintPath] = []
    for fn in ast.walk(cls):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            out.extend(_taint_in_function(cls.name, fn, call_aliases))
    return out


def _taint_in_function(
    owner: str, fn: ast.FunctionDef | ast.AsyncFunctionDef,
    call_aliases: dict[str, str],
) -> list[TaintPath]:
    # name -> (where it came in, what kind of place that is, how it got here)
    tainted: dict[str, tuple[str, str, list[str]]] = {}
    found: list[TaintPath] = []
    seen: set[tuple[str, str]] = set()

    def entry_in(expr: ast.AST) -> tuple[str, str] | None:
        """Where, if anywhere, this expression reads data from outside."""
        for n in ast.walk(expr):
            if isinstance(n, ast.Call):
                name = _call_name(n, call_aliases)
                if name in _ENTRY_CALLS:
                    return (f"{name}() at line {n.lineno}", _ENTRY_CALLS[name])
            if isinstance(n, ast.Attribute | ast.Subscript):
                base = n.value if isinstance(n, ast.Subscript) else n
                path = _attr_path(base, call_aliases)
                if path in _ENTRY_ATTRS:
                    return (f"{path} at line {n.lineno}", _ENTRY_ATTRS[path])
        return None

    def carriers(expr: ast.AST) -> list[str]:
        """Tainted local names this expression reads."""
        return [n.id for n in ast.walk(expr)
                if isinstance(n, ast.Name) and n.id in tainted]

    def taint_targets(targets: list[ast.expr], value: ast.expr) -> None:
        origin = entry_in(value)
        if origin is not None:
            entry, kind, flow = origin[0], origin[1], [origin[0]]
        else:
            carried = carriers(value)
            if not carried:
                return
            entry, kind, flow = tainted[carried[0]]
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name):
                    tainted[n.id] = (entry, kind, [*flow, n.id])

    def check_sinks(expr: ast.AST) -> None:
        for n in ast.walk(expr):
            if not isinstance(n, ast.Call):
                continue
            name = _call_name(n, call_aliases)
            if name is None or name not in _SINKS:
                continue
            cwe, title, cap = _SINKS[name]
            if not cwe or cap not in _INPUT_DRIVEN:
                continue
            reads = [*n.args, *[k.value for k in n.keywords]]
            for arg in reads:
                origin = entry_in(arg)
                if origin is not None:
                    flow = [origin[0]]
                    entry, kind = origin
                else:
                    carried = carriers(arg)
                    if not carried:
                        continue
                    entry, kind, flow = tainted[carried[0]]
                if (name, entry) in seen:
                    continue
                seen.add((name, entry))
                found.append(TaintPath(
                    sink=name, owner=owner, entry=entry, entry_kind=kind,
                    flow=[*flow, name], cwe=cwe, cwe_title=title))
                break

    def walk(body: list[ast.stmt]) -> None:
        """Statements in the order they run, so a sink only sees taint that
        an earlier line established."""
        for st in body:
            if isinstance(st, ast.Assign):
                check_sinks(st.value)
                taint_targets(st.targets, st.value)
            elif isinstance(st, ast.AnnAssign) and st.value is not None:
                check_sinks(st.value)
                taint_targets([st.target], st.value)
            elif isinstance(st, ast.With | ast.AsyncWith):
                for item in st.items:
                    check_sinks(item.context_expr)
                    if item.optional_vars is not None:
                        taint_targets([item.optional_vars], item.context_expr)
                walk(st.body)
            elif isinstance(st, ast.For | ast.AsyncFor):
                check_sinks(st.iter)
                taint_targets([st.target], st.iter)
                walk(st.body)
                walk(st.orelse)
            elif isinstance(st, ast.If | ast.While):
                check_sinks(st.test)
                walk(st.body)
                walk(st.orelse)
            elif isinstance(st, ast.Try):
                walk(st.body)
                for h in st.handlers:
                    walk(h.body)
                walk(st.orelse)
                walk(st.finalbody)
            elif isinstance(st, ast.FunctionDef | ast.AsyncFunctionDef):
                continue        # handled as its own function
            else:
                check_sinks(st)

    walk(fn.body)
    return found


def _alias_map(tree: ast.AST) -> dict[str, str]:
    """Map every imported name/alias to its top-level package.

    `import torch` -> torch->torch; `import torch.nn as nn` -> nn->torch;
    `from torch import nn` -> nn->torch; `from einops import rearrange` ->
    rearrange->einops. So a reference to `nn` or `rearrange` inside a class
    resolves to the package it came from."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                out[(a.asname or a.name).split(".")[0]] = top
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            top = node.module.split(".")[0]
            for a in node.names:
                out[a.asname or a.name] = top
    return out


def _root_names(node: ast.AST) -> set[str]:
    """The set of root identifiers referenced in a subtree: `nn` from
    `nn.Module`, `rearrange` from `rearrange(x, ...)`."""
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            base = n
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                names.add(base.id)
    return names


def extract_code_entities(source: str) -> list[ExtractedClass]:
    """Parse *source* and return each class with the packages it uses.

    Deterministic: this is the mechanical half of code provenance, so it
    never guesses. A syntax error raises, by design — you cannot capture the
    provenance of code that does not parse."""
    tree = ast.parse(source)
    aliases = _alias_map(tree)
    call_aliases = _call_alias_map(tree)
    out: list[ExtractedClass] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        refs = _root_names(node)
        packages = sorted({
            aliases[r] for r in refs if r in aliases
        })
        bases = []
        for b in node.bases:
            bases.append(ast.unparse(b) if hasattr(ast, "unparse") else "")
        out.append(ExtractedClass(
            name=node.name, packages=packages, bases=bases,
            sinks=_detect_sinks(node, call_aliases),
            taints=_detect_taint(node, call_aliases),
        ))
    return out


@dataclass
class CodeGraphRecorder:
    """Records code provenance into a governed MemoryStore."""

    memory: MemoryStore
    _sources: dict[str, str] = field(default_factory=dict)      # id -> ulid
    _decisions: dict[str, str] = field(default_factory=dict)    # id -> ulid
    _modules: dict[str, str] = field(default_factory=dict)      # name -> ulid
    _classes: dict[str, str] = field(default_factory=dict)      # name -> ulid

    def _projection_subject(
        self, external_event_id: str | None, component: str,
    ) -> str | None:
        if not external_event_id:
            return None
        return f"{P_ACTIVITY}{external_event_id}:{component}"

    def _projected(
        self, external_event_id: str | None, component: str,
    ) -> str | None:
        """Return the live ULID for one event component, if already written."""
        subject = self._projection_subject(external_event_id, component)
        if subject is None:
            return None
        for ulid in reversed(self.memory.find_by_subject(subject)):
            record = self.memory.get(ulid)
            if record is not None and not record.tombstoned:
                return ulid
        return None

    # ── sources and decisions ─────────────────────────────────────────────

    def record_source(
        self, source_id: str, statement: str, *, origin: str = "external",
        trusted: bool = False,
    ) -> str:
        """A source that informs decisions: a user request, a doc, a paper.
        External sources enter unverified by default (the gate caps them);
        `trusted` marks a user-stated one."""
        org, st = (
            (Origin.USER, Status.USER_STATED) if origin == "user"
            else (Origin.EXTERNAL,
                  Status.VERIFIED if trusted else Status.UNVERIFIED)
        )
        ulid = self.memory.write(
            Kind.FACT, [f"{P_SOURCE}{source_id}"],
            origin=org, status=st if org != Origin.EXTERNAL or trusted
            else Status.UNVERIFIED,
            source=f"{P_SOURCE}{source_id}",
            payload={"type": "source", "statement": statement},
        )
        self._sources[source_id] = ulid
        return ulid

    def record_decision(
        self, decision_id: str, statement: str, *, from_sources: list[str],
        unexplained: bool = False, external_event_id: str | None = None,
    ) -> str:
        """A design decision, linked to the sources that informed it.

        `unexplained` marks a decision nobody actually stated. An external
        recorder watching a file write sees the code and not the reason for
        it, and code has to hang off a decision; inventing a plausible one
        would be the worst answer. Recording the absence instead makes "how
        much of this estate has a stated why" a number rather than a guess."""
        component = f"decision:{decision_id}"
        if existing := self._projected(external_event_id, component):
            self._decisions[decision_id] = existing
            return existing
        parents = [self._sources[s] for s in from_sources if s in self._sources]
        subjects = [f"{P_DECISION}{decision_id}"]
        if marker := self._projection_subject(external_event_id, component):
            subjects.append(marker)
        ulid = self.memory.write(
            Kind.FACT, subjects,
            origin=Origin.AGENT, status=Status.UNVERIFIED,
            source="agent:design",
            payload={"type": "decision", "statement": statement,
                     "unexplained": unexplained,
                     "external_event_id": external_event_id},
            derived_from=parents or None,
        )
        self._decisions[decision_id] = ulid
        return ulid

    def knows_decision(self, decision_id: str) -> bool:
        """Whether this recorder can hang code off *decision_id*."""
        return decision_id in self._decisions

    def restore(self, *, sources: dict[str, str] | None = None,
                decisions: dict[str, str] | None = None) -> None:
        """Re-seat the id -> ULID maps from a store that already holds them.

        A recorder normally wrote everything it knows about. One rebuilt
        against an existing store did not, and an external agent's session
        outlives any single process here: without this, code posted after a
        restart could not link to the decision that explains it."""
        self._sources.update(sources or {})
        self._decisions.update(decisions or {})

    # ── activity an external recorder observes ────────────────────────────

    def record_tool(
        self, name: str, detail: str = "", *, external_event_id: str | None = None,
    ) -> str:
        """A tool an agent invoked: a shell command, a package install, a
        test run. Not provenance for any particular line of code, but it is
        how the code came to exist, and a reviewer asking why a package
        appeared has nowhere else to look."""
        component = "tool"
        if existing := self._projected(external_event_id, component):
            return existing
        subjects = [f"{P_TOOL}{name}"]
        if marker := self._projection_subject(external_event_id, component):
            subjects.append(marker)
        return self.memory.write(
            Kind.FACT, subjects,
            origin=Origin.AGENT, status=Status.UNVERIFIED,
            source="agent:tool",
            payload={"type": "tool", "name": name, "detail": detail,
                     "external_event_id": external_event_id},
        )

    # ── code ──────────────────────────────────────────────────────────────

    def record_module(self, source_code: str, *, decision_id: str,
                      module: str = "main",
                      external_event_id: str | None = None) -> str:
        """Record *source_code* verbatim as a module, and nothing more.

        This is the half of `record_code` that holds for any language. The
        class decomposition below it is a Python AST walk, so code this build
        cannot parse still gets recorded and read back -- described as
        undecomposed rather than quietly dropped."""
        if decision_id not in self._decisions:
            raise ValueError(f"unknown decision {decision_id!r}")

        # The text itself is a governed record, not a by-product: every class
        # below is an assertion *about* this module, and a reader who cannot
        # see the module has to take those assertions on trust. It is kept
        # off the provenance graph deliberately -- `module:` is not a node
        # kind there, and a class already carries the decision as its parent.
        component = "module"
        if existing := self._projected(external_event_id, component):
            self._modules[module] = existing
            return existing
        subjects = [f"{P_MODULE}{module}"]
        if marker := self._projection_subject(external_event_id, component):
            subjects.append(marker)
        module_ulid = self.memory.write(
            Kind.FACT, subjects,
            origin=Origin.AGENT, status=Status.VERIFIED,
            source="agent:codegen",
            payload={"type": "module", "name": module, "code": source_code,
                     "external_event_id": external_event_id},
            derived_from=[self._decisions[decision_id]],
        )
        self._modules[module] = module_ulid
        return module_ulid

    def record_code(self, source_code: str, *, decision_id: str,
                    module: str = "main",
                    external_event_id: str | None = None) -> list[str]:
        """Record *source_code* as a module, then each class it defines as a
        hyperedge over the class and the packages it imports. The module is
        kept verbatim so the code can be read back; the classes derive from
        it and from *decision_id*. Returns the ULIDs of the class edges."""
        self.record_module(
            source_code, decision_id=decision_id, module=module,
            external_event_id=external_event_id,
        )
        decision_ulid = self._decisions[decision_id]

        written: list[str] = []
        for class_index, cls in enumerate(extract_code_entities(source_code)):
            component = f"class:{class_index}"
            members = [f"{P_CLASS}{cls.name}"]
            members += [f"{P_PACKAGE}{p}" for p in cls.packages]
            if marker := self._projection_subject(external_event_id, component):
                members.append(marker)
            ulid = self._projected(external_event_id, component)
            if ulid is None:
                ulid = self.memory.write(
                    Kind.FACT, members,
                    origin=Origin.AGENT, status=Status.VERIFIED,
                    source="agent:codegen",
                    payload={
                        "type": "class",
                        "name": cls.name,
                        "packages": cls.packages,
                        "bases": cls.bases,
                        "module": module,
                        "external_event_id": external_event_id,
                    },
                    derived_from=[decision_ulid],
                )
            self._classes[cls.name] = ulid
            written.append(ulid)
            # each dangerous call site becomes its own n-ary finding edge:
            # {class, sink, cwe, capability}, derived from the class.
            for sink_index, s in enumerate(cls.sinks):
                sink_component = f"class:{class_index}:sink:{sink_index}"
                if self._projected(external_event_id, sink_component):
                    continue
                members = [
                    f"{P_CLASS}{cls.name}",
                    f"{P_SINK}{s.call}",
                    f"{P_CAP}{s.capability}",
                ]
                if s.cwe:
                    members.append(f"{P_CWE}{s.cwe}")
                if marker := self._projection_subject(
                    external_event_id, sink_component,
                ):
                    members.append(marker)
                self.memory.write(
                    Kind.FACT, members,
                    origin=Origin.AGENT, status=Status.VERIFIED,
                    source="agent:security-scan",
                    payload={
                        "type": "finding",
                        "call": s.call,
                        "cwe": s.cwe,
                        "cwe_title": s.cwe_title,
                        "capability": s.capability,
                        "shell_true": s.shell_true,
                        "class": cls.name,
                        "external_event_id": external_event_id,
                    },
                    derived_from=[ulid],
                )
            # reachability, where the scan can actually point at the path.
            # No severity is attached: the scan establishes that a value from
            # outside reaches the call, which is not the same as knowing how
            # bad that is, and inventing a rating would undo the point.
            for taint_index, t in enumerate(cls.taints):
                taint_component = f"class:{class_index}:taint:{taint_index}"
                if self._projected(external_event_id, taint_component):
                    continue
                members = [f"{P_SINK}{t.sink}", f"{P_ENTRY}{t.entry}"]
                members += [f"{P_CWE}{t.cwe}"] if t.cwe else []
                if marker := self._projection_subject(
                    external_event_id, taint_component,
                ):
                    members.append(marker)
                self.memory.write(
                    Kind.FACT,
                    members,
                    origin=Origin.AGENT, status=Status.VERIFIED,
                    source="agent:taint-scan",
                    payload={
                        "type": "taint", "sink": t.sink, "entry": t.entry,
                        "entry_kind": t.entry_kind, "cwe": t.cwe,
                        "severity": None, "rule": "py/external-data-reaches-sink",
                        "flow": t.flow, "reachable": True, "class": t.owner,
                        "external_event_id": external_event_id,
                    },
                    derived_from=[ulid],
                )
        return written

    # ── SBOM: versions and licenses ───────────────────────────────────────

    def record_version(
        self, package: str, version: str, *, license: str,
        external_event_id: str | None = None,
    ) -> str:
        """One SBOM entry as an n-ary edge {version, package, license}. In a
        real run these come from the lockfile; here they are supplied."""
        component = "version"
        if existing := self._projected(external_event_id, component):
            return existing
        vsub = f"{P_VERSION}{package}@{version}"
        members = [vsub, f"{P_PACKAGE}{package}", f"{P_LICENSE}{license}"]
        if marker := self._projection_subject(external_event_id, component):
            members.append(marker)
        return self.memory.write(
            Kind.FACT, members,
            origin=Origin.AGENT, status=Status.VERIFIED, source="agent:sbom",
            payload={
                "type": "version", "package": package, "version": version,
                "license": license,
                "external_event_id": external_event_id,
            },
        )

    # ── CVE / advisory (sample feed; real OSV join is the next phase) ──────

    def record_cve(
        self, cve_id: str, *, affects: list[tuple[str, str]],
        severity: str, summary: str, cwe: str | None = None,
        feed: str = "sample",
    ) -> str:
        """An advisory as an n-ary edge {cve, affected version(s), cwe?}.
        `feed` is recorded so the UI can label sample data honestly; a real
        deployment sets feed='osv' after the OSV join."""
        members = [f"{P_CVE}{cve_id}"]
        members += [f"{P_VERSION}{p}@{v}" for p, v in affects]
        if cwe:
            members.append(f"{P_CWE}{cwe}")
        return self.memory.write(
            Kind.FACT, members,
            origin=Origin.EXTERNAL, status=Status.UNVERIFIED,
            source=f"feed:{feed}",
            payload={
                "type": "cve", "id": cve_id, "severity": severity,
                "summary": summary, "cwe": cwe, "feed": feed,
                "affects": [f"{p}@{v}" for p, v in affects],
            },
        )

    # ── reachability: taint findings from a SARIF scan ────────────────────

    def record_taint(
        self, *, sink_call: str, entry: str, cwe: str | None = None,
        severity: str | None = None, rule: str | None = None,
        flow: list[str] | None = None, tool: str | None = None,
    ) -> str:
        """Record that a dangerous call is REACHABLE from an untrusted entry
        point, as an n-ary edge {sink, entry, cwe?}. This is the evidence
        that upgrades a finding from 'present' to 'exploitable', and it is a
        scanner result, so it enters as gated external content.

        `tool` names the scanner that proved it. Two analysers can disagree
        about the same sink, and a reader who cannot see which one spoke
        cannot weigh the claim."""
        members = [f"{P_SINK}{sink_call}", f"{P_ENTRY}{entry}"]
        if cwe:
            members.append(f"{P_CWE}{cwe}")
        return self.memory.write(
            Kind.FACT, members,
            origin=Origin.EXTERNAL, status=Status.UNVERIFIED,
            source=f"tool:{tool}" if tool else "tool:sarif",
            payload={
                "type": "taint", "sink": sink_call, "entry": entry,
                "cwe": cwe, "severity": severity, "rule": rule,
                "flow": flow or [], "reachable": True, "tool": tool,
            },
        )

    def record_scan(
        self, *, tool: str, modules: list[str], results: int = 0,
        reachable: int = 0, at: int | None = None,
    ) -> str:
        """Record that *tool* examined *modules*, whatever it found.

        Without this there is no way to tell a sink a scanner cleared from a
        sink no scanner ever looked at, and the two would render identically
        as "not exploitable" -- which is the single most misleading thing a
        security view can do. Coverage is the evidence for a negative, so it
        has to be recorded as deliberately as a hit."""
        covered = sorted({m for m in modules if m})
        return self.memory.write(
            Kind.FACT,
            [f"{P_MODULE}{m}" for m in covered] or [f"{P_MODULE}"],
            origin=Origin.EXTERNAL, status=Status.UNVERIFIED,
            source=f"tool:{tool}",
            payload={
                "type": "scan", "tool": tool, "modules": covered,
                "results": results, "reachable": reachable,
                "at": at if at is not None else int(time.time()),
            },
        )

    # ── export for visualization ──────────────────────────────────────────

    def export(self) -> dict[str, Any]:
        """The code graph as viz-friendly JSON: typed nodes plus the two
        link flavors a layered provenance view needs — `imports`
        (class -> package) and `derives` (source -> decision -> class).
        Tombstoned nodes are marked so a forget lights up its blast radius."""
        return export_graph(self.memory)


def _node_kind(name: str) -> str:
    for pfx, kind in (
        (P_SOURCE, "source"), (P_DECISION, "decision"),
        (P_CLASS, "class"), (P_PACKAGE, "package"),
        (P_SINK, "sink"), (P_CWE, "cwe"), (P_CAP, "capability"),
        (P_VERSION, "version"), (P_LICENSE, "license"), (P_CVE, "cve"),
        (P_ENTRY, "entry"),
    ):
        if name.startswith(pfx):
            return kind
    return "other"


def export_graph(memory: MemoryStore) -> dict[str, Any]:
    """Walk the code-graph edges in memory and emit nodes + links for the
    visualization. Reads only edges whose members are code entities, so it
    ignores unrelated beliefs in the same store."""
    verbs = Verbs(memory)
    nodes: dict[str, dict[str, Any]] = {}
    imports: list[dict[str, str]] = []
    derives: list[dict[str, str]] = []
    security: list[dict[str, str]] = []
    seen_edges: set[str] = set()

    def ensure_node(name: str, **extra: Any) -> None:
        n = nodes.setdefault(name, {"id": name, "kind": _node_kind(name)})
        n.update({k: v for k, v in extra.items() if v is not None})

    sbom: list[dict[str, str]] = []
    taint: list[dict[str, Any]] = []
    ctx = {"imports": imports, "derives": derives, "security": security,
           "sbom": sbom, "taint": taint}

    # discover code entities via the store's registry (one id space)
    for name in _all_entity_names(memory):
        if _node_kind(name) == "other":
            continue
        for ulid in memory.find_by_subject(name):
            if ulid in seen_edges:
                continue
            m = memory.get(ulid)
            if m is None or m.content is None:
                continue
            ctype = m.content.get("type")
            if ctype not in (
                "source", "decision", "class", "finding", "version", "cve",
                "taint",
            ):
                continue
            seen_edges.add(ulid)
            _emit_edge(m, ulid, memory, verbs, ensure_node, ctx)

    return {
        "nodes": list(nodes.values()),
        "imports": imports,
        "derives": derives,
        "security": security,
        "sbom": ctx["sbom"],
        "taint": ctx["taint"],
    }


def _emit_edge(m, ulid, memory, verbs, ensure_node, ctx) -> None:
    ctype = m.content["type"]

    if ctype == "finding":
        # an n-ary security finding: {class, sink, capability, cwe?}
        c = m.content
        cls = f"{P_CLASS}{c['class']}"
        sink = f"{P_SINK}{c['call']}"
        cap = f"{P_CAP}{c['capability']}"
        ensure_node(cls)
        ensure_node(
            sink, statement=c["call"], cwe=c.get("cwe") or None,
            cwe_title=c.get("cwe_title") or None,
            shell_true=c.get("shell_true"),
            tombstoned=m.tombstoned,
        )
        ensure_node(cap, statement=c["capability"])
        ctx["security"].append({"source": cls, "target": sink, "rel": "calls"})
        ctx["security"].append({"source": sink, "target": cap, "rel": "grants"})
        if c.get("cwe"):
            cwe = f"{P_CWE}{c['cwe']}"
            ensure_node(cwe, statement=c.get("cwe_title"))
            ctx["security"].append({"source": sink, "target": cwe, "rel": "weakness"})
        return

    if ctype == "version":
        c = m.content
        vsub = f"{P_VERSION}{c['package']}@{c['version']}"
        pkg = f"{P_PACKAGE}{c['package']}"
        lic = f"{P_LICENSE}{c['license']}"
        ensure_node(vsub, statement=f"{c['package']}@{c['version']}",
                    version=c["version"], package=c["package"])
        ensure_node(pkg)
        ensure_node(lic, statement=c["license"])
        ctx["sbom"].append({"source": pkg, "target": vsub, "rel": "has_version"})
        ctx["sbom"].append({"source": vsub, "target": lic, "rel": "licensed"})
        return

    if ctype == "cve":
        c = m.content
        cve = f"{P_CVE}{c['id']}"
        ensure_node(
            cve, statement=c.get("summary"), severity=c.get("severity"),
            feed=c.get("feed"), cwe=c.get("cwe") or None,
            tombstoned=m.tombstoned,
        )
        for v in c.get("affects", []):
            vsub = f"{P_VERSION}{v}"
            ensure_node(vsub)
            ctx["sbom"].append({"source": cve, "target": vsub, "rel": "affects"})
        return

    if ctype == "taint":
        # a reachability edge from a scanner: {sink, entry, cwe?}. It upgrades
        # the sink from "present" to "exploitable" and draws entry -> sink.
        c = m.content
        sink = f"{P_SINK}{c['sink']}"
        entry = f"{P_ENTRY}{c['entry']}"
        ensure_node(
            entry, statement=c["entry"], severity=c.get("severity") or None,
        )
        # mark the sink exploitable in place, carrying the taint's severity
        ensure_node(
            sink, statement=c["sink"], exploitable=True,
            severity=c.get("severity") or None,
            cwe=c.get("cwe") or None, tombstoned=m.tombstoned,
        )
        rec = {"source": entry, "target": sink, "rel": "reaches",
               "severity": c.get("severity"), "rule": c.get("rule"),
               "flow": c.get("flow") or []}
        ctx["security"].append(rec)
        ctx["taint"].append(rec)
        if c.get("cwe"):
            cwe = f"{P_CWE}{c['cwe']}"
            ensure_node(cwe)
            ctx["security"].append(
                {"source": sink, "target": cwe, "rel": "weakness"})
        return

    subj = next(
        (n for n in m.member_names if not n.startswith("edge:")
         and _node_kind(n) not in ("package", "sink", "cwe", "capability")),
        None,
    )
    if subj is None:
        return
    ensure_node(
        subj,
        status=m.envelope.status.name.lower(),
        tombstoned=m.tombstoned,
        statement=(m.content or {}).get("statement")
        or (m.content or {}).get("name"),
    )
    if ctype == "class":
        for pkg in [n for n in m.member_names if n.startswith(P_PACKAGE)]:
            ensure_node(pkg)
            ctx["imports"].append({"source": subj, "target": pkg})
    # provenance parents (source->decision, decision->class)
    for p in verbs.why(ulid, max_depth=1).flatten()[1:]:
        pm = memory.get(p["ulid"])
        if pm is None or pm.content is None:
            continue
        psubj = next(
            (n for n in pm.member_names if not n.startswith("edge:")
             and _node_kind(n) not in ("package", "sink", "cwe", "capability")),
            None,
        )
        if psubj is not None:
            ctx["derives"].append({"source": psubj, "target": subj, "rel": p["via"]})


def cve_impact(memory: MemoryStore, cve_id: str) -> dict[str, list[str]]:
    """The blast radius of an advisory: the versions it affects, the
    packages those are, the classes importing them, and the decisions those
    classes rest on. This is the CVE-drops-tonight view — everything that
    inherits the risk, one traversal over the same graph."""
    g = export_graph(memory)
    imported_by: dict[str, list[str]] = {}
    for e in g["imports"]:
        imported_by.setdefault(e["target"], []).append(e["source"])
    parents: dict[str, list[str]] = {}
    for e in g["derives"]:
        parents.setdefault(e["target"], []).append(e["source"])

    cve = f"{P_CVE}{cve_id}"
    versions = [e["target"] for e in g["sbom"]
                if e["source"] == cve and e["rel"] == "affects"]
    pkgs = sorted({
        f"{P_PACKAGE}{v.split(':', 1)[1].split('@')[0]}" for v in versions
    })
    classes = sorted({c for p in pkgs for c in imported_by.get(p, [])})
    decisions = sorted({
        d for c in classes for d in parents.get(c, [])
        if d.startswith(P_DECISION)
    })
    return {
        "versions": versions, "packages": pkgs,
        "classes": classes, "decisions": decisions,
    }


def _all_entity_names(memory: MemoryStore) -> list[str]:
    """Every registered entity name, via the store's SQLite registry."""
    reg = memory._registry  # one id space; read all names
    cur = reg._conn().execute("SELECT name FROM entities")
    return [str(r[0]) for r in cur.fetchall()]
