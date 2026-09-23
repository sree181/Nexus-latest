"""
Project:     MeshAgent
File:        meshagent/sarif.py
Description: The SARIF ingest. Turns a real CodeQL / Semgrep / Bandit run's
             taint results into `reachable-from` edges in the governed graph,
             upgrading a finding from "present" (a dangerous call exists) to
             "exploitable" (untrusted input actually reaches it). A SARIF
             result carries a codeFlow/threadFlow exactly when the scanner
             traced a path from an untrusted source to the sink, so the
             presence of that flow IS the reachability evidence — MeshAgent
             does not run its own taint engine, it ingests the one the
             scanner already ran. Findings enter as EXTERNAL/UNVERIFIED
             through the same write gate as any third-party fact, so
             reachability intel is governed like everything else.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from hypermeshdb.agentmem import MemoryStore

from .codegraph import CodeGraphRecorder

# CodeQL/Semgrep tag the weakness with an external taxonomy reference such as
# "external/cwe/cwe-502"; pull the CWE id out of any tag shaped that way.
_CWE_TAG = re.compile(r"cwe[-/](\d+)", re.IGNORECASE)

# how a sink call is spelled varies by tool; map the common rule ids and
# message fragments onto the canonical dotted call name the code graph uses.
_SINK_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"unsafe.?deserial|pickle", re.I), "pickle.load"),
    (re.compile(r"yaml.?load", re.I), "yaml.load"),
    (re.compile(r"marshal", re.I), "marshal.load"),
    (re.compile(r"command.?injection|os.?system|subprocess", re.I),
     "subprocess.run"),
    (re.compile(r"code.?injection|eval|exec", re.I), "eval"),
    (re.compile(r"weak.?(crypto|hash)|md5|sha1", re.I), "hashlib.md5"),
]

_SEV_BANDS = [
    (9.0, "critical"), (7.0, "high"), (4.0, "medium"), (0.1, "low"),
]


@dataclass
class TaintFinding:
    """One reachable finding lifted from a SARIF result. `reachable` is True
    exactly when the scanner attached a code/thread flow, which is the whole
    point: no flow, no reachability claim."""

    sink_call: str                # canonical dotted call, e.g. "pickle.load"
    entry: str                    # the untrusted entry point (flow origin)
    cwe: str | None
    severity: str                 # critical|high|medium|low|unknown
    rule: str                     # the scanner rule id
    reachable: bool               # a codeFlow/threadFlow was present
    flow: list[str] = field(default_factory=list)   # ordered step locations


def _sev_from_score(score: str | None) -> str:
    if score is None:
        return "unknown"
    try:
        val = float(score)
    except (TypeError, ValueError):
        return "unknown"
    for lo, label in _SEV_BANDS:
        if val >= lo:
            return label
    return "unknown"


def _cwe_from_tags(tags: list[str]) -> str | None:
    for t in tags:
        mt = _CWE_TAG.search(t)
        if mt:
            return f"CWE-{mt.group(1)}"
    return None


def _canonical_sink(rule_id: str, message: str) -> str:
    hay = f"{rule_id} {message}"
    for pat, call in _SINK_HINTS:
        if pat.search(hay):
            return call
    # fall back to the last dotted token in the rule id (py/unsafe-deserialization
    # -> unsafe-deserialization); keeps the finding rather than dropping it.
    return rule_id.rsplit("/", 1)[-1] or rule_id


def _phys(loc: dict[str, Any]) -> str:
    """A compact 'file:line' label from a SARIF physicalLocation."""
    pl = (loc.get("physicalLocation") or {})
    uri = (((pl.get("artifactLocation") or {}).get("uri")) or "?")
    line = ((pl.get("region") or {}).get("startLine"))
    return f"{uri}:{line}" if line is not None else uri


def _flow_locations(result: dict[str, Any]) -> list[str]:
    """Ordered locations of the first threadFlow, source to sink. Empty when
    the result has no code flow (i.e. the finding is not proven reachable)."""
    steps: list[str] = []
    for cf in result.get("codeFlows", []) or []:
        for tf in cf.get("threadFlows", []) or []:
            for loc in tf.get("locations", []) or []:
                inner = (loc.get("location") or {})
                steps.append(_phys(inner))
            if steps:
                return steps
    return steps


def _rule_meta(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index the run's rule metadata by id, so a result can read its tags and
    security-severity even though SARIF stores them on the rule, not the hit."""
    driver = ((run.get("tool") or {}).get("driver") or {})
    out: dict[str, dict[str, Any]] = {}
    for r in driver.get("rules", []) or []:
        rid = r.get("id")
        if rid:
            out[rid] = r
    return out


