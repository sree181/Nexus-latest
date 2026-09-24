"""Engine-mode tests: the EngineGateway on real HyperMesh memory. Skipped when
the engine (services/engine, plus its compiled core) is not usable, so the
suite still runs anywhere.

Importing app.engine_gateway puts services/engine on sys.path (see engine_seed),
so these assert that what the gateway reports is computed from genuine engine
records: the decomposition of the seeded stores, the `why` chain the engine
walks, the certificate its `forget` issues, and the taint edges that decide
which findings are exploitable."""

from __future__ import annotations

import threading
import time

import pytest

engine_gateway = pytest.importorskip("app.engine_gateway")

from app import engine_seed  # noqa: E402  (engine on sys.path only after the import above)
from app.gateway import Gateway, NotFound, Stale  # noqa: E402
from hypermeshdb.agentmem import Kind, Origin, Status  # noqa: E402

RUN = engine_seed.RUN_ID


def test_engine_gateway_implements_the_whole_interface():
    """The lockstep guard from the other side: every Gateway method has a real
    engine implementation, none is inherited from the abstract base."""
    cls = engine_gateway.EngineGateway
    assert cls.__abstractmethods__ == frozenset()
    inherited = {name for name in Gateway.__abstractmethods__
                 if getattr(cls, name) is getattr(Gateway, name)}
    assert inherited == set()


def test_the_mode_says_writes_are_kept(gw):
    """The other half of the flag the mode banner reads. Sample mode discards a
    batch and says so; this one has to be able to say the opposite, or the
    banner would be on permanently and mean nothing."""
    mode = gw.mode()
    assert (mode.engine, mode.persists) == (True, True)
    assert "HyperMesh" in mode.note


def _gateway():
    """A gateway on fresh stores. The compiled HyperMesh core is optional, so
    skip rather than fail when it is absent -- but only for absence. A
    broader catch here once turned a locked store into 23 quietly skipped
    tests, which is indistinguishable from a green suite."""
    try:
        return engine_gateway.EngineGateway()
    except (ImportError, OSError) as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"HyperMesh engine unavailable: {exc}")


@pytest.fixture(scope="module")
def gw():
    """Shared, read-only across the tests that do not mutate memory."""
    return _gateway()


@pytest.fixture
def fresh():
    """Own gateway for tests that forget, so they cannot pollute the rest."""
    return _gateway()


# -- structure (unchanged behavior, real records) ------------------------------

def test_run_block_is_the_exploitable_knot(gw):
    d = gw.run_decomposition(RUN)
    assert d.b1 >= 1                       # native import/call relations add cycles
    assert len(d.blocks) >= 1
    # the finding edge and the taint edge the scan derived from the same
    # source, both spanning pickle.load and CWE-502
    assert any(block.eta == 0.25 for block in d.blocks)
    assert d.branches >= 1                 # provenance / supply trails are trees


def test_fleet_has_forbidden_cluster(gw):
    d = gw.fleet_decomposition()
    assert d.blocks and d.blocks[0].eta > 0.4
    assert any(f.kind == "2-adjacent-bundle-3edges" for f in d.forbidden)
    shared = d.forbidden[0].shared
    assert "version:numpy@1.26.4" in shared and "cwe:CWE-476" in shared


def test_run_graph_comes_from_engine_export(gw):
    g = gw.run_graph(RUN)
    assert g.nodes and g.edges
    assert any(n.exploitable for n in g.nodes)   # taint upgraded the sink


def test_relations_are_the_real_hyperedges_behind_the_drawn_lines(gw):
    """export_graph flattens each record into pairwise lines. The relations
    are those same records read straight, so a finding is one 4-ary fact and
    the class is one edge over every package it imports."""
    g = gw.run_graph(RUN)
    assert len(g.relations) < len(g.edges)       # the flattening really inflates

    findings = [r for r in g.relations if r.kind == "finding"]
    assert len(findings) == 3
    for f in findings:
        assert len(f.members) == 4               # class, sink, capability, cwe
        assert "class:UnsafeShardLoader" in f.members

    cls = next(r for r in g.relations if r.kind == "class")
    assert {"pkg:pickle", "pkg:numpy"} <= set(cls.members)


