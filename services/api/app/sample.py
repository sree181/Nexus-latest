"""Curated sample data that mirrors the MeshAgent demo graph. Honest by
construction: sample CVEs are labelled, reachability is only asserted where a
taint path exists. Replace by wiring EngineGateway (see gateway.py)."""

from __future__ import annotations

import ast
import time
from collections import Counter

import hashlib

from .hgviz import Hypergraph
from .models import (
    AgentHit,
    CodeEvent,
    CodeModule,
    CodeOut,
    CoverageOut,
    CveImpact,
    DecisionEvent,
    DeletionCertificate,
    DoomedEdge,
    EvidenceNode,
    Finding,
    FindingsOut,
    FleetOverview,
    ForgetPreview,
    GraphEdge,
    GraphNode,
    GraphPayload,
    MemoryEvent,
    PurgedEdge,
    QueryResult,
    RecorderBatch,
    RecorderReceipt,
    Recommendation,
    Relation,
    RewindMemory,
    RewindOut,
    RunSummary,
    SbomEntry,
    SbomOut,
    ScanOut,
    WhyOut,
)


def fleet_overview() -> FleetOverview:
    return FleetOverview(
        agents_active=3,
        memories_governed=128,
        exploitable_findings=1,
        deletion_certificates=2,
        # the curated run's other two sinks: present, and examined by nobody
        unassessed_findings=2,
        runs_with_code=1,
        runs_unscanned=1,
    )


def run_graph(run_id: str) -> GraphPayload:
    """Provenance + security for one run: the poisoned-source -> pickle.load story."""
    nodes = [
        GraphNode(id="source:poisoned-mirror", kind="source", label="poisoned mirror doc",
                  planes=["provenance"]),
        GraphNode(id="decision:pickle-shards", kind="decision", label="pickle shards",
                  planes=["provenance"]),
        GraphNode(id="class:UnsafeShardLoader", kind="class", label="UnsafeShardLoader",
                  planes=["provenance", "security", "supply"]),
        GraphNode(id="pkg:numpy", kind="package", label="numpy", planes=["provenance", "supply"]),
        GraphNode(id="pkg:pickle", kind="package", label="pickle", planes=["provenance"]),
        GraphNode(id="sink:pickle.load", kind="sink", label="pickle.load",
                  planes=["security"], exploitable=True, severity="critical"),
        GraphNode(id="cwe:CWE-502", kind="cwe", label="CWE-502", planes=["security"]),
        # The capability the sink grants. Drawn because the finding is one
        # 4-ary fact over {class, sink, capability, weakness}; omitting a
        # member would leave that relation unrepresentable here.
        GraphNode(id="cap:deserialization", kind="capability",
                  label="deserialization", planes=["security"]),
        GraphNode(id="entry:api/serve.py:19", kind="entry", label="api/serve.py:19",
                  planes=["security"], severity="critical"),
    ]
    edges = [
        GraphEdge(id="e1", source="source:poisoned-mirror", target="decision:pickle-shards", rel="derived"),
        GraphEdge(id="e2", source="decision:pickle-shards", target="class:UnsafeShardLoader", rel="derived"),
        GraphEdge(id="e3", source="class:UnsafeShardLoader", target="pkg:numpy", rel="imports"),
        GraphEdge(id="e4", source="class:UnsafeShardLoader", target="pkg:pickle", rel="imports"),
        GraphEdge(id="e5", source="class:UnsafeShardLoader", target="sink:pickle.load", rel="calls"),
        GraphEdge(id="e6", source="sink:pickle.load", target="cwe:CWE-502", rel="weakness"),
        GraphEdge(id="e7", source="entry:api/serve.py:19", target="sink:pickle.load", rel="reaches"),
        GraphEdge(id="e8", source="sink:pickle.load", target="cap:deserialization",
                  rel="grants"),
    ]
    # Derived from the curated records rather than listed beside them, so the
    # relations and the why-chain cannot drift apart: both read _MEMORY. The
    # nodes above are only those this run's story draws, so relations spanning
    # entities outside it (the SBOM and advisory edges) are left out.
    drawn = {n.id for n in nodes}
    relations = [
        Relation(id=r.ulid, kind=r.ctype, members=r.members, label=r.statement)
        for r in _MEMORY if drawn.issuperset(r.members)
    ]
    return GraphPayload(nodes=nodes, edges=edges, relations=relations)


