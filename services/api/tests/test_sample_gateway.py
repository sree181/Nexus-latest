"""Sample-mode tests: the whole API surface must work with no engine present,
and it must tell the same story engine mode computes. These run everywhere, so
they are the guard that sample mode never breaks when engine behavior is added.

Also checks the honesty rules: sample responses say so, and reachability is
asserted only for the call site that has a taint path."""

from __future__ import annotations

import pytest

from app import sample
from app.gateway import Gateway, NotFound, SampleGateway, Stale
from app.models import RecorderBatch

RUN = sample.RUN_ID


@pytest.fixture
def gw():
    return SampleGateway()


def test_sample_gateway_implements_the_whole_interface():
    """The two gateways stay in lockstep: anything added to the Gateway
    interface has to be implemented here too, or sample mode breaks."""
    assert Gateway.__abstractmethods__            # the interface has methods
    assert SampleGateway.__abstractmethods__ == frozenset()
    inherited = {name for name in Gateway.__abstractmethods__
                 if getattr(SampleGateway, name) is getattr(Gateway, name)}
    assert inherited == set()


def test_the_mode_admits_that_a_recorded_batch_is_thrown_away(gw):
    """The one thing this gateway must never let a caller believe. It answers
    /api/recorder with a success receipt for a batch it discarded, so the
    saying-so has to happen here, in words a screen can show."""
    mode = gw.mode()
    assert mode.engine is False
    assert mode.persists is False
    assert "discarded" in mode.note
    assert "/api/recorder" in mode.note


# -- runs ----------------------------------------------------------------------

def test_runs_lists_the_curated_run_and_labels_it_sample(gw):
    runs = gw.runs()
    assert [r.id for r in runs] == [RUN]
    assert runs[0].status == "complete"
    assert runs[0].sample is True
    assert runs[0].findings == 3


def test_the_curated_run_is_seeded_and_so_is_every_developers_to_read(gw):
    """It belongs to nobody and is published as the deployment's reference
    build. A developer whose own list is empty otherwise has no run at all,
    and every screen here reads as broken rather than as new."""
    assert gw.run(RUN).seeded is True
    listed = gw.runs(owner="maya@example.com")
    assert [r.id for r in listed] == [RUN]


def test_a_developers_own_run_is_not_published_to_anybody_else(gw):
    """The flag is set where a run is seeded, never derived from a null owner:
    a created run has no seed behind it and must stay theirs."""
    mine = gw.create_run("Mine", owner="maya@example.com")
    assert mine.seeded is False
    assert mine.id not in {r.id for r in gw.runs(owner="raj@example.com")}


def test_create_run_appends_a_recording_run(gw):
    run = gw.create_run("Add a caching layer to the loader")
    assert run.status == "recording"
    assert run.sample is True
    assert [r.id for r in gw.runs()] == [RUN, run.id]


def test_a_created_run_has_nothing_recorded_yet(gw):
    run = gw.create_run("Add a caching layer to the loader")
    with pytest.raises(NotFound):
        gw.run_findings(run.id)


# -- the run stream ------------------------------------------------------------

def test_stream_replays_the_curated_memory_events(gw):
    frames = list(gw.run_stream(RUN))
    events = [f.memory for f in frames if f.type == "memory"]
    assert len(events) == gw.runs()[0].memory_count
    assert [e.step for e in events][:3] == [
        "Read a source", "Made a decision", "Wrote code"]
    assert events[0].members == ["source:poisoned-mirror"]
    assert "caps it at unverified" in events[0].gate
    assert frames[-1].type == "done"
    assert frames[-1].run.status == "complete"


def test_every_event_carries_a_write_gate_line(gw):
    for frame in gw.run_stream(RUN):
        if frame.type == "memory":
            assert frame.memory.gate
            assert frame.memory.members


def test_streaming_a_created_run_says_it_is_a_replay(gw):
    run = gw.create_run("Add a caching layer to the loader")
    frames = list(gw.run_stream(run.id))
    notices = [f.detail for f in frames if f.type == "notice"]
    assert notices and "no engine to record into" in notices[0]
    assert frames[-1].run.status == "complete"


def test_streaming_an_unknown_run_is_not_found(gw):
    with pytest.raises(NotFound):
        next(gw.run_stream("nope"))


# -- provenance ----------------------------------------------------------------

