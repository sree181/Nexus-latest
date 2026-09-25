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
import hashlib
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
P_FUNCTION = "function:"
P_API = "api:"
P_FEED = "feed:"
P_FIX = "fix:"
P_POLICY = "policy:"
P_REVIEW = "review:"
P_REVIEW_EVENT = "review-event:"
P_SESSION = "session:"
P_REPOSITORY = "repository:"
P_ACTOR = "actor:"
P_POLICY_VERSION = "policy-version:"
P_GOVERNANCE_EVENT = "governance-event:"
P_EXCEPTION = "exception:"
P_SCOPE = "scope:"
P_CONTROL = "control:"
P_NATIVE_ROOT = "native-root:"


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


@dataclass
class ExtractedFunction:
    """One function or method and the imported package APIs it invokes."""

    name: str
    invocations: list[tuple[str, str]]


@dataclass
class ExtractedCode:
    """Deterministic native relationships extracted from one Python module."""

    module_packages: list[str]
    classes: list[ExtractedClass]
    functions: list[ExtractedFunction]


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


def _declared_packages(tree: ast.AST) -> list[str]:
    packages: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            packages.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            packages.add(node.module.split(".", 1)[0])
    return sorted(packages)


def _root_names_in_class(node: ast.ClassDef) -> set[str]:
    """Names used by this class while excluding nested class scopes."""
    names: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, child: ast.ClassDef) -> None:  # noqa: N802
            if child is node:
                self.generic_visit(child)

        def visit_Name(self, child: ast.Name) -> None:  # noqa: N802
            names.add(child.id)

        def visit_Attribute(self, child: ast.Attribute) -> None:  # noqa: N802
            base: ast.AST = child
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                names.add(base.id)
            self.generic_visit(child)

    Visitor().visit(node)
    return names


def _call_root(node: ast.Call) -> str | None:
    func = node.func
    while isinstance(func, ast.Attribute):
        func = func.value
    return func.id if isinstance(func, ast.Name) else None


def _function_invocations(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    call_aliases: dict[str, str], aliases: dict[str, str],
) -> list[tuple[str, str]]:
    found: set[tuple[str, str]] = set()

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, child: ast.FunctionDef) -> None:  # noqa: N802
            if child is node:
                self.generic_visit(child)

        def visit_AsyncFunctionDef(self, child: ast.AsyncFunctionDef) -> None:  # noqa: N802
            if child is node:
                self.generic_visit(child)

        def visit_ClassDef(self, child: ast.ClassDef) -> None:  # noqa: N802
            return

        def visit_Call(self, child: ast.Call) -> None:  # noqa: N802
            root = _call_root(child)
            if root is not None and root in call_aliases:
                api = _call_name(child, call_aliases)
                package = aliases.get(root)
                if api and package:
                    found.add((package, api))
            self.generic_visit(child)

    Visitor().visit(node)
    return sorted(found)