def test_no_relation_dangles_and_every_id_is_the_engines_own(gw):
    g = gw.run_graph(RUN)
    drawn = {n.id for n in g.nodes}
    assert g.relations
    for r in g.relations:
        assert drawn.issuperset(r.members), f"{r.id} dangles"
        assert gw._run(RUN).store.get(r.id) is not None, "not a real ULID"


def test_a_forget_takes_the_whole_relation_with_it(fresh):
    """The point of carrying the hyperedge: the members go together, because
    the fact goes together."""
    before = fresh.run_graph(RUN)
    finding = next(r for r in before.relations if r.kind == "finding")
    assert len(finding.members) == 4

    fresh.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror")

    after = fresh.run_graph(RUN)
    assert finding.id not in {r.id for r in after.relations}


def test_scales_reduce_b1_monotonically(gw):
    b1s = [s.b1 for s in gw.run_scales(RUN)]
    assert b1s == sorted(b1s, reverse=True)


def test_fleet_query_finds_agents(gw):
    q = gw.fleet_query("numpy")
    assert len(q.hits) == 8
    assert sum(1 for h in q.hits if h.status == "exploitable") == 1


# -- runs ----------------------------------------------------------------------

def test_runs_lists_the_seeded_run(gw):
    runs = gw.runs()
    seeded = next(r for r in runs if r.id == RUN)
    assert seeded.status == "complete"
    assert seeded.sample is False          # engine mode never claims sample data
    assert seeded.memory_count > 0
    assert seeded.findings == 3            # pickle.load, hashlib.md5, subprocess.run


def test_the_seeded_run_is_published_to_every_developer(fresh):
    """It is this deployment's reference build, not one developer's work, so it
    is in everybody's list -- and a run somebody actually owns is not."""
    assert fresh.run(RUN).seeded is True
    mine = fresh.create_run("Mine", owner="maya@example.com")
    assert mine.seeded is False

    theirs = fresh.runs(owner="raj@example.com")
    assert RUN in {r.id for r in theirs}
    assert mine.id not in {r.id for r in theirs}


def test_create_run_records_the_task(fresh):
    run = fresh.create_run("Add a caching layer to the loader")
    assert run.status == "recording"
    assert run.memory_count == 1           # the task, and nothing it has not done
    assert run.findings == 0
    assert any(r.id == run.id for r in fresh.runs())
    # the task is real memory: why() can reach it
    why = fresh.run_why(run.id, f"source:task-{run.id}")
    assert why.chain[0].origin == "USER"
    assert why.chain[0].status == "USER_STATED"


def test_unknown_run_is_not_found(gw):
    with pytest.raises(NotFound):
        gw.run_findings("nope")


# -- the live run stream -------------------------------------------------------

def _memories(frames):
    return [f.memory for f in frames if f.type == "memory"]


def test_stream_replays_the_real_records_of_a_complete_run(gw):
    frames = list(gw.run_stream(RUN))
    events = _memories(frames)
    assert len(events) == gw.runs()[0].memory_count
    assert all(e.ulid for e in events)             # every frame is a real edge
    # the module lands before the classes parsed out of it, so the code the
    # rest of the screens talk about is itself a record in the stream
    assert [e.step for e in events][:3] == [
        "Read a source", "Made a decision", "Submitted a module"]
    assert "Mapped module imports" in [e.step for e in events]
    assert "Wrote code" in [e.step for e in events]
    # the envelopes are the ones the write gate issued, not decoration
    source = events[0]
    assert (source.origin, source.status) == ("EXTERNAL", "UNVERIFIED")
    assert "caps it at unverified" in source.gate
    assert source.members == ["source:poisoned-mirror"]
    # the scan derives reachability from the code, so it lands with the code
    # rather than being appended at the end by a separate tool
    taint = next(e for e in events if e.step == "Scanned for reachability")
    assert "open() at line 11 reaches pickle.load" in taint.detail
    assert frames[-1].type == "done"
    assert frames[-1].run.status == "complete"