# 8 of 20 agents import the vulnerable numpy; only Maya's path is reachable.
_FLEET_AGENTS = [
    ("MA", "Maya · data loader", "exploitable"),
    ("A2", "Ravi · feature store", "present"),
    ("A3", "Lena · eval harness", "present"),
    ("A4", "Omar · ingest job", "present"),
    ("A5", "Nia · batch scorer", "present"),
    ("A6", "Sam · export tool", "present"),
    ("A7", "Ivy · label service", "present"),
    ("A8", "Ken · metrics job", "present"),
]


def fleet_query(query: str) -> QueryResult:
    """A cross-fleet traversal from a shared node out to the agents that touch it.
    Here: everyone importing numpy@1.26.4. The shared node has ONE identity, which
    is exactly why the traversal is possible."""
    center = GraphNode(id="version:numpy@1.26.4", kind="version",
                       label="numpy@1.26.4", owners=[a[0] for a in _FLEET_AGENTS])
    nodes: list[GraphNode] = [center]
    edges: list[GraphEdge] = []
    hits: list[AgentHit] = []
    for i, (aid, name, status) in enumerate(_FLEET_AGENTS):
        nodes.append(GraphNode(
            id=f"agent:{aid}", kind="agent", label=aid,
            exploitable=(status == "exploitable"),
            severity="critical" if status == "exploitable" else None,
        ))
        edges.append(GraphEdge(id=f"fe{i}", source=f"agent:{aid}",
                               target="version:numpy@1.26.4", rel="uses"))
        hits.append(AgentHit(agent=aid, name=name, status=status))  # type: ignore[arg-type]
    return QueryResult(
        query=query, graph=GraphPayload(nodes=nodes, edges=edges), hits=hits,
        # the curated traversal is fixed, and saying so is better than
        # letting the result pass for an answer to whatever was typed
        interpreted=("Sample data: this curated traversal always shows the "
                     "agents importing numpy@1.26.4, whatever is asked."),
    )


def recommendations() -> list[Recommendation]:
    every = [a for a, _, _ in _FLEET_AGENTS]
    return [
        Recommendation(id="rec-numpy", title="Patch numpy to 1.26.5",
                       detail="Resolves CVE-2021-41496 across every agent that inherited it.",
                       kind="HIGH", agents=8, agent_ids=every,
                       blast_radius={"agents": 8, "classes": 8, "decisions": 6, "exploitable": 1}),
        Recommendation(id="rec-source", title="Cut poisoned source",
                       detail="Forget at the source, propagates to 3 agents with certificates.",
                       kind="CRITICAL", agents=3, agent_ids=every[:3],
                       blast_radius={"agents": 3, "classes": 3, "decisions": 3}),
        Recommendation(id="rec-gate", title="Add gate rule",
                       detail="Quarantine unsafe-deserialization fleet-wide.",
                       kind="POLICY", agents=8, agent_ids=every),
        Recommendation(id="rec-standardize", title="Standardize a split decision",
                       detail="Two agents chose conflicting data loaders.",
                       kind="REVIEW", agents=2, agent_ids=every[:2]),
        Recommendation(id="rec-license", title="License exposure",
                       detail="MPL-2.0 in a shipping path across 4 agents.",
                       kind="LICENSE", agents=4, agent_ids=every[:4]),
    ]



# -- hypergraphs for structure analysis (hgviz) -------------------------------

