from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import auth, control_plane, main, sample


@pytest.fixture
def clients(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "workflow_store", control_plane.load(str(tmp_path)))
    analyst = TestClient(
        main.app,
        headers={auth.DEV_USER: "priya@example.com", auth.DEV_ROLE: "analyst"},
    )
    ciso = TestClient(
        main.app,
        headers={auth.DEV_USER: "alex@example.com", auth.DEV_ROLE: "ciso"},
    )
    developer = TestClient(
        main.app,
        headers={auth.DEV_USER: "maya@example.com", auth.DEV_ROLE: "developer"},
    )
    return analyst, ciso, developer


def _case(client: TestClient) -> dict:
    response = client.post(
        "/api/cases",
        json={
            "finding_id": "pickle.load",
            "run_id": sample.RUN_ID,
            "title": "Unsafe deserialization is reachable",
            "severity": "critical",
            "rationale": "The scanner proved an entry-to-sink path.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _policy(client: TestClient) -> dict:
    response = client.post(
        "/api/policies",
        json={
            "name": "Critical package gate",
            "scope": "production/*",
            "severity_threshold": "high",
            "denied_licenses": ["AGPL-3.0"],
            "block_on_unknown": True,
            "rationale": "Production dependency risk must be known before install.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_roles_receive_distinct_capabilities_and_landing_authority(clients):
    analyst, ciso, developer = clients
    assert analyst.get("/api/me").json()["primary_role"] == "analyst"
    assert "case.write" in analyst.get("/api/me").json()["capabilities"]
    assert "policy.write" not in analyst.get("/api/me").json()["capabilities"]
    assert "policy.write" in ciso.get("/api/me").json()["capabilities"]
    assert developer.get("/api/cases").status_code == 403
    assert analyst.get("/api/governance/overview").status_code == 403


def test_analyst_case_lifecycle_requires_evidence_for_resolution(clients):
    analyst, _, _ = clients
    case = _case(analyst)
    assert case["state"] == "open"
    assert case["origin"] == "sample"
    assigned = analyst.post(
        f"/api/cases/{case['id']}/assign",
        json={
            "expected_version": case["version"],
            "assignee": "priya@example.com",
            "assignee_name": "Priya Shah",
            "sla_due_at": int(time.time()) + 86400,
        },
    ).json()
    investigating = analyst.post(
        f"/api/cases/{case['id']}/transition",
        json={
            "expected_version": assigned["version"],
            "to_state": "investigating",
            "rationale": "Reproducing the scanner path.",
            "evidence_ids": [],
        },
    ).json()
    refused = analyst.post(
        f"/api/cases/{case['id']}/transition",
        json={
            "expected_version": investigating["version"],
            "to_state": "resolved",
            "disposition": "fixed",
            "rationale": "Patched.",
            "evidence_ids": [],
        },
    )
    assert refused.status_code == 409
    resolved = analyst.post(
        f"/api/cases/{case['id']}/transition",
        json={
            "expected_version": investigating["version"],
            "to_state": "resolved",
            "disposition": "verified remediated",
            "rationale": "The replacement build no longer reaches the sink.",
            "evidence_ids": ["scan:codeql:replacement-build"],
        },
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["state"] == "resolved"
    assert body["events"][-1]["evidence_ids"] == ["scan:codeql:replacement-build"]


def test_case_updates_use_optimistic_concurrency(clients):
    analyst, _, _ = clients
    case = _case(analyst)
    first = analyst.post(
        f"/api/cases/{case['id']}/assign",
        json={
            "expected_version": case["version"],
            "assignee": "priya@example.com",
            "assignee_name": "Priya Shah",
        },
    )
    assert first.status_code == 200
    stale = analyst.post(
        f"/api/cases/{case['id']}/assign",
        json={
            "expected_version": case["version"],
            "assignee": "other@example.com",
            "assignee_name": "Other Analyst",
        },
    )
    assert stale.status_code == 412


def test_only_ciso_can_create_policy_and_approve_an_exception(clients):
    analyst, ciso, _ = clients
    assert analyst.post("/api/policies", json={}).status_code == 403
    policy = _policy(ciso)
    assert analyst.get("/api/policies").json()[0]["id"] == policy["id"]
    requested = analyst.post(
        "/api/exceptions",
        json={
            "policy_id": policy["id"],
            "scope": "payments/legacy-worker",
            "rationale": "Replacement requires a coordinated vendor upgrade.",
            "compensating_controls": "Network isolation and daily scan.",
            "owner": "payments-platform",
            "expires_at": int(time.time()) + 7 * 86400,
        },
    )
    assert requested.status_code == 201, requested.text
    approval = requested.json()
    decision = ciso.post(
        f"/api/approvals/{approval['id']}/decision",
        json={
            "expected_version": approval["version"],
            "decision": "approve",
            "rationale": "Time bounded and compensating controls are measurable.",
        },
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "approved"
    assert ciso.get("/api/exceptions").json()[0]["status"] == "approved"


def test_requester_cannot_approve_their_own_exception(clients):
    _, ciso, _ = clients
    policy = _policy(ciso)
    approval = ciso.post(
        "/api/exceptions",
        json={
            "policy_id": policy["id"],
            "scope": "platform/emergency",
            "rationale": "Emergency compatibility window.",
            "compensating_controls": "Restricted network and owner review.",
            "owner": "platform",
            "expires_at": int(time.time()) + 86400,
        },
    ).json()
    response = ciso.post(
        f"/api/approvals/{approval['id']}/decision",
        json={
            "expected_version": approval["version"],
            "decision": "approve",
            "rationale": "I requested this.",
        },
    )
    assert response.status_code == 409


def test_ciso_overview_labels_seeded_data_and_reports_exclude_it(clients):
    _, ciso, _ = clients
    overview = ciso.get("/api/governance/overview")
    assert overview.status_code == 200
    assert overview.json()["origin"]["data_origin"] in ("sample", "mixed")
    report = ciso.post(
        "/api/reports",
        json={
            "title": "Executive posture",
            "period_start": int(time.time()) - 86400,
            "period_end": int(time.time()) + 86400,
        },
    )
    assert report.status_code == 409
    assert "sample data is excluded" in report.json()["detail"]


def test_analyst_cannot_apply_ciso_recommendations(clients):
    analyst, _, _ = clients
    rec = analyst.get("/api/recommendations").json()[0]
    assert analyst.post(f"/api/recommendations/{rec['id']}/apply").status_code == 403