def test_streaming_a_recording_run_performs_the_writes(fresh):
    run = fresh.create_run("Add a caching layer to the loader")
    assert fresh.run_findings(run.id).present == 0   # nothing recorded yet

    frames = list(fresh.run_stream(run.id))
    events = _memories(frames)

    # the task the user stated comes first, then the build's own writes
    assert events[0].step == "Stated the task"
    assert (events[0].origin, events[0].status) == ("USER", "USER_STATED")
    assert any(f.type == "notice" for f in frames)

    # the memory now exists in the engine, and the other endpoints can see it
    done = frames[-1]
    assert done.type == "done" and done.run.status == "complete"
    assert done.run.memory_count == len(events)
    assert fresh.run_findings(run.id).present == 3
    assert fresh.run_findings(run.id).exploitable == 1
    assert [e.entity for e in fresh.run_why(run.id, "class:UnsafeShardLoader").chain] == [
        "class:UnsafeShardLoader", "decision:pickle-shards", "source:poisoned-mirror"]


def test_streaming_twice_does_not_record_twice(fresh):
    run = fresh.create_run("Add a caching layer to the loader")
    first = _memories(list(fresh.run_stream(run.id)))
    second = _memories(list(fresh.run_stream(run.id)))
    assert [e.ulid for e in first] == [e.ulid for e in second]
    assert fresh.run_findings(run.id).present == 3


def test_an_abandoned_recording_is_marked_failed_not_complete(fresh):
    run = fresh.create_run("Add a caching layer to the loader")
    events = fresh.run_stream(run.id)
    next(events)                                   # the task frame
    next(events)                                   # the notice
    next(events)                                   # the first real write
    events.close()                                 # the client went away
    summary = next(r for r in fresh.runs() if r.id == run.id)
    assert summary.status == "failed"              # the recording did not finish
    assert summary.memory_count >= 2               # what it did write is kept


def test_streaming_an_unknown_run_is_not_found(gw):
    with pytest.raises(NotFound):
        next(gw.run_stream("nope"))


# -- provenance: why / forget --------------------------------------------------

def test_why_walks_the_real_derivation_to_the_poisoned_source(gw):
    why = gw.run_why(RUN, "class:UnsafeShardLoader")
    entities = [e.entity for e in why.chain]
    assert entities[0] == "class:UnsafeShardLoader"
    assert "decision:pickle-shards" in entities
    assert "source:poisoned-mirror" in entities
    source = next(e for e in why.chain if e.entity == "source:poisoned-mirror")
    assert source.origin == "EXTERNAL" and source.status == "UNVERIFIED"
    assert why.chain[1].via == "DERIVED_FROM"


def test_why_on_an_unknown_node_is_not_found(gw):
    with pytest.raises(NotFound):
        gw.run_why(RUN, "class:NoSuchThing")


def test_the_engines_certificate_carries_the_caller_not_a_placeholder(fresh):
    """The engine writes the actor into its own audit chain, so the name has
    to reach it rather than being stamped on the response afterwards."""
    cert = fresh.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror",
                            actor="priya@example.com")
    assert cert.actor == "priya@example.com"


def test_a_forget_with_no_caller_is_marked_unattributed(fresh):
    """Every route passes an identity, so this should be unreachable -- and
    if it ever happens the certificate must say so rather than name someone."""
    cert = fresh.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror")
    assert cert.actor == "unattributed"


def test_forget_certificate_covers_the_closure(fresh):
    cert = fresh.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror")
    assert cert.purged_count >= 3          # source, decision, class and its findings
    assert cert.classes_pruned == ["class:UnsafeShardLoader"]
    assert len(cert.retained_hash) == 64   # the payloads are gone, the hashes are not
    assert all(p.content_sha_retained for p in cert.purged)
    assert cert.issued_at > 0


def test_preview_names_the_closure_without_destroying_it(fresh):
    """The whole point: asking what a forget would cost is not a way of
    finding out the hard way."""
    before = fresh.runs()[0].memory_count
    pv = fresh.forget_preview(RUN, "source:poisoned-mirror")

    assert pv.purged_count >= 3
    assert pv.classes_pruned == ["class:UnsafeShardLoader"]
    # every doomed edge is described, so the operator approves statements
    # rather than a number
    assert all(d.statement for d in pv.doomed)
    assert len(pv.doomed) == pv.purged_count
    assert any("derived from this one" in w for w in pv.warnings)

    # nothing moved
    assert fresh.runs()[0].memory_count == before
    assert fresh.fleet_overview().deletion_certificates == 0
    assert fresh.forget_preview(RUN, "source:poisoned-mirror").version == pv.version


