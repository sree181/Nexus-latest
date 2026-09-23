"""The MeshAgent gateway: the ONE seam between this API and the engine.

Today it returns sample data so the whole stack runs with no engine present.
To go live, implement `EngineGateway` against your real MeshAgent package and
select it in `get_gateway()`. Nothing else in the API changes, because both
gateways return the same Pydantic models.

Real wiring sketch (EngineGateway):

    import hypermeshdb
    from hypermeshdb.agentmem import MemoryStore
    from meshagent.codegraph import export_graph, cve_impact
    from meshagent.osv import join_osv

    class EngineGateway(Gateway):
        def __init__(self, db_dir: str):
            self._db = hypermeshdb.connect(db_dir)
            self._store = MemoryStore(self._db, db_dir)

        def run_graph(self, run_id: str) -> GraphPayload:
            g = export_graph(self._store)      # nodes/imports/derives/security/sbom/taint
            return _to_payload(g)              # map engine export -> wire model

The point: the UI depends on the gateway interface, not on HyperMesh.
"""

from __future__ import annotations

import os
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator

from . import analysis, gate, sample
from .models import (
    ApplyReceipt,
    CodeOut,
    CoverageOut,
    CveImpact,
    DecompositionOut,
    DeletionCertificate,
    Finding,
    FindingsOut,
    FleetOverview,
    ForgetPreview,
    GateAdvisory,
    GateDecision,
    GateRequest,
    GatewayMode,
    GraphPayload,
    HypergraphOut,
    QueryResult,
    RecorderBatch,
    RecorderReceipt,
    Recommendation,
    RewindOut,
    RunStreamEvent,
    RunSummary,
    SbomOut,
    ScaleOut,
    ScanOut,
    WhyOut,
)


class NotFound(LookupError):
    """A run, node or advisory the gateway does not hold. Routes map this to
    404 so neither gateway has to import FastAPI."""


class Stale(Exception):
    """A forget was confirmed against memory that has since moved.

    Routes map this to 412. It is a refusal, not a failure: the closure the
    operator read and approved is no longer the closure that would be
    destroyed, and forget is not a verb that gets to be approximately right."""


def gate_decision(req: GateRequest, *, fleet_agents: int,
                  sample: bool = False) -> GateDecision:
    """Run the policy and dress the result as the wire model.

    Shared by both gateways deliberately. The gate is the one place that
    refuses, so the two implementations diverging on what counts as a block
    would mean a package allowed in sample mode and refused in production,
    or worse, the other way round."""
    pol = gate.policy()
    got = gate.check(req.package, req.version, pol)
    return GateDecision(
        package=got.package, version=got.version, verdict=got.verdict,
        reasons=got.reasons, worst=got.worst, unavailable=got.unavailable,
        advisories=[GateAdvisory(id=a.id, severity=a.severity,
                                 summary=a.summary, cwe=a.cwe)
                    for a in got.advisories],
        policy=pol.described, fleet_agents=fleet_agents, sample=sample,
    )