def test_why_reaches_the_poisoned_source(gw):
    why = gw.run_why(RUN, "class:UnsafeShardLoader")
    assert [e.entity for e in why.chain] == [
        "class:UnsafeShardLoader", "decision:pickle-shards", "source:poisoned-mirror"]
    assert why.chain[-1].origin == "EXTERNAL"
    assert why.chain[-1].status == "UNVERIFIED"
    assert why.chain[1].via == "DERIVED_FROM"
    assert why.sample is True


def test_why_on_an_unknown_node_is_not_found(gw):
    with pytest.raises(NotFound):
        gw.run_why(RUN, "class:NoSuchThing")


def test_forget_certificate_covers_the_closure(gw):
    cert = gw.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror")
    # source -> decision -> class -> its three findings
    assert cert.purged_count == 6
    assert cert.classes_pruned == ["class:UnsafeShardLoader"]
    assert len(cert.retained_hash) == 64
    assert cert.sample is True


def test_the_preview_names_the_same_closure_the_forget_purges(gw):
    pv = gw.forget_preview(RUN, "source:poisoned-mirror")
    assert pv.purged_count == 6                 # same closure as the forget
    assert pv.classes_pruned == ["class:UnsafeShardLoader"]
    assert all(d.statement for d in pv.doomed)
    assert pv.sample is True
    assert gw.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror",
                         expected_version=pv.version).purged_count == 6


def test_previewing_an_unknown_node_is_not_found(gw):
    with pytest.raises(NotFound):
        gw.forget_preview(RUN, "class:NoSuchThing")


def test_a_forget_quoting_a_version_curated_memory_never_had_is_refused(gw):
    """Curated memory never moves, so this can only be a caller quoting a
    version from somewhere else -- which is exactly what the check is for."""
    with pytest.raises(Stale):
        gw.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror",
                      expected_version="from-another-world")


def test_a_replayed_forget_returns_the_first_certificate(gw):
    once = gw.run_forget(RUN, "source:poisoned-mirror", "x", idempotency_key="k")
    assert gw.run_forget(RUN, "source:poisoned-mirror", "x",
                         idempotency_key="k") == once


def test_forget_counts_toward_the_certificates_tile(gw):
    before = gw.fleet_overview().deletion_certificates
    gw.run_forget(RUN, "source:poisoned-mirror", "poisoned mirror")
    assert gw.fleet_overview().deletion_certificates == before + 1


# -- security ------------------------------------------------------------------

def test_findings_assert_exploitable_only_where_taint_reaches(gw):
    out = gw.run_findings(RUN)
    assert (out.present, out.exploitable) == (3, 1)
    assert [f.sink for f in out.findings if f.exploitable] == ["pickle.load"]
    assert all(f.severity is None for f in out.findings if not f.exploitable)
    assert all(not f.path for f in out.findings if not f.exploitable)


def test_exploitable_finding_carries_the_proven_path(gw):
    f = gw.run_finding(RUN, "pickle.load")
    assert f.rule == "py/external-data-reaches-sink"
    # the curated path names a line of the curated source, so the sample is
    # checkable against the code its own Code screen shows
    assert f.path[0] == "open() at line 16" and f.path[-1] == "pickle.load"


def test_unknown_sink_is_not_found(gw):
    with pytest.raises(NotFound):
        gw.run_finding(RUN, "os.system")


def test_an_uploaded_scan_is_read_and_reported_as_having_changed_nothing(gw):
    """Uploading a scan is the one place a developer hands this product
    evidence. Curated memory is fixed, so the receipt has to say the evidence
    went nowhere rather than letting the upload read as accepted."""
    doc = {"runs": [{"tool": {"driver": {"name": "Semgrep"}},
                     "results": [{
                         "codeFlows": [{}],
                         "locations": [{"physicalLocation": {
                             "artifactLocation": {"uri": "src/shards.py"}}}],
                     }]}]}
    got = gw.ingest_scan(RUN, doc)
    assert got.sample is True
    # and it genuinely read the document rather than shrugging at it
    assert got.tool == "semgrep"
    assert got.modules == ["shards"]
    assert (got.results, got.reachable) == (1, 1)
    # nothing landed: the findings are exactly as they were
    assert gw.run_findings(RUN).scans == []


# -- supply chain --------------------------------------------------------------