_VKIND = {
    "source:poisoned-mirror": "source",
    "decision:pickle-shards": "decision",
    "class:UnsafeShardLoader": "class",
    "pkg:numpy": "package",
    "pkg:pickle": "package",
    "sink:pickle.load": "sink",
    "cap:deserialization": "capability",
    "cwe:CWE-502": "cwe",
    "sink:hashlib.md5": "sink",
    "cap:weak-crypto": "capability",
    "cwe:CWE-327": "cwe",
    "sink:subprocess.run": "sink",
    "cap:subprocess-spawn": "capability",
    "cwe:CWE-78": "cwe",
    "entry:api/serve.py:19": "entry",
    "version:numpy@1.26.4": "version",
    "license:BSD-3-Clause": "license",
    "cve:CVE-2021-41496": "cve",
    "cwe:CWE-476": "cwe",
}


def run_hypergraph(run_id: str) -> Hypergraph:
    """The pickle.load story as a real hypergraph. The exploitable knot
    (finding + taint around pickle.load and CWE-502) forms a topological block;
    the provenance and supply-chain trails are tree-structured bridges/branches."""
    h = Hypergraph()
    h.add_edge("prov-source", ["source:poisoned-mirror", "decision:pickle-shards"])
    h.add_edge("prov-decision", ["decision:pickle-shards", "class:UnsafeShardLoader"])
    h.add_edge("class", ["class:UnsafeShardLoader", "pkg:numpy", "pkg:pickle"])
    h.add_edge("finding", ["class:UnsafeShardLoader", "sink:pickle.load",
                           "cap:deserialization", "cwe:CWE-502"])
    h.add_edge("finding-md5", ["class:UnsafeShardLoader", "sink:hashlib.md5",
                               "cap:weak-crypto", "cwe:CWE-327"])
    h.add_edge("finding-shell", ["class:UnsafeShardLoader", "sink:subprocess.run",
                                 "cap:subprocess-spawn", "cwe:CWE-78"])
    h.add_edge("taint", ["sink:pickle.load", "entry:api/serve.py:19", "cwe:CWE-502"])
    h.add_edge("sbom", ["version:numpy@1.26.4", "pkg:numpy", "license:BSD-3-Clause"])
    h.add_edge("cve", ["cve:CVE-2021-41496", "version:numpy@1.26.4", "cwe:CWE-476"])
    h.vkind = {v: k for v, k in _VKIND.items() if v in h.vertices}
    return h


def fleet_hypergraph() -> Hypergraph:
    """The cross-fleet view: several agents' loaders all hang off the shared
    vulnerable numpy version. Three of them also share the CWE, forming an
    entangled block with a forbidden cluster: genuine, unavoidable coupling."""
    h = Hypergraph()
    shared_v = "version:numpy@1.26.4"
    for a in ["MA", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]:
        h.add_edge(f"use-{a}", [f"agent:{a}", shared_v])
    # three agents additionally entangled through the same weakness
    for a in ["MA", "A2", "A3"]:
        h.add_edge(f"weak-{a}", [f"agent:{a}", shared_v, "cwe:CWE-476"])
    h.vkind = {v: ("version" if v == shared_v else "cwe" if v.startswith("cwe:")
                   else "agent") for v in h.vertices}
    return h


# -- the curated run: memory records behind the pickle.load story -------------
#
# One row per memory hyperedge the engine would have written, carrying the same
# envelope fields (origin, status, source) and the same derivation links. These
# back the runs list, the why-chain, the forget closure and the findings, so
# sample mode tells exactly the story engine mode computes. Every response
# built from them carries sample=True.

RUN_ID = "7f3a"
RUN_TASK = "Build a data loader for the training pipeline that reads model shards."
RUN_CREATED_AT = 1_758_000_000   # fixed, so curated data does not drift


class _Record:
    """One curated memory edge: the envelope, the entity it is about, the
    members the hyperedge spans, and the step that wrote it."""

    def __init__(self, ulid: str, entity: str, statement: str, origin: str,
                 status: str, source: str, step: str, members: list[str],
                 parent: str | None = None, via: str | None = None,
                 *, ctype: str) -> None:
        self.ulid = ulid
        self.entity = entity
        self.statement = statement
        self.origin = origin
        self.status = status
        self.source = source
        self.step = step
        self.members = members
        self.parent = parent
        self.via = via
        # What kind of fact this is, mirroring the engine's content "type".
        # Stated rather than inferred from the entity: a finding is *about* a
        # sink, so reading the kind off the entity would call it a sink.
        self.ctype = ctype
        # When this memory was written. Filled in below from the record's
        # position in the story, so rewind has a real timeline to walk.
        self.at = 0


