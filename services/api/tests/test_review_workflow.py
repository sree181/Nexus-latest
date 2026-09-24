from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import auth, control_plane, developer_sessions, engine_gateway, main
from app.models import GateDecision
from app.review_service import _suggested_version


DEV = {auth.DEV_USER: "maya@example.com", auth.DEV_ROLE: "developer"}
ANALYST = {auth.DEV_USER: "priya@example.com", auth.DEV_ROLE: "analyst"}
CISO = {auth.DEV_USER: "alex@example.com", auth.DEV_ROLE: "ciso"}


def test_suggested_version_clears_all_advisories():
    advisories = [
        SimpleNamespace(fixed_versions=["git-commit", "2.20.0"]),
        SimpleNamespace(fixed_versions=["2.32.4"]),
        SimpleNamespace(fixed_versions=["v2.33.0"]),
    ]
    assert _suggested_version(advisories) == "v2.33.0"


@pytest.fixture
def clients(tmp_path, monkeypatch):
    ledger = developer_sessions.Store(str(tmp_path / "sessions"))
    reviews = control_plane.ControlPlane(str(tmp_path / "control"))
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "engine"))
    gateway = engine_gateway.EngineGateway()
    monkeypatch.setattr(main, "developer_session_store", ledger)
    monkeypatch.setattr(main, "workflow_store", reviews)
    monkeypatch.setattr(main, "gateway", gateway)
    return (
        TestClient(main.app, headers=DEV),
        TestClient(main.app, headers=ANALYST),
        TestClient(main.app, headers=CISO),
        gateway,
    )


def create_attention(developer: TestClient) -> tuple[str, str]:
    session_id = "ses_reviewworkflow00000001"
    opened = developer.post("/api/v1/developer/sessions", json={
        "id": session_id,
        "source_session_id": "cursor-review-1",
        "source_event_id": "cursor-review-open-1",
        "adapter": "cursor",
        "adapter_version": "1.0.0",
        "repository": {"id": "repo-payments", "name": "payments-api"},
        "task": "Review an unsafe dependency",
        "started_at_ms": int(time.time() * 1000),
        "sequence": 1,
    })
    assert opened.status_code == 201, opened.text
    event_time = int(time.time() * 1000)
    events = developer.post(
        f"/api/v1/developer/sessions/{session_id}/events",
        json={"events": [
            {
                "event_id": "evt_reviewfile00000001",
                "source_event_id": "cursor-review-file-1",
                "sequence": 2,
                "occurred_at_ms": event_time,
                "type": "file.changed",
                "payload": {
                    "path": "src/client.py", "operation": "update",
                    "code": (
                        "import httpx\n\nclass Client:\n"
                        "    def get(self, url):\n        return httpx.get(url)\n"
                    ),
                },
            },
            {
                "event_id": "evt_reviewpolicy000001",
                "source_event_id": "cursor-review-policy-1",
                "sequence": 3,
                "occurred_at_ms": event_time + 1,
                "type": "policy.evaluated",
                "payload": {
                    "package": "httpx", "version": "0.27.2",
                    "ecosystem": "PyPI", "verdict": "block",
                    "worst": "high", "policy": "block high advisories",
                    "reasons": ["CVE-2026-1234 affects this version"],
                    "advisories": [{
                        "id": "CVE-2026-1234", "severity": "high",
                        "summary": "Redirect handling may disclose credentials.",
                        "cwe": "CWE-200", "fixed_versions": ["0.28.1"],
                        "references": ["https://osv.dev/vulnerability/CVE-2026-1234"],
                    }],
                },
            },
        ]},
    )
    assert events.status_code == 200, events.text
    evaluation = developer.get(
        f"/api/v1/developer/sessions/{session_id}/policy-evaluations",
    ).json()["evaluations"][0]
    return session_id, evaluation["id"]


