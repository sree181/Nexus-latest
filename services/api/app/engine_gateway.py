"""EngineGateway: the Gateway implemented against the real MeshAgent engine.

Runs on genuine HyperMesh-backed memory (see engine_seed). run_graph comes from
the engine's own export_graph; the structure endpoints reconstruct the real
hyperedges and analyze them with hgviz; provenance uses the engine's `why` and
`forget` verbs; the fleet endpoints read the real multi-agent memory. Selected
by MESHAGENT_ENGINE=1 (see gateway.get_gateway)."""

from __future__ import annotations

import ast
import functools
import hashlib
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, TypeVar

from . import analysis, engine_seed, gateway, operations, registry
from .gateway import Conflict, Gateway, NotFound, Stale, new_run_id
from .models import (
    AgentHit,
    ApplyReceipt,
    CodeEvent,
    CodeModule,
    CodeOut,
    CoverageOut,
    CoverageRow,
    CveImpact,
    DecisionEvent,
    DecompositionOut,
    DeletionCertificate,
    DoomedEdge,
    EvidenceNode,
    Finding,
    FindingsOut,
    FleetOverview,
    ForgetPreview,
    GateDecision,
    GateRequest,
    GatewayMode,
    GraphEdge,
    GraphNode,
    GraphPayload,
    HypergraphOut,
    MemoryEvent,
    PackageEvent,
    PurgedEdge,
    QueryResult,
    RecorderBatch,
    RecorderReceipt,
    Recommendation,
    Relation,
    RewindMemory,
    RewindOut,
    RunStatus,
    RunStreamEvent,
    RunSummary,
    SbomEntry,
    SbomOut,
    ScaleOut,
    ScanOut,
    SessionEvent,
    ToolEvent,
    WhyOut,
)

_ALLOWED_KINDS = {
    "source", "decision", "class", "package", "version", "license",
    "sink", "cwe", "cve", "entry", "agent", "capability", "other",
}

# The record type that DEFINES each kind of entity, so a why-chain starts from
# the memory the node is about rather than any edge that merely mentions it.
_DEFINING_CTYPE = {
    "source": "source", "decision": "decision", "class": "class",
    "sink": "finding", "entry": "taint", "version": "version", "cve": "cve",
}

_SEVERITY_ORDER = ["critical", "high", "medium", "low", "unknown"]


def _analyser(rec: Any) -> str:
    """Which analyser wrote a taint record, for older records that predate
    the tool being stored in the payload. Our own walk enters as an agent
    observation; a scanner enters as external content."""
    src = getattr(getattr(rec, "envelope", None), "source", "") or ""
    if src.startswith("agent:"):
        return "builtin"
    return src.split(":", 1)[1] if ":" in src else (src or "unknown")

# Only reached when a caller somehow arrives with no identity at all. Every
# route passes the authenticated subject, so a certificate carrying this is
# itself the finding.
_ACTOR = "unattributed"

#: HyperMesh's core is a shared C library reached over ctypes, and it is not
#: safe for concurrent use: two threads reading at once overwrite each other's
#: row buffer, which surfaces as a KeyError on a column that should be there.
#: FastAPI runs sync endpoints in a threadpool and the stream steps its
#: generator in a worker thread, so more than one caller is the normal case,
#: not an edge case. Every engine call therefore takes this one process-wide
#: lock. It is re-entrant because the gateway's own methods call each other.
_ENGINE = threading.RLock()

# How often a viewer following someone else's recording checks for new writes.
# Short enough to feel live, long enough not to spin on the engine lock.
_FOLLOW_POLL = 0.4

# How long a follower waits with nothing new written before it gives up and
# says so. A model turn can be slow and silent, so this is generous; the point
# is only that an abandoned recording cannot hold a viewer open forever.
_FOLLOW_IDLE = 90.0

_T = TypeVar("_T")


def _serialized(fn: Callable[..., _T]) -> Callable[..., _T]:
    """Hold the engine lock for one whole call.

    Not for generators: a generator would hold the lock across its yields and
    block every other caller for the length of the stream. Those take the lock
    per step instead, so the pacing between frames stays lock-free."""
    @functools.wraps(fn)
    def guarded(*args: Any, **kwargs: Any) -> _T:
        with _ENGINE:
            return fn(*args, **kwargs)

    return guarded


def _label(node_id: str) -> str:
    return node_id.split(":", 1)[1] if ":" in node_id else node_id


def _node(n: dict[str, Any]) -> GraphNode:
    kind = n.get("kind", "other")
    return GraphNode(
        id=n["id"],
        kind=kind if kind in _ALLOWED_KINDS else "other",
        label=n.get("statement") or _label(n["id"]),
        exploitable=bool(n.get("exploitable", False)),
        severity=n.get("severity"),
        tombstoned=bool(n.get("tombstoned", False)),
    )


def _export_to_payload(g: dict[str, Any]) -> GraphPayload:
    nodes = [_node(n) for n in g["nodes"]]
    edges: list[GraphEdge] = []
    i = 0

    def add(lst: list[dict], rel_default: str) -> None:
        nonlocal i
        for e in lst:
            edges.append(GraphEdge(id=f"e{i}", source=e["source"], target=e["target"],
                                   rel=e.get("rel", rel_default)))
            i += 1

    add(g.get("derives", []), "derived")
    add(g.get("imports", []), "imports")
    add(g.get("security", []), "calls")
    add(g.get("sbom", []), "affects")
    add(g.get("taint", []), "reaches")
    return GraphPayload(nodes=nodes, edges=edges)


@dataclass
class _Run:
    """One run and the HyperMesh-backed store holding its memory."""
    id: str
    task: str
    store: Any
    status: RunStatus
    created_at: int
    store_name: str = ""        # its directory under the memory base
    # This deployment's own reference build, published to every caller. Set
    # where the run is seeded rather than persisted in the registry: it is a
    # property of what this process seeds, so a store copied into a deployment
    # that does not seed it cannot arrive claiming to be publishable.
    seeded: bool = False
    owner: str | None = None    # the developer whose agent produced it
    owner_name: str | None = None
    streaming: bool = False     # a recording is in flight; do not start a second
    # its memory is the reference build, not an execution of `task`
    reference_build: bool = False
    model: str | None = None    # the model that built it, when one did
    # kept so the build's decision can be linked to the task the user stated:
    # only the recorder that wrote the task knows the ULID it became
    new_run: engine_seed.NewRun | None = None
    # an agent outside this product is writing it through the recorder. Its
    # memory arrives by POST, so nothing here may try to build it.
    external: bool = False
    session: str | None = None  # the adapter session it belongs to
    agent: str | None = None    # which agent is writing: "claude-code", ...
    # Whether `owner` was proven -- by a device token or an identity provider
    # -- rather than asserted in a header. Coverage names people against
    # their gaps, so it has to know which of those names it can stand behind.
    attributed: bool = False


