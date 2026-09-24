from __future__ import annotations

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app import auth, control_plane, main


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
    return analyst, ciso


def _create_policy(ciso: TestClient) -> dict:
    response = ciso.post(
        "/api/policies",
        json={
            "name": "Production dependency policy",
            "scope": "production/*",
            "severity_threshold": "high",
            "denied_licenses": ["AGPL-3.0"],
            "block_on_unknown": True,
            "rationale": "Production dependencies require a governed baseline.",
        },
        headers={"X-Correlation-ID": "test-policy-create"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_policy_versions_have_digests_events_and_optimistic_lifecycle(clients):
    analyst, ciso = clients
    policy = _create_policy(ciso)

    assert policy["status"] == "active"
    assert policy["version"] == 1
    assert policy["active_version"] == 1
    assert policy["current"]["state"] == "active"
    assert len(policy["current"]["content_digest"]) == 64
    assert policy["events"][0]["action"] == "policy.created"
    assert policy["events"][0]["correlation_id"] == "test-policy-create"
    assert analyst.get(f"/api/policies/{policy['id']}").status_code == 200
    assert analyst.post(f"/api/policies/{policy['id']}/versions", json={}).status_code == 403

    draft_response = ciso.post(
        f"/api/policies/{policy['id']}/versions",
        json={
            "expected_version": policy["version"],
            "severity_threshold": "medium",
            "denied_licenses": ["AGPL-3.0", "SSPL-1.0", "SSPL-1.0"],
            "block_on_unknown": True,
            "rationale": "Expand the production enforcement threshold.",
        },
    )
    assert draft_response.status_code == 201, draft_response.text
    draft = draft_response.json()
    assert draft["version"] == 2
    assert draft["active_version"] == 1
    assert draft["versions"][0]["version"] == 2
    assert draft["versions"][0]["state"] == "draft"
    assert draft["versions"][0]["denied_licenses"] == ["AGPL-3.0", "SSPL-1.0"]

    stale = ciso.post(
        f"/api/policies/{policy['id']}/versions",
        json={
            "expected_version": policy["version"],
            "severity_threshold": "low",
            "denied_licenses": [],
            "block_on_unknown": False,
            "rationale": "This write is based on stale state.",
        },
    )
    assert stale.status_code == 412

    activate_draft = ciso.post(
        f"/api/policies/{policy['id']}/versions/2/activate",
        json={"expected_version": draft["version"], "rationale": "Skip review."},
    )
    assert activate_draft.status_code == 409

    submitted_response = ciso.post(
        f"/api/policies/{policy['id']}/versions/2/submit",
        json={
            "expected_version": draft["version"],
            "rationale": "The candidate is ready for controlled activation.",
        },
    )
    assert submitted_response.status_code == 200, submitted_response.text
    submitted = submitted_response.json()
    assert submitted["version"] == 3
    assert submitted["versions"][0]["state"] == "in_review"

    activated_response = ciso.post(
        f"/api/policies/{policy['id']}/versions/2/activate",
        json={
            "expected_version": submitted["version"],
            "rationale": "The reviewed candidate becomes the enforced version.",
        },
    )
    assert activated_response.status_code == 200, activated_response.text
    activated = activated_response.json()
    assert activated["version"] == 4
    assert activated["active_version"] == 2
    assert activated["current"]["state"] == "active"
    assert activated["versions"][1]["state"] == "superseded"
    assert [event["action"] for event in activated["events"]] == [
        "policy.created",
        "policy.version_created",
        "policy.version_submitted",
        "policy.version_activated",
    ]

    unfinished_response = ciso.post(
        f"/api/policies/{policy['id']}/versions",
        json={
            "expected_version": activated["version"],
            "severity_threshold": "low",
            "denied_licenses": [],
            "block_on_unknown": False,
            "rationale": "Evaluate a less restrictive candidate without activating it.",
        },
    )
    assert unfinished_response.status_code == 201, unfinished_response.text
    unfinished = unfinished_response.json()
    blocked_retirement = ciso.post(
        f"/api/policies/{policy['id']}/retire",
        json={
            "expected_version": unfinished["version"],
            "rationale": "Retirement must not strand an unfinished version.",
        },
    )
    assert blocked_retirement.status_code == 409

    withdrawn_response = ciso.post(
        f"/api/policies/{policy['id']}/versions/3/withdraw",
        json={
            "expected_version": unfinished["version"],
            "rationale": "The candidate will not proceed to review.",
        },
    )
    assert withdrawn_response.status_code == 200, withdrawn_response.text
    withdrawn = withdrawn_response.json()
    assert withdrawn["version"] == 6
    assert withdrawn["versions"][0]["state"] == "withdrawn"
    assert withdrawn["events"][-1]["action"] == "policy.version_withdrawn"

    retired_response = ciso.post(
        f"/api/policies/{policy['id']}/retire",
        json={
            "expected_version": withdrawn["version"],
            "rationale": "This control has been replaced by a successor policy.",
        },
    )
    assert retired_response.status_code == 200, retired_response.text
    retired = retired_response.json()
    assert retired["status"] == "retired"
    assert retired["current"]["state"] == "retired"
    assert retired["events"][-1]["action"] == "policy.retired"


def test_exception_binds_exact_policy_version_digest_evidence_and_decision(clients):
    analyst, ciso = clients
    policy = _create_policy(ciso)
    request = {
        "policy_id": policy["id"],
        "policy_version": policy["active_version"],
        "scope": "payments/legacy-worker",
        "rationale": "A vendor upgrade requires a controlled migration window.",
        "compensating_controls": "Network isolation and daily dependency scans.",
        "owner": "payments-platform",
        "owner_name": "Payments Platform",
        "evidence_ids": ["review:rev-1", "case:case-1", "review:rev-1"],
        "expires_at": int(time.time()) + 7 * 86400,
    }
    requested = analyst.post(
        "/api/exceptions",
        json=request,
        headers={"X-Correlation-ID": "test-exception-request"},
    )
    assert requested.status_code == 201, requested.text
    approval = requested.json()
    assert approval["resource_version"] == 1
    assert approval["evidence_ids"] == ["review:rev-1", "case:case-1"]
    assert len(approval["request_digest"]) == 64

    exception_id = approval["resource_id"]
    assert analyst.get(f"/api/exceptions/{exception_id}").status_code == 403
    exception = ciso.get(f"/api/exceptions/{exception_id}").json()
    assert exception["policy_version"] == policy["active_version"]
    assert exception["policy_digest"] == policy["current"]["content_digest"]
    assert exception["request_digest"] == approval["request_digest"]
    assert exception["owner_name"] == "Payments Platform"
    assert exception["events"][0]["action"] == "exception.requested"
    assert exception["events"][0]["correlation_id"] == "test-exception-request"

    duplicate = analyst.post("/api/exceptions", json=request)
    assert duplicate.status_code == 409
    assert "active exception already exists" in duplicate.json()["detail"]

    decision_response = ciso.post(
        f"/api/approvals/{approval['id']}/decision",
        json={
            "expected_version": approval["version"],
            "decision": "approve",
            "rationale": "The controls are measurable and the expiry is bounded.",
        },
        headers={"X-Correlation-ID": "test-exception-approve"},
    )
    assert decision_response.status_code == 200, decision_response.text
    decision = decision_response.json()
    assert decision["status"] == "approved"
    assert decision["approver_name"] == "alex@example.com"

    decided = ciso.get(f"/api/exceptions/{exception_id}").json()
    assert decided["status"] == "approved"
    assert decided["version"] == 2
    assert decided["approved_by"] == "alex@example.com"
    assert decided["decision_rationale"] == decision["decision_rationale"]
    assert decided["events"][-1]["action"] == "exception.approved"
    assert decided["events"][-1]["correlation_id"] == "test-exception-approve"

    stale_decision = ciso.post(
        f"/api/approvals/{approval['id']}/decision",
        json={
            "expected_version": approval["version"],
            "decision": "reject",
            "rationale": "This is based on stale approval state.",
        },
    )
    assert stale_decision.status_code == 412


def test_legacy_governance_rows_are_upgraded_without_data_loss(tmp_path):
    base = tmp_path / "legacy-control-plane"
    base.mkdir()
    database = base / "control-plane.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            CREATE TABLE policies (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, scope TEXT NOT NULL,
              status TEXT NOT NULL, active_version INTEGER NOT NULL,
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
              created_by TEXT NOT NULL
            );
            CREATE TABLE policy_versions (
              policy_id TEXT NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
              version INTEGER NOT NULL, severity_threshold TEXT NOT NULL,
              denied_licenses TEXT NOT NULL, block_on_unknown INTEGER NOT NULL,
              rationale TEXT NOT NULL, created_at INTEGER NOT NULL,
              created_by TEXT NOT NULL, PRIMARY KEY(policy_id, version)
            );
            CREATE TABLE exceptions (
              id TEXT PRIMARY KEY, policy_id TEXT NOT NULL, scope TEXT NOT NULL,
              rationale TEXT NOT NULL, compensating_controls TEXT NOT NULL,
              owner TEXT NOT NULL, expires_at INTEGER NOT NULL, status TEXT NOT NULL,
              version INTEGER NOT NULL, requested_by TEXT NOT NULL, approved_by TEXT,
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE TABLE approvals (
              id TEXT PRIMARY KEY, kind TEXT NOT NULL, resource_id TEXT NOT NULL,
              requester TEXT NOT NULL, requester_name TEXT NOT NULL,
              status TEXT NOT NULL, rationale TEXT NOT NULL, approver TEXT,
              decision_rationale TEXT, version INTEGER NOT NULL,
              expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
              decided_at INTEGER
            );
            """
        )
        conn.execute(
            "INSERT INTO policies VALUES (?,?,?,?,?,?,?,?)",
            ("pol_legacy", "Legacy policy", "legacy/*", "active", 2, 10, 20, "alex@example.com"),
        )
        conn.execute(
            "INSERT INTO policy_versions VALUES (?,?,?,?,?,?,?,?)",
            ("pol_legacy", 1, "high", '["AGPL-3.0"]', 1, "Legacy rationale", 10, "alex@example.com"),
        )
        conn.execute(
            "INSERT INTO policy_versions VALUES (?,?,?,?,?,?,?,?)",
            ("pol_legacy", 2, "medium", '["AGPL-3.0","SSPL-1.0"]', 1, "Legacy revision", 15, "alex@example.com"),
        )
        conn.execute(
            "INSERT INTO exceptions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "exc_legacy", "pol_legacy", "legacy/service", "Migration window",
                "Network isolation", "legacy-team", int(time.time()) + 86400,
                "approved", 2, "priya@example.com", "alex@example.com", 11, 20,
            ),
        )
        conn.execute(
            "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "apr_legacy", "exception", "exc_legacy", "priya@example.com",
                "Priya Shah", "approved", "Migration window", "alex@example.com",
                "Approved before migration", 2, int(time.time()) + 3600, 11, 20,
            ),
        )

    store = control_plane.ControlPlane(str(base))
    policy = store.policy("pol_legacy")
    assert policy["version"] == 1
    assert policy["current"]["state"] == "active"
    assert policy["current"]["version"] == 2
    assert policy["versions"][1]["state"] == "superseded"
    assert len(policy["current"]["content_digest"]) == 64
    assert policy["events"][0]["action"] == "policy.created"

    exception = store.exception("exc_legacy")
    approval = store.approval("apr_legacy")
    assert exception["policy_version"] == 2
    assert exception["policy_digest"] == policy["current"]["content_digest"]
    assert exception["request_digest"] == approval["request_digest"]
    assert [event["action"] for event in exception["events"]] == [
        "exception.requested",
        "exception.approved",
    ]

    with sqlite3.connect(database) as conn:
        conn.execute(
            "DELETE FROM exception_events WHERE exception_id=? AND action=?",
            ("exc_legacy", "exception.approved"),
        )

    repaired = control_plane.ControlPlane(str(base))
    policy_after_restart = repaired.policy("pol_legacy")
    exception_after_restart = repaired.exception("exc_legacy")
    assert [version["state"] for version in policy_after_restart["versions"]] == [
        "active",
        "superseded",
    ]
    assert len(policy_after_restart["events"]) == 1
    assert len(exception_after_restart["events"]) == 2

    reopened = control_plane.ControlPlane(str(base))
    assert len(reopened.policy("pol_legacy")["events"]) == 1
    assert len(reopened.exception("exc_legacy")["events"]) == 2