def sarif_findings(sarif: dict[str, Any]) -> list[TaintFinding]:
    """Parse a SARIF document into taint findings. Only results carrying a
    codeFlow are reported: that flow is the scanner's proof that untrusted
    input reaches the sink, which is exactly the 'reachable-from' evidence
    this ingest exists to capture."""
    out: list[TaintFinding] = []
    for run in sarif.get("runs", []) or []:
        rules = _rule_meta(run)
        for res in run.get("results", []) or []:
            flow = _flow_locations(res)
            if not flow:
                continue   # no flow -> not proven reachable -> skip
            rule_id = res.get("ruleId", "")
            rule = rules.get(rule_id, {})
            props = rule.get("properties", {}) or {}
            tags = list(props.get("tags", []) or [])
            # severity: prefer the result-level security-severity, then rule
            sev_score = (
                (res.get("properties") or {}).get("security-severity")
                or props.get("security-severity")
            )
            cwe = _cwe_from_tags(tags) or _cwe_from_tags(
                [rule_id, res.get("ruleId", "")])
            msg = ((res.get("message") or {}).get("text")) or ""
            out.append(TaintFinding(
                sink_call=_canonical_sink(rule_id, msg),
                entry=flow[0],
                cwe=cwe,
                severity=_sev_from_score(sev_score),
                rule=rule_id,
                reachable=True,
                flow=flow,
            ))
    return out


def scan_tool(sarif: dict[str, Any]) -> str:
    """The scanner's own name, lowercased. Which analyser made a reachability
    claim is part of the claim."""
    for run in sarif.get("runs", []) or []:
        name = ((run.get("tool") or {}).get("driver") or {}).get("name")
        if name:
            return str(name).strip().lower()
    return "unknown"


def scanned_modules(sarif: dict[str, Any]) -> list[str]:
    """Module names the scan examined, taken from the artifacts it lists and
    the files its results point at.

    This is what licenses a negative. A sink in a module named here and not
    reported is a sink the scanner cleared; a sink in a module absent from
    this list was never looked at, and must never be shown as clean."""
    uris: set[str] = set()
    for run in sarif.get("runs", []) or []:
        for art in run.get("artifacts", []) or []:
            uri = ((art.get("location") or {}).get("uri"))
            if uri:
                uris.add(str(uri))
        # Semgrep does not always populate `artifacts`, so fall back to the
        # files the results themselves name.
        for res in run.get("results", []) or []:
            for loc in res.get("locations", []) or []:
                pl = (loc.get("physicalLocation") or {})
                uri = ((pl.get("artifactLocation") or {}).get("uri"))
                if uri:
                    uris.add(str(uri))
    # "src/pipeline.py" -> "pipeline": MeshAgent records a module by name,
    # and the scanner talks in paths.
    out = set()
    for u in uris:
        stem = u.rsplit("/", 1)[-1]
        out.add(stem[:-3] if stem.endswith(".py") else stem)
    return sorted(out)


@dataclass
class ScanIngest:
    """What one ingested scan did to the graph."""

    tool: str
    modules: list[str]          # what it examined
    results: int                # results in the document
    reachable: int              # taint edges written from it
    scan_ulid: str = ""
    taint_ulids: list[str] = field(default_factory=list)


def ingest_scan(memory: MemoryStore, sarif: dict[str, Any]) -> ScanIngest:
    """Record a scanner run: what it covered, and what it proved reachable.

    Both halves matter. The taint edges upgrade findings to exploitable; the
    coverage record is what lets everything it did *not* flag be described as
    assessed rather than merely unexamined."""
    rec = CodeGraphRecorder(memory)
    tool = scan_tool(sarif)
    modules = scanned_modules(sarif)

    have: set[tuple[str, str]] = set()
    for ulid in _existing_taint(memory):
        m = memory.get(ulid)
        if m and m.content:
            have.add((m.content.get("sink"), m.content.get("entry")))

    written: list[str] = []
    for f in sarif_findings(sarif):
        key = (f.sink_call, f.entry)
        if key in have:
            continue
        written.append(rec.record_taint(
            sink_call=f.sink_call, entry=f.entry, cwe=f.cwe,
            severity=f.severity, rule=f.rule, flow=f.flow, tool=tool,
        ))
        have.add(key)

    results = sum(len(r.get("results", []) or [])
                  for r in sarif.get("runs", []) or [])
    scan_ulid = rec.record_scan(tool=tool, modules=modules, results=results,
                                reachable=len(written))
    return ScanIngest(tool=tool, modules=modules, results=results,
                      reachable=len(written), scan_ulid=scan_ulid,
                      taint_ulids=written)


def ingest_sarif(memory: MemoryStore, sarif: dict[str, Any]) -> list[str]:
    """Record every reachable finding in *sarif* as a taint edge in the
    governed graph. Returns the ULIDs written. Idempotent against already-
    recorded (sink, entry) pairs, so re-ingesting the same scan adds nothing.

    The findings that matter — the ones the scanner proved reachable — land as
    `reaches` edges that flip their sink node to exploitable in every view."""
    return ingest_scan(memory, sarif).taint_ulids


def _existing_taint(memory: MemoryStore) -> list[str]:
    """ULIDs of taint edges already in the store (any sink node carries them
    among its edges)."""
    from .codegraph import export_graph  # local import avoids a cycle at load

    seen: set[str] = set()
    g = export_graph(memory)
    for n in g["nodes"]:
        if n["kind"] != "sink":
            continue
        for ulid in memory.find_by_subject(n["id"]):
            m = memory.get(ulid)
            if m and m.content and m.content.get("type") == "taint":
                seen.add(ulid)
    return list(seen)
