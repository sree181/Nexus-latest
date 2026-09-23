"""Route-level tests: the wire shapes the frontend fetches and the status codes
it has to handle. The gateways raise NotFound; these assert the API turns that
into a 404 rather than a 500.

Deliberately gateway-agnostic. Run them with MESHAGENT_ENGINE=1 and they hit
the real engine instead, and must still pass: that is the seam working."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth, sample
from app.main import app

RUN = sample.RUN_ID


@pytest.fixture(scope="module")
def client():
    """The security office. These tests read the governance views and the
    seeded run, which belongs to no developer, so the analyst is the role
    that can legitimately see all of it. Scoping itself is tested in
    test_identity.py, from both sides."""
    return TestClient(app, headers={auth.DEV_ROLE: "analyst",
                                    auth.DEV_USER: "priya@example.com"})


def test_health_names_the_gateway(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["gateway"] in ("SampleGateway", "EngineGateway")


def test_health_says_whether_a_write_here_is_kept(client):
    """The flag the mode banner renders. Sample mode returns a success receipt
    for a recorder batch it discards, and nothing else on the wire says so."""
    body = client.get("/api/health").json()
    mode = body["mode"]
    assert mode["engine"] is (body["gateway"] == "EngineGateway")
    assert mode["persists"] is mode["engine"]
    assert mode["note"], "the flags are useless to a reader who is not told"
    assert isinstance(body["durable"], bool)
    assert isinstance(body["identity_provider"], bool)
    assert body["web_url"].startswith("http")
    # the path the hook configurations are generated against, which has to be
    # absolute or the hook cannot find the script it names
    assert body["checkout"].startswith("/")


def test_health_answers_without_an_identity():
    """It must stay unauthenticated: the banner saying a recording will be
    thrown away is most needed where nothing can answer /api/me."""
    assert TestClient(app).get("/api/health").status_code == 200


def test_runs_round_trip(client):
    listed = client.get("/api/runs")
    assert listed.status_code == 200
    assert any(r["id"] == RUN for r in listed.json())

    created = client.post("/api/runs", json={"task": "Add a caching layer"})
    assert created.status_code == 201
    assert created.json()["status"] == "recording"
    # no model is wired up, so the run says on the wire that the memory it
    # will hold is the reference build and not this task
    assert created.json()["reference_build"] is True


def test_empty_task_is_rejected(client):
    assert client.post("/api/runs", json={"task": "   "}).status_code == 422


def test_task_length_is_bounded_at_the_api_boundary(client):
    response = client.post("/api/runs", json={"task": "x" * 4_097})
    assert response.status_code == 422


def test_recorder_batch_and_code_fields_are_bounded(client):
    too_many = client.post("/api/recorder", json={
        "agent": "claude-code", "session": "too-many",
        "events": [{"type": "tool", "name": "test"}] * 101,
    })
    assert too_many.status_code == 422

    too_large_code = client.post("/api/recorder", json={
        "agent": "claude-code", "session": "too-large-code",
        "events": [{"type": "code", "module": "module.py",
                    "code": "x" * 200_001}],
    })
    assert too_large_code.status_code == 422


def test_package_gate_and_sarif_top_level_containers_are_bounded(client):
    package = client.post("/api/gate/package", json={
        "package": "p" * 257, "version": "1.0",
    })
    assert package.status_code == 422

    sarif = client.post(f"/api/runs/{RUN}/scan", json={
        "runs": [{}] * 101,
    })
    assert sarif.status_code == 422


def test_why_requires_the_node_query_param(client):
    assert client.get(f"/api/runs/{RUN}/why").status_code == 422
    ok = client.get(f"/api/runs/{RUN}/why", params={"node": "class:UnsafeShardLoader"})
    assert ok.status_code == 200
    assert ok.json()["chain"][0]["entity"] == "class:UnsafeShardLoader"


def test_findings_and_one_finding(client):
    out = client.get(f"/api/runs/{RUN}/findings").json()
    assert (out["present"], out["exploitable"]) == (3, 1)
    one = client.get(f"/api/runs/{RUN}/findings/pickle.load")
    assert one.status_code == 200
    assert one.json()["exploitable"] is True


def test_sbom_and_cve_impact(client):
    assert client.get(f"/api/runs/{RUN}/sbom").status_code == 200
    impact = client.get("/api/cve/CVE-2021-41496/impact")
    assert impact.status_code == 200
    assert impact.json()["classes"] == ["class:UnsafeShardLoader"]


@pytest.mark.parametrize("path", [
    "/api/runs/nope/sbom",
    "/api/runs/nope/findings",
    "/api/runs/7f3a/findings/os.system",
    "/api/cve/CVE-0000-0000/impact",
])
def test_missing_things_are_404_not_500(client, path):
    res = client.get(path)
    assert res.status_code == 404
    assert "detail" in res.json()


def test_forget_returns_a_certificate(client):
    res = client.post(f"/api/runs/{RUN}/forget", json={"node": "source:poisoned-mirror"})
    assert res.status_code == 200
    cert = res.json()
    assert cert["purged_count"] > 0
    assert len(cert["retained_hash"]) == 64
    assert cert["classes_pruned"] == ["class:UnsafeShardLoader"]


def test_applying_a_recommendation_returns_a_receipt(client):
    rec = client.get("/api/recommendations").json()[0]
    res = client.post(f"/api/recommendations/{rec['id']}/apply")
    assert res.status_code == 200
    receipt = res.json()
    assert receipt["recommendation_id"] == rec["id"]
    assert receipt["agents"] == rec["agents"]
    # the receipt always says whether governed memory really changed
    assert isinstance(receipt["changed_memory"], bool)
    assert receipt["note"]


def test_applying_an_unknown_recommendation_is_404(client):
    assert client.post("/api/recommendations/rec-nope/apply").status_code == 404


def test_forget_an_unknown_node_is_404(client):
    res = client.post(f"/api/runs/{RUN}/forget", json={"node": "class:NoSuchThing"})
    assert res.status_code == 404


def test_the_preview_reads_the_closure_and_writes_nothing(client):
    def forgets() -> int:
        return sum(e["action"] == "run.forget"
                   for e in client.get("/api/audit").json()["entries"])

    before = forgets()
    res = client.get(f"/api/runs/{RUN}/forget/preview",
                     params={"node": "source:poisoned-mirror"})
    assert res.status_code == 200
    pv = res.json()
    assert pv["purged_count"] > 0
    assert len(pv["doomed"]) == pv["purged_count"]
    assert all(d["statement"] for d in pv["doomed"])
    assert pv["version"]

    # asking what a deletion would cost is not a step towards it, and logging
    # it as one would make the log worse at naming who destroyed something
    assert forgets() == before


def test_previewing_an_unknown_node_is_404(client):
    res = client.get(f"/api/runs/{RUN}/forget/preview",
                     params={"node": "class:NoSuchThing"})
    assert res.status_code == 404


def test_a_forget_quoting_a_version_that_moved_on_is_412(client):
    res = client.post(f"/api/runs/{RUN}/forget",
                      json={"node": "source:poisoned-mirror"},
                      headers={"if-match": "not-the-current-version"})
    assert res.status_code == 412
    assert "detail" in res.json()

    # and the refusal really was a refusal: the memory is still there
    assert client.get(f"/api/runs/{RUN}/forget/preview",
                      params={"node": "source:poisoned-mirror"}
                      ).status_code == 200


# -- the recorder --------------------------------------------------------------

def test_recorder_accepts_a_batch_and_answers_with_a_receipt(client):
    """Not a bare 200: an adapter that cannot tell whether its events landed
    will report silence as compliance."""
    res = client.post("/api/recorder", json={
        "agent": "claude-code",
        "session": "routes-1",
        "events": [
            {"type": "session", "agent": "claude-code",
             "task": "Add a shard loader."},
            {"type": "decision", "id": "d", "statement": "use pickle"},
            {"type": "code", "module": "loader", "because": "d",
             "code": "class Loader:\n    pass\n"},
        ],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["recorded"] == 3
    assert body["unexplained"] == 0
    assert body["refused"] == []
    assert body["run_id"]


def test_recorder_rejects_an_event_type_it_does_not_know(client):
    """The vocabulary is closed on purpose. An adapter inventing event types
    should find out at the boundary, not have them silently dropped."""
    res = client.post("/api/recorder", json={
        "agent": "claude-code", "session": "routes-2",
        "events": [{"type": "telepathy", "thought": "maybe"}],
    })
    assert res.status_code == 422


def test_coverage_is_the_security_offices_view_not_a_developers(client):
    """Coverage names developers against their gaps, so it is a report about
    people. A developer should not be able to pull the whole fleet's."""
    assert client.get("/api/fleet/coverage").status_code == 200
    res = client.get("/api/fleet/coverage",
                     headers={auth.DEV_ROLE: "developer"})
    assert res.status_code == 403