_CLASS = "class:UnsafeShardLoader"

_MEMORY: list[_Record] = [
    _Record("sample-01", "source:poisoned-mirror",
            "a docs mirror recommending pickle.load on shard files",
            "EXTERNAL", "UNVERIFIED", "source:poisoned-mirror",
            "Read a source", ["source:poisoned-mirror"], ctype="source"),
    _Record("sample-02", "decision:pickle-shards",
            "load data shards with pickle for speed",
            "AGENT", "UNVERIFIED", "agent:design",
            "Made a decision", ["decision:pickle-shards"],
            "sample-01", "DERIVED_FROM", ctype="decision"),
    _Record("sample-03", _CLASS, "class UnsafeShardLoader (numpy, pickle)",
            "AGENT", "VERIFIED", "agent:codegen",
            "Wrote code", [_CLASS, "pkg:numpy", "pkg:pickle"],
            "sample-02", "DERIVED_FROM", ctype="class"),
    _Record("sample-04", "sink:pickle.load",
            "pickle.load grants deserialization (CWE-502)",
            "AGENT", "VERIFIED", "agent:security-scan",
            "Found a dangerous call",
            [_CLASS, "sink:pickle.load", "cap:deserialization", "cwe:CWE-502"],
            "sample-03", "DERIVED_FROM", ctype="finding"),
    _Record("sample-05", "sink:hashlib.md5",
            "hashlib.md5 is a broken digest (CWE-327)",
            "AGENT", "VERIFIED", "agent:security-scan",
            "Found a dangerous call",
            [_CLASS, "sink:hashlib.md5", "cap:weak-crypto", "cwe:CWE-327"],
            "sample-03", "DERIVED_FROM", ctype="finding"),
    _Record("sample-06", "sink:subprocess.run",
            "subprocess.run with shell=True (CWE-78)",
            "AGENT", "VERIFIED", "agent:security-scan",
            "Found a dangerous call",
            [_CLASS, "sink:subprocess.run", "cap:subprocess-spawn", "cwe:CWE-78"],
            "sample-03", "DERIVED_FROM", ctype="finding"),
    _Record("sample-07", "version:numpy@1.26.4", "numpy@1.26.4 · BSD-3-Clause",
            "AGENT", "VERIFIED", "agent:sbom",
            "Recorded a dependency",
            ["version:numpy@1.26.4", "pkg:numpy", "license:BSD-3-Clause"],
            ctype="version"),
    _Record("sample-08", "cve:CVE-2021-41496", "NULL pointer dereference in numpy",
            "EXTERNAL", "UNVERIFIED", "feed:sample",
            "Joined the advisory feed",
            ["cve:CVE-2021-41496", "version:numpy@1.26.4", "cwe:CWE-476"],
            ctype="cve"),
    _Record("sample-09", "entry:api/serve.py:19",
            "api/serve.py:19 reaches pickle.load · py/unsafe-deserialization",
            "EXTERNAL", "UNVERIFIED", "tool:sarif",
            "Scanned for reachability",
            ["sink:pickle.load", "entry:api/serve.py:19", "cwe:CWE-502"],
            ctype="taint"),
]

# The curated story in order, one minute apart. Derived rather than written
# out so the times cannot drift from the order the records are listed in --
# which is the order the story happens.
for _i, _r in enumerate(_MEMORY):
    _r.at = RUN_CREATED_AT + (_i + 1) * 60

_BY_ULID = {r.ulid: r for r in _MEMORY}
_BY_ENTITY = {r.entity: r for r in _MEMORY}