def test_developer_attention_to_analyst_review_to_verified_fix(clients, monkeypatch):
    developer, analyst, ciso, gateway = clients
    session_id, evaluation_id = create_attention(developer)

    attention = developer.get("/api/v1/developer/attention")
    assert attention.status_code == 200, attention.text
    item = attention.json()["items"][0]
    assert item["package"] == "httpx"
    assert item["ecosystem"] == "PyPI"
    assert item["suggested_version"] == "0.28.1"
    assert item["advisories"][0]["id"] == "CVE-2026-1234"
    assert "Client" in item["code_entities"]
    assert "Client.get" in item["code_entities"]
    assert "httpx.get" in item["code_entities"]
    run_id = developer.get(
        f"/api/v1/developer/sessions/{session_id}",
    ).json()["run_id"]
    run_graph = gateway.run_graph(run_id)
    assert any(edge.rel == "imports" and edge.source == "module:src/client.py"
               and edge.target == "pkg:httpx" for edge in run_graph.edges)
    assert any(edge.rel == "invokes" and edge.source.endswith(":Client.get")
               and edge.target == "api:httpx.get" for edge in run_graph.edges)

    created = developer.post("/api/v1/developer/review-requests", json={
        "session_id": session_id,
        "policy_evaluation_id": evaluation_id,
        "kind": "safe_version",
        "rationale": "Confirm the least disruptive safe version.",
    })
    assert created.status_code == 201, created.text
    request = created.json()
    assert request["state"] == "waiting"
    assert request["events"][-1]["action"] == "review.requested"
    assert len(request["evidence_digest"]) == 64
    assert len(request["evidence_root_ulid"]) == 26

    # Duplicate submission returns the same immutable evidence snapshot.
    duplicate = developer.post("/api/v1/developer/review-requests", json={
        "session_id": session_id,
        "policy_evaluation_id": evaluation_id,
        "kind": "safe_version",
        "rationale": "Confirm the least disruptive safe version.",
    })
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == request["id"]

    assert developer.get("/api/reviews").status_code == 403
    assert analyst.get("/api/v1/developer/review-requests").status_code == 403

    queue = analyst.get("/api/reviews")
    assert queue.status_code == 200
    assert queue.json()["requests"][0]["id"] == request["id"]
    decided = analyst.post(f"/api/reviews/{request['id']}/decision", json={
        "expected_version": request["version_counter"],
        "decision": "request_changes",
        "rationale": "Upgrade to the first fixed version and rerun the check.",
        "recommended_version": "0.28.1",
    })
    assert decided.status_code == 200, decided.text
    assert decided.json()["state"] == "changes_requested"

    developer_view = developer.get(
        f"/api/v1/developer/review-requests/{request['id']}",
    ).json()
    assert developer_view["recommended_version"] == "0.28.1"
    assert developer_view["events"][-1]["action"] == "review.request_changes"

    dev_graph = developer.get(
        f"/api/v1/developer/review-requests/{request['id']}/graph",
    ).json()
    assert dev_graph["perspective"] == "developer"
    assert dev_graph["evidence_root_ulid"] == request["evidence_root_ulid"]
    assert dev_graph["evidence_digest"] == request["evidence_digest"]
    assert {node["kind"] for node in dev_graph["graph"]["nodes"]} >= {
        "agent", "module", "package", "version", "cve", "policy",
        "review", "review_event",
    }
    assert all(not edge["id"].startswith("rev-e")
               for edge in dev_graph["graph"]["edges"])
    assert all(len(relation["id"]) == 26
               for relation in dev_graph["graph"]["relations"])
    assert {edge["rel"] for edge in dev_graph["graph"]["edges"]} >= {
        "imports", "affects", "reported_by", "fixed_by", "attests",
    }
    stranger = TestClient(
        main.app,
        headers={auth.DEV_USER: "other@example.com", auth.DEV_ROLE: "developer"},
    )
    assert stranger.get(
        f"/api/v1/developer/review-requests/{request['id']}/graph",
    ).status_code == 404
    assert ciso.get(f"/api/reviews/{request['id']}/graph").json()["perspective"] == "ciso"

    monkeypatch.setattr(gateway, "check_package", lambda req: GateDecision(
        package=req.package, version=req.version, ecosystem=req.ecosystem,
        verdict="allow", reasons=["No advisory affects the replacement."],
        advisories=[], policy="block high advisories",
    ))
    checked = developer.post("/api/gate/package", json={
        "package": "httpx", "version": "0.28.1", "ecosystem": "PyPI",
        "session": session_id,
    })
    assert checked.status_code == 200, checked.text
    assert checked.json()["verdict"] == "allow"
    verified = developer.get(
        f"/api/v1/developer/review-requests/{request['id']}",
    ).json()
    assert verified["state"] == "verified"
    assert verified["verification_evidence_id"].startswith("gate:")
    assert verified["events"][-1]["action"] == "review.verified"
    verified_graph = developer.get(
        f"/api/v1/developer/review-requests/{request['id']}/graph",
    ).json()["graph"]
    review_events = [relation for relation in verified_graph["relations"]
                     if relation["kind"] == "review_event"]
    assert len(review_events) == 3