def extract_code_relationships(source: str) -> ExtractedCode:
    """Return exact Python module, class, and function package relations."""
    tree = ast.parse(source)
    aliases = _alias_map(tree)
    call_aliases = _call_alias_map(tree)
    classes: list[ExtractedClass] = []
    functions: list[ExtractedFunction] = []

    def walk(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                refs = _root_names_in_class(node)
                classes.append(ExtractedClass(
                    name=node.name,
                    packages=sorted({aliases[name] for name in refs if name in aliases}),
                    bases=[ast.unparse(base) if hasattr(ast, "unparse") else ""
                           for base in node.bases],
                    sinks=_detect_sinks(node, call_aliases),
                    taints=_detect_taint(node, call_aliases),
                ))
                walk(node.body, f"{prefix}{node.name}.")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                functions.append(ExtractedFunction(
                    name=f"{prefix}{node.name}",
                    invocations=_function_invocations(node, call_aliases, aliases),
                ))

    walk(tree.body)
    return ExtractedCode(
        module_packages=_declared_packages(tree),
        classes=classes,
        functions=functions,
    )


def extract_code_entities(source: str) -> list[ExtractedClass]:
    """Parse *source* and return each class with the packages it uses.

    Deterministic: this is the mechanical half of code provenance, so it
    never guesses. A syntax error raises, by design — you cannot capture the
    provenance of code that does not parse."""
    return extract_code_relationships(source).classes


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
        module_ulid = self.record_module(
            source_code, decision_id=decision_id, module=module,
            external_event_id=external_event_id,
        )
        decision_ulid = self._decisions[decision_id]
        extracted = extract_code_relationships(source_code)

        for import_index, package in enumerate(extracted.module_packages):
            component = f"module-import:{import_index}"
            if self._projected(external_event_id, component):
                continue
            members = [f"{P_MODULE}{module}", f"{P_PACKAGE}{package}"]
            if marker := self._projection_subject(external_event_id, component):
                members.append(marker)
            self.memory.write(
                Kind.FACT, members,
                origin=Origin.AGENT, status=Status.VERIFIED,
                source="agent:code-analysis",
                payload={
                    "type": "module_import", "module": module,
                    "packages": [package],
                    "external_event_id": external_event_id,
                },
                derived_from=[module_ulid],
            )

        for function_index, function in enumerate(extracted.functions):
            for invocation_index, (package, api) in enumerate(function.invocations):
                component = f"function:{function_index}:invoke:{invocation_index}"
                if self._projected(external_event_id, component):
                    continue
                function_id = f"{P_FUNCTION}{module}:{function.name}"
                members = [function_id, f"{P_API}{api}", f"{P_PACKAGE}{package}"]
                if marker := self._projection_subject(external_event_id, component):
                    members.append(marker)
                self.memory.write(
                    Kind.FACT, members,
                    origin=Origin.AGENT, status=Status.VERIFIED,
                    source="agent:code-analysis",
                    payload={
                        "type": "invocation", "module": module,
                        "function": function.name, "api": api,
                        "package": package,
                        "external_event_id": external_event_id,
                    },
                    derived_from=[module_ulid],
                )

        written: list[str] = []
        for class_index, cls in enumerate(extracted.classes):
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
        feed: str = "sample", ecosystem: str | None = None,
        fixed_versions: list[str] | None = None,
        references: list[str] | None = None,
        external_event_id: str | None = None,
    ) -> str:
        """An advisory as an n-ary edge {cve, affected version(s), cwe?}.
        `feed` is recorded so the UI can label sample data honestly; a real
        deployment sets feed='osv' after the OSV join."""
        component = f"policy-cve:{cve_id}"
        if existing := self._projected(external_event_id, component):
            return existing
        fixed = sorted(set(fixed_versions or []))
        refs = sorted(set(references or []))
        members = [f"{P_CVE}{cve_id}", f"{P_FEED}{feed}"]
        members += [f"{P_VERSION}{p}@{v}" for p, v in affects]
        members += [f"{P_FIX}{p}@{v}" for p, _ in affects for v in fixed]
        if cwe:
            members.append(f"{P_CWE}{cwe}")
        if marker := self._projection_subject(external_event_id, component):
            members.append(marker)
        return self.memory.write(
            Kind.FACT, members,
            origin=Origin.EXTERNAL, status=Status.UNVERIFIED,
            source=f"feed:{feed}",
            payload={
                "type": "cve", "id": cve_id, "severity": severity,
                "summary": summary, "cwe": cwe, "feed": feed,
                "affects": [f"{p}@{v}" for p, v in affects],
                "ecosystem": ecosystem, "fixed_versions": fixed,
                "references": refs, "external_event_id": external_event_id,
            },
        )

    def record_policy_evaluation(
        self, *, evaluation_id: str, session_id: str, repository_id: str,
        package: str, version: str, ecosystem: str, verdict: str,
        reasons: list[str], policy: str, advisories: list[dict[str, Any]],
        unavailable: str | None = None, external_event_id: str | None = None,
    ) -> list[str]:
        """Record a package policy observation without asserting installation."""
        written: list[str] = []
        advisory_ulids: list[str] = []
        for advisory in advisories:
            ulid = self.record_cve(
                str(advisory["id"]), affects=[(package, version)],
                severity=str(advisory.get("severity") or "unknown"),
                summary=str(advisory.get("summary") or ""),
                cwe=advisory.get("cwe"), feed="osv", ecosystem=ecosystem,
                fixed_versions=list(advisory.get("fixed_versions") or []),
                references=list(advisory.get("references") or []),
                external_event_id=external_event_id,
            )
            advisory_ulids.append(ulid)
            written.append(ulid)

        component = "policy-evaluation"
        if existing := self._projected(external_event_id, component):
            written.append(existing)
            return written
        members = [
            f"{P_POLICY}{evaluation_id}", f"{P_SESSION}{session_id}",
            f"{P_REPOSITORY}{repository_id}", f"{P_PACKAGE}{package}",
        ]
        if version:
            members.append(f"{P_VERSION}{package}@{version}")
        members += [f"{P_CVE}{item['id']}" for item in advisories]
        if marker := self._projection_subject(external_event_id, component):
            members.append(marker)
        policy_ulid = self.memory.write(
            Kind.FACT, members,
            origin=Origin.AGENT, status=Status.VERIFIED,
            source="agent:package-policy",
            payload={
                "type": "policy_evaluation", "evaluation_id": evaluation_id,
                "session_id": session_id, "repository_id": repository_id,
                "package": package, "version": version,
                "ecosystem": ecosystem, "verdict": verdict,
                "reasons": reasons, "policy": policy,
                "advisory_ids": [str(item["id"]) for item in advisories],
                "unavailable": unavailable,
                "external_event_id": external_event_id,
            },
            derived_from=advisory_ulids or None,
        )
        written.append(policy_ulid)
        return written

    def record_review_event(
        self, *, request_id: str, event_id: str, action: str,
        to_state: str, actor: str, actor_role: str, occurred_at: int,
        canonical_sha256: str, snapshot: dict[str, Any],
        parent_ulids: list[str] | None = None,
    ) -> str:
        """Append one immutable review episode under a stable projection key."""
        component = "review-event"
        if existing := self._projected(event_id, component):
            return existing
        members = [
            f"{P_REVIEW}{request_id}", f"{P_REVIEW_EVENT}{event_id}",
            f"{P_ACTOR}{actor}",
        ]
        for key, prefix in (
            ("session_id", P_SESSION), ("repository_id", P_REPOSITORY),
            ("policy_evaluation_id", P_POLICY),
        ):
            if snapshot.get(key):
                members.append(f"{prefix}{snapshot[key]}")
        package = str(snapshot.get("package") or "")
        version = str(snapshot.get("version") or "")
        if package:
            members.append(f"{P_PACKAGE}{package}")
        if package and version:
            members.append(f"{P_VERSION}{package}@{version}")
        members += [str(value) for value in (snapshot.get("code_entity_ids") or [])[:24]]
        members += [
            f"{P_CVE}{value}" for value in (snapshot.get("advisory_ids") or [])[:24]
        ]
        if marker := self._projection_subject(event_id, component):
            members.append(marker)
        return self.memory.write(
            Kind.EPISODE, list(dict.fromkeys(members)),
            origin=Origin.AGENT, status=Status.VERIFIED,
            source="agent:review-workflow", event_ts=occurred_at,
            payload={
                "type": "review_event", "request_id": request_id,
                "event_id": event_id, "action": action,
                "to_state": to_state, "actor_role": actor_role,
                "canonical_sha256": canonical_sha256,
            },
            derived_from=parent_ulids or None,
        )

    def record_governance_event(
        self, *, event_id: str, relation_kind: str, resource_kind: str,
        resource_id: str, actor: str, occurred_at: int,
        canonical_sha256: str, payload: dict[str, Any],
        parent_ulids: list[str] | None = None,
    ) -> str:
        """Append one immutable, digest-bound governance episode.

        The relational outbox event is the idempotency key.  The canonical
        payload is frozen by the control plane before this method runs; this
        writer never rebuilds a decision from current mutable state.
        """
        if existing := self._projected(event_id, relation_kind):
            return existing
        members = [
            f"{P_GOVERNANCE_EVENT}{event_id}", f"{P_ACTOR}{actor}",
        ]
        policy = dict(payload.get("policy") or {})
        exception = dict(payload.get("exception") or {})
        if resource_kind == "policy":
            members.append(f"{P_POLICY}{resource_id}")
        else:
            members.append(f"{P_EXCEPTION}{resource_id}")
        if policy:
            policy_id = str(policy.get("id") or resource_id)
            version = policy.get("version")
            members.append(f"{P_POLICY}{policy_id}")
            if version is not None:
                members.append(f"{P_POLICY_VERSION}{policy_id}@{version}")
            if predecessor := policy.get("predecessor_version"):
                members.append(f"{P_POLICY_VERSION}{policy_id}@{predecessor}")
        if exception:
            exception_id = str(exception.get("id") or resource_id)
            policy_id = str(exception.get("policy_id") or "")
            policy_version = exception.get("policy_version")
            members.append(f"{P_EXCEPTION}{exception_id}")
            if policy_id:
                members.append(f"{P_POLICY}{policy_id}")
                if policy_version is not None:
                    members.append(
                        f"{P_POLICY_VERSION}{policy_id}@{policy_version}"
                    )
            if exception.get("scope"):
                members.append(f"{P_SCOPE}{exception['scope']}")
            if exception.get("owner"):
                members.append(f"{P_ACTOR}{exception['owner']}")
            if exception.get("compensating_controls"):
                control_digest = hashlib.sha256(
                    str(exception["compensating_controls"]).encode()
                ).hexdigest()[:24]
                members.append(f"{P_CONTROL}{control_digest}")
            if predecessor := exception.get("predecessor_exception_id"):
                members.append(f"{P_EXCEPTION}{predecessor}")
            if successor := exception.get("superseded_by_exception_id"):
                members.append(f"{P_EXCEPTION}{successor}")
        for item in payload.get("resolved_evidence") or []:
            kind, linked = item.get("kind"), item.get("resource_id")
            if kind in ("review", "case") and linked:
                members.append(f"{kind}:{linked}")
            if item.get("resolved") and item.get("native_ulid"):
                members.append(f"{P_NATIVE_ROOT}{item['native_ulid']}")
        if marker := self._projection_subject(event_id, relation_kind):
            members.append(marker)
        return self.memory.write(
            Kind.EPISODE, list(dict.fromkeys(members)),
            origin=Origin.AGENT, status=Status.VERIFIED,
            source="agent:governance-workflow", event_ts=occurred_at,
            payload={
                **payload,
                "type": relation_kind,
                "canonical_sha256": canonical_sha256,
            },
            derived_from=parent_ulids or None,
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
        (P_MODULE, "module"), (P_FUNCTION, "function"), (P_API, "api"),
        (P_CLASS, "class"), (P_PACKAGE, "package"),
        (P_SINK, "sink"), (P_CWE, "cwe"), (P_CAP, "capability"),
        (P_VERSION, "version"), (P_LICENSE, "license"), (P_CVE, "cve"),
        (P_ENTRY, "entry"), (P_FEED, "source"), (P_FIX, "version"),
        (P_POLICY, "policy"), (P_REVIEW, "review"),
        (P_REVIEW_EVENT, "review_event"), (P_SESSION, "session"),
        (P_REPOSITORY, "repository"), (P_ACTOR, "agent"),
        (P_POLICY_VERSION, "policy_version"),
        (P_GOVERNANCE_EVENT, "governance_event"),
        (P_EXCEPTION, "exception"), (P_SCOPE, "other"),
        (P_CONTROL, "other"), (P_NATIVE_ROOT, "other"),
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
    module_imports: list[dict[str, str]] = []
    invokes: list[dict[str, str]] = []
    api_of: list[dict[str, str]] = []
    derives: list[dict[str, str]] = []
    security: list[dict[str, str]] = []
    seen_edges: set[str] = set()

    def ensure_node(name: str, **extra: Any) -> None:
        n = nodes.setdefault(name, {"id": name, "kind": _node_kind(name)})
        n.update({k: v for k, v in extra.items() if v is not None})

    sbom: list[dict[str, str]] = []
    taint: list[dict[str, Any]] = []
    ctx = {"imports": imports, "module_imports": module_imports,
           "invokes": invokes, "api_of": api_of,
           "derives": derives, "security": security,
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
                "source", "decision", "module", "module_import", "class",
                "invocation", "finding", "version", "cve", "taint",
                "policy_evaluation", "review_event",
            ):
                continue
            seen_edges.add(ulid)
            _emit_edge(m, ulid, memory, verbs, ensure_node, ctx)

    return {
        "nodes": list(nodes.values()),
        "imports": imports,
        "module_imports": module_imports,
        "invokes": invokes,
        "api_of": api_of,
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
        feed = f"{P_FEED}{c.get('feed') or 'unknown'}"
        ensure_node(feed, statement=(c.get("feed") or "unknown").upper())
        ctx["sbom"].append({"source": cve, "target": feed, "rel": "reported_by"})
        for affected in c.get("affects", []):
            package = affected.split("@", 1)[0]
            for version in c.get("fixed_versions") or []:
                fix = f"{P_FIX}{package}@{version}"
                ensure_node(fix, statement=f"{package}@{version}")
                ctx["sbom"].append({"source": cve, "target": fix, "rel": "fixed_by"})
        return

    if ctype == "module_import":
        module = f"{P_MODULE}{m.content['module']}"
        ensure_node(module, statement=m.content["module"])
        for package in m.content.get("packages") or []:
            pkg = f"{P_PACKAGE}{package}"
            ensure_node(pkg)
            ctx["module_imports"].append(
                {"source": module, "target": pkg, "rel": "imports"})
        return

    if ctype == "invocation":
        function = f"{P_FUNCTION}{m.content['module']}:{m.content['function']}"
        api = f"{P_API}{m.content['api']}"
        package = f"{P_PACKAGE}{m.content['package']}"
        ensure_node(function, statement=m.content["function"])
        ensure_node(api, statement=m.content["api"])
        ensure_node(package)
        ctx["invokes"].append(
            {"source": function, "target": api, "rel": "invokes"})
        ctx["api_of"].append(
            {"source": api, "target": package, "rel": "api_of"})
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
