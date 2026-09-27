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
    final_evidence = developer.get(
        f"/api/v1/developer/attention/{item['id']}/evidence",
    )
    assert final_evidence.status_code == 200, final_evidence.text
    assert final_evidence.json()["outcome_change"]["status"] == "answered"
    assert "verified" in final_evidence.json()["outcome_change"]["headline"]
    verified_graph = developer.get(
        f"/api/v1/developer/review-requests/{request['id']}/graph",
    ).json()["graph"]
    review_events = [relation for relation in verified_graph["relations"]
                     if relation["kind"] == "review_event"]
    assert len(review_events) == 3

    terminal_escalation = analyst.post(
        f"/api/reviews/{request['id']}/escalate",
        json={
            "expected_version": verified["version_counter"],
            "title": "Terminal review must stay closed",
            "rationale": "A verified review cannot be reopened by a direct API call.",
        },
    )
    assert terminal_escalation.status_code == 409
    assert terminal_escalation.json()["detail"] == (
        "terminal review requests cannot be escalated"
    )
    assert analyst.get("/api/cases").json()["cases"] == []


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


def test_priority_four_analyst_operations_lifecycle(clients):
    developer, analyst, ciso, _ = clients
    session_id, evaluation_id = create_attention(developer)
    created = developer.post("/api/v1/developer/review-requests", json={
        "session_id": session_id,
        "policy_evaluation_id": evaluation_id,
        "kind": "safe_version",
        "rationale": "Confirm the replacement version before release.",
    }).json()

    queue = analyst.get("/api/operations/work")
    assert queue.status_code == 200, queue.text
    assert queue.json()["items"][0]["id"] == created["id"]
    assert queue.json()["counts"]["unassigned"] == 1
    assert developer.get("/api/operations/work").status_code == 403
    assert ciso.get("/api/operations/work").status_code == 200

    due = int(time.time()) + 3_600
    assigned = analyst.post(f"/api/reviews/{created['id']}/assign", json={
        "expected_version": created["version_counter"],
        "assignee": "priya@example.com",
        "assignee_name": "Priya Shah",
        "sla_due_at": due,
    })
    assert assigned.status_code == 200, assigned.text
    review = assigned.json()
    assert review["assignee"] == "priya@example.com"
    assert review["sla_due_at"] == due
    assert review["events"][-1]["action"] == "review.assigned"
    stale = analyst.post(f"/api/reviews/{created['id']}/assign", json={
        "expected_version": created["version_counter"],
        "assignee": "other@example.com",
        "assignee_name": "Other Analyst",
    })
    assert stale.status_code == 412

    note = analyst.post(
        f"/api/operations/review/{created['id']}/comments",
        json={"message": "Please confirm whether the service follows redirects.",
              "mentions": ["alex@example.com"]},
    )
    assert note.status_code == 201, note.text
    assert analyst.get(
        f"/api/operations/review/{created['id']}/comments"
    ).json()[0]["message"].startswith("Please confirm")
    assert ciso.get(
        f"/api/operations/review/{created['id']}/comments"
    ).status_code == 200
    assert developer.get(
        f"/api/operations/review/{created['id']}/comments"
    ).status_code == 403
    assert analyst.get(
        "/api/operations/review/rev_missing/comments"
    ).status_code == 404
    activity = analyst.get("/api/operations/activity?q=follows%20redirects").json()
    assert activity["total"] == 1
    assert activity["items"][0]["action"] == "work.comment"

    saved = analyst.post("/api/operations/views", json={
        "name": "My urgent reviews",
        "filters": {"kind": "review", "assignee": "priya@example.com"},
    })
    assert saved.status_code == 201, saved.text
    assert analyst.get("/api/operations/views").json()[0]["filters"]["kind"] == "review"
    assert ciso.get("/api/operations/views").json() == []

    notifications = analyst.get("/api/notifications").json()
    assert notifications["unread"] >= 2
    assignment_notice = next(
        item for item in notifications["notifications"]
        if item["kind"] == "work.assigned"
    )
    assert analyst.post(
        f"/api/notifications/{assignment_notice['id']}/read"
    ).status_code == 200
    assert analyst.get("/api/notifications").json()["unread"] == notifications["unread"] - 1
    assert ciso.get("/api/notifications").json()["unread"] >= 1

    escalated = analyst.post(f"/api/reviews/{created['id']}/escalate", json={
        "expected_version": review["version_counter"],
        "title": "Unsafe httpx version in payments API",
        "rationale": "The vulnerable call is reachable and needs coordinated remediation.",
        "assignee": "priya@example.com",
        "assignee_name": "Priya Shah",
        "sla_due_at": due,
    })
    assert escalated.status_code == 201, escalated.text
    case = escalated.json()
    assert case["finding_id"] == f"review:{created['id']}"
    assert created["id"] in case["events"][0]["evidence_ids"]
    final_review = analyst.get(f"/api/reviews/{created['id']}").json()
    assert final_review["state"] == "escalated"
    assert final_review["escalated_case_id"] == case["id"]
    combined = analyst.get("/api/operations/work?q=Unsafe%20httpx").json()
    assert combined["items"][0]["kind"] == "case"

    latest_review = analyst.get(f"/api/reviews/{created['id']}").json()
    latest_case = analyst.get(f"/api/cases/{case['id']}").json()
    bulk = analyst.post("/api/operations/work/bulk-assign", json={
        "items": [
            {"kind": "review", "id": created["id"],
             "expected_version": latest_review["version_counter"]},
            {"kind": "case", "id": case["id"],
             "expected_version": latest_case["version"]},
        ],
        "assignee": "alex@example.com",
        "assignee_name": "Alex Morgan",
        "sla_due_at": due,
    })
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["succeeded"] == 2
    assert bulk.json()["failed"] == 0

    current_case = analyst.get(f"/api/cases/{case['id']}").json()
    partial = analyst.post("/api/operations/work/bulk-assign", json={
        "items": [
            {"kind": "case", "id": case["id"],
             "expected_version": current_case["version"]},
            {"kind": "review", "id": "rev_missing",
             "expected_version": 1},
        ],
        "assignee": "priya@example.com",
        "assignee_name": "Priya Shah",
        "sla_due_at": due,
    })
    assert partial.status_code == 200, partial.text
    assert partial.json()["succeeded"] == 1
    assert partial.json()["failed"] == 1
    assert partial.json()["results"] == [
        {"kind": "case", "id": case["id"], "ok": True, "error": None},
        {"kind": "review", "id": "rev_missing", "ok": False,
         "error": "unknown review request"},
    ]
    assert analyst.get(f"/api/cases/{case['id']}").json()["assignee"] == "priya@example.com"