class Gateway(ABC):
    @abstractmethod
    def mode(self) -> GatewayMode:
        """Whether this deployment keeps what it is sent.

        On the interface rather than read off the class name, because the one
        caller that needs it is the banner telling a developer their recording
        was discarded, and that answer must come from the implementation doing
        the discarding."""

    @abstractmethod
    def fleet_overview(self) -> FleetOverview: ...

    @abstractmethod
    def run_graph(self, run_id: str) -> GraphPayload: ...

    @abstractmethod
    def fleet_query(self, query: str) -> QueryResult: ...

    @abstractmethod
    def recommendations(self) -> list[Recommendation]: ...

    @abstractmethod
    def apply_recommendation(self, rec_id: str) -> ApplyReceipt: ...

    # -- runs --
    @abstractmethod
    def runs(self, *, owner: str | None = None) -> list[RunSummary]:
        """Oldest first, so the last entry is the newest run.

        `owner` narrows the list to one developer's runs; None is the
        analyst's fleet-wide view. A seeded run is in both: the deployment
        publishes it as a reference build, and a developer whose list is
        empty has no way to see what any of these screens are for."""

    @abstractmethod
    def run(self, run_id: str) -> RunSummary:
        """One run, for callers that need its owner before serving it."""

    @abstractmethod
    def create_run(self, task: str, *, owner: str | None = None,
                   owner_name: str | None = None) -> RunSummary: ...

    @abstractmethod
    def run_stream(self, run_id: str) -> Iterator[RunStreamEvent]:
        """Frames for the run WebSocket. A generator, pulled one frame at a
        time: in engine mode advancing it performs the run's next memory
        writes, so every frame describes memory that now exists."""

    # -- provenance: why / forget --
    @abstractmethod
    def run_why(self, run_id: str, node: str) -> WhyOut: ...

    @abstractmethod
    def run_rewind(self, run_id: str, at: int) -> RewindOut:
        """What this run's memory held at epoch-second *at*.

        Reads only. A memory forgotten since must come back listed and
        redacted, never readable: rewind is allowed to prove that something
        was there, and is not allowed to undo a deletion."""

    @abstractmethod
    def forget_preview(self, run_id: str, node: str) -> ForgetPreview:
        """What forgetting *node* would destroy, without destroying it.

        Must not write. The whole value of the preview is that calling it is
        not a way of finding out the hard way."""

    @abstractmethod
    def run_forget(self, run_id: str, node: str, reason: str, *,
                   actor: str = "", expected_version: str | None = None,
                   idempotency_key: str | None = None) -> DeletionCertificate:
        """Forget a node and its closure, and issue the certificate.

        `actor` is the authenticated caller. It is the whole point of the
        certificate: a deletion nobody is named for proves only that
        something was destroyed.

        `expected_version` is the preview's version. When given and no longer
        current, raise `Stale` rather than purging: the operator approved a
        closure, not a node. When omitted the forget proceeds unguarded, which
        is what the recommendation path and the engine's own seeding need.

        `idempotency_key` makes a replay return the first certificate instead
        of purging again. A retried forget that silently reports zero purged
        edges reads like nothing was there, which is the opposite of true."""

    # -- security: present vs exploitable --
    @abstractmethod
    def run_findings(self, run_id: str) -> FindingsOut: ...

    @abstractmethod
    def run_finding(self, run_id: str, sink: str) -> Finding: ...

    @abstractmethod
    def ingest_scan(self, run_id: str, sarif: dict) -> ScanOut:
        """Record an external scanner's run: the flows it traced, and the
        modules it merely looked at. The second half is what licenses
        describing an unflagged sink as assessed rather than unexamined."""

    # -- external agents reporting in --
    @abstractmethod
    def fleet_coverage(self) -> CoverageOut:
        """How much of the recorded code states a reason, per developer.

        The honest answer to how much of the estate this tool actually sees
        the reasoning behind, which no other view gives."""

    @abstractmethod
    def record_events(self, batch: RecorderBatch, *, owner: str | None = None,
                      owner_name: str | None = None,
                      attributed: bool = False) -> RecorderReceipt:
        """Record what an agent this product does not own actually did.

        The batch names its own session rather than a run, because the
        adapter has no reason to know run ids: the first batch of a session
        opens a run, and every later batch finds it again.

        `attributed` says whether `owner` was proven rather than asserted, and
        is carried all the way to the coverage table, which names people."""

    @abstractmethod
    def check_package(self, req: GateRequest) -> GateDecision:
        """Whether an agent may install this package at this version.

        The only method on this interface that refuses anything, which is
        why its verdict has four values rather than two: not knowing is a
        distinct answer from allowing, and a caller must be able to tell
        them apart."""

    # -- the code the run wrote --
    @abstractmethod
    def run_code(self, run_id: str) -> CodeOut: ...

    # -- supply chain --
    @abstractmethod
    def run_sbom(self, run_id: str) -> SbomOut: ...

    @abstractmethod
    def cve_impact(self, cve: str) -> CveImpact: ...

    # -- structure analysis (hgviz) --
    @abstractmethod
    def run_hypergraph(self, run_id: str) -> HypergraphOut: ...

    @abstractmethod
    def run_decomposition(self, run_id: str) -> DecompositionOut: ...

    @abstractmethod
    def run_scales(self, run_id: str) -> list[ScaleOut]: ...

    @abstractmethod
    def fleet_decomposition(self) -> DecompositionOut: ...


def new_run_id() -> str:
    """Short, URL-friendly run id, matching the curated one's shape."""
    return uuid.uuid4().hex[:4]