class EngineGateway(Gateway):
    def __init__(self) -> None:
        # imported here so the module loads even when the engine is absent
        from hypermeshdb.agentmem import Kind, Origin, Status, Verbs
        from meshagent.codegraph import cve_impact, export_graph

        self._export_graph = export_graph
        self._engine_cve_impact = cve_impact
        self._verbs = Verbs
        self._kind, self._origin, self._status = Kind, Origin, Status

        self._fleet = engine_seed.seed_fleet_store()
        self._registry = registry.load(engine_seed.base_dir())
        self._operations = operations.load(engine_seed.base_dir())
        seeded = self._registry.runs.get(engine_seed.RUN_ID)
        self._runs: dict[str, _Run] = {
            engine_seed.RUN_ID: _Run(
                id=engine_seed.RUN_ID, task=engine_seed.RUN_TASK,
                store=engine_seed.seed_run_store(), status="complete",
                # the reference build is reopened, not rebuilt, so it keeps
                # the age it had. Re-stamping it each boot would make the
                # fixture the newest run on every restart, and every screen
                # that opens "the latest run" would land on it
                created_at=seeded.created_at if seeded else int(time.time()),
                store_name="run", seeded=True,
            ),
        }
        self._certificates: list[DeletionCertificate] = list(
            self._registry.certificates)
        self._reopen()
        self._recover_operations()

    def _reopen(self) -> None:
        """Bring back the runs a previous process recorded.

        Their memory is on disk either way; without this the app simply has
        nothing pointing at it. A run still marked `recording` was cut off
        mid-build by whatever ended that process -- it is reported `failed`,
        because the alternative is a run that claims to have finished work it
        abandoned."""
        for rec in self._registry.runs.values():
            if rec.id in self._runs:
                continue
            if not engine_seed.store_exists(rec.store):
                continue        # indexed but the memory is gone: say nothing
            self._runs[rec.id] = _Run(
                id=rec.id, task=rec.task,
                store=engine_seed.new_store(rec.store),
                status="failed" if rec.status == "recording" else rec.status,
                created_at=rec.created_at, store_name=rec.store,
                reference_build=rec.reference_build, model=rec.model,
                owner=rec.owner, owner_name=rec.owner_name,
                external=rec.session is not None,
                session=rec.session, agent=rec.agent,
                attributed=rec.attributed,
            )
        self._remember()

    def _remember(self) -> None:
        """Persist the run index and the certificates issued so far."""
        if not engine_seed.durable():
            return              # nothing to come back to; do not litter
        self._registry.runs = {
            r.id: registry.RunRecord(
                id=r.id, task=r.task, status=r.status,
                created_at=r.created_at, store=r.store_name,
                reference_build=r.reference_build, model=r.model,
                owner=r.owner, owner_name=r.owner_name,
                session=r.session, agent=r.agent,
                attributed=r.attributed,
            )
            for r in self._runs.values()
        }
        self._registry.certificates = list(self._certificates)
        self._registry.save()

    def _recover_operations(self) -> None:
        """Finish deletions whose process ended after durable preparation.

        Startup fails if recovery cannot complete. Serving traffic while an
        approved deletion is half-applied would make both the graph and its
        certificate unreliable.
        """
        for operation in self._operations.incomplete():
            self._finish_forget_operation(operation)

    # -- run bookkeeping --

    def _run(self, run_id: str) -> _Run:
        run = self._runs.get(run_id)
        if run is None:
            raise NotFound(f"unknown run {run_id}")
        return run

    def _primary(self) -> _Run:
        """The run whose memory the fleet-wide views read. The seeded run is
        the one with a recorded build; created runs start empty."""
        return self._runs[engine_seed.RUN_ID]

    def _summary(self, run: _Run) -> RunSummary:
        live = _live(run.store)
        return RunSummary(
            id=run.id, task=run.task, status=run.status,
            memory_count=len(live),
            findings=sum(1 for r in live if _ctype(r) == "finding"),
            created_at=run.created_at,
            reference_build=run.reference_build,
            model=run.model,
            owner=run.owner, owner_name=run.owner_name,
            seeded=run.seeded,
        )

    def mode(self) -> GatewayMode:
        return GatewayMode(
            engine=True, persists=True,
            note=("Engine mode: every write goes through the real write gate "
                  "into HyperMesh, and what a receipt reports landed is "
                  "memory that exists."),
        )

    # -- overview / graph / query / recs (real data) --

    @_serialized
    def fleet_overview(self) -> FleetOverview:
        runs = [self._summary(r) for r in self._runs.values()]
        found = [self.run_findings(r.id) for r in self._runs.values()]
        # Runs holding code but read by no external scanner. A fleet with few
        # proven findings and many unscanned runs is not a safe fleet, and
        # the headline numbers have to admit which one this is.
        with_code = [f for f in found if f.scanned > 0]
        return FleetOverview(
            agents_active=len(engine_seed.fleet_agents(self._fleet)),
            memories_governed=sum(r.memory_count for r in runs) + len(_live(self._fleet)),
            exploitable_findings=sum(f.reachable for f in found),
            deletion_certificates=len(self._certificates),
            unassessed_findings=sum(f.not_assessed for f in found),
            runs_with_code=len(with_code),
            runs_unscanned=sum(1 for f in with_code if not f.scans),
        )

    @_serialized
    def run_graph(self, run_id: str) -> GraphPayload:
        store = self._run(run_id).store
        payload = _export_to_payload(self._export_graph(store))
        payload.relations = self._relations(store, {n.id for n in payload.nodes})
        return payload

    @staticmethod
    def _relations(store: Any, drawn: set[str]) -> list[Relation]:
        """The memory hyperedges behind the drawn graph, unflattened.

        `export_graph` turns each record into pairwise lines -- a finding
        becomes class->sink, sink->capability, sink->weakness -- which is what
        a 2-ary renderer needs and is also a lie about what was recorded.
        These are the same records, read straight, so the payload carries both
        the drawing and the facts it was drawn from.

        Restricted to relations all of whose members are drawn: a relation
        naming a node the caller was never sent is a dangling reference, and
        pointing at nothing is worse than being absent."""
        out: list[Relation] = []
        for rec in _records(store):
            ctype = _ctype(rec)
            if ctype is None:
                continue
            members = _members(store, rec.ulid) or []
            if not members or not drawn.issuperset(members):
                continue
            out.append(Relation(id=rec.ulid, kind=ctype, members=members,
                                label=_detail(rec), tombstoned=rec.tombstoned))
        return out

    @_serialized
    def fleet_query(self, query: str) -> QueryResult:
        """The fleet agents a query asks about, and the versions they name.

        Both the roster and the edges are read from memory rather than listed,
        so a run that joined the fleet appears with the packages it actually
        imports -- and is not drawn onto a version it never used."""
        exploitable_agents = self._exploitable_agents()
        roster = [a for a, _ in engine_seed.fleet_agents(self._fleet)]
        used_by = {a: set(engine_seed.agent_versions(self._fleet, a))
                   for a in roster}
        wanted, interpreted = _read_query(
            query, {v for vs in used_by.values() for v in vs})

        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        hits: list[AgentHit] = []
        versions: dict[str, list[str]] = {}

        for agent in roster:
            if wanted is not None and not (used_by[agent] & wanted):
                continue
            exploit = agent in exploitable_agents
            nodes.append(GraphNode(id=f"agent:{agent}", kind="agent", label=agent,
                                   exploitable=exploit,
                                   severity="critical" if exploit else None))
            # only the versions the query asked about, so the picture answers
            # the question rather than restating the whole fleet around it
            used = sorted(used_by[agent] & wanted) if wanted is not None \
                else engine_seed.agent_versions(self._fleet, agent)
            for version in used:
                versions.setdefault(version, []).append(agent)
                edges.append(GraphEdge(id=f"uses{len(edges)}",
                                       source=f"agent:{agent}",
                                       target=version, rel="uses"))
            hits.append(AgentHit(
                agent=agent, name=agent,
                status="exploitable" if exploit else "present"))

        for version, owners in sorted(versions.items()):
            nodes.append(GraphNode(id=version, kind="version",
                                   label=_label(version), owners=sorted(owners)))
        return QueryResult(query=query, interpreted=interpreted,
                           graph=GraphPayload(nodes=nodes, edges=edges), hits=hits)

    def _exploitable_agents(self) -> set[str]:
        """The fleet agents whose memory carries a proven-reachable finding.
        Only the agent that owns the taint-scanned build qualifies; the rest
        have the package present, which is not the same claim."""
        if self.run_findings(self._primary().id).exploitable == 0:
            return set()
        return {engine_seed.PRIMARY_AGENT}

    @_serialized
    def recommendations(self) -> list[Recommendation]:
        """Derived end to end from real memory and the real fleet decomposition:
        advisories that the fleet actually shares, the forbidden clusters the
        topology proves unavoidable, the bridges that decouple regions in one
        cut, and the forget closures an untrusted source would purge."""
        fleet_h = engine_seed.build_hypergraph(self._fleet)
        fleet_dec = analysis.decomposition_out(fleet_h)
        out: list[Recommendation] = []

        # 1. advisories, sized by their real blast radius across the fleet.
        #    An advisory is a fleet-wide fact, so it earns one recommendation
        #    however many runs hold a record of it. Gather the runs that do
        #    first: they are what the exploitable count is summed over, and
        #    they can disagree on severity if they read the feed at different
        #    times, in which case the worst reading stands.
        carriers: dict[str, dict[str, Any]] = {}
        for run in self._runs.values():
            for rec in _live(run.store):
                c = rec.content or {}
                if c.get("type") != "cve":
                    continue
                seen = carriers.setdefault(c["id"], {"severity": None, "runs": []})
                seen["runs"].append(run.id)
                seen["severity"] = _worse(c.get("severity"), seen["severity"])

        for cve, carried in sorted(carriers.items()):
            impact = self.cve_impact(cve)
            if not impact.agents:
                continue
            pkgs = ", ".join(_label(p) for p in impact.packages) or "the package"
            out.append(Recommendation(
                id=f"rec-cve-{cve}",
                title=f"Patch {pkgs} past {cve}",
                detail=(f"{len(impact.agents)} agents share an affected version. "
                        f"It reached {len(impact.classes)} class(es) resting on "
                        f"{len(impact.decisions)} decision(s)."),
                kind="CRITICAL" if carried["severity"] == "critical" else "HIGH",
                agents=len(impact.agents), agent_ids=impact.agents,
                blast_radius={
                    "agents": len(impact.agents),
                    "packages": len(impact.packages),
                    "classes": len(impact.classes),
                    "decisions": len(impact.decisions),
                    "exploitable": sum(self.run_findings(r).exploitable
                                       for r in carried["runs"]),
                },
            ))

        # 2. forbidden clusters: coupling the topology says cannot be designed away
        for i, f in enumerate(fleet_dec.forbidden):
            agents = sorted({m for e in f.edges for m in fleet_h.edges.get(e, set())
                             if m.startswith("agent:")})
            shared = ", ".join(_label(s) for s in f.shared)
            out.append(Recommendation(
                id=f"rec-forbidden-{i}",
                title=f"Unavoidable coupling across {len(agents)} agents",
                detail=(f"{len(f.edges)} memory edges share {shared}. This overlap is "
                        "structural, so decoupling means changing what they share."),
                kind="REVIEW", agents=len(agents),
                agent_ids=[_label(a) for a in agents],
                blast_radius={"agents": len(agents), "memories": len(f.edges),
                              "shared": len(f.shared)},
            ))

        # 3. bridges: single points of propagation, cut once to decouple
        for br in fleet_dec.bridges[:2]:
            bridge_agents = [m for m in br.members if m.startswith("agent:")]
            out.append(Recommendation(
                id=f"rec-{br.id}",
                title=f"Decouple at {', '.join(_label(m) for m in br.members) or br.id}",
                detail=br.recommendation, kind="POLICY",
                agents=len(bridge_agents),
                agent_ids=[_label(a) for a in bridge_agents],
                blast_radius={"members": len(br.members)},
            ))

        # 4. untrusted sources, sized by the closure a forget would purge
        for run in self._runs.values():
            for rec in _live(run.store):
                c = rec.content or {}
                if c.get("type") != "source":
                    continue
                if rec.envelope.status.name != "UNVERIFIED":
                    continue
                closure = run.store.closure(rec.ulid)
                members = [m for u in closure
                           for m in (_members(run.store, u) or [])]
                out.append(Recommendation(
                    id=f"rec-cut-{rec.ulid}",
                    title=f"Cut the untrusted source {_label(_entity_of(rec) or rec.ulid)}",
                    detail=(f"Forgetting it purges the {len(closure)} memories derived "
                            "from it and issues a deletion certificate."),
                    kind="CRITICAL", agents=1,
                    blast_radius={
                        "memories": len(closure) + 1,
                        "classes": len({m for m in members if m.startswith("class:")}),
                        "decisions": len({m for m in members if m.startswith("decision:")}),
                    },
                ))
        return out

    @_serialized
    def apply_recommendation(self, rec_id: str, *, actor: str = "") -> ApplyReceipt:
        """Perform what MeshAgent can actually perform. A cut recommendation is
        a real forget, so it runs and returns its certificate. Everything else
        is a change in the agents' own repositories, so the engine records the
        decision as governed memory and says plainly that it did only that."""
        rec = next((r for r in self.recommendations() if r.id == rec_id), None)
        if rec is None:
            raise NotFound(f"unknown recommendation {rec_id}")

        if rec_id.startswith("rec-cut-"):
            ulid = rec_id[len("rec-cut-"):]
            for run in self._runs.values():
                held = run.store.get(ulid)
                if held is None:
                    continue
                node = _entity_of(held) or ulid
                cert = self.run_forget(
                    run.id,
                    node,
                    f"applied {rec_id}",
                    actor=actor,
                    expected_version=self._version(run.store),
                    idempotency_key=f"recommendation:{rec_id}:{actor}",
                )
                return ApplyReceipt(
                    recommendation_id=rec_id, title=rec.title,
                    action=(f"Forgot {node} and the {cert.purged_count - 1} "
                            "memories derived from it"),
                    changed_memory=True, agents=rec.agents, memories_written=0,
                    certificates=[cert],
                    note=("The purged payloads are gone; their hashes are "
                          "retained in the certificate."),
                    issued_at=cert.issued_at,
                )
            raise NotFound(f"the memory behind {rec_id} is gone")

        members = [f"policy:{rec_id}", *(f"agent:{a}" for a in rec.agent_ids)]
        self._fleet.write(
            self._kind.FACT, members, origin=self._origin.USER,
            status=self._status.USER_STATED, source="user:console",
            payload={"type": "policy", "recommendation": rec_id,
                     "title": rec.title, "kind": rec.kind},
        )
        return ApplyReceipt(
            recommendation_id=rec_id, title=rec.title,
            action=f"Recorded as accepted across {len(rec.agent_ids)} agent(s)",
            changed_memory=True, agents=rec.agents, memories_written=1,
            note=("The change itself lands in the agents' repositories. What "
                  "the engine did here is record the decision as memory, so it "
                  "is auditable and reversible."),
            issued_at=int(time.time()),
        )

    # -- runs --

    @_serialized
    def runs(self, *, owner: str | None = None) -> list[RunSummary]:
        """Oldest first, so callers can take the last entry as the newest run.

        Timestamps are whole seconds, so a run created in the same second as
        the seeded build would tie. The tie is broken towards the reference
        build being older: it is the state the deployment started in, not
        work someone did, and it should never present itself as the newest
        thing that happened."""
        def order(r: _Run) -> tuple[int, int]:
            return (r.created_at, 0 if r.id == engine_seed.RUN_ID else 1)

        held = [r for r in self._runs.values()
                if owner is None or r.seeded or r.owner == owner]
        return [self._summary(r) for r in sorted(held, key=order)]

    @_serialized
    def run(self, run_id: str) -> RunSummary:
        return self._summary(self._run(run_id))

    @_serialized
    def create_run(self, task: str, *, owner: str | None = None,
                   owner_name: str | None = None) -> RunSummary:
        """Record the task as the first governed memory of a new run: USER
        origin, USER_STATED status. The run is `recording` until the agent
        writes against it.

        With a model configured the run will be built by it, and the memory
        that follows is that model's real work. Without one there is nothing
        here to carry the task out, so the run records the reference build
        instead and is flagged `reference_build` so every screen says so."""
        run_id = new_run_id()
        model = engine_seed.model_name()
        new = engine_seed.record_task(run_id, task)
        run = _Run(id=run_id, task=task, store=new.store,
                   status="recording", created_at=int(time.time()),
                   reference_build=model is None, model=model, new_run=new,
                   store_name=f"run-{run_id}",
                   owner=owner, owner_name=owner_name)
        self._runs[run_id] = run
        self._remember()
        return self._summary(run)

    def run_stream(self, run_id: str) -> Iterator[RunStreamEvent]:
        """The memory the run holds, then -- if it has not recorded yet -- the
        writes as they land. Advancing this generator is what performs them, so
        every frame describes a hyperedge that exists in HyperMesh by the time
        it is sent."""
        with _ENGINE:
            run = self._run(run_id)
            # An external run's memory arrives by POST from its own agent.
            # Building it here would have MeshAgent write code into a run
            # somebody else is already writing, and the screen would show
            # both as one piece of work.
            start = (run.status == "recording" and not run.streaming
                     and not run.external)
            if start:
                # claim the recording under the lock, so two viewers arriving
                # together cannot both start writing this run's memory
                run.streaming = True
            # someone else is recording it: watch, rather than conclude
            following = not start and run.status == "recording"
        seen: set[str] = set()
        yield from self._emit_new(run, seen)
        if start:
            yield from self._record(run, seen)
        elif following:
            yield from self._follow(run, seen)
        with _ENGINE:
            done = RunStreamEvent(type="done", run=self._summary(run))
        yield done

    def _emit_new(self, run: _Run, seen: set[str]) -> Iterator[RunStreamEvent]:
        """Every live record not yet emitted, in write order. ULIDs sort by
        time, so this is the order the agent actually wrote them in.

        The engine read happens under the lock; the yields do not, so a paced
        stream never keeps another caller waiting."""
        with _ENGINE:
            fresh = sorted((r for r in _live(run.store) if r.ulid not in seen),
                           key=lambda r: r.ulid)
        for rec in fresh:
            seen.add(rec.ulid)
            yield RunStreamEvent(type="memory", memory=_memory_event(rec))

    def _join_fleet(self, run: _Run) -> int:
        """Take a finished run's place in the fleet, and return the versions
        written.

        Only what the run's own memory establishes: the versions its code
        imports, plus an exposure wherever an advisory it recorded names a
        weakness against one of those versions. Its code findings are not
        exposures -- they are about the code, not the package -- so they are
        not written here."""
        live = _live(run.store)
        versions: set[str] = set()
        exposures: set[tuple[str, str]] = set()
        for rec in live:
            c = rec.content or {}
            if c.get("type") == "version":
                versions.add(f"version:{c['package']}@{c['version']}")
            elif c.get("type") == "cve" and c.get("cwe"):
                exposures |= {(m, c["cwe"]) for m in rec.member_names
                              if m.startswith("version:")}
        if not versions:
            return 0
        engine_seed.join_fleet(
            self._fleet, f"run-{run.id}",
            versions=sorted(versions),
            # an exposure needs a version the run is recorded as using
            exposures=sorted(e for e in exposures if e[0] in versions),
        )
        return len(versions)

    def _follow(self, run: _Run, seen: set[str]) -> Iterator[RunStreamEvent]:
        """Watch a recording someone else started, until it ends.

        A viewer that arrives mid-recording must not report the run finished:
        it would show whatever had landed by then and call it the whole run.
        That is easy to miss with a build that records in milliseconds and
        certain to happen with one a model takes half a minute over -- not
        least because a remounting client opens the socket twice, and the
        recording belongs to the first."""
        yield RunStreamEvent(type="notice", detail=(
            f"This run is already being recorded"
            + (f" by {run.agent or run.model}" if run.agent or run.model else "")
            + ". Following its writes as they land."))
        idle = 0.0
        while True:
            with _ENGINE:
                recording = run.status == "recording"
            if not recording:
                break
            time.sleep(_FOLLOW_POLL)
            before = len(seen)
            yield from self._emit_new(run, seen)
            idle = 0.0 if len(seen) > before else idle + _FOLLOW_POLL
            if idle >= _FOLLOW_IDLE:
                # whoever claimed this recording stopped reporting. Say that,
                # rather than waiting on it forever or calling the run done
                yield RunStreamEvent(type="notice", detail=(
                    f"Nothing has been written for {int(idle)}s, so the "
                    "recording may have stopped. The run is left as it stands."))
                return
        # the status may have changed between the check and the last read, so
        # sweep once more: the closing writes are the ones worth having
        yield from self._emit_new(run, seen)

    def _record(self, run: _Run, seen: set[str]) -> Iterator[RunStreamEvent]:
        """Run the build, emitting each step's real writes as they happen.

        Caller has already claimed `run.streaming` under the engine lock.

        A model build holds no lock across its steps: a model turn takes
        seconds, and its writes serialize themselves through the guarded store
        instead, so the rest of the app keeps answering while it works."""
        log = None
        if run.model is not None and run.new_run is not None:
            log = engine_seed.new_build_log()
            steps = engine_seed.model_build_steps(
                run.new_run, run.task,
                llm=engine_seed.model_llm(run.model), log=log)
            yield RunStreamEvent(type="notice", detail=(
                f"{run.model} is carrying out this task now. Every record "
                "below is its own work, written through the real write gate "
                "into HyperMesh."))
        else:
            steps = engine_seed.build_steps(run.store)
            yield RunStreamEvent(type="notice", detail=(
                "No model is configured, so this run records the reference "
                "shard-loader build. Every write below goes through the real "
                "write gate into HyperMesh and is this run's own memory."))
        try:
            exhausted = object()   # the steps themselves yield None
            told = 0
            while True:
                if log is not None:
                    if next(steps, exhausted) is exhausted:
                        break
                else:
                    # one step of real writes per lock acquisition
                    with _ENGINE:
                        if next(steps, exhausted) is exhausted:
                            break
                yield from self._emit_new(run, seen)
                while log is not None and told < len(log.notices):
                    yield RunStreamEvent(type="notice", detail=log.notices[told])
                    told += 1
            run.status = "complete"
            with _ENGINE:
                joined = self._join_fleet(run)
            if joined:
                yield RunStreamEvent(type="notice", detail=(
                    f"This run joined the fleet as run-{run.id}, with the "
                    f"{joined} package version(s) its code imports. The fleet "
                    "views now count it in their shared risk."))
        finally:
            if run.status == "recording":
                # the caller stopped pulling: the recording did not finish, and
                # what it did write stays as the honest partial record
                run.status = "failed"
            run.streaming = False
            self._remember()    # however it ended, a restart must agree

    # -- provenance: the engine's why and forget verbs --

    def _ulid_for(self, store: Any, node: str) -> str:
        ulids = store.find_by_subject(node)
        if not ulids:
            raise NotFound(f"no memory about {node}")
        want = _DEFINING_CTYPE.get(engine_seed.entity_kind(node))
        if want:
            for u in ulids:
                rec = store.get(u)
                if rec is not None and (rec.content or {}).get("type") == want:
                    return u
        return ulids[0]

    @_serialized
    def run_why(self, run_id: str, node: str) -> WhyOut:
        store = self._run(run_id).store
        evidence = self._verbs(store).why(self._ulid_for(store, node))
        chain: list[EvidenceNode] = []
        for link in evidence.flatten():
            rec = store.get(link["ulid"])
            if rec is None:
                continue
            entity = _entity_of(rec)
            chain.append(EvidenceNode(
                ulid=link["ulid"],
                entity=entity,
                kind=engine_seed.entity_kind(entity or ""),  # type: ignore[arg-type]
                # record_code stores the class under "name", not "statement"
                statement=link["statement"] or (rec.content or {}).get("name"),
                origin=rec.envelope.origin.name,  # type: ignore[arg-type]
                status=rec.envelope.status.name,  # type: ignore[arg-type]
                source=rec.envelope.source,
                via=link["via"],
                tombstoned=rec.tombstoned,
            ))
        return WhyOut(run_id=run_id, node=node, chain=chain)

    @_serialized
    def run_rewind(self, run_id: str, at: int) -> RewindOut:
        """The engine's `as_of` primitive, shaped for the wire.

        `as_of` reconstructs from validity intervals and the supersession and
        tombstone history, so a memory written after *at* is absent and one
        tombstoned before *at* is absent, both for the right reasons.

        The one thing this must get right is a memory that was held then and
        has been forgotten since. It comes back -- the edge and its hash
        survived the tombstone, so the store can honestly say something was
        there -- but its payload was destroyed, so it comes back unreadable.
        Rewind proving a deletion never happened would make every certificate
        this system issues a lie."""
        run = self._run(run_id)
        store = run.store
        memories: list[RewindMemory] = []
        for ulid in store.as_of(at):
            rec = store.get(ulid)
            if rec is None:
                continue
            entity = _entity_of(rec)
            if entity is None:          # not a code-graph memory
                continue
            gone = rec.redacted or rec.tombstoned
            memories.append(RewindMemory(
                ulid=ulid, entity=entity,
                kind=engine_seed.entity_kind(entity),  # type: ignore[arg-type]
                statement=None if gone else _detail(rec),
                origin=rec.envelope.origin.name,  # type: ignore[arg-type]
                status=rec.envelope.status.name,  # type: ignore[arg-type]
                redacted=gone,
            ))
        memories.sort(key=lambda m: m.ulid)
        return RewindOut(
            run_id=run_id, at=at, memories=memories,
            milestones=self._milestones(run),
            held=len(memories),
            redacted=sum(1 for m in memories if m.redacted),
            now=len(_live(store)),
        )

    def _milestones(self, run: _Run) -> list[int]:
        """Instants when this run's memory changed: every write, and every
        deletion certificate issued against it.

        The certificates matter more than the writes. A forget is the only
        event that makes memory smaller, so it is the only point on the
        timeline where rewinding across it shows something the present has
        lost -- which is the question rewind exists to answer."""
        stamps = {rec.event_ts for rec in _records(run.store) if rec.event_ts}
        stamps |= {c.issued_at for c in self._certificates if c.run_id == run.id}
        return sorted(stamps)

    @_serialized
    def forget_preview(self, run_id: str, node: str) -> ForgetPreview:
        """The blast radius, read-only.

        Deliberately the same `store.closure` call the engine's forget makes
        before it tombstones anything, so this is not a second opinion about
        what would be destroyed -- it is the same computation, stopped one
        step short."""
        run = self._run(run_id)
        store = run.store
        root = self._ulid_for(store, node)
        doomed: list[DoomedEdge] = []
        pruned: set[str] = set()
        for u in [root, *store.closure(root)]:
            rec = store.get(u)
            if rec is None:
                continue
            pruned |= {m for m in (_members(store, u) or [])
                       if m.startswith("class:")}
            doomed.append(DoomedEdge(ulid=u, entity=_entity_of(rec),
                                     statement=_detail(rec)))
        warnings: list[str] = []
        if len(doomed) > 1:
            warnings.append(f"{len(doomed) - 1} memory(s) derived from this "
                            f"one go with it.")
        if pruned:
            warnings.append("The agent stops knowing about "
                            + ", ".join(sorted(_label(c) for c in pruned)) + ".")
        return ForgetPreview(
            run_id=run_id, node=node, root=root, version=self._version(store),
            purged_count=len(doomed), classes_pruned=sorted(pruned),
            doomed=doomed, warnings=warnings,
        )

    @staticmethod
    def _version(store: Any) -> str:
        """A digest over live memory: what the preview was computed against.

        Every write and every tombstone changes the live set, so this moves
        whenever the closure could have. It is not a revision counter and
        nothing orders two versions -- the only question ever asked of it is
        whether it is still the same one."""
        return hashlib.sha256(
            "".join(sorted(r.ulid for r in _live(store))).encode()
        ).hexdigest()[:16]

    def _finish_forget_operation(
        self, operation: operations.OperationRecord,
    ) -> DeletionCertificate:
        """Complete a prepared deletion using its immutable approved closure."""
        if operation.certificate is not None and operation.state == "committed":
            return operation.certificate
        run = self._run(operation.run_id)
        persisted = next((
            certificate
            for certificate in self._certificates
            if certificate.run_id == operation.run_id
            and certificate.root == operation.root
            and certificate.actor == (operation.actor or _ACTOR)
            and certificate.reason == operation.reason
        ), None)
        if persisted is not None:
            complete = all(
                (memory := run.store.get(edge.ulid)) is not None
                and memory.tombstoned
                and memory.redacted
                for edge in operation.planned
            )
            if complete:
                self._operations.transition(
                    operation.idempotency_key,
                    "committed",
                    certificate=persisted,
                )
                return persisted
        self._operations.transition(operation.idempotency_key, "mutating")
        issued_at = int(time.time())
        purged: list[PurgedEdge] = []
        classes_pruned: set[str] = set()
        try:
            for planned in operation.planned:
                rec = run.store.get(planned.ulid)
                if rec is None:
                    raise RuntimeError(
                        f"prepared deletion references missing memory {planned.ulid}"
                    )
                classes_pruned |= {
                    member
                    for member in rec.member_names
                    if member.startswith("class:")
                }
                result = run.store.tombstone(
                    planned.ulid,
                    reason=operation.reason,
                    actor=operation.actor or _ACTOR,
                    event_ts=issued_at,
                )
                retained = result["content_sha_retained"] or planned.content_sha
                if retained != planned.content_sha:
                    raise RuntimeError(
                        f"content hash changed during deletion of {planned.ulid}"
                    )
                purged.append(PurgedEdge(
                    ulid=planned.ulid,
                    entity=planned.entity,
                    content_sha_retained=retained,
                ))
            cert = DeletionCertificate(
                run_id=operation.run_id,
                node=operation.node,
                root=operation.root,
                reason=operation.reason,
                actor=operation.actor or _ACTOR,
                issued_at=issued_at,
                purged_count=len(purged),
                classes_pruned=sorted(classes_pruned),
                retained_hash=DeletionCertificate.digest(
                    [edge.content_sha_retained for edge in purged]
                ),
                purged=purged,
            )
            if not any(
                existing.run_id == cert.run_id
                and existing.root == cert.root
                and existing.actor == cert.actor
                and existing.reason == cert.reason
                for existing in self._certificates
            ):
                self._certificates.append(cert)
            self._remember()
            self._operations.transition(
                operation.idempotency_key, "committed", certificate=cert
            )
            return cert
        except BaseException as exc:
            self._operations.transition(
                operation.idempotency_key, "mutating", error=str(exc)[:500]
            )
            raise

    @_serialized
    def run_forget(self, run_id: str, node: str, reason: str, *,
                   actor: str = "", expected_version: str | None = None,
                   idempotency_key: str | None = None) -> DeletionCertificate:
        gateway.require_actor(actor)
        run = self._run(run_id)
        if idempotency_key is not None:
            if seen := self._operations.get(idempotency_key):
                if not seen.matches(
                    run_id=run_id,
                    node=node,
                    reason=reason,
                    actor=actor,
                    expected_version=expected_version,
                ):
                    raise Conflict(
                        "Idempotency-Key was already used for a different request"
                    )
                if seen.state == "committed" and seen.certificate is not None:
                    return seen.certificate
                if seen.state == "failed":
                    raise Conflict(
                        "the previous deletion attempt failed; operator review is required"
                    )
                return self._finish_forget_operation(seen)
        if (expected_version is not None
                and expected_version != self._version(run.store)):
            raise Stale("the memory moved since that preview; look again")
        if idempotency_key is None:
            return self._certificate(
                run, self._ulid_for(run.store, node), node, reason, actor=actor
            )

        root = self._ulid_for(run.store, node)
        approved_version = expected_version or self._version(run.store)
        planned: list[operations.PlannedEdge] = []
        for ulid in [root, *run.store.closure(root)]:
            rec = run.store.get(ulid)
            if rec is None:
                raise NotFound(f"memory {ulid} disappeared before deletion")
            planned.append(operations.PlannedEdge(
                ulid=ulid,
                entity=_entity_of(rec),
                content_sha=rec.envelope.content_sha,
            ))
        operation = operations.OperationRecord(
            idempotency_key=idempotency_key,
            kind="forget",
            run_id=run_id,
            node=node,
            root=root,
            reason=reason,
            actor=actor,
            expected_version=approved_version,
            planned=planned,
        )
        prepared = self._operations.prepare(operation)
        return self._finish_forget_operation(prepared)

    def _certificate(self, run: _Run, ulid: str, node: str,
                     reason: str, *, actor: str = "") -> DeletionCertificate:
        """Run the engine's forget verb and shape its certificate for the wire."""
        store = run.store
        raw = self._verbs(store).revert(ulid, reason=reason,
                                        actor=actor or _ACTOR)
        purged: list[PurgedEdge] = []
        pruned: set[str] = set()
        for p in raw.purged:
            members = _members(store, p["ulid"]) or []
            pruned |= {m for m in members if m.startswith("class:")}
            rec = store.get(p["ulid"])
            purged.append(PurgedEdge(
                ulid=p["ulid"],
                entity=_entity_of(rec) if rec is not None else None,
                content_sha_retained=p["content_sha_retained"],
            ))
        cert = DeletionCertificate(
            run_id=run.id, node=node, root=raw.root, reason=raw.reason,
            actor=raw.actor, issued_at=raw.issued_at, purged_count=raw.count,
            classes_pruned=sorted(pruned),
            retained_hash=DeletionCertificate.digest(
                [p.content_sha_retained for p in purged]),
            purged=purged,
        )
        self._certificates.append(cert)
        self._remember()        # the receipt outlives the process that issued it
        return cert

    # -- security: present vs exploitable, from the real findings and taint --

    @_serialized
    def run_findings(self, run_id: str) -> FindingsOut:
        live = _live(self._run(run_id).store)
        # Keyed by call site, not by sink: the same dangerous call in two
        # classes is two places to fix, and the owner is what says where.
        # Keying by sink alone silently dropped all but the last one.
        by_site: dict[tuple[str, str | None], Finding] = {}
        # class -> module, so a finding can be matched against what a scan
        # actually covered
        module_of: dict[str, str] = {}
        for rec in live:
            c = rec.content or {}
            if c.get("type") == "class":
                module_of[c.get("name", "")] = c.get("module") or "main"
            if c.get("type") != "finding":
                continue
            by_site[(c["call"], c.get("class"))] = Finding(
                sink=c["call"], owner=c.get("class"),
                cwe=c.get("cwe") or None, cwe_title=c.get("cwe_title") or None,
                capability=c.get("capability") or None, severity=None,
                present=True,
            )

        scans = [ScanOut(tool=c.get("tool") or "unknown", at=c.get("at") or 0,
                         modules=list(c.get("modules") or []),
                         results=c.get("results") or 0,
                         reachable=c.get("reachable") or 0)
                 for rec in live if (c := rec.content or {}).get("type") == "scan"]
        # every module any external scanner examined
        covered = {m for s in scans for m in s.modules}

        for rec in live:
            c = rec.content or {}
            if c.get("type") != "taint":
                continue
            # a taint edge names the sink, and where the scan knows which
            # class it was in, it upgrades only that call site. An older
            # scanner record without a class still reaches all of them.
            owner = c.get("class")
            reached = [f for (call, cls), f in by_site.items()
                       if call == c["sink"] and (owner is None or cls == owner)]
            if not reached:
                f = Finding(sink=c["sink"], owner=None, cwe=None, cwe_title=None,
                            capability=None, severity=None)
                by_site[(c["sink"], None)] = f
                reached = [f]
            tool = c.get("tool") or _analyser(rec)
            for f in reached:
                # Two analysers both tracing a path is corroboration, not
                # conflict. Where they overlap the accredited scanner tells
                # the story, because its path is the one a reader can check.
                if (f.reachability == "reachable"
                        and f.asserted_by not in (None, "builtin")
                        and tool == "builtin"):
                    continue
                f.asserted_by = tool
                f.reachability = "reachable"
                f.rule = c.get("rule")
                f.entry = c.get("entry")
                f.severity = c.get("severity") or f.severity
                # a scan that already ends its flow at the sink must not have
                # it appended again
                flow = list(c.get("flow") or [])
                f.path = flow if flow[-1:] == [c["sink"]] else [*flow, c["sink"]]
                f.cwe = f.cwe or (c.get("cwe") or None)

        # Anything no taint edge reached is only "cleared" where a scanner
        # actually read the module it lives in. Everywhere else it stays
        # unassessed, however tempting a zero would look.
        for (_, cls), f in by_site.items():
            read_by = next((s.tool for s in scans
                            if module_of.get(cls or "", "") in s.modules), None)
            if f.reachability == "reachable":
                # A scanner that read this module and did not corroborate our
                # own walk is worth showing. It is not a refutation -- its
                # rules may not cover this sink at all -- so the claim stands
                # and the reader is told who declined to second it.
                if f.asserted_by == "builtin" and read_by:
                    f.disputed_by = read_by
                continue
            if read_by:
                f.reachability = "not-reachable"
                f.asserted_by = read_by

        order = {"reachable": 0, "not-assessed": 1, "not-reachable": 2}
        findings = sorted(by_site.values(),
                          key=lambda f: (order[f.reachability], f.sink,
                                         f.owner or ""))
        tally = Counter(f.reachability for f in findings)
        return FindingsOut(
            run_id=run_id, present=len(findings),
            exploitable=tally["reachable"],
            findings=findings,
            scanned=sum(1 for r in live if _ctype(r) == "class"),
            reachable=tally["reachable"],
            not_reachable=tally["not-reachable"],
            not_assessed=tally["not-assessed"],
            scans=sorted(scans, key=lambda s: s.at),
        )

    @_serialized
    def ingest_scan(self, run_id: str, sarif: dict) -> ScanOut:
        """Record an external scanner's run against this code.

        Both halves land: the flows it traced become reachability evidence,
        and what it merely looked at becomes the licence to call anything it
        did not flag assessed."""
        from meshagent.sarif import ingest_scan as _ingest

        run = self._run(run_id)
        got = _ingest(run.store, sarif)
        return ScanOut(tool=got.tool, at=int(time.time()), modules=got.modules,
                       results=got.results, reachable=got.reachable)

    # -- the recorder: agents this product does not own --

    @_serialized
    def fleet_coverage(self) -> CoverageOut:
        """Count modules against the decisions they hang off.

        A module is explained when the decision it derives from was one
        somebody actually stated. The recorder writes an explicitly
        unexplained decision where an agent volunteered nothing, so this is a
        read of what is in the graph rather than an inference about what is
        missing from it."""
        rows: dict[str | None, CoverageRow] = {}
        modules = explained = self_recorded = 0

        for run in self._runs.values():
            live = list(_live(run.store))
            hollow = {rec.ulid for rec in live
                      if (c := rec.content or {}).get("type") == "decision"
                      and c.get("unexplained")}

            for rec in live:
                if (rec.content or {}).get("type") != "module":
                    continue
                if not run.external:
                    # MeshAgent's own loop is made to state a decision before
                    # it writes, so it is always 100% and tells you nothing
                    # about whether anyone adopted the tool.
                    self_recorded += 1
                    continue

                row = rows.setdefault(run.owner, CoverageRow(
                    owner=run.owner, owner_name=run.owner_name,
                    attributed=True))
                if run.agent and run.agent not in row.agents:
                    row.agents.append(run.agent)
                # One unproven run is enough to make the whole row unproven.
                # A name is either something the security office can act on
                # or it is not, and averaging that would hide the gap.
                row.attributed = row.attributed and run.attributed
                row.modules += 1
                modules += 1
                if not (self._parent_ulids(run.store, rec.ulid) & hollow):
                    row.explained += 1
                    explained += 1

        return CoverageOut(
            modules=modules, explained=explained, self_recorded=self_recorded,
            # worst first: the point of the table is who needs talking to
            by_developer=sorted(rows.values(),
                                key=lambda r: (-r.unexplained, r.owner or "")),
        )

    @_serialized
    def check_package(self, req: GateRequest) -> GateDecision:
        """The same policy decision, plus what the fleet already holds.

        The count is context, not permission. It is tempting to read "nine
        agents already use this" as reassurance, and it is the opposite:
        widely used and vulnerable is the worst case on this screen, not the
        best. So it is returned as a number beside the verdict rather than
        allowed to influence it."""
        decision = gateway.gate_decision(
            req, fleet_agents=self._fleet_holders(req.package, req.version))
        return decision

    def _fleet_holders(self, package: str, version: str) -> int:
        """Fleet agents whose memory already names this exact version."""
        if not version:
            return 0
        wanted = f"version:{package}@{version}"
        agents: set[str] = set()
        for rec in _live(self._fleet):
            members = set(rec.member_names)
            if wanted in members:
                agents |= {m for m in members if m.startswith("agent:")}
        return len(agents)

    @staticmethod
    def _parent_ulids(store: Any, ulid: str) -> set[str]:
        """The memories this one was derived from, one level up."""
        return {p["ulid"] for p in store.why(ulid, max_depth=1).get("parents", [])}

    def _session_run(self, batch: RecorderBatch, owner: str | None,
                     owner_name: str | None,
                     attributed: bool = False) -> tuple[_Run, bool]:
        """The run this session writes to, opening it on first sight.

        Scoped by owner as well as session id, because the id is the
        adapter's and two developers' editors may well pick the same one.
        Returning someone else's run on a collision would file one
        developer's code under another's name."""
        for run in self._runs.values():
            if run.session == batch.session and run.owner == owner:
                return run, False

        opening = next((e for e in batch.events
                        if isinstance(e, SessionEvent)), None)
        if opening is None:
            # Refusing beats inventing a task. A run whose stated purpose the
            # product made up is worse than a batch the adapter has to resend
            # with its session event attached.
            raise NotFound(
                f"unknown session {batch.session}: the batch that opens a "
                "session must carry its session event")

        run_id = new_run_id()
        new = engine_seed.record_task(run_id, opening.task)
        run = _Run(id=run_id, task=opening.task, store=new.store,
                   status="recording", created_at=int(time.time()),
                   store_name=f"run-{run_id}", new_run=new,
                   owner=owner, owner_name=owner_name,
                   external=True, session=batch.session, agent=batch.agent,
                   attributed=attributed)
        self._runs[run_id] = run
        self._remember()
        return run, True

    def _recorder(self, run: _Run) -> Any:
        """A recorder bound to this run's store, with its sources and
        decisions restored.

        The in-process one is reused where it exists. Where it does not --
        the API restarted mid-session, which for a developer's editor is
        entirely normal -- it is rebuilt from what the store already holds,
        so code posted today can still hang off a decision posted yesterday.
        """
        from meshagent.codegraph import P_DECISION, P_SOURCE, CodeGraphRecorder

        if run.new_run is not None:
            return run.new_run.recorder

        sources: dict[str, str] = {}
        decisions: dict[str, str] = {}
        for record in _live(run.store):
            kind = (record.content or {}).get("type")
            prefix = {"source": P_SOURCE, "decision": P_DECISION}.get(kind)
            if prefix is None:
                continue
            held = sources if kind == "source" else decisions
            for member in record.member_names:
                if member.startswith(prefix):
                    held[member[len(prefix):]] = record.ulid

        rec = CodeGraphRecorder(run.store)
        rec.restore(sources=sources, decisions=decisions)
        return rec

    @_serialized
    def record_events(self, batch: RecorderBatch, *, owner: str | None = None,
                      owner_name: str | None = None,
                      attributed: bool = False) -> RecorderReceipt:
        """Write an external agent's reported activity into governed memory.

        Every event lands through the same recorder the built-in loop uses,
        so memory written by Claude Code is indistinguishable downstream from
        memory written here -- which is the point. What it cannot do is
        invent: code that arrives without a stated reason is recorded as
        explicitly unexplained, and code in a language this build cannot
        parse is stored and readable but not decomposed."""
        run, opened = self._session_run(batch, owner, owner_name, attributed)
        rec = self._recorder(run)
        receipt = RecorderReceipt(run_id=run.id, session=batch.session,
                                  opened=opened)

        for event in batch.events:
            if isinstance(event, SessionEvent):
                if event.ends and run.status == "recording":
                    run.status = "complete"
                    self._join_fleet(run)
                receipt.recorded += 1
            elif isinstance(event, DecisionEvent):
                rec.record_decision(
                    event.id, event.statement,
                    from_sources=[f"task-{run.id}"])
                receipt.recorded += 1
            elif isinstance(event, CodeEvent):
                self._record_code_event(rec, run, event, receipt)
            elif isinstance(event, PackageEvent):
                rec.record_version(event.package, event.version,
                                   license=event.license)
                receipt.recorded += 1
            elif isinstance(event, ToolEvent):
                rec.record_tool(event.name, event.detail)
                receipt.recorded += 1

        self._remember()
        return receipt

    def _record_code_event(self, rec: Any, run: _Run, event: CodeEvent,
                           receipt: RecorderReceipt) -> None:
        """One file the agent wrote.

        Code has to hang off a decision, and a hook watching a file write has
        none. Rather than attributing the code to a reason nobody gave, an
        explicitly unexplained decision is recorded for it, which turns the
        missing why into something countable."""
        decision = event.because
        if decision is not None and not rec.knows_decision(decision):
            receipt.refused.append(
                f"code {event.module}: unknown decision {decision!r}")
            return
        if decision is None:
            decision = f"unexplained-{event.module}"
            if not rec.knows_decision(decision):
                rec.record_decision(
                    decision,
                    f"No rationale was recorded for {event.module}. "
                    f"{run.agent or 'The agent'} wrote it without saying why.",
                    from_sources=[f"task-{run.id}"], unexplained=True)
            receipt.unexplained += 1

        # Decided before writing anything, not by letting the decomposition
        # fail partway: record_code writes the module first, so catching the
        # parse error afterwards would record the text twice.
        try:
            ast.parse(event.code)
        except SyntaxError:
            # Not Python, or not valid Python. The text is still governed
            # memory and still readable; it simply has no class
            # decomposition, and `analysed` is what stops a reader from
            # reading that silence as "this file defines no classes".
            rec.record_module(event.code, decision_id=decision,
                              module=event.module)
        else:
            rec.record_code(event.code, decision_id=decision,
                            module=event.module)
            receipt.analysed += 1
        receipt.recorded += 1

    @_serialized
    def run_code(self, run_id: str) -> CodeOut:
        """The source the agent actually submitted, read back out of memory.
        Classes are listed per module from the class records, so the listing
        is the scan's own view of the text rather than a re-parse."""
        live = list(_live(self._run(run_id).store))
        by_module: dict[str, list[str]] = {}
        for rec in live:
            c = rec.content or {}
            if c.get("type") == "class":
                by_module.setdefault(c.get("module") or "main", []).append(
                    c.get("name", ""))
        modules = [
            CodeModule(name=c["name"], code=c.get("code") or "",
                       classes=sorted(by_module.get(c["name"], [])))
            for rec in live
            if (c := rec.content or {}).get("type") == "module"
        ]
        return CodeOut(run_id=run_id, modules=modules)

    @_serialized
    def run_finding(self, run_id: str, sink: str) -> Finding:
        for f in self.run_findings(run_id).findings:
            if f.sink == sink:
                return f
        raise NotFound(f"no finding for sink {sink}")

    # -- supply chain --

    @_serialized
    def run_sbom(self, run_id: str) -> SbomOut:
        live = _live(self._run(run_id).store)
        entries: dict[str, SbomEntry] = {}
        for rec in live:
            c = rec.content or {}
            if c.get("type") != "version":
                continue
            entries[f"{c['package']}@{c['version']}"] = SbomEntry(
                package=c["package"], version=c["version"], license=c["license"],
            )
        for rec in live:
            c = rec.content or {}
            if c.get("type") != "cve":
                continue
            for affected in c.get("affects", []):
                entry = entries.get(affected)
                if entry is None:
                    continue
                entry.cves.append(c["id"])
                entry.feed = c.get("feed")
                entry.severity = _worse(entry.severity, c.get("severity"))
        return SbomOut(run_id=run_id,
                       entries=sorted(entries.values(), key=lambda e: e.package))

    @_serialized
    def cve_impact(self, cve: str) -> CveImpact:
        for run in self._runs.values():
            rec = next((r for r in _live(run.store)
                        if (r.content or {}).get("type") == "cve"
                        and r.content["id"] == cve), None)
            if rec is None:
                continue
            impact = self._engine_cve_impact(run.store, cve)
            content = rec.content or {}
            return CveImpact(
                cve=cve, severity=content.get("severity"),
                summary=content.get("summary"), feed=content.get("feed"),
                versions=impact["versions"], packages=impact["packages"],
                classes=impact["classes"], decisions=impact["decisions"],
                agents=self._agents_on(impact["versions"]),
            )
        raise NotFound(f"no advisory {cve}")

    def _agents_on(self, versions: list[str]) -> list[str]:
        """Fleet agents whose real memory shares one of these versions."""
        wanted = set(versions)
        agents: set[str] = set()
        for rec in _live(self._fleet):
            members = set(rec.member_names)
            if members & wanted:
                agents |= {_label(m) for m in members if m.startswith("agent:")}
        return sorted(agents)

    # -- structure analysis on real hyperedges --

    @_serialized
    def run_hypergraph(self, run_id: str) -> HypergraphOut:
        return analysis.hypergraph_out(engine_seed.build_hypergraph(self._run(run_id).store))

    @_serialized
    def run_decomposition(self, run_id: str) -> DecompositionOut:
        return analysis.decomposition_out(engine_seed.build_hypergraph(self._run(run_id).store))

    @_serialized
    def run_scales(self, run_id: str) -> list[ScaleOut]:
        return analysis.scales_out(engine_seed.build_hypergraph(self._run(run_id).store))

    @_serialized
    def fleet_decomposition(self) -> DecompositionOut:
        return analysis.decomposition_out(engine_seed.build_hypergraph(self._fleet))


