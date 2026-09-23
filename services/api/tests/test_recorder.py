"""The recorder: agents MeshAgent does not own, writing into governed memory.

The blocker this closes is that nothing a developer actually uses could write
here. These assert the properties that make the protocol worth having: that
memory posted by an external agent is indistinguishable downstream from memory
the built-in loop wrote, that a session survives a restart of this process, and
above all that the recorder never invents a reason for code that arrived
without one.
"""

from __future__ import annotations

import pytest

from app.models import RecorderBatch

engine_gateway = pytest.importorskip("app.engine_gateway")

from app.gateway import NotFound  # noqa: E402

DEV = "priya@example.com"

PY = """
import pickle


class Loader:
    def load(self, path):
        with open(path, "rb") as fh:
            return pickle.load(fh)
"""

# Deliberately not Python. The built-in walk is a Python AST walk, and a hook
# watching a developer's editor sees whatever that developer writes.
GO = """
package main

func main() { println("hello") }
"""


def _gateway():
    try:
        return engine_gateway.EngineGateway()
    except (ImportError, OSError) as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"HyperMesh engine unavailable: {exc}")


@pytest.fixture
def gw():
    return _gateway()


def _batch(*events, session="sess-1", agent="claude-code"):
    return RecorderBatch(agent=agent, session=session, events=list(events))


def _open(session="sess-1", task="Add a shard loader.", **kw):
    return {"type": "session", "agent": "claude-code", "task": task, **kw}


# -- opening a session ---------------------------------------------------------

def test_first_batch_opens_a_run_and_later_batches_find_it(gw):
    first = gw.record_events(_batch(_open()), owner=DEV, owner_name=DEV)
    assert first.opened is True

    second = gw.record_events(
        _batch({"type": "tool", "name": "pytest", "detail": "3 passed"}),
        owner=DEV, owner_name=DEV)
    assert second.opened is False
    assert second.run_id == first.run_id


def test_a_batch_with_no_session_event_is_refused_rather_than_invented(gw):
    """The alternative is a run whose stated purpose this product made up."""
    with pytest.raises(NotFound):
        gw.record_events(
            _batch({"type": "tool", "name": "ls"}, session="never-opened"),
            owner=DEV, owner_name=DEV)


def test_two_developers_sharing_a_session_id_get_separate_runs(gw):
    """The id belongs to the adapter, so a collision is ordinary. Filing one
    developer's code under another's name would not be."""
    mine = gw.record_events(_batch(_open(), session="s"), owner=DEV,
                            owner_name=DEV)
    theirs = gw.record_events(_batch(_open(), session="s"),
                              owner="maya@example.com",
                              owner_name="maya@example.com")
    assert mine.run_id != theirs.run_id
    assert gw.run(mine.run_id).owner == DEV
    assert gw.run(theirs.run_id).owner == "maya@example.com"


def test_the_task_the_developer_stated_is_the_run_task(gw):
    got = gw.record_events(_batch(_open(task="Parse the shard index.")),
                           owner=DEV, owner_name=DEV)
    assert gw.run(got.run_id).task == "Parse the shard index."


# -- code, and the reason for it -----------------------------------------------

def test_explained_code_hangs_off_the_decision_that_explains_it(gw):
    got = gw.record_events(_batch(
        _open(),
        {"type": "decision", "id": "use-pickle",
         "statement": "load shards with pickle for speed"},
        {"type": "code", "module": "loader", "code": PY,
         "because": "use-pickle"},
    ), owner=DEV, owner_name=DEV)

    assert got.unexplained == 0
    assert got.analysed == 1
    assert got.refused == []

    why = gw.run_why(got.run_id, "class:Loader")
    assert any(n.entity == "decision:use-pickle" for n in why.chain)


def test_code_with_no_reason_is_recorded_as_unexplained_not_invented(gw):
    """The hook case: a file write is observed, the reason for it is not."""
    got = gw.record_events(_batch(
        _open(),
        {"type": "code", "module": "loader", "code": PY},
    ), owner=DEV, owner_name=DEV)

    assert got.unexplained == 1

    why = gw.run_why(got.run_id, "class:Loader")
    decisions = [n for n in why.chain
                 if (n.entity or "").startswith("decision:")]
    assert decisions, "code must still hang off a decision"
    assert all("unexplained" in (n.entity or "") for n in decisions)
    # and it says so in as many words, rather than reading as a real reason
    assert all("No rationale was recorded" in (n.statement or "")
               for n in decisions)


def test_code_naming_a_decision_nobody_recorded_is_refused(gw):
    got = gw.record_events(_batch(
        _open(),
        {"type": "code", "module": "loader", "code": PY,
         "because": "never-stated"},
    ), owner=DEV, owner_name=DEV)

    assert got.refused == ["code loader: unknown decision 'never-stated'"]
    assert gw.run_code(got.run_id).modules == []


def test_a_decision_from_an_earlier_batch_still_explains_later_code(gw):
    """A developer's session spans hours; a batch is minutes."""
    first = gw.record_events(_batch(
        _open(),
        {"type": "decision", "id": "use-pickle", "statement": "speed"},
    ), owner=DEV, owner_name=DEV)

    later = gw.record_events(_batch(
        {"type": "code", "module": "loader", "code": PY,
         "because": "use-pickle"},
    ), owner=DEV, owner_name=DEV)

    assert later.run_id == first.run_id
    assert later.refused == []
    assert later.unexplained == 0


