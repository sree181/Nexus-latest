from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import auth, control_plane, main, sample


@pytest.fixture
def clients(monkeypatch, tmp_path):
    store = control_plane.load(str(tmp_path))
    monkeypatch.setattr(main, "workflow_store", store)
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
    return store, analyst, ciso, developer, tmp_path


def test_remediation_requires_evidence_and_preserves_history(clients):
    store, analyst, ciso, developer, state_dir = clients
    case_response = analyst.post(
        "/api/cases",
        json={
            "finding_id": "pickle.load",
            "run_id": sample.RUN_ID,
            "title": "Unsafe deserialization is reachable",
            "severity": "critical",
            "rationale": "The recorded path reaches an unsafe sink.",
        },
    )
    assert case_response.status_code == 201, case_response.text
    case_id = case_response.json()["id"]

    created_response = ciso.post(
        "/api/remediations",
        json={
            "case_id": case_id,
            "title": "Replace unsafe deserialization",
            "owner": "platform-security",
            "due_at": int(time.time()) + 86_400,
            "target_revision": "release/2026.09",
        },
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["status"] == "accepted"
    assert [event["action"] for event in created["events"]] == [
        "remediation.created"
    ]

    assert analyst.get("/api/remediations").status_code == 403
    assert developer.get("/api/remediations").status_code == 403

    missing_evidence = ciso.post(
        f"/api/remediations/{created['id']}/transition",
        json={
            "expected_version": created["version"],
            "to_state": "verified_remediated",
            "rationale": "The fix was deployed.",
            "evidence_ids": [],
        },
    )
    assert missing_evidence.status_code == 409

    started_response = ciso.post(
        f"/api/remediations/{created['id']}/transition",
        json={
            "expected_version": created["version"],
            "to_state": "in_progress",
            "rationale": "A fix owner and target release are confirmed.",
            "evidence_ids": [],
        },
    )
    assert started_response.status_code == 200, started_response.text
    started = started_response.json()
    assert started["version"] == created["version"] + 1

    stale = ciso.post(
        f"/api/remediations/{created['id']}/transition",
        json={
            "expected_version": created["version"],
            "to_state": "failed",
            "rationale": "This stale command must not win.",
            "evidence_ids": [],
        },
    )
    assert stale.status_code == 412

    verified_response = ciso.post(
        f"/api/remediations/{created['id']}/transition",
        json={
            "expected_version": started["version"],
            "to_state": "verified_remediated",
            "rationale": "A clean verification run no longer reaches the sink.",
            "evidence_ids": ["review:rev-verification", "run:run-fixed"],
        },
    )
    assert verified_response.status_code == 200, verified_response.text
    verified = verified_response.json()
    assert verified["status"] == "verified_remediated"
    assert verified["evidence_ids"] == [
        "review:rev-verification",
        "run:run-fixed",
    ]
    assert [event["action"] for event in verified["events"]] == [
        "remediation.created",
        "remediation.in_progress",
        "remediation.verified_remediated",
    ]

    reopened = control_plane.load(str(state_dir)).remediation(created["id"])
    assert reopened["status"] == "verified_remediated"
    assert reopened["events"] == verified["events"]

    terminal = ciso.post(
        f"/api/remediations/{created['id']}/transition",
        json={
            "expected_version": verified["version"],
            "to_state": "failed",
            "rationale": "A terminal outcome cannot be reopened.",
            "evidence_ids": [],
        },
    )
    assert terminal.status_code == 409