def _records(store: Any) -> list[Any]:
    from meshagent.codegraph import _all_entity_names

    seen: set[str] = set()
    out: list[Any] = []
    for name in _all_entity_names(store):
        for ulid in store.find_by_subject(name):
            if ulid in seen:
                continue
            seen.add(ulid)
            rec = store.get(ulid)
            if rec is not None:
                out.append(rec)
    return out


def _live(store: Any) -> list[Any]:
    """Memory that has not been tombstoned. A forget redacts the payload and
    keeps the hash, so tombstoned edges are audit trail, not current memory."""
    return [r for r in _records(store) if not r.tombstoned]


def _ctype(rec: Any) -> str | None:
    return rec.content.get("type") if getattr(rec, "content", None) else None


def _members(store: Any, ulid: str) -> list[str] | None:
    rec = store.get(ulid)
    return None if rec is None else [m for m in rec.member_names
                                     if not m.startswith("edge:")]


def _entity_of(rec: Any) -> str | None:
    """The entity a memory record is about. Findings are about their sink and
    taint edges about their entry point; everything else names its subject."""
    c = rec.content or {}
    ctype = c.get("type")
    if ctype == "finding":
        return f"sink:{c['call']}"
    if ctype == "taint":
        return f"entry:{c['entry']}"
    if ctype == "version":
        return f"version:{c['package']}@{c['version']}"
    if ctype == "cve":
        return f"cve:{c['id']}"
    return next(
        (n for n in rec.member_names
         if not n.startswith("edge:")
         and engine_seed.entity_kind(n) not in
         ("package", "sink", "cwe", "capability")),
        None,
    )