# -- what this build can and cannot read ---------------------------------------

def test_code_this_build_cannot_parse_is_stored_but_not_claimed_as_analysed(gw):
    """Honesty about our own reach: the walk is Python-only, and a caller who
    read silence as "no classes" would be reading it wrong."""
    got = gw.record_events(_batch(
        _open(),
        {"type": "code", "module": "main.go", "code": GO},
    ), owner=DEV, owner_name=DEV)

    assert got.recorded == 2
    assert got.analysed == 0

    modules = gw.run_code(got.run_id).modules
    assert [m.name for m in modules] == ["main.go"]
    assert modules[0].code == GO
    assert modules[0].classes == []


# -- the rest of the vocabulary ------------------------------------------------

def test_a_package_event_lands_in_the_runs_sbom(gw):
    got = gw.record_events(_batch(
        _open(),
        {"type": "package", "package": "numpy", "version": "1.26.4",
         "license": "BSD-3-Clause"},
    ), owner=DEV, owner_name=DEV)

    entries = gw.run_sbom(got.run_id).entries
    assert [(e.package, e.version) for e in entries] == [("numpy", "1.26.4")]


def test_recorded_findings_are_the_same_findings_the_built_in_loop_produces(gw):
    """The whole point: downstream cannot tell who wrote the memory. The
    pickle.load in PY is reachable from open(), and the security screen should
    say so without knowing Claude Code put it there."""
    got = gw.record_events(_batch(
        _open(),
        {"type": "decision", "id": "d", "statement": "speed"},
        {"type": "code", "module": "loader", "code": PY, "because": "d"},
    ), owner=DEV, owner_name=DEV)

    found = gw.run_findings(got.run_id)
    pickled = next(f for f in found.findings if f.sink == "pickle.load")
    assert pickled.reachability == "reachable"
    assert pickled.asserted_by == "builtin"


def test_ending_a_session_completes_the_run(gw):
    got = gw.record_events(_batch(_open()), owner=DEV, owner_name=DEV)
    assert gw.run(got.run_id).status == "recording"

    gw.record_events(_batch(_open(ends=True)), owner=DEV, owner_name=DEV)
    assert gw.run(got.run_id).status == "complete"


# -- surviving this process ----------------------------------------------------

def test_a_session_outlives_the_process_that_opened_it(gw, tmp_path, monkeypatch):
    """An editor session runs for hours; this API restarts on every code
    change. A restart that opened a second run would split one piece of work
    across two."""
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "durable"))
    first = _gateway()
    opened = first.record_events(_batch(
        _open(),
        {"type": "decision", "id": "use-pickle", "statement": "speed"},
    ), owner=DEV, owner_name=DEV)

    restarted = _gateway()
    after = restarted.record_events(_batch(
        {"type": "code", "module": "loader", "code": PY,
         "because": "use-pickle"},
    ), owner=DEV, owner_name=DEV)

    assert after.run_id == opened.run_id
    assert after.opened is False
    assert after.refused == []      # the decision from before the restart held


# -- coverage: how much of it anyone can explain -------------------------------

def test_coverage_counts_explained_against_unexplained_code(gw):
    gw.record_events(_batch(
        _open(),
        {"type": "decision", "id": "d", "statement": "speed"},
        {"type": "code", "module": "loader", "code": PY, "because": "d"},
        {"type": "code", "module": "helpers", "code": "class H:\n    pass\n"},
    ), owner=DEV, owner_name=DEV)

    cov = gw.fleet_coverage()
    assert cov.modules == 2
    assert cov.explained == 1

    row = next(r for r in cov.by_developer if r.owner == DEV)
    assert (row.modules, row.explained, row.unexplained) == (2, 1, 1)
    assert row.agents == ["claude-code"]


def test_meshagents_own_runs_are_kept_out_of_the_adoption_number(gw):
    """The built-in loop is made to state a decision before it writes, so
    counting it would report a healthy adoption number for a fleet nobody
    adopted the tool in."""
    cov = gw.fleet_coverage()
    assert cov.modules == 0             # nothing external recorded yet
    assert cov.self_recorded >= 1       # but the reference build is there
    assert cov.by_developer == []


def test_coverage_names_the_developer_with_the_largest_gap_first(gw):
    gw.record_events(_batch(_open(session="a"), {
        "type": "code", "module": "one", "code": PY}), owner=DEV,
        owner_name=DEV)
    gw.record_events(_batch(
        _open(session="b"),
        {"type": "decision", "id": "d", "statement": "why"},
        {"type": "code", "module": "two", "code": PY, "because": "d"},
    ), owner="maya@example.com", owner_name="maya@example.com")

    assert [r.owner for r in gw.fleet_coverage().by_developer] == [
        DEV, "maya@example.com"]


def test_an_external_run_is_never_built_by_meshagents_own_loop(gw):
    """Its memory arrives by POST. Building it here would have MeshAgent write
    code into a run somebody else is already writing."""
    got = gw.record_events(_batch(_open()), owner=DEV, owner_name=DEV)
    before = gw.run(got.run_id).memory_count

    events = list(gw.run_stream(got.run_id))

    assert gw.run(got.run_id).memory_count == before
    assert events[-1].type == "done"