# -- the run WebSocket ---------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_stream_delay(monkeypatch):
    """The route paces memory frames for the UI; tests do not want to wait."""
    monkeypatch.setenv("MESHAGENT_STREAM_DELAY", "0")


def test_stream_sends_memory_frames_then_done(client):
    with client.websocket_connect(f"/api/runs/{RUN}/stream") as ws:
        memories = []
        while True:
            frame = ws.receive_json()
            if frame["type"] == "done":
                break
            if frame["type"] == "memory":
                memories.append(frame["memory"])
    assert memories
    first = memories[0]
    assert {"step", "detail", "origin", "status", "members", "ulid", "gate"} <= set(first)
    assert first["gate"]
    assert frame["run"]["status"] == "complete"


def test_stream_of_an_unknown_run_sends_an_error_frame(client):
    with client.websocket_connect("/api/runs/nope/stream") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert "unknown run" in frame["detail"]


def test_rewind_returns_a_past_state_and_a_set_of_stops(client):
    first = client.get(f"/api/runs/{RUN}/rewind", params={"at": 0})
    assert first.status_code == 200
    early = first.json()
    assert early["held"] == 0 and early["memories"] == []
    assert early["now"] > 0
    assert len(early["milestones"]) > 1

    latest = client.get(f"/api/runs/{RUN}/rewind",
                        params={"at": early["milestones"][-1]}).json()
    assert latest["held"] == latest["now"]
    assert latest["redacted"] == 0


def test_rewind_without_an_instant_is_rejected(client):
    """`at` is the whole question. Defaulting it to now would turn the verb
    into a second, worse way of listing present memory."""
    assert client.get(f"/api/runs/{RUN}/rewind").status_code == 422


def test_rewinding_an_unknown_run_is_404(client):
    res = client.get("/api/runs/nosuchrun/rewind", params={"at": 2_000_000_000})
    assert res.status_code == 404
