"""Does a deployment remember anything after it restarts?

Memory that a process restart erases cannot be audited, so these tests stand
in for the restart: two EngineGateway instances over one directory, the second
standing for the process that comes back up. What the first wrote, the second
has to find -- and what the first left unfinished, the second has to describe
honestly rather than tidy away.
"""

from __future__ import annotations

import pytest

engine_gateway = pytest.importorskip("app.engine_gateway")

from app import engine_seed, registry  # noqa: E402


@pytest.fixture
def deployment(monkeypatch, tmp_path):
    """A directory that stands for one deployment's disk. `restart()` returns
    a gateway that knows only what is written there."""
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "deployment"))
    return lambda: engine_gateway.EngineGateway()


def _drain(gateway, run_id):
    return [e for e in gateway.run_stream(run_id)]


def test_a_run_recorded_before_the_restart_is_still_there_after_it(deployment):
    first = deployment()
    created = first.create_run("Parse the nightly export.")
    _drain(first, created.id)

    after = deployment()

    revived = next((r for r in after.runs() if r.id == created.id), None)
    assert revived is not None, "the run vanished with the process that made it"
    assert revived.task == "Parse the nightly export."
    assert revived.status == "complete"


def test_the_memory_itself_comes_back_not_just_the_run_row(deployment):
    """An index entry pointing at an empty store would be worse than nothing:
    the run would be listed and have no evidence behind it."""
    first = deployment()
    created = first.create_run("Parse the nightly export.")
    _drain(first, created.id)
    before = first.runs()
    written = next(r.memory_count for r in before if r.id == created.id)
    assert written > 0, "nothing was recorded, so the test proves nothing"

    after = deployment()

    recovered = next(r for r in after.runs() if r.id == created.id)
    assert recovered.memory_count == written
    assert after.run_graph(created.id).nodes, "the graph came back empty"


def test_a_run_cut_off_mid_recording_does_not_come_back_claiming_it_finished(
    deployment,
):
    """The process died while the agent was still writing. Its partial memory
    is real and worth keeping; the claim that the work completed is not."""
    first = deployment()
    created = first.create_run("Parse the nightly export.")
    # created, never streamed: exactly the state a kill -9 mid-build leaves
    assert created.status == "recording"

    after = deployment()

    revived = next(r for r in after.runs() if r.id == created.id)
    assert revived.status == "failed", (
        "an interrupted run reported itself complete after the restart")


def test_a_deletion_certificate_outlives_the_process_that_issued_it(deployment):
    """The certificate is the evidence half of `forget`. Evidence that a
    restart destroys is not evidence."""
    first = deployment()
    cert = first.run_forget(
        engine_seed.RUN_ID, "source:poisoned-mirror", "poisoned mirror")
    assert cert.purged_count > 0

    after = deployment()

    assert after.fleet_overview().deletion_certificates >= 1, (
        "the receipt for a deletion was lost on restart")


def test_what_was_forgotten_stays_forgotten_across_a_restart(deployment):
    """Re-seeding the reference store on boot would resurrect the very
    records a user had just proved were gone."""
    first = deployment()
    before = first.run_graph(engine_seed.RUN_ID)
    first.run_forget(
        engine_seed.RUN_ID, "source:poisoned-mirror", "poisoned mirror")
    pruned = first.run_graph(engine_seed.RUN_ID)
    assert len(pruned.nodes) < len(before.nodes), "the cut removed nothing"

    after = deployment()

    assert len(after.run_graph(engine_seed.RUN_ID).nodes) == len(pruned.nodes), (
        "the forgotten memory came back when the process restarted")


def test_the_reference_build_does_not_become_the_newest_run_on_every_boot(
    deployment,
):
    """It is reopened, not rebuilt, so it keeps the age it had. Re-stamping it
    at boot would make the fixture sort newest after every restart, and every
    screen that opens "the latest run" would land on it instead of the work
    the user actually did."""
    first = deployment()
    created = first.create_run("Parse the nightly export.")
    _drain(first, created.id)

    after = deployment()

    # runs() is oldest-first and the UI opens the last entry as "the latest
    # run", so that position is the one that has to be right
    assert after.runs()[-1].id == created.id, (
        "the seeded reference build displaced the user's own latest run")


def test_without_a_configured_directory_nothing_is_left_behind(monkeypatch, tmp_path):
    """The ephemeral default stays ephemeral: a developer running the app with
    no configuration should not silently accumulate an index on disk."""
    monkeypatch.delenv("MESHAGENT_DB_DIR", raising=False)
    gateway = engine_gateway.EngineGateway()
    gateway.create_run("Parse the nightly export.")

    assert not (tmp_path / "index.json").exists()
    assert not engine_seed.durable()


def test_an_index_from_a_future_build_is_ignored_rather_than_guessed_at(tmp_path):
    """A wrong run list is worse than an empty one: every screen over it would
    make claims nothing backs."""
    base = str(tmp_path / "deployment")
    reg = registry.Registry(base=base)
    reg.runs["abcd"] = registry.RunRecord(
        id="abcd", task="from a later version", status="complete",
        created_at=0, store="run-abcd")
    reg.save()

    # the same file, written by a build whose fields mean something else
    import json
    with open(reg.path) as fh:
        body = json.load(fh)
    body["version"] = registry.VERSION + 1
    with open(reg.path, "w") as fh:
        json.dump(body, fh)

    assert registry.load(base).runs == {}