def test_developer_cannot_decide_own_request(clients):
    developer, _, _, _ = clients
    session_id, evaluation_id = create_attention(developer)
    request = developer.post("/api/v1/developer/review-requests", json={
        "session_id": session_id,
        "policy_evaluation_id": evaluation_id,
        "kind": "exception",
        "rationale": "A vendor dependency prevents an immediate upgrade.",
    }).json()
    response = developer.post(f"/api/reviews/{request['id']}/decision", json={
        "expected_version": 1, "decision": "reject",
        "rationale": "Self decision should never be accepted.",
    })
    assert response.status_code == 403


def test_review_decisions_use_optimistic_concurrency(clients):
    developer, analyst, _, _ = clients
    session_id, evaluation_id = create_attention(developer)
    request = developer.post("/api/v1/developer/review-requests", json={
        "session_id": session_id,
        "policy_evaluation_id": evaluation_id,
        "kind": "security_guidance",
        "rationale": "Explain the practical impact.",
    }).json()
    body = {
        "expected_version": request["version_counter"],
        "decision": "escalate", "rationale": "CISO input is required.",
    }
    assert analyst.post(f"/api/reviews/{request['id']}/decision", json=body).status_code == 200
    assert analyst.post(f"/api/reviews/{request['id']}/decision", json=body).status_code == 412


def test_review_projection_retries_without_duplicate_native_events(clients, monkeypatch):
    developer, _, _, gateway = clients
    session_id, evaluation_id = create_attention(developer)
    real_project = gateway.project_review_event
    calls = 0

    def fail_once(run_id, *, event):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary projection failure")
        return real_project(run_id, event=event)

    monkeypatch.setattr(gateway, "project_review_event", fail_once)
    created = developer.post("/api/v1/developer/review-requests", json={
        "session_id": session_id,
        "policy_evaluation_id": evaluation_id,
        "kind": "security_guidance",
        "rationale": "Confirm the native evidence chain.",
    })
    assert created.status_code == 201
    request_id = created.json()["id"]
    assert created.json()["evidence_root_ulid"] is None

    with main.workflow_store._write() as conn:
        conn.execute(
            "UPDATE review_projection_outbox SET next_attempt_at=0 WHERE request_id=?",
            (request_id,),
        )
    main._reconcile_review_projections()
    projected = developer.get(
        f"/api/v1/developer/review-requests/{request_id}",
    ).json()
    assert len(projected["evidence_root_ulid"]) == 26
    graph = developer.get(
        f"/api/v1/developer/review-requests/{request_id}/graph",
    ).json()["graph"]
    before = [relation["id"] for relation in graph["relations"]
              if relation["kind"] == "review_event"]
    assert len(before) == 1

    main._reconcile_review_projections()
    graph = developer.get(
        f"/api/v1/developer/review-requests/{request_id}/graph",
    ).json()["graph"]
    after = [relation["id"] for relation in graph["relations"]
             if relation["kind"] == "review_event"]
    assert after == before
