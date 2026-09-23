"""Seed real HyperMesh-backed memory for the EngineGateway. Every write here
goes through the real MeshAgent write gate into the HyperMesh engine tables, so
the app runs on genuine governed memory, not sample dicts.

Two stores: `run_store` holds one agent's pickle.load build; `fleet_store` holds
several agents sharing the vulnerable numpy version (three also entangled through
the weakness). Both are reconstructed into hgviz hypergraphs from their real
member sets."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Callable, ContextManager

# The engine lives beside the api service in the merged repo.
_ENGINE = os.path.join(os.path.dirname(__file__), "..", "..", "engine")
if os.path.isdir(_ENGINE) and _ENGINE not in sys.path:
    sys.path.insert(0, os.path.abspath(_ENGINE))

import hypermeshdb  # noqa: E402
from hypermeshdb.agentmem import Kind, MemoryStore, Origin, Status  # noqa: E402
from meshagent.codegraph import CodeGraphRecorder  # noqa: E402

from . import paths  # noqa: E402
from .hgviz import Hypergraph  # noqa: E402

# The seeded run: the id the UI links to and the task it was given. The task is
# seed metadata, not a recorded memory -- this run is pre-seeded, so no user
# ever stated it here. Runs created through the API do record their own task.
RUN_ID = "7f3a"
RUN_TASK = "Build a data loader for the training pipeline that reads model shards."

_PREFIX_KIND = [
    ("source:", "source"), ("decision:", "decision"), ("class:", "class"),
    ("pkg:", "package"), ("sink:", "sink"), ("cwe:", "cwe"), ("cap:", "capability"),
    ("version:", "version"), ("license:", "license"), ("cve:", "cve"),
    ("entry:", "entry"), ("agent:", "agent"),
]


def entity_kind(vertex: str) -> str:
    """The NodeKind an entity name declares through its prefix."""
    for pfx, k in _PREFIX_KIND:
        if vertex.startswith(pfx):
            return k
    return "other"


# Re-exported: the route layer and the audit log need the same answer, and
# they must be able to ask it without importing the engine.
base_dir = paths.base_dir
durable = paths.durable


def new_store(name: str) -> MemoryStore:
    """The store called `name` under the base, opened fresh or reopened with
    its records intact -- connecting to an existing directory recovers it."""
    db_dir = os.path.join(base_dir(), name)
    os.makedirs(db_dir, exist_ok=True)
    db = hypermeshdb.connect(db_dir)
    return MemoryStore(db, db_dir)


def store_exists(name: str) -> bool:
    """Whether `name` already holds memory, so a caller can reopen it rather
    than seeding over the top of records someone may have acted on."""
    return os.path.isdir(os.path.join(base_dir(), name))


# The shard loader the seeded run built. The classes, the packages they import
# and the dangerous call sites below are all extracted from THIS text by the
# recorder's AST layer, so the graph is captured, not hand-listed. Three sinks
# are present; only pickle.load gets a taint path, so only it is exploitable.
RUN_SOURCE_CODE = '''
import numpy as np
import pickle
import hashlib
import subprocess


class UnsafeShardLoader:
    def __init__(self, path):
        self.path = path
        self.data = pickle.load(open(path, 'rb'))
        self.index = np.arange(len(self.data))
        self.fingerprint = hashlib.md5(open(path, 'rb').read()).hexdigest()

    def repack(self):
        subprocess.run(f"tar -czf {self.path}.tgz {self.path}", shell=True)
'''


def build_steps(store: MemoryStore) -> Iterator[None]:
    """The shard-loader build, paused after each recorder call so a caller can
    observe the memory that step wrote.

    Draining it seeds a store; advancing it one step at a time is exactly what
    the run stream does. One definition, so the live recording and the seeded
    run are the same build, and every write goes through the real write gate."""
    rec = CodeGraphRecorder(store)

    rec.record_source("poisoned-mirror",
                      "a docs mirror recommending pickle.load on shard files",
                      trusted=False)
    yield

    rec.record_decision("pickle-shards", "load data shards with pickle for speed",
                        from_sources=["poisoned-mirror"])
    yield

    # one class edge plus one finding edge per dangerous call site the AST finds
    rec.record_code(RUN_SOURCE_CODE, decision_id="pickle-shards")
    yield

    rec.record_version("numpy", "1.26.4", license="BSD-3-Clause")
    yield

    rec.record_cve("CVE-2021-41496", affects=[("numpy", "1.26.4")], severity="high",
                   summary="NULL pointer dereference in numpy", cwe="CWE-476", feed="osv")
    yield

    # No hand-placed taint here any more. record_code's own scan finds the
    # real path -- open() into pickle.load, in this very source -- and an
    # entry naming a file this build does not contain could not be checked
    # by anyone reading it.


def seed_run_store() -> MemoryStore:
    """One agent's pickle.load build, written through the real recorder.

    Reopened rather than rebuilt when it is already on disk. Seeding over the
    top would resurrect anything a user had forgotten from it, which would
    make `forget` a lie the next time the process restarted."""
    if store_exists("run"):
        return new_store("run")
    store = new_store("run")
    for _ in build_steps(store):
        pass
    return store


Guard = Callable[[], ContextManager[Any]]


class GuardedStore:
    """Makes every write to a store exclusive, without making the work
    between writes exclusive too.

    hypermeshdb's core is not thread-safe, so a write must not overlap another
    caller. A model turn, though, takes seconds: holding the engine lock
    across one would stall every other request in the process. Wrapping the
    store instead means the lock is held for the write and released for the
    thinking, and it covers everything written through it -- the recorder's
    edges and the agent loop's episode alike."""

    def __init__(self, store: MemoryStore, guard: Guard) -> None:
        self._store = store
        self._guard = guard

    def write(self, *args: Any, **kwargs: Any) -> Any:
        with self._guard():
            return self._store.write(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


@dataclass
class NewRun:
    """A run's fresh store and the recorder that opened it.

    The recorder is kept, not rebuilt, because it holds the mapping from the
    task's source id to the ULID it was written as. A build that records its
    decision `from_sources=[task_source]` is only linked to the task because
    the same recorder wrote both."""
    store: MemoryStore
    recorder: CodeGraphRecorder
    task_source: str

    @property
    def guarded(self) -> Any:
        """The store the recorder writes through: guarded, so anything else
        given this store inherits the same serialization."""
        return self.recorder.memory


def record_task(run_id: str, task: str, *, guard: Guard | None = None) -> NewRun:
    """Open a fresh store for a new run and record the task itself as the
    first governed memory: USER origin, USER_STATED status. Nothing else is
    written, because nothing else has happened yet."""
    store = new_store(f"run-{run_id}")
    rec = CodeGraphRecorder(
        store if guard is None else GuardedStore(store, guard))
    source_id = f"task-{run_id}"
    rec.record_source(source_id, task, origin="user", trusted=True)
    return NewRun(store=store, recorder=rec, task_source=source_id)


def new_build_log() -> Any:
    """A fresh BuildLog. Here so callers need not import the engine."""
    from meshagent.build_tools import BuildLog

    return BuildLog()


# How many advisories to record per package. Resolving the latest release
# usually finds none; an old pin can carry dozens, and a run's memory should
# not be flooded by one package's history. The caller reports any remainder.
MAX_ADVISORIES = 10


def model_build_steps(
    run: NewRun, task: str, *, llm: Any, log: Any,
) -> Iterator[None]:
    """The real build: the model works through the governed toolset, then the
    packages its code imported are resolved against real feeds.

    Same generator contract as `build_steps` -- paused after each group of
    writes -- so the run stream records a model build the same way it records
    the reference one. What the model writes goes through the same write gate;
    what it merely claims is not recorded at all.

    No taint path is written. There is no reachability analysis for arbitrary
    code here, so findings stay `present` and exploitability is left unproven
    rather than asserted."""
    from meshagent.build_tools import SYSTEM, build_registry
    from meshagent.loop import AgentLoop

    from . import advisories

    tools = build_registry(run.recorder, log, from_sources=[run.task_source])
    # the guarded store, so the loop's episode write is serialized like the
    # recorder's edges are
    loop = AgentLoop(llm=llm, tools=tools, memory=run.guarded, system=SYSTEM)

    # One step: the model's whole turn sequence. Every write it makes lands
    # inside this call, and the episode hyperedge closes it.
    result = loop.run(task, task_id=f"task:{run.task_source}")
    log.final_text = result.final_text
    if not log.code:
        # the model never produced parseable code; the run keeps its task, its
        # episode and anything it did record, and says so rather than inventing
        log.note(
            f"The model {result.outcome} after {result.turns} turn(s) without "
            "submitting code that parses, so this run has no code graph to "
            "govern. Nothing was recorded on its behalf.")
        yield
        return
    yield

    if not log.packages:
        log.note("The code imports only the standard library, so there is no "
                 "third-party bill of materials to resolve.")
        yield
        return

    # the build produces no lockfile, so say what the versions actually are
    log.note("Versions come from the latest release on PyPI at record time, "
             "not from a lockfile pin, and advisories are the ones OSV holds "
             "against that release.")
    yield

    for package in log.packages:
        found = advisories.resolve(package)
        if found.version is None:
            log.note(f"No version recorded for {package}: "
                     f"{found.unavailable or 'PyPI published none'}.")
            yield
            continue

        # recorded under the imported name, because that is the entity the
        # code graph already holds: it is what joins an advisory to the
        # classes that import it
        run.recorder.record_version(package, found.version,
                                    license=found.license or "unstated")
        if found.project and found.project != package:
            log.note(f"`{package}` is imported from the PyPI project "
                     f"{found.project}; its version and advisories are that "
                     "project's.")
        yield

        for adv in found.advisories[:MAX_ADVISORIES]:
            run.recorder.record_cve(
                adv.id, affects=[(package, found.version)],
                severity=adv.severity, summary=adv.summary,
                cwe=adv.cwe, feed="osv",
            )
            yield

        extra = len(found.advisories) - MAX_ADVISORIES
        if extra > 0:
            log.note(f"{extra} further advisory(ies) against "
                     f"{package}@{found.version} were not recorded.")
            yield
        if found.unavailable:
            log.note(f"{package}: {found.unavailable}.")
            yield


def model_name() -> str | None:
    """The model configured to build runs, or None when none is.

    Absence is the honest default: with no key the app records the reference
    build and says so, rather than pretending a model ran."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    return os.environ.get("MESHAGENT_MODEL", "gpt-4o-mini")


def model_llm(name: str) -> Any:
    """Bind the configured OpenAI-compatible model."""
    from meshagent.llm import OpenAILLM

    return OpenAILLM(
        model=name,
        api_key=os.environ.get("OPENAI_API_KEY"),
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
    )


_FLEET = ["MA", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]
_ENTANGLED = ["MA", "A2", "A3"]  # also share the weakness -> forbidden cluster

# The fleet agent that owns the taint-scanned build in the seeded run store.
# It is the only agent a reachability claim can honestly be made about.
PRIMARY_AGENT = "MA"


def seed_fleet_store() -> MemoryStore:
    """Several agents sharing the vulnerable numpy version, written as real
    n-ary memory edges.

    Reopened when already present, for the same reason the run store is: the
    fleet accumulates the runs that joined it, and re-seeding would write the
    synthetic agents a second time over real history."""
    if store_exists("fleet"):
        return new_store("fleet")
    store = new_store("fleet")
    shared = "version:numpy@1.26.4"
    for a in _FLEET:
        store.write(Kind.FACT, [f"agent:{a}", shared], origin=Origin.AGENT,
                    status=Status.VERIFIED, source="agent:sbom",
                    payload={"type": "uses", "agent": a})
    for a in _ENTANGLED:
        store.write(Kind.FACT, [f"agent:{a}", shared, "cwe:CWE-476"],
                    origin=Origin.EXTERNAL, status=Status.UNVERIFIED, source="feed:osv",
                    payload={"type": "exposure", "agent": a})
    return store


def build_hypergraph(store: MemoryStore) -> Hypergraph:
    """Reconstruct an hgviz Hypergraph from a store's real memory records: each
    memory edge is a hyperedge over its member entities (minus its minted id)."""
    from meshagent.codegraph import _all_entity_names  # local import: engine on path

    h = Hypergraph()
    seen: set[str] = set()
    for name in _all_entity_names(store):
        for ulid in store.find_by_subject(name):
            if ulid in seen:
                continue
            seen.add(ulid)
            rec = store.get(ulid)
            if rec is None:
                continue
            members = [x for x in rec.member_names if not x.startswith("edge:")]
            if len(members) >= 2:
                h.add_edge(ulid, members)
    h.vkind = {v: entity_kind(v) for v in h.vertices}
    return h


def live_records(store: MemoryStore) -> Iterator[Any]:
    """Every memory record in a store that has not been tombstoned."""
    from meshagent.codegraph import _all_entity_names  # engine on path

    seen: set[str] = set()
    for name in _all_entity_names(store):
        for ulid in store.find_by_subject(name):
            if ulid in seen:
                continue
            seen.add(ulid)
            rec = store.get(ulid)
            if rec is not None and not rec.tombstoned:
                yield rec


def fleet_agents(store: MemoryStore) -> list[tuple[str, bool]]:
    """(agent id, entangled?) pairs, read from the fleet store's real edges.

    Derived rather than listed: a run that joins the fleet has to show up here
    on the same terms as a seeded agent, or the fleet screens would go on
    describing only what was seeded. Entangled means the agent shares an edge
    that also names a weakness -- the exposure shape, not mere use."""
    agents: dict[str, bool] = {}
    for rec in live_records(store):
        named = [m[len("agent:"):] for m in rec.member_names
                 if m.startswith("agent:")]
        if not named:
            continue
        exposed = any(m.startswith("cwe:") for m in rec.member_names)
        for a in named:
            agents[a] = agents.get(a, False) or exposed
    # seeded agents first, in their seeded order, then anything that joined
    order = {a: i for i, a in enumerate(_FLEET)}
    return sorted(agents.items(), key=lambda kv: (order.get(kv[0], len(_FLEET)),
                                                  kv[0]))


def agent_versions(store: MemoryStore, agent: str) -> list[str]:
    """The package versions one fleet agent's memory actually names."""
    subject = f"agent:{agent}"
    found: set[str] = set()
    for rec in live_records(store):
        if subject not in rec.member_names:
            continue
        found |= {m for m in rec.member_names if m.startswith("version:")}
    return sorted(found)


def join_fleet(
    store: MemoryStore, agent: str, *,
    versions: list[str], exposures: list[tuple[str, str]],
) -> int:
    """Record a completed run as a fleet agent, and return the edges written.

    `versions` are the package versions its code really imports. `exposures`
    are (version, cwe) pairs and are written only where an advisory the run
    recorded names a weakness against a version it uses -- so sharing a
    package is never silently upgraded into sharing a vulnerability."""
    written = 0
    for version in versions:
        store.write(Kind.FACT, [f"agent:{agent}", version],
                    origin=Origin.AGENT, status=Status.VERIFIED,
                    source="agent:sbom",
                    payload={"type": "uses", "agent": agent})
        written += 1
    for version, cwe in exposures:
        store.write(Kind.FACT, [f"agent:{agent}", version, f"cwe:{cwe}"],
                    origin=Origin.EXTERNAL, status=Status.UNVERIFIED,
                    source="feed:osv",
                    payload={"type": "exposure", "agent": agent})
        written += 1
    return written