def test_sbom_labels_the_advisory_feed_as_sample(gw):
    out = gw.run_sbom(RUN)
    assert [e.package for e in out.entries] == ["numpy"]
    assert out.entries[0].feed == "sample"
    assert out.entries[0].cves == ["CVE-2021-41496"]


def test_cve_impact_has_every_tier(gw):
    impact = gw.cve_impact("CVE-2021-41496")
    assert impact.packages == ["pkg:numpy"]
    assert impact.classes == ["class:UnsafeShardLoader"]
    assert impact.decisions == ["decision:pickle-shards"]
    assert len(impact.agents) == 8
    assert impact.sample is True


def test_unknown_cve_is_not_found(gw):
    with pytest.raises(NotFound):
        gw.cve_impact("CVE-0000-0000")


def test_a_created_run_admits_its_memory_is_not_its_task(gw):
    run = gw.create_run("Build a computer vision pipeline with tensorflow")
    assert run.reference_build is True
    # the curated run's task matches the memory it holds, so it claims nothing
    assert next(r for r in gw.runs() if r.id == sample.RUN_ID).reference_build is False


# -- applying a recommendation -------------------------------------------------

def test_every_recommendation_names_the_agents_it_would_touch(gw):
    for rec in gw.recommendations():
        assert rec.agent_ids
        assert len(rec.agent_ids) == rec.agents


def test_applying_the_cut_returns_a_certificate(gw):
    receipt = gw.apply_recommendation("rec-source", actor="priya@example.com")
    assert receipt.changed_memory is True
    assert receipt.certificates[0].classes_pruned == ["class:UnsafeShardLoader"]
    assert receipt.certificates[0].actor == "priya@example.com"
    assert receipt.sample is True


def test_applying_anything_else_does_not_claim_the_change(gw):
    receipt = gw.apply_recommendation("rec-numpy")
    assert receipt.changed_memory is False
    assert not receipt.certificates
    assert "repositories" in receipt.note


def test_applying_an_unknown_recommendation_is_not_found(gw):
    with pytest.raises(NotFound):
        gw.apply_recommendation("rec-nope")


# -- the recorder, with nothing to record into --------------------------------

def _batch(*events):
    return RecorderBatch(agent="claude-code", session="sess-1",
                         events=list(events))


def test_a_recorder_batch_is_read_and_reported_but_never_claimed_as_written(gw):
    """There is no engine here. Reporting what would land is what an adapter
    author needs; claiming it landed would be a lie the adapter believes."""
    got = gw.record_events(_batch(
        {"type": "session", "agent": "claude-code", "task": "Add a loader."},
        {"type": "decision", "id": "d", "statement": "use pickle"},
        {"type": "code", "module": "loader", "code": "class A:\n    pass\n",
         "because": "d"},
    ))
    assert got.sample is True
    assert got.recorded == 3
    assert got.analysed == 1
    assert got.unexplained == 0


def test_sample_mode_counts_unexplained_code_by_the_same_rule_as_the_engine(gw):
    got = gw.record_events(_batch(
        {"type": "session", "agent": "claude-code", "task": "Add a loader."},
        {"type": "code", "module": "loader", "code": "class A:\n    pass\n"},
    ))
    assert got.unexplained == 1


def test_sample_mode_refuses_code_naming_a_decision_it_has_not_seen(gw):
    got = gw.record_events(_batch(
        {"type": "session", "agent": "claude-code", "task": "Add a loader."},
        {"type": "code", "module": "loader", "code": "class A:\n    pass\n",
         "because": "never-stated"},
    ))
    assert got.refused == ["code loader: unknown decision 'never-stated'"]
    assert got.recorded == 1


def test_sample_mode_does_not_claim_to_analyse_what_it_cannot_parse(gw):
    got = gw.record_events(_batch(
        {"type": "session", "agent": "claude-code", "task": "Add a loader."},
        {"type": "code", "module": "main.go", "code": "package main\n"},
    ))
    assert got.recorded == 2
    assert got.analysed == 0


def test_curated_coverage_reports_no_adopters_rather_than_a_flattering_number(gw):
    """The curated run was written by MeshAgent's own loop, which always
    states a decision. Counting it as adoption would show a healthy number
    for a fleet with nobody in it."""
    cov = gw.fleet_coverage()
    assert cov.sample is True
    assert cov.modules == 0
    assert cov.by_developer == []
    assert cov.self_recorded >= 1


# -- relations: memory is n-ary, and the payload has to say so -----------------