def test_the_preview_matches_what_the_forget_actually_purges(fresh):
    """If these two disagreed the preview would be worse than nothing: it
    would be a confident answer to the wrong question."""
    pv = fresh.forget_preview(RUN, "source:poisoned-mirror")
    cert = fresh.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror",
                            expected_version=pv.version)
    assert cert.purged_count == pv.purged_count
    assert cert.classes_pruned == pv.classes_pruned
    assert {p.ulid for p in cert.purged} == {d.ulid for d in pv.doomed}


def test_a_forget_against_a_stale_preview_is_refused_and_destroys_nothing(fresh):
    pv = fresh.forget_preview(RUN, "source:poisoned-mirror")

    # memory moves underneath: something else records into the same run
    # while the operator is still reading the preview
    fresh._run(RUN).store.write(
        Kind.FACT, ["note:recorded-while-you-were-reading"],
        origin=Origin.AGENT, status=Status.VERIFIED, source="agent:test")
    assert fresh._version(fresh._run(RUN).store) != pv.version

    before = fresh.runs()[0].memory_count
    with pytest.raises(Stale):
        fresh.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror",
                         expected_version=pv.version)
    assert fresh.runs()[0].memory_count == before
    assert fresh.fleet_overview().deletion_certificates == 0

    # and the refusal is recoverable: look again, then it goes through
    fresh.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror",
                     expected_version=fresh.forget_preview(
                         RUN, "source:poisoned-mirror").version)
    assert fresh.runs()[0].memory_count < before


def test_a_replayed_forget_returns_the_first_certificate(fresh):
    """A retried POST must not read as "nothing was there". The closure is
    gone after the first call, so a second real forget would report zero
    purged edges and quietly describe a different universe."""
    once = fresh.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror",
                            idempotency_key="k-1")
    twice = fresh.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror",
                             idempotency_key="k-1")
    assert twice == once
    assert twice.purged_count == once.purged_count > 0
    assert fresh.fleet_overview().deletion_certificates == 1


def test_forget_shrinks_live_memory_and_counts_a_certificate(fresh):
    before = fresh.runs()[0].memory_count
    assert fresh.fleet_overview().deletion_certificates == 0
    fresh.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror")
    assert fresh.runs()[0].memory_count < before
    assert fresh.fleet_overview().deletion_certificates == 1


# -- security: present vs exploitable -----------------------------------------

def test_findings_assert_exploitable_only_where_taint_reaches(gw):
    out = gw.run_findings(RUN)
    assert out.present == 3
    assert out.exploitable == 1
    exploitable = [f for f in out.findings if f.exploitable]
    assert [f.sink for f in exploitable] == ["pickle.load"]
    # the other two call sites are real, and no reachability is claimed for them
    assert {f.sink for f in out.findings if not f.exploitable} == {
        "hashlib.md5", "subprocess.run"}
    assert all(f.severity is None for f in out.findings if not f.exploitable)


def test_exploitable_finding_carries_the_proven_path(gw):
    f = gw.run_finding(RUN, "pickle.load")
    assert f.rule == "py/external-data-reaches-sink"
    assert f.cwe == "CWE-502"
    # the entry names a line of this run's own source, so a reader can go and
    # check it, which "api/serve.py:19" -- a file the build never had -- could
    # never let them do
    assert f.entry == "open() at line 11"
    assert f.path[0] == "open() at line 11" and f.path[-1] == "pickle.load"
    # the scan proves the value arrives; it does not get to say how bad it is
    assert f.severity is None


def test_unknown_sink_is_not_found(gw):
    with pytest.raises(NotFound):
        gw.run_finding(RUN, "os.system")


# -- supply chain --------------------------------------------------------------

