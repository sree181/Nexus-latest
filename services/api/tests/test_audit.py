"""Can the tool account for what was done to the evidence it holds?

`forget` destroys governed memory, and the deletion certificate is the
product's proof that it happened. A certificate that names nobody proves
only that something was destroyed, which is the opposite of the point. These
tests hold that line: the certificate names the authenticated caller, the
action log records it, and editing the log after the fact is detectable.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import audit, auth, gateway as gw, main, sample
from app.auth import Principal

RUN = sample.RUN_ID


@pytest.fixture(autouse=True)
def isolated_gateway(monkeypatch):
    """These tests delete memory, and the engine's forget really destroys it.

    Pointed at the shared seeded store they would strip out the class every
    other module's findings and SBOM tests hang off -- which is exactly how
    they were caught. The curated gateway's forget is pure, so the deletions
    here cost nothing to anyone else."""
    monkeypatch.setattr(main, "gateway", gw.SampleGateway())


@pytest.fixture
def priya():
    return TestClient(main.app, headers={auth.DEV_USER: "priya@example.com",
                                         auth.DEV_ROLE: "analyst"})


@pytest.fixture
def maya():
    return TestClient(main.app, headers={auth.DEV_USER: "maya@example.com",
                                         auth.DEV_ROLE: "developer"})


@pytest.fixture(autouse=True)
def fresh_log(monkeypatch, tmp_path):
    """Each test gets its own log, and the app writes to it."""
    log = audit.Log(base=str(tmp_path / "audit"))
    monkeypatch.setattr(main, "audit_log", log)
    return log


# -- the certificate names a person -------------------------------------------

def test_a_deletion_certificate_names_whoever_asked_for_it(priya):
    cert = priya.post(f"/api/runs/{RUN}/forget",
                      json={"node": "source:poisoned-mirror",
                            "reason": "poisoned mirror"}).json()
    assert cert["actor"] == "priya@example.com", (
        "the certificate could not say who destroyed the memory")


def test_two_people_deleting_get_two_different_certificates(priya):
    """Otherwise the certificate is decoration, not attribution."""
    first = priya.post(f"/api/runs/{RUN}/forget",
                       json={"node": "source:poisoned-mirror",
                             "reason": "one"}).json()
    other = TestClient(main.app, headers={auth.DEV_USER: "sam@example.com",
                                          auth.DEV_ROLE: "analyst"})
    second = other.post(f"/api/runs/{RUN}/forget",
                        json={"node": "class:UnsafeShardLoader",
                              "reason": "two"}).json()
    assert first["actor"] != second["actor"]


# -- the action log ------------------------------------------------------------

def test_a_deletion_is_written_to_the_action_log(priya, fresh_log):
    priya.post(f"/api/runs/{RUN}/forget",
               json={"node": "source:poisoned-mirror", "reason": "poisoned"})

    entries = [e for e in fresh_log.read() if e.action == "run.forget"]
    assert len(entries) == 1
    assert entries[0].actor == "priya@example.com"
    assert entries[0].role == "analyst"
    assert "poisoned" in entries[0].detail


def test_a_forget_fails_closed_when_its_audit_commit_fails(monkeypatch):
    """The gateway must not receive a destructive request without an audit fsync."""
    client = TestClient(
        main.app,
        headers={auth.DEV_USER: "priya@example.com", auth.DEV_ROLE: "analyst"},
        raise_server_exceptions=False,
    )

    def broken_record(**_kwargs):
        raise OSError("audit volume unavailable")

    monkeypatch.setattr(main.audit_log, "record", broken_record)
    monkeypatch.setattr(
        main.gateway, "run_forget",
        lambda *_args, **_kwargs: pytest.fail("forget reached the gateway"),
    )
    response = client.post(f"/api/runs/{RUN}/forget", json={
        "node": "source:poisoned-mirror", "reason": "poisoned",
    })
    assert response.status_code == 500


def test_a_recommendation_fails_closed_when_its_audit_commit_fails(monkeypatch):
    client = TestClient(
        main.app,
        headers={auth.DEV_USER: "priya@example.com", auth.DEV_ROLE: "analyst"},
        raise_server_exceptions=False,
    )

    def broken_record(**_kwargs):
        raise OSError("audit volume unavailable")

    monkeypatch.setattr(main.audit_log, "record", broken_record)
    monkeypatch.setattr(
        main.gateway, "apply_recommendation",
        lambda *_args, **_kwargs: pytest.fail("recommendation reached the gateway"),
    )
    response = client.post("/api/recommendations/rec-source/apply")
    assert response.status_code == 500


def test_a_cut_recommendation_certificate_carries_the_authenticated_actor(priya):
    receipt = priya.post("/api/recommendations/rec-source/apply")
    assert receipt.status_code == 200
    assert receipt.json()["certificates"][0]["actor"] == "priya@example.com"


def test_production_forget_requires_both_replay_preconditions(monkeypatch):
    """Development/test keep compatibility; production rejects unsafe POSTs."""
    monkeypatch.setenv("MESHAGENT_ENV", "production")
    principal = Principal(subject="priya", name="Priya", email="p@example.com",
                          role="analyst")
    main.app.dependency_overrides[main.caller] = lambda: principal
    try:
        client = TestClient(main.app)
        missing = client.post(f"/api/runs/{RUN}/forget", json={
            "node": "source:poisoned-mirror",
        })
        assert missing.status_code == 428
        assert "If-Match" in missing.json()["detail"]

        only_version = client.post(f"/api/runs/{RUN}/forget", json={
            "node": "source:poisoned-mirror",
        }, headers={"If-Match": sample.VERSION})
        assert only_version.status_code == 428
        assert "Idempotency-Key" in only_version.json()["detail"]

        accepted = client.post(f"/api/runs/{RUN}/forget", json={
            "node": "source:poisoned-mirror",
        }, headers={"If-Match": sample.VERSION, "Idempotency-Key": "prod-forget-1"})
        assert accepted.status_code == 200
    finally:
        main.app.dependency_overrides.clear()


def test_starting_a_run_is_recorded_against_its_developer(maya, fresh_log):
    maya.post("/api/runs", json={"task": "Summarise the export"})
    created = [e for e in fresh_log.read() if e.action == "run.create"]
    assert created and created[0].actor == "maya@example.com"


def test_being_refused_someone_elses_run_leaves_a_trace(maya, priya, fresh_log):
    """Someone walking run ids should be visible afterwards, even though
    they are told nothing at the time."""
    theirs = priya.post("/api/runs", json={"task": "Theirs"}).json()["id"]
    assert maya.get(f"/api/runs/{theirs}/findings").status_code == 404

    denied = [e for e in fresh_log.read() if e.action == "access.denied"]
    assert denied and denied[-1].actor == "maya@example.com"
    assert theirs in denied[-1].target


def test_an_unverified_actor_is_marked_as_one(maya, fresh_log):
    """No identity provider is configured here, so every line has to admit
    the actor was asserted rather than authenticated."""
    maya.post("/api/runs", json={"task": "Summarise the export"})
    assert all(e.verified is False for e in fresh_log.read())


# -- tamper evidence -----------------------------------------------------------

def test_an_intact_log_verifies(fresh_log):
    for i in range(3):
        fresh_log.record(actor="p", actor_name="P", role="analyst",
                         verified=True, action="run.forget", target=f"n{i}")
    assert fresh_log.verify() is None


def test_editing_an_entry_after_the_fact_is_detected(fresh_log):
    for i in range(3):
        fresh_log.record(actor="p", actor_name="P", role="analyst",
                         verified=True, action="run.forget", target=f"n{i}")

    # rewrite the middle line to blame someone else
    lines = open(fresh_log.path).read().splitlines()
    body = json.loads(lines[1])
    body["actor"] = "someone-else"
    lines[1] = json.dumps(body, sort_keys=True)
    with open(fresh_log.path, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    assert fresh_log.verify() == 1, "an altered entry passed verification"


def test_removing_an_entry_is_detected(fresh_log):
    """The realistic insider move: delete the line recording your deletion."""
    for i in range(3):
        fresh_log.record(actor="p", actor_name="P", role="analyst",
                         verified=True, action="run.forget", target=f"n{i}")

    lines = open(fresh_log.path).read().splitlines()
    del lines[1]
    with open(fresh_log.path, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    assert fresh_log.verify() == 1, "a snipped-out entry passed verification"


def test_the_chain_continues_across_a_restart(tmp_path):
    """A new process must extend the existing chain, not start a second one
    that verifies happily while hiding everything before it."""
    base = str(tmp_path / "audit")
    first = audit.Log(base=base)
    first.record(actor="p", actor_name="P", role="analyst", verified=True,
                 action="run.forget", target="n0")

    second = audit.Log(base=base)      # stands for the restarted process
    second.record(actor="p", actor_name="P", role="analyst", verified=True,
                  action="run.forget", target="n1")

    assert second.verify() is None
    assert len(second.read()) == 2


# -- what the endpoint says ----------------------------------------------------

def test_the_log_is_the_security_offices_to_read(maya, priya):
    assert maya.get("/api/audit").status_code == 403
    assert priya.get("/api/audit").status_code == 200


def test_the_endpoint_states_what_it_does_not_cover(priya):
    """Silence in a log reads as evidence that nothing happened. It is not,
    because reads are not recorded, and the wire has to say so."""
    body = priya.get("/api/audit").json()
    assert "Reads are not recorded" in body["covers"]
    assert body["intact"] is True


def test_the_endpoint_reports_a_broken_chain_rather_than_hiding_it(priya, fresh_log):
    fresh_log.record(actor="p", actor_name="P", role="analyst", verified=True,
                     action="run.forget", target="n0")
    fresh_log.record(actor="p", actor_name="P", role="analyst", verified=True,
                     action="run.forget", target="n1")
    lines = open(fresh_log.path).read().splitlines()
    del lines[0]
    with open(fresh_log.path, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    body = priya.get("/api/audit").json()
    assert body["intact"] is False
    assert body["broken_at"] == 0