def test_the_finding_survives_as_one_fact_not_three_lines(gw):
    """A finding is a single 4-ary memory over {class, sink, capability,
    weakness}. Flattened to pairwise edges it becomes three lines with nothing
    saying they were ever one thing -- which is the structure everything else
    here reasons about."""
    g = gw.run_graph(RUN)
    finding = next(r for r in g.relations if r.kind == "finding")
    assert set(finding.members) == {
        "class:UnsafeShardLoader", "sink:pickle.load",
        "cap:deserialization", "cwe:CWE-502",
    }
    # and the drawing still exists beside it, unchanged
    assert any(e.source == "class:UnsafeShardLoader"
               and e.target == "sink:pickle.load" for e in g.edges)


def test_a_relation_never_points_at_a_node_the_caller_was_not_sent(gw):
    g = gw.run_graph(RUN)
    drawn = {n.id for n in g.nodes}
    assert g.relations
    for r in g.relations:
        assert drawn.issuperset(r.members), f"{r.id} dangles"


def test_relations_carry_the_id_the_why_chain_uses(gw):
    """The chain and the relations have to join, or the UI cannot say which
    recorded fact a link actually is."""
    g = gw.run_graph(RUN)
    by_id = {r.id: r for r in g.relations}
    chain = gw.run_why(RUN, "class:UnsafeShardLoader").chain
    cls = next(e for e in chain if e.entity == "class:UnsafeShardLoader")
    assert set(by_id[cls.ulid].members) == {
        "class:UnsafeShardLoader", "pkg:numpy", "pkg:pickle"}


def test_a_traversal_claims_no_relations(gw):
    """The fleet query draws agents onto versions. That is a question being
    answered, not memory that was written, and saying otherwise would invent
    provenance for it."""
    assert gw.fleet_query("who imports numpy").graph.relations == []


# -- structure (the curated hypergraph still decomposes as documented) ---------

def test_curated_run_still_has_one_entangled_block(gw):
    d = gw.run_decomposition(RUN)
    assert d.b1 == 1
    assert len(d.blocks) == 1
    assert d.branches >= 1


def test_curated_fleet_has_a_forbidden_cluster(gw):
    d = gw.fleet_decomposition()
    assert any(f.kind == "2-adjacent-bundle-3edges" for f in d.forbidden)


# -- rewind ---------------------------------------------------------------

def test_rewind_walks_the_curated_story_in_order(gw):
    """Memory only grows across the sample timeline, and every stop holds
    strictly more than the one before it. The curated run is never forgotten
    from, so this is the shape it should have."""
    stops = gw.run_rewind(RUN, 0).milestones
    assert len(stops) > 1, "a timeline with one stop is not a timeline"

    counts = [gw.run_rewind(RUN, t).held for t in stops]
    assert counts == sorted(counts)
    assert counts[0] >= 1 and counts[-1] == gw.run_rewind(RUN, stops[-1]).now


def test_rewind_before_the_run_began_holds_nothing(gw):
    out = gw.run_rewind(RUN, 0)

    assert out.memories == [] and out.held == 0
    assert out.now > 0, "the present is not empty, only the past is"
    assert out.sample is True


def test_the_curated_timeline_never_claims_a_deletion(gw):
    """Nothing is tombstoned in sample memory, so nothing may come back
    redacted. The forget-vs-rewind argument is engine mode's to have; sample
    mode must not stage a fake version of it."""
    out = gw.run_rewind(RUN, gw.run_rewind(RUN, 0).milestones[-1])

    assert out.redacted == 0
    assert all(m.statement for m in out.memories)
    assert not any(m.redacted for m in out.memories)


def test_the_last_stop_is_the_present_and_holds_every_curated_memory(gw):
    """Rewinding to the final milestone must not disagree with the curated
    records themselves, and every ULID it returns must be one the `why` verb
    can be asked about -- a timeline citing memories nothing else knows is a
    second store, not a view of this one."""
    last = gw.run_rewind(RUN, 0).milestones[-1]
    out = gw.run_rewind(RUN, last)

    assert {m.ulid for m in out.memories} == {r.ulid for r in sample._MEMORY}
    assert out.held == out.now


def test_rewinding_an_unknown_run_is_not_found(gw):
    with pytest.raises(NotFound):
        gw.run_rewind("nosuchrun", 2_000_000_000)