def test_sbom_comes_from_the_recorded_versions(gw):
    out = gw.run_sbom(RUN)
    numpy = next(e for e in out.entries if e.package == "numpy")
    assert numpy.version == "1.26.4"
    assert numpy.license == "BSD-3-Clause"
    assert numpy.cves == ["CVE-2021-41496"]
    assert numpy.feed == "osv"             # the honesty label the recorder wrote


def test_cve_impact_walks_the_real_blast_radius(gw):
    impact = gw.cve_impact("CVE-2021-41496")
    assert impact.versions == ["version:numpy@1.26.4"]
    assert impact.packages == ["pkg:numpy"]
    assert impact.classes == ["class:UnsafeShardLoader"]
    assert impact.decisions == ["decision:pickle-shards"]
    assert len(impact.agents) == 8         # the fleet really shares that version
    assert impact.severity == "high"


def test_unknown_cve_is_not_found(gw):
    with pytest.raises(NotFound):
        gw.cve_impact("CVE-0000-0000")


# -- recommendations derived from real memory ----------------------------------

def test_recommendations_derive_from_the_real_decomposition(gw):
    recs = gw.recommendations()
    assert recs
    advisory = next(r for r in recs if r.id == "rec-cve-CVE-2021-41496")
    assert advisory.agents == 8
    assert advisory.blast_radius["classes"] == 1
    assert advisory.blast_radius["exploitable"] == 1

    forbidden = gw.fleet_decomposition().forbidden
    assert sum(1 for r in recs if r.id.startswith("rec-forbidden-")) == len(forbidden)

    cut = next(r for r in recs if r.id.startswith("rec-cut-"))
    assert cut.kind == "CRITICAL"
    assert cut.blast_radius["classes"] == 1
    assert cut.blast_radius["decisions"] == 1


def test_applying_a_cut_really_forgets(fresh):
    cut = next(r for r in fresh.recommendations() if r.id.startswith("rec-cut-"))
    receipt = fresh.apply_recommendation(cut.id, actor="priya@example.com")
    assert receipt.changed_memory is True
    assert receipt.certificates and receipt.certificates[0].purged_count >= 3
    assert receipt.certificates[0].node == "source:poisoned-mirror"
    assert receipt.certificates[0].actor == "priya@example.com"
    assert fresh.fleet_overview().deletion_certificates == 1

    # The class, its finding edges and the reachability the scan derived from
    # that same code all rest on the source, so the closure takes the lot.
    # Reachability of code that no longer exists would be a claim about
    # nothing -- this is what makes the cut worth applying.
    after = fresh.run_findings(RUN)
    assert after.findings == []
    assert after.present == 0 and after.exploitable == 0


def test_applying_an_advisory_records_the_decision_without_claiming_the_patch(fresh):
    rec = next(r for r in fresh.recommendations() if r.id.startswith("rec-cve-"))
    before = len(fresh.fleet_decomposition().blocks)
    receipt = fresh.apply_recommendation(rec.id)
    assert receipt.memories_written == 1
    assert not receipt.certificates
    assert "repositories" in receipt.note      # the patch itself is not claimed
    assert receipt.agents == rec.agents
    # the decision is now real memory over those agents
    assert len(fresh.fleet_decomposition().blocks) >= before


def test_applying_an_unknown_recommendation_is_not_found(gw):
    with pytest.raises(NotFound):
        gw.apply_recommendation("rec-nope")


# -- concurrency ---------------------------------------------------------------