def test_attention_evidence_is_scoped_traceable_and_owner_only(clients):
    developer, analyst, _, _ = clients
    session_id, evaluation_id = create_attention(developer)
    now = int(time.time() * 1000)
    unrelated = developer.post(
        f"/api/v1/developer/sessions/{session_id}/events",
        json={"events": [
            {
                "event_id": "evt_otherfile000000001",
                "source_event_id": "cursor-other-file",
                "sequence": 4,
                "occurred_at_ms": now,
                "type": "file.changed",
                "payload": {
                    "path": "src/other.py", "operation": "update",
                    "code": "import requests\n\ndef fetch(url):\n    return requests.get(url)\n",
                },
            },
            {
                "event_id": "evt_otherpolicy0000001",
                "source_event_id": "cursor-other-policy",
                "sequence": 5,
                "occurred_at_ms": now + 1,
                "type": "policy.evaluated",
                "payload": {
                    "package": "requests", "version": "2.19.0",
                    "ecosystem": "PyPI", "verdict": "block",
                    "worst": "critical", "policy": "block critical advisories",
                    "reasons": ["An unrelated package is blocked."],
                    "advisories": [{
                        "id": "CVE-2024-9999", "severity": "critical",
                        "summary": "Unrelated request issue.", "cwe": "CWE-79",
                        "fixed_versions": ["2.32.0"], "references": [],
                    }],
                },
            },
        ]},
    )
    assert unrelated.status_code == 200, unrelated.text

    items = developer.get("/api/v1/developer/attention").json()["items"]
    item = next(value for value in items if value["policy_evaluation_id"] == evaluation_id)
    response = developer.get(
        f"/api/v1/developer/attention/{item['id']}/evidence",
    )
    assert response.status_code == 200, response.text
    evidence = response.json()
    assert evidence["scope"] == {
        "attention_id": item["id"],
        "session_id": session_id,
        "policy_evaluation_id": evaluation_id,
        "run_id": item["run_id"],
        "repository_id": "repo-payments",
        "repository_name": "payments-api",
        "package": "httpx",
        "version": "0.27.2",
        "ecosystem": "PyPI",
        "checked_at_ms": item["checked_at_ms"],
        "review_request_id": None,
        "review_status": None,
    }
    assert evidence["why_blocked"]["status"] == "answered"
    assert evidence["affected_code"]["status"] == "answered"
    assert evidence["outcome_change"]["status"] == "partial"
    assert "0.28.1" in evidence["outcome_change"]["headline"]
    assert {
        relation["kind"] for relation in evidence["why_blocked"]["graph"]["relations"]
    } == {"cve", "policy_evaluation"}
    assert {
        relation["kind"] for relation in evidence["affected_code"]["graph"]["relations"]
    } == {"module_import", "invocation", "class"}
    assert all(
        len(relation["id"]) == 26
        for answer in ("why_blocked", "affected_code", "outcome_change")
        for relation in evidence[answer]["graph"]["relations"]
    )
    assert all(
        "requests" not in node["id"].casefold()
        for answer in ("why_blocked", "affected_code", "outcome_change")
        for node in evidence[answer]["graph"]["nodes"]
    )
    assert all(
        statement["relation_ids"]
        for answer in ("why_blocked", "affected_code", "outcome_change")
        for statement in evidence[answer]["statements"]
    )
    for answer in ("why_blocked", "affected_code", "outcome_change"):
        relation_ids = {
            relation["id"] for relation in evidence[answer]["graph"]["relations"]
        }
        assert all(
            edge["id"].split(":member:", 1)[0] in relation_ids
            for edge in evidence[answer]["graph"]["edges"]
        )

    stranger = TestClient(
        main.app,
        headers={auth.DEV_USER: "other@example.com", auth.DEV_ROLE: "developer"},
    )
    assert stranger.get(
        f"/api/v1/developer/attention/{item['id']}/evidence",
    ).status_code == 404
    assert developer.get(
        "/api/v1/developer/attention/att_000000000000000000000000/evidence",
    ).status_code == 404
    assert analyst.get(
        f"/api/v1/developer/attention/{item['id']}/evidence",
    ).status_code == 403