class SampleGateway(Gateway):
    """Runs the full UI on curated, honest sample data. No engine required.
    Runs created here and certificates issued here are held in process
    memory: sample mode is a demonstration, not a store."""

    def __init__(self) -> None:
        self._created: list[RunSummary] = []
        self._certificates = 0
        # idempotency key -> the certificate that key already issued
        self._forgotten: dict[str, DeletionCertificate] = {}

    def mode(self) -> GatewayMode:
        return GatewayMode(
            engine=False, persists=False,
            note=("Sample mode: nothing sent to this deployment is kept. A "
                  "batch posted to /api/recorder is validated, counted and "
                  "discarded, and the receipt it returns describes a dry run "
                  "rather than a write. Start the API with MESHAGENT_ENGINE=1 "
                  "to record into HyperMesh for real."),
        )

    def fleet_overview(self) -> FleetOverview:
        base = sample.fleet_overview()
        return base.model_copy(update={
            "deletion_certificates": base.deletion_certificates + self._certificates,
        })

    def run_graph(self, run_id: str) -> GraphPayload:
        return sample.run_graph(run_id)

    def fleet_query(self, query: str) -> QueryResult:
        return sample.fleet_query(query)

    def recommendations(self) -> list[Recommendation]:
        return sample.recommendations()

    def apply_recommendation(self, rec_id: str) -> ApplyReceipt:
        """Applies the one curated recommendation that is a forget (that much
        this gateway can really do) and records the rest as accepted."""
        rec = next((r for r in self.recommendations() if r.id == rec_id), None)
        if rec is None:
            raise NotFound(f"unknown recommendation {rec_id}")
        if rec_id == "rec-source":
            cert = self.run_forget(sample.RUN_ID, "source:poisoned-mirror",
                                   f"applied {rec_id}")
            return ApplyReceipt(
                recommendation_id=rec_id, title=rec.title,
                action=(f"Forgot {cert.node} and the {cert.purged_count - 1} "
                        "memories derived from it"),
                changed_memory=True, agents=rec.agents, memories_written=0,
                certificates=[cert],
                note="The purged payloads are gone; their hashes are retained.",
                issued_at=cert.issued_at, sample=True,
            )
        return ApplyReceipt(
            recommendation_id=rec_id, title=rec.title,
            action=f"Recorded as accepted for {rec.agents} agent(s)",
            changed_memory=False, agents=rec.agents, memories_written=1,
            note=("The change itself happens in the agents' repositories. "
                  "Sample mode records the decision only."),
            issued_at=int(time.time()), sample=True,
        )

    # -- runs --

    def runs(self, *, owner: str | None = None) -> list[RunSummary]:
        held = sample.runs() + list(self._created)
        return [r for r in held
                if owner is None or r.seeded or r.owner == owner]

    def run(self, run_id: str) -> RunSummary:
        for r in sample.runs() + list(self._created):
            if r.id == run_id:
                return r
        raise NotFound(f"unknown run {run_id}")

    def create_run(self, task: str, *, owner: str | None = None,
                   owner_name: str | None = None) -> RunSummary:
        run = RunSummary(
            id=new_run_id(), task=task, status="recording",
            memory_count=1,        # the task itself, recorded as USER origin
            findings=0, created_at=int(time.time()), sample=True,
            # sample mode replays the curated run's memory for any new run, so
            # what gets shown is not an execution of this task either
            reference_build=True,
            owner=owner, owner_name=owner_name,
        )
        self._created.append(run)
        return run

    def run_stream(self, run_id: str) -> Iterator[RunStreamEvent]:
        """Replays the curated run's memory events. Sample mode has no engine
        to record into, so a run created here is told plainly that what it is
        watching is the curated run's memory, not its own."""
        run = next((r for r in self.runs() if r.id == run_id), None)
        if run is None:
            raise NotFound(f"unknown run {run_id}")
        if run.id != sample.RUN_ID:
            yield RunStreamEvent(type="notice", detail=(
                "Sample mode has no engine to record into. The events below are "
                "the curated run's memory, replayed so the stream can be seen."))
        events = sample.memory_events()
        for event in events:
            yield RunStreamEvent(type="memory", memory=event)
        if run.status == "recording":
            run = run.model_copy(update={"status": "complete",
                                         "memory_count": len(events)})
            self._created = [run if r.id == run.id else r for r in self._created]
        yield RunStreamEvent(type="done", run=run)

    def _require_curated(self, run_id: str) -> None:
        """The curated story exists only for the sample run; a run created in
        sample mode has nothing recorded against it yet."""
        if run_id != sample.RUN_ID:
            raise NotFound(f"no recorded memory for run {run_id}")

    # -- provenance --

    def run_why(self, run_id: str, node: str) -> WhyOut:
        self._require_curated(run_id)
        out = sample.why(run_id, node)
        if not out.chain:
            raise NotFound(f"no memory about {node}")
        return out

    def run_rewind(self, run_id: str, at: int) -> RewindOut:
        self._require_curated(run_id)
        return sample.rewind(run_id, at)

    def forget_preview(self, run_id: str, node: str) -> ForgetPreview:
        self._require_curated(run_id)
        try:
            return sample.forget_preview(run_id, node)
        except KeyError as exc:
            raise NotFound(f"no memory about {node}") from exc

    def run_forget(self, run_id: str, node: str, reason: str, *,
                   actor: str = "", expected_version: str | None = None,
                   idempotency_key: str | None = None) -> DeletionCertificate:
        self._require_curated(run_id)
        if expected_version is not None and expected_version != sample.VERSION:
            raise Stale("the memory moved since that preview; look again")
        if idempotency_key is not None:
            if (seen := self._forgotten.get(idempotency_key)) is not None:
                return seen
        try:
            cert = sample.forget(run_id, node, reason,
                                 actor or "sample-operator",
                                 int(time.time()))
        except KeyError as exc:
            raise NotFound(f"no memory about {node}") from exc
        if idempotency_key is not None:
            self._forgotten[idempotency_key] = cert
        self._certificates += 1
        return cert

    # -- security --

    def run_findings(self, run_id: str) -> FindingsOut:
        self._require_curated(run_id)
        return sample.findings(run_id)

    def run_finding(self, run_id: str, sink: str) -> Finding:
        for f in self.run_findings(run_id).findings:
            if f.sink == sink:
                return f
        raise NotFound(f"no finding for sink {sink}")

    def ingest_scan(self, run_id: str, sarif: dict) -> ScanOut:
        """Curated memory is fixed, so a scan is parsed and reported but
        changes nothing. Saying so beats pretending it landed."""
        self._require_curated(run_id)
        return sample.scan_summary(sarif)

    def record_events(self, batch: RecorderBatch, *, owner: str | None = None,
                      owner_name: str | None = None,
                      attributed: bool = False) -> RecorderReceipt:
        """Validate a batch and report what it would have recorded, without
        recording it: there is no engine here to record into.

        Worth doing rather than refusing outright, because it is what someone
        writing an adapter needs to develop against. `sample` is set so no
        caller mistakes the dry run for a write."""
        del owner, owner_name, attributed   # nothing here is attributable
        return sample.recorder_receipt(batch)

    def check_package(self, req: GateRequest) -> GateDecision:
        """The real gate, with no fleet context behind it.

        The policy and the advisory feeds are not the engine's, so this is
        genuinely the same decision the live deployment would make -- which
        matters, because someone developing an adapter against sample mode
        needs the block path to actually block. Only `fleet_agents` is
        missing, and it is context rather than permission."""
        return gate_decision(req, fleet_agents=0, sample=True)

    def fleet_coverage(self) -> CoverageOut:
        return sample.coverage()

    # -- supply chain --

    def run_code(self, run_id: str) -> CodeOut:
        self._require_curated(run_id)
        return sample.code(run_id)

    def run_sbom(self, run_id: str) -> SbomOut:
        self._require_curated(run_id)
        return sample.sbom(run_id)

    def cve_impact(self, cve: str) -> CveImpact:
        try:
            return sample.cve_impact(cve)
        except KeyError as exc:
            raise NotFound(f"no advisory {cve}") from exc

    # -- structure analysis --

    def run_hypergraph(self, run_id: str) -> HypergraphOut:
        return analysis.hypergraph_out(sample.run_hypergraph(run_id))

    def run_decomposition(self, run_id: str) -> DecompositionOut:
        return analysis.decomposition_out(sample.run_hypergraph(run_id))

    def run_scales(self, run_id: str) -> list[ScaleOut]:
        return analysis.scales_out(sample.run_hypergraph(run_id))

    def fleet_decomposition(self) -> DecompositionOut:
        return analysis.decomposition_out(sample.fleet_hypergraph())


def get_gateway() -> Gateway:
    """Selects the real engine gateway when MESHAGENT_ENGINE=1 (the merged repo
    ships the engine under services/engine), else the sample gateway. The engine
    import is deferred so the API still runs anywhere without it."""
    if os.environ.get("MESHAGENT_ENGINE") == "1":
        from .engine_gateway import EngineGateway  # deferred: engine on path
        return EngineGateway()
    return SampleGateway()