def test_concurrent_readers_do_not_corrupt_each_other(gw):
    """HyperMesh's C core shares one row buffer, so two threads reading at once
    used to blow up decoding a record. FastAPI serves sync endpoints from a
    threadpool, so this is the ordinary case: two people with the app open."""
    errors: list[BaseException] = []
    counts: list[int] = []

    def read() -> None:
        try:
            for _ in range(12):
                counts.append(gw.run_findings(RUN).present)
                counts.append(len(gw.run_graph(RUN).nodes))
                gw.run_why(RUN, "class:UnsafeShardLoader")
                gw.fleet_overview()
        except BaseException as exc:      # noqa: BLE001 - the test is the assert
            errors.append(exc)

    threads = [threading.Thread(target=read) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent engine reads failed: {errors[0]!r}"
    assert len(set(counts)) == 2       # every reader saw the same two numbers


def test_a_created_run_admits_its_memory_is_not_its_task(fresh):
    """There is no model here, so a new run records the reference build no
    matter what was asked for. That has to be visible on the run itself, not
    only in a stream frame that a reload throws away."""
    run = fresh.create_run("Build a computer vision pipeline with tensorflow")
    assert run.reference_build is True
    assert fresh.run_stream(run.id) is not None

    # and it stays true once the memory is recorded and the run is complete
    list(fresh.run_stream(run.id))
    after = next(r for r in fresh.runs() if r.id == run.id)
    assert after.status == "complete"
    assert after.reference_build is True

    # the seeded run's task is the build it actually holds, so it claims nothing
    assert next(r for r in fresh.runs() if r.id == RUN).reference_build is False


def test_two_viewers_of_a_recording_run_do_not_both_record(fresh, monkeypatch):
    """The second stream follows what the first is writing. It must not start a
    competing recording into the same memory, and it must not report the run
    finished while the first is still writing it.

    Here the first viewer is deliberately abandoned after claiming the
    recording, which is the one case the follower cannot wait out: it gives up
    on the idle bound and says so instead of hanging."""
    monkeypatch.setattr(engine_gateway, "_FOLLOW_IDLE", 0.8)
    run = fresh.create_run("Add a caching layer")
    first = fresh.run_stream(run.id)
    next(first)                         # claims the recording

    second = list(fresh.run_stream(run.id))
    assert second[-1].type == "done"
    # it followed rather than concluding, and admits the run is unfinished
    assert second[-1].run is not None and second[-1].run.status == "recording"
    notices = " ".join(f.detail or "" for f in second if f.type == "notice")
    assert "already being recorded" in notices
    assert "may have stopped" in notices

    # the abandoned recording still finishes when it is drained
    rest = list(first)
    assert rest[-1].run is not None and rest[-1].run.status == "complete"


# -- the fleet query actually reads the query ---------------------------------

def test_a_query_naming_a_pinned_version_returns_only_agents_using_it(gw):
    """The box said numpy@1.26.4 and every agent came back, including ones on
    another numpy entirely. A result list under a search box is read as an
    answer to it, so it has to be one."""
    out = gw.fleet_query("agents importing numpy@1.26.4")

    assert out.hits, "the seeded fleet does use numpy@1.26.4"
    for hit in out.hits:
        used = engine_seed.agent_versions(gw._fleet, hit.agent)
        assert "version:numpy@1.26.4" in used
    assert "numpy@1.26.4" in (out.interpreted or "")


def test_a_query_naming_nothing_in_memory_says_it_did_not_filter(gw):
    out = gw.fleet_query("agents importing zzzznotarealpackage")

    assert "names a package in fleet memory" in (out.interpreted or "")
    # everything is shown, and the note above is what stops that being a lie
    assert len(out.hits) == len(engine_seed.fleet_agents(gw._fleet))


def test_two_different_queries_do_not_give_the_same_answer(gw):
    """The regression in one line: the query string was echoed back and never
    read, so 'numpy@1.26.4', 'zzzznotreal' and 'hello' drew the same picture.

    Every seeded agent really is on numpy@1.26.4, so here the rosters
    coincide honestly -- what must differ is the graph, which should hold
    only the version that was asked about."""
    pinned = gw.fleet_query("numpy@1.26.4")
    nonsense = gw.fleet_query("hello")

    def versions(result):
        return {n.id for n in result.graph.nodes if n.kind == "version"}

    assert versions(pinned) == {"version:numpy@1.26.4"}
    assert versions(nonsense) >= versions(pinned)
    assert pinned.interpreted != nonsense.interpreted


# -- rewind: the 4th verb, and the one forget has to win against ---------------

def test_rewind_before_a_memory_was_written_does_not_hold_it(fresh):
    """The floor of the timeline. Ask about an instant before the run existed
    and memory is empty -- not because the query failed, but because there was
    nothing yet."""
    store = fresh._run(RUN).store
    written = store.write(
        Kind.FACT, ["note:written-late"], origin=Origin.AGENT,
        status=Status.VERIFIED, source="agent:test")
    at = fresh._run(RUN).store.get(written).event_ts

    before = fresh.run_rewind(RUN, at - 1)
    after = fresh.run_rewind(RUN, at + 1)

    assert written not in {m.ulid for m in before.memories}
    assert written in {m.ulid for m in after.memories}


def test_a_memory_forgotten_since_comes_back_proven_but_unreadable(fresh):
    """The contract of the whole verb.

    Rewind past a deletion and the memory is listed: the edge and its hash
    survived the tombstone, so the store can honestly say something was there.
    Its statement is gone, because the payload was destroyed. If this test
    ever flips to returning the text, every deletion certificate MeshAgent has
    issued has been quietly repealed.

    The two instants are written rather than borrowed from the seed, which
    writes its whole run in one second and so has no interval to sit in."""
    store = fresh._run(RUN).store
    an_hour_ago = int(time.time()) - 3600
    entity = "module:doomed_module"
    ulid = store.write(
        Kind.FACT, [entity], origin=Origin.AGENT, status=Status.VERIFIED,
        source="agent:test", payload={"detail": "a sentence nobody may read"},
        event_ts=an_hour_ago)

    midway = an_hour_ago + 1800
    assert ulid in {m.ulid for m in fresh.run_rewind(RUN, midway).memories}

    preview = fresh.forget_preview(RUN, entity)
    destroyed = {d.ulid for d in preview.doomed}
    fresh.run_forget(RUN, entity, "right to be forgotten",
                     actor="dpo@acme.test", expected_version=preview.version)
    assert ulid in destroyed

    was = fresh.run_rewind(RUN, midway)
    listed = {m.ulid: m for m in was.memories}

    assert destroyed <= set(listed), \
        "rewind lost memories that were demonstrably there at that instant"
    for gone in destroyed:
        assert listed[gone].redacted, "a forgotten memory came back unmarked"
        assert listed[gone].statement is None, \
            "a forgotten memory came back readable"
    assert "a sentence nobody may read" not in str(was.model_dump())
    assert was.redacted == len(destroyed)


def test_rewinding_across_a_forget_shows_what_the_present_has_lost(fresh):
    """Why anyone would use this: the count at an instant before the deletion
    is larger than the count now, and the difference is exactly the closure
    the certificate said it destroyed."""
    before_ts = int(time.time())
    before = fresh.run_rewind(RUN, before_ts)

    target = next(m for m in before.memories if m.statement)
    preview = fresh.forget_preview(RUN, target.entity)
    cert = fresh.run_forget(RUN, target.entity, "gdpr erasure",
                            actor="dpo@acme.test",
                            expected_version=preview.version)

    after = fresh.run_rewind(RUN, int(time.time()))

    assert cert.purged, "the forget destroyed something"
    assert after.now < before.held, \
        "memory did not shrink, so rewind has nothing to show"
    assert after.now == before.now - len(cert.purged)


def test_a_deletion_puts_a_stop_on_the_timeline(fresh):
    """Milestones exist so the UI can offer moments that meant something. A
    forget is the only event that makes memory smaller, so it is the one
    stop a reader most needs to be able to land on."""
    target = next(m for m in fresh.run_rewind(RUN, int(time.time())).memories
                  if m.statement)
    preview = fresh.forget_preview(RUN, target.entity)
    cert = fresh.run_forget(RUN, target.entity, "gdpr erasure",
                            actor="dpo@acme.test",
                            expected_version=preview.version)

    assert cert.issued_at in fresh.run_rewind(RUN, int(time.time())).milestones


def test_rewind_writes_nothing(fresh):
    """It is a read. The version digest is the sharpest way to say so."""
    store = fresh._run(RUN).store
    before = fresh._version(store)

    fresh.run_rewind(RUN, int(time.time()))
    fresh.run_rewind(RUN, 0)

    assert fresh._version(store) == before


def test_rewinding_an_unknown_run_is_not_found(gw):
    with pytest.raises(NotFound):
        gw.run_rewind("nosuchrun", int(time.time()))