_STEP_LABEL = {
    "source": "Read a source",
    "decision": "Made a decision",
    "module": "Submitted a module",
    "class": "Wrote code",
    "finding": "Found a dangerous call",
    "version": "Recorded a dependency",
    "cve": "Joined the advisory feed",
    "taint": "Scanned for reachability",
}


def _detail(rec: Any) -> str:
    """One line describing what this memory edge says, from its real payload."""
    c = rec.content or {}
    ctype = c.get("type")
    if ctype == "module":
        lines = len((c.get("code") or "").strip().splitlines())
        return f"{c.get('name')} · {lines} line{'' if lines == 1 else 's'}"
    if ctype == "class":
        pkgs = ", ".join(c.get("packages") or []) or "no packages"
        return f"class {c.get('name')} ({pkgs})"
    if ctype == "finding":
        weakness = f" ({c['cwe']})" if c.get("cwe") else ""
        shell = " with shell=True" if c.get("shell_true") else ""
        return f"{c.get('call')}{shell} grants {c.get('capability')}{weakness}"
    if ctype == "version":
        return f"{c.get('package')}@{c.get('version')} · {c.get('license')}"
    if ctype == "cve":
        return f"{c.get('id')} {c.get('severity')}: {c.get('summary')}"
    if ctype == "taint":
        rule = f" · {c['rule']}" if c.get("rule") else ""
        return f"{c.get('entry')} reaches {c.get('sink')}{rule}"
    if _is_episode(rec):
        tools = ", ".join(dict.fromkeys(
            str(t.get("tool")) for t in c.get("tool_calls") or []))
        turns = c.get("turns")
        return (f"{c.get('outcome')} after {turns} turn{'' if turns == 1 else 's'}"
                + (f" · {tools}" if tools else ""))
    statement = c.get("statement") or c.get("name")
    if statement:
        return str(statement)
    # a forget keeps the edge and drops the payload, so an empty one here is
    # genuinely redacted. Anything else is a payload shape we do not render,
    # and saying "redacted" about it would be a lie.
    return "redacted" if rec.tombstoned or not c else "no detail recorded"