# The three call sites the AST scanner finds. Only pickle.load has a taint
# path, so it is the only one this data calls exploitable.
_FINDINGS = [
    Finding(sink="pickle.load", owner="UnsafeShardLoader", cwe="CWE-502",
            cwe_title="Deserialization of untrusted data",
            capability="deserialization", severity="critical",
            present=True, reachability="reachable", asserted_by="builtin",
            rule="py/external-data-reaches-sink",
            # the same path a scan of _SOURCE finds, so the claim can be
            # checked against the code the Code screen shows
            entry="open() at line 16",
            path=["open() at line 16", "handle", "pickle.load"]),
    Finding(sink="hashlib.md5", owner="UnsafeShardLoader", cwe="CWE-327",
            cwe_title="Use of a broken cryptographic algorithm",
            capability="weak-crypto", severity=None,
            present=True, reachability="not-assessed"),
    Finding(sink="subprocess.run", owner="UnsafeShardLoader", cwe="CWE-78",
            cwe_title="OS command injection", capability="subprocess-spawn",
            severity=None, present=True, reachability="not-assessed"),
]


def _kind_of(entity: str) -> str:
    return _VKIND.get(entity, "other")


def runs() -> list[RunSummary]:
    """The curated run list: one complete run, the pickle.load build.

    Seeded, so it is the deployment's own reference build rather than any
    developer's work -- which is what makes it readable by whoever asks."""
    return [RunSummary(
        id=RUN_ID, task=RUN_TASK, status="complete",
        memory_count=len(_MEMORY), findings=len(_FINDINGS),
        created_at=RUN_CREATED_AT, sample=True, seeded=True,
    )]


def memory_events() -> list[MemoryEvent]:
    """The 'memory forming' stream for the curated run, in write order. Built
    from the same records that back why, forget and the findings, so the stream
    and the screens never disagree."""
    return [
        MemoryEvent(
            step=r.step, detail=r.statement, origin=r.origin,  # type: ignore[arg-type]
            status=r.status, members=r.members, ulid=r.ulid,  # type: ignore[arg-type]
            gate=MemoryEvent.gate_note(r.origin, r.status),  # type: ignore[arg-type]
        )
        for r in _MEMORY
    ]


def why(run_id: str, node: str) -> WhyOut:
    """The evidence chain behind *node*, nearest first, walking the curated
    derivation links the same way the engine's `why` verb walks real ones."""
    rec = _BY_ENTITY.get(node)
    chain: list[EvidenceNode] = []
    via: str | None = None
    while rec is not None:
        chain.append(EvidenceNode(
            ulid=rec.ulid, entity=rec.entity, kind=_kind_of(rec.entity),  # type: ignore[arg-type]
            statement=rec.statement, origin=rec.origin,  # type: ignore[arg-type]
            status=rec.status, source=rec.source, via=via,  # type: ignore[arg-type]
        ))
        via = rec.via
        rec = _BY_ULID.get(rec.parent) if rec.parent else None
    return WhyOut(run_id=run_id, node=node, chain=chain, sample=True)


def rewind(run_id: str, at: int) -> RewindOut:
    """What the curated run held at *at*, walking the same records `why` does.

    Curated memory is only ever written, never tombstoned, so nothing here
    comes back redacted. That is a property of the sample story rather than a
    claim about rewind -- engine mode is where a forget and a rewind actually
    have to argue."""
    held = [r for r in _MEMORY if r.at <= at]
    return RewindOut(
        run_id=run_id, at=at,
        memories=[
            RewindMemory(
                ulid=r.ulid, entity=r.entity,
                kind=_kind_of(r.entity),  # type: ignore[arg-type]
                statement=r.statement,
                origin=r.origin,  # type: ignore[arg-type]
                status=r.status,  # type: ignore[arg-type]
            ) for r in held
        ],
        milestones=sorted({r.at for r in _MEMORY}),
        held=len(held), redacted=0, now=len(_MEMORY), sample=True,
    )


def _content_sha(rec: _Record) -> str:
    """Stand-in for the engine's retained content hash: the digest the real
    store keeps after a tombstone redacts the payload."""
    return hashlib.sha256(f"{rec.ulid}:{rec.statement}".encode()).hexdigest()


def _closure(node: str) -> list[_Record]:
    """The record for *node* and everything derived from it, forwards."""
    root = _BY_ENTITY[node]
    closure = [root]
    frontier = [root.ulid]
    while frontier:
        nxt = [r for r in _MEMORY if r.parent in frontier]
        closure.extend(nxt)
        frontier = [r.ulid for r in nxt]
    return closure