def test_attention_evidence_does_not_infer_unrecorded_code(clients):
    developer, _, _, _ = clients
    now = int(time.time() * 1000)
    session_id = "ses_nocode000000000000001"
    opened = developer.post("/api/v1/developer/sessions", json={
        "id": session_id,
        "source_session_id": "cursor-no-code",
        "source_event_id": "cursor-no-code-open",
        "adapter": "cursor",
        "adapter_version": "1.0.0",
        "repository": {"id": "repo-empty", "name": "empty-api"},
        "task": "Evaluate a package without source evidence",
        "started_at_ms": now,
        "sequence": 1,
    })
    assert opened.status_code == 201, opened.text
    recorded = developer.post(
        f"/api/v1/developer/sessions/{session_id}/events",
        json={"events": [{
            "event_id": "evt_nocodepolicy000001",
            "source_event_id": "cursor-no-code-policy",
            "sequence": 2,
            "occurred_at_ms": now + 1,
            "type": "policy.evaluated",
            "payload": {
                "package": "urllib3", "version": "1.25.0",
                "ecosystem": "PyPI", "verdict": "unknown",
                "reasons": [], "advisories": [],
                "unavailable": "The advisory provider did not respond.",
                "policy": "block when advisory data is unavailable",
            },
        }]},
    )
    assert recorded.status_code == 200, recorded.text
    item = next(
        value for value in developer.get("/api/v1/developer/attention").json()["items"]
        if value["session_id"] == session_id
    )
    evidence = developer.get(
        f"/api/v1/developer/attention/{item['id']}/evidence",
    ).json()
    assert evidence["affected_code"]["status"] == "unavailable"
    assert evidence["affected_code"]["graph"] == {
        "nodes": [], "edges": [], "relations": [],
    }
    assert "does not infer" in evidence["affected_code"]["limitations"][0]
    assert evidence["outcome_change"]["status"] == "unavailable"