def _is_episode(rec: Any) -> bool:
    """The agent loop's audit record of one task, written as its own Kind."""
    kind = getattr(getattr(rec, "envelope", None), "kind", None)
    return getattr(kind, "name", "") == "EPISODE"


def _memory_event(rec: Any) -> MemoryEvent:
    origin = rec.envelope.origin.name
    status = rec.envelope.status.name
    ctype = _ctype(rec) or ""
    # the run's own task is recorded as a source, but the user stated it
    if ctype == "source" and origin == "USER":
        step = "Stated the task"
    elif _is_episode(rec):
        step = "Closed the task"
    else:
        step = _STEP_LABEL.get(ctype, "Wrote memory")
    return MemoryEvent(
        step=step,
        detail=_detail(rec), origin=origin, status=status,
        members=sorted(m for m in rec.member_names if not m.startswith("edge:")),
        ulid=rec.ulid, gate=MemoryEvent.gate_note(origin, status),
    )


_PINNED = re.compile(r"[A-Za-z_][\w.-]*@[\w.!+-]+")
_WORD = re.compile(r"[A-Za-z_][\w.-]*")


def _read_query(
    query: str, known: set[str],
) -> tuple[set[str] | None, str]:
    """Which version entities a query asks about, and how it was read.

    A tiny language on purpose: a pinned `name@version`, or a bare package
    name meaning every version of it. `None` means the query named nothing in
    fleet memory and no filter was applied -- which the caller has to say out
    loud, because a list under a search box reads as an answer to it."""
    labels = {v: _label(v) for v in known}

    pinned = {v for v in known
              if any(labels[v] == tok for tok in _PINNED.findall(query))}
    if pinned:
        return pinned, f"Agents whose memory names {_join(sorted(
            labels[v] for v in pinned))}."

    words = {w.lower() for w in _WORD.findall(query)}
    by_package = {v for v in known if labels[v].split("@")[0].lower() in words}
    if by_package:
        packages = sorted({labels[v].split("@")[0] for v in by_package})
        return by_package, (
            f"Agents importing {_join(packages)}, at any version "
            f"({_join(sorted(labels[v] for v in by_package))}).")

    return None, ("Nothing in this query names a package in fleet memory, so "
                  "every agent is shown and none of them were filtered.")


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return items[0] if items else ""
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _worse(a: str | None, b: str | None) -> str | None:
    """The more severe of two severities, so an entry reports its worst CVE."""
    ranked = [s for s in (a, b) if s in _SEVERITY_ORDER]
    if not ranked:
        return a or b
    return min(ranked, key=_SEVERITY_ORDER.index)