#: Curated memory is a fixed story: nothing writes to `_MEMORY`, so the state a
#: preview is computed against never moves and one constant is the honest
#: version. Sample mode therefore cannot demonstrate a stale refusal, and
#: pretending otherwise by churning this value would be inventing data.
VERSION = "curated"


def forget_preview(run_id: str, node: str) -> ForgetPreview:
    """What forgetting *node* would destroy. Reads only."""
    closure = _closure(node)
    return ForgetPreview(
        run_id=run_id, node=node, root=closure[0].ulid, version=VERSION,
        purged_count=len(closure),
        classes_pruned=sorted({r.entity for r in closure
                               if r.entity.startswith("class:")}),
        doomed=[DoomedEdge(ulid=r.ulid, entity=r.entity,
                           statement=r.statement) for r in closure],
        warnings=([] if len(closure) == 1 else
                  [f"{len(closure) - 1} memory(s) derived from this source "
                   f"go with it."]),
        sample=True,
    )


def forget(run_id: str, node: str, reason: str, actor: str,
           issued_at: int) -> DeletionCertificate:
    """Tombstone the curated record for *node* and its forward derivation
    closure, and issue the same certificate shape the engine's forget returns."""
    closure = _closure(node)
    root = closure[0]

    purged = [PurgedEdge(ulid=r.ulid, entity=r.entity,
                         content_sha_retained=_content_sha(r)) for r in closure]
    return DeletionCertificate(
        run_id=run_id, node=node, root=root.ulid, reason=reason, actor=actor,
        issued_at=issued_at, purged_count=len(purged),
        classes_pruned=sorted({r.entity for r in closure
                               if r.entity.startswith("class:")}),
        retained_hash=DeletionCertificate.digest(
            [p.content_sha_retained for p in purged]),
        purged=purged, sample=True,
    )


def scan_summary(sarif: dict) -> ScanOut:
    """What a scan document says about itself, read without the engine.

    Curated mode has to run with no HyperMesh present, so the real ingest in
    meshagent.sarif is out of reach here. Only the envelope is read -- the
    tool, the files, the counts -- and nothing is written, because curated
    memory is fixed. Reporting what was read beats claiming it landed."""
    tool, uris, results, flows = "unknown", set(), 0, 0
    for run in sarif.get("runs", []) or []:
        name = ((run.get("tool") or {}).get("driver") or {}).get("name")
        if name and tool == "unknown":
            tool = str(name).strip().lower()
        for art in run.get("artifacts", []) or []:
            if uri := ((art.get("location") or {}).get("uri")):
                uris.add(str(uri))
        for res in run.get("results", []) or []:
            results += 1
            if res.get("codeFlows"):
                flows += 1
            for loc in res.get("locations", []) or []:
                pl = loc.get("physicalLocation") or {}
                if uri := ((pl.get("artifactLocation") or {}).get("uri")):
                    uris.add(str(uri))
    stems = {u.rsplit("/", 1)[-1] for u in uris}
    return ScanOut(tool=tool, at=int(time.time()),
                   modules=sorted(s[:-3] if s.endswith(".py") else s
                                  for s in stems),
                   results=results, reachable=flows, sample=True)


def recorder_receipt(batch: RecorderBatch) -> RecorderReceipt:
    """Read a recorder batch the way the engine would, and write nothing.

    The counts are computed honestly -- the same rules the engine applies for
    what counts as unexplained and what this build can decompose -- so an
    adapter author can develop against curated mode and see real numbers.
    Only the writing is absent, and `sample` says so.

    `run_id` is the seeded run, which the batch did not write to. It is the
    only readable run curated mode has, and an adapter carries this id into
    its own read calls -- so pointing at it is what makes `why` on the
    adapter's own module answer "nothing here explains that", which is the
    true answer. The alternative, an empty id, reads to every one of those
    callers as a field they failed to receive."""
    stated = {e.id for e in batch.events if isinstance(e, DecisionEvent)}
    recorded = unexplained = analysed = 0
    refused: list[str] = []

    for event in batch.events:
        if isinstance(event, CodeEvent):
            # The engine also accepts a decision recorded by an earlier
            # batch. Curated mode holds nothing between calls, so here the
            # batch is all there is -- same rule, less to check it against.
            if event.because and event.because not in stated:
                refused.append(
                    f"code {event.module}: unknown decision {event.because!r}")
                continue
            if not event.because:
                unexplained += 1
            try:
                ast.parse(event.code)
                analysed += 1
            except SyntaxError:
                pass            # recorded verbatim, just not decomposed
        recorded += 1

    return RecorderReceipt(
        run_id=RUN_ID, session=batch.session, recorded=recorded,
        unexplained=unexplained, analysed=analysed, refused=refused,
        sample=True,
    )


def coverage() -> CoverageOut:
    """Curated coverage, built from the curated run rather than asserted.

    The one module the curated run holds was written by MeshAgent's own loop
    against a stated decision, so it is counted as self-recorded and not as
    an external agent's explained code -- otherwise curated mode would show a
    healthy adoption number for a fleet with no adopters in it."""
    modules = len(code(RUN_ID).modules)
    return CoverageOut(modules=0, explained=0, by_developer=[],
                       self_recorded=modules, sample=True)


def findings(run_id: str) -> FindingsOut:
    tally = Counter(f.reachability for f in _FINDINGS)
    return FindingsOut(
        run_id=run_id, present=len(_FINDINGS),
        exploitable=tally["reachable"],
        findings=list(_FINDINGS), sample=True,
        scanned=1,              # the curated run holds one class, UnsafeShardLoader
        reachable=tally["reachable"],
        not_reachable=tally["not-reachable"],
        not_assessed=tally["not-assessed"],
    )


# The curated run's source. Written so that a real AST scan over it finds
# exactly the three call sites _FINDINGS claims -- pickle.load, hashlib.md5
# and subprocess.run -- and imports exactly the one third-party package the
# SBOM lists. The sample must not assert anything its own code does not do.
_SOURCE = '''\
import hashlib
import pickle
import subprocess

import numpy


class UnsafeShardLoader:
    """Loads model shards written by the training job."""

    def __init__(self, shard_dir, checksum_algorithm="md5"):
        self.shard_dir = shard_dir
        self.checksum_algorithm = checksum_algorithm

    def load(self, name):
        with open(f"{self.shard_dir}/{name}", "rb") as handle:
            shard = pickle.load(handle)
        return numpy.asarray(shard["weights"])

    def checksum(self, payload):
        return hashlib.md5(payload).hexdigest()

    def fetch(self, remote):
        subprocess.run(f"aws s3 cp {remote} {self.shard_dir}", shell=True)
'''


def code(run_id: str) -> CodeOut:
    """The source behind the curated run, so the findings can be read against
    the code that produced them."""
    return CodeOut(run_id=run_id, sample=True, modules=[
        CodeModule(name="data/loader.py", code=_SOURCE,
                   classes=["UnsafeShardLoader"]),
    ])


def sbom(run_id: str) -> SbomOut:
    """The run's third-party dependencies. Only numpy is third-party here;
    the other imports are standard library and carry no version record."""
    return SbomOut(run_id=run_id, entries=[
        SbomEntry(package="numpy", version="1.26.4", license="BSD-3-Clause",
                  cves=["CVE-2021-41496"], severity="high", feed="sample"),
    ], sample=True)


def cve_impact(cve: str) -> CveImpact:
    """Blast radius of the advisory, tier by tier."""
    if cve != "CVE-2021-41496":
        raise KeyError(cve)
    return CveImpact(
        cve=cve, severity="high", summary="NULL pointer dereference in numpy",
        feed="sample",
        versions=["version:numpy@1.26.4"], packages=["pkg:numpy"],
        classes=["class:UnsafeShardLoader"], decisions=["decision:pickle-shards"],
        agents=[a for a, _, _ in _FLEET_AGENTS], sample=True,
    )
