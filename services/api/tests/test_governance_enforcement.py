from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import auth, control_plane, main


@pytest.fixture
def clients(monkeypatch, tmp_path):
    store = control_plane.load(str(tmp_path))
    monkeypatch.setattr(main, "workflow_store", store)
    return {
        "base": tmp_path,
        "store": store,
        "analyst": TestClient(
            main.app,
            headers={auth.DEV_USER: "priya@example.com", auth.DEV_ROLE: "analyst"},
        ),
        "other_analyst": TestClient(
            main.app,
            headers={auth.DEV_USER: "sam@example.com", auth.DEV_ROLE: "analyst"},
        ),
        "ciso": TestClient(
            main.app,
            headers={auth.DEV_USER: "alex@example.com", auth.DEV_ROLE: "ciso"},
        ),
        "other_ciso": TestClient(
            main.app,
            headers={auth.DEV_USER: "morgan@example.com", auth.DEV_ROLE: "ciso"},
        ),
        "developer": TestClient(
            main.app,
            headers={auth.DEV_USER: "maya@example.com", auth.DEV_ROLE: "developer"},
        ),
    }


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
    )
    assert response.status_code == 201, response.text
    return response.json()


def _request_exception(analyst: TestClient, policy: dict, *, scope: str, expires_at: int) -> dict:
    response = analyst.post(
        "/api/exceptions",
        json={
            "policy_id": policy["id"],
            "policy_version": policy["active_version"],
            "scope": scope,
            "rationale": "A bounded migration window is required.",
            "compensating_controls": "Network isolation and daily scans.",
            "owner": "payments-platform",
            "owner_name": "Payments Platform",
            "evidence_ids": ["review:rev-1", "case:case-1"],
            "expires_at": expires_at,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve(ciso: TestClient, approval: dict) -> dict:
    response = ciso.post(
        f"/api/approvals/{approval['id']}/decision",
        json={
            "expected_version": approval["version"],
            "decision": "approve",
            "rationale": "The controls are measurable and time bounded.",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_production_policy_activation_requires_an_independent_ciso(clients, monkeypatch):
    ciso = clients["ciso"]
    other_ciso = clients["other_ciso"]
    policy = _create_policy(ciso)
    draft_response = ciso.post(
        f"/api/policies/{policy['id']}/versions",
        json={
            "expected_version": policy["version"],
            "severity_threshold": "medium",
            "denied_licenses": ["AGPL-3.0", "SSPL-1.0"],
            "block_on_unknown": True,
            "rationale": "Expand the governed threshold.",
        },
    )
    assert draft_response.status_code == 201, draft_response.text
    draft = draft_response.json()
    submitted_response = ciso.post(
        f"/api/policies/{policy['id']}/versions/2/submit",
        json={
            "expected_version": draft["version"],
            "rationale": "Ready for independent review.",
        },
    )
    assert submitted_response.status_code == 200, submitted_response.text
    submitted = submitted_response.json()
    candidate = submitted["versions"][0]
    assert candidate["submitted_by"] == "alex@example.com"
    assert candidate["submitted_at"] is not None

    monkeypatch.setattr(auth, "is_production", lambda: True)
    self_approval = ciso.post(
        f"/api/policies/{policy['id']}/versions/2/activate",
        json={
            "expected_version": submitted["version"],
            "rationale": "Attempt to approve my own policy change.",
        },
    )
    assert self_approval.status_code == 409
    assert "cannot activate" in self_approval.json()["detail"]

    activated_response = other_ciso.post(
        f"/api/policies/{policy['id']}/versions/2/activate",
        json={
            "expected_version": submitted["version"],
            "rationale": "Independent review completed.",
        },
    )
    assert activated_response.status_code == 200, activated_response.text
    activated = activated_response.json()
    assert activated["active_version"] == 2
    assert activated["current"]["activated_by"] == "morgan@example.com"
    assert activated["current"]["activated_at"] is not None

    author_updates = ciso.get("/api/notifications").json()["notifications"]
    assert any(item["kind"] == "policy.version_activated" for item in author_updates)


def test_production_new_policy_requires_independent_first_activation(clients, monkeypatch):
    ciso = clients["ciso"]
    other_ciso = clients["other_ciso"]
    monkeypatch.setattr(auth, "is_production", lambda: True)
    policy = _create_policy(ciso)
    assert policy["status"] == "pending"
    assert policy["active_version"] == 1
    assert policy["current"]["state"] == "in_review"
    assert policy["current"]["submitted_by"] == "alex@example.com"
    assert policy["current"]["activated_by"] is None
    reopened = control_plane.ControlPlane(str(clients["base"]))
    pending_after_restart = reopened.policy(policy["id"])
    assert pending_after_restart["status"] == "pending"
    assert pending_after_restart["current"]["state"] == "in_review"
    assert pending_after_restart["current"]["content_digest"] == policy["current"][
        "content_digest"
    ]
    assert pending_after_restart["current"]["activated_by"] is None

    self_activation = ciso.post(
        f"/api/policies/{policy['id']}/versions/1/activate",
        json={
            "expected_version": policy["version"],
            "rationale": "The creator must not activate the initial policy.",
        },
    )
    assert self_activation.status_code == 409
    independently_activated = other_ciso.post(
        f"/api/policies/{policy['id']}/versions/1/activate",
        json={
            "expected_version": policy["version"],
            "rationale": "Independent first-policy review passed.",
        },
    )
    assert independently_activated.status_code == 200, independently_activated.text
    activated = independently_activated.json()
    assert activated["status"] == "active"
    assert activated["active_version"] == 1
    assert activated["current"]["activated_by"] == "morgan@example.com"
    assert [event["action"] for event in activated["events"]] == [
        "policy.created",
        "policy.version_activated",
    ]


def test_exception_renewal_supersedes_only_after_approval_and_revocation_is_terminal(clients):
    analyst = clients["analyst"]
    other_analyst = clients["other_analyst"]
    ciso = clients["ciso"]
    developer = clients["developer"]
    now = int(time.time())
    policy = _create_policy(ciso)
    original_approval = _request_exception(
        analyst, policy, scope="payments/legacy-worker", expires_at=now + 7 * 86400,
    )
    _approve(ciso, original_approval)
    original_id = original_approval["resource_id"]
    original = ciso.get(f"/api/exceptions/{original_id}").json()
    assert original["status"] == "approved"

    renewal_payload = {
        "expected_version": original["version"],
        "rationale": "The vendor migration needs one final controlled window.",
        "compensating_controls": "Isolation, daily scans, and change freeze.",
        "owner": "payments-platform",
        "owner_name": "Payments Platform",
        "evidence_ids": ["case:case-2", "case:case-2"],
        "expires_at": now + 14 * 86400,
    }
    self_renewal = analyst.post(
        f"/api/exceptions/{original_id}/renew", json=renewal_payload,
    )
    assert self_renewal.status_code == 409
    assert "cannot renew" in self_renewal.json()["detail"]

    renewal_response = other_analyst.post(
        f"/api/exceptions/{original_id}/renew",
        json=renewal_payload,
        headers={"X-Correlation-ID": "test-exception-renew"},
    )
    assert renewal_response.status_code == 201, renewal_response.text
    renewal_approval = renewal_response.json()
    renewal_id = renewal_approval["resource_id"]
    renewal = ciso.get(f"/api/exceptions/{renewal_id}").json()
    assert renewal["status"] == "pending"
    assert renewal["predecessor_exception_id"] == original_id
    assert renewal["renewal_number"] == 1
    assert renewal["evidence_ids"] == ["case:case-2"]
    assert renewal["events"][0]["action"] == "exception.renewal_requested"
    assert renewal["events"][0]["correlation_id"] == "test-exception-renew"

    original_during_review = ciso.get(f"/api/exceptions/{original_id}").json()
    assert original_during_review["status"] == "approved"
    assert original_during_review["events"][-1]["action"] == "exception.renewal_started"

    duplicate = other_analyst.post(
        f"/api/exceptions/{original_id}/renew",
        json={
            "expected_version": original_during_review["version"],
            "rationale": "A duplicate renewal must not be created.",
            "compensating_controls": "Isolation.",
            "owner": "payments-platform",
            "evidence_ids": [],
            "expires_at": now + 15 * 86400,
        },
    )
    assert duplicate.status_code == 409

    approved_renewal = _approve(ciso, renewal_approval)
    assert approved_renewal["status"] == "approved"
    original_after = ciso.get(f"/api/exceptions/{original_id}").json()
    successor = ciso.get(f"/api/exceptions/{renewal_id}").json()
    assert original_after["status"] == "superseded"
    assert original_after["superseded_by_exception_id"] == renewal_id
    assert original_after["events"][-1]["action"] == "exception.superseded"
    assert successor["status"] == "approved"

    assert developer.post(
        f"/api/exceptions/{renewal_id}/revoke",
        json={
            "expected_version": successor["version"],
            "rationale": "A developer cannot revoke governance decisions.",
            "evidence_ids": [],
        },
    ).status_code == 403

    revoked_response = ciso.post(
        f"/api/exceptions/{renewal_id}/revoke",
        json={
            "expected_version": successor["version"],
            "rationale": "The migration completed ahead of schedule.",
            "evidence_ids": ["case:case-3"],
        },
        headers={"X-Correlation-ID": "test-exception-revoke"},
    )
    assert revoked_response.status_code == 200, revoked_response.text
    revoked = revoked_response.json()
    assert revoked["status"] == "revoked"
    assert revoked["revoked_by"] == "alex@example.com"
    assert revoked["revocation_rationale"] == "The migration completed ahead of schedule."
    assert revoked["events"][-1]["action"] == "exception.revoked"
    assert revoked["events"][-1]["correlation_id"] == "test-exception-revoke"

    reopened = control_plane.ControlPlane(str(clients["base"]))
    reopened_renewal = reopened.exception(renewal_id)
    assert reopened_renewal["request_digest"] == revoked["request_digest"]
    assert [event["action"] for event in reopened_renewal["events"]] == [
        "exception.renewal_requested",
        "exception.approved",
        "exception.revoked",
    ]

    stale = ciso.post(
        f"/api/exceptions/{renewal_id}/revoke",
        json={
            "expected_version": successor["version"],
            "rationale": "A stale duplicate must fail.",
            "evidence_ids": [],
        },
    )
    assert stale.status_code == 412


def test_expiry_reconciliation_is_materialized_idempotent_and_notifies_only_recipients(
    monkeypatch, tmp_path,
):
    clock = {"now": 1_800_000_000}
    monkeypatch.setattr(control_plane, "_now", lambda: clock["now"])
    store = control_plane.ControlPlane(str(tmp_path))
    policy = store.create_policy(
        name="Expiry policy",
        scope="production/*",
        severity_threshold="high",
        denied_licenses=[],
        block_on_unknown=True,
        rationale="Test durable expiry.",
        actor="alex@example.com",
        actor_name="Alex Morgan",
        actor_role="ciso",
        correlation_id="test-expiry-policy",
    )
    pending, pending_approval = store.create_exception(
        policy_id=policy["id"],
        policy_version=policy["active_version"],
        scope="payments/pending",
        rationale="Pending expiry test.",
        controls="Isolation.",
        owner="payments-platform",
        owner_name="Payments Platform",
        evidence_ids=["case:pending"],
        expires_at=clock["now"] + 100,
        actor="priya@example.com",
        actor_name="Priya Shah",
        actor_role="analyst",
        correlation_id="test-pending-expiry",
    )

    clock["now"] += 100
    first = store.reconcile_governance_expiry(now=clock["now"])
    assert first == {"approvals_expired": 1, "exceptions_expired": 1}
    assert store.approval(pending_approval["id"])["status"] == "expired"
    expired_pending = store.exception(pending["id"])
    assert expired_pending["status"] == "expired"
    assert expired_pending["events"][-1]["action"] == "exception.approval_expired"
    assert expired_pending["events"][-1]["actor_role"] == "system"

    second = store.reconcile_governance_expiry(now=clock["now"] + 1)
    assert second == {"approvals_expired": 0, "exceptions_expired": 0}
    assert len(store.exception(pending["id"])["events"]) == 2
    reopened = control_plane.ControlPlane(str(tmp_path))
    assert len(reopened.exception(pending["id"])["events"]) == 2
    status = store.governance_lifecycle_status()
    assert status["last_completed_at"] == clock["now"] + 1
    assert status["last_error"] is None

    requester_notifications = store.notifications(
        subject="priya@example.com", role="analyst",
    )
    unrelated_notifications = store.notifications(
        subject="sam@example.com", role="analyst",
    )
    ciso_notifications = store.notifications(
        subject="alex@example.com", role="ciso",
    )
    expiry = next(
        item for item in requester_notifications
        if item["kind"] == "exception.expired"
    )
    assert sum(
        item["kind"] == "exception.expired"
        for item in requester_notifications
    ) == 1
    assert expiry["resource_kind"] == "exception"
    assert expiry["route"] == "/analyst/queue"
    assert not any(item["kind"] == "exception.expired" for item in unrelated_notifications)
    assert any(item["kind"] == "exception.expired" for item in ciso_notifications)

    store.read_notification(
        expiry["id"], subject="priya@example.com", role="analyst",
    )
    reread = store.notifications(subject="priya@example.com", role="analyst")
    assert next(item for item in reread if item["id"] == expiry["id"])["read"] is True
    with pytest.raises(control_plane.Missing):
        store.read_notification(
            expiry["id"], subject="sam@example.com", role="analyst",
        )


def test_revocation_cancels_a_pending_renewal_atomically(clients):
    analyst = clients["analyst"]
    other_analyst = clients["other_analyst"]
    ciso = clients["ciso"]
    now = int(time.time())
    policy = _create_policy(ciso)
    approval = _request_exception(
        analyst, policy, scope="identity/legacy", expires_at=now + 7 * 86400,
    )
    _approve(ciso, approval)
    original_id = approval["resource_id"]
    original = ciso.get(f"/api/exceptions/{original_id}").json()
    renewal_response = other_analyst.post(
        f"/api/exceptions/{original_id}/renew",
        json={
            "expected_version": original["version"],
            "rationale": "Request one final migration window.",
            "compensating_controls": "Isolation and daily verification.",
            "owner": "identity-platform",
            "owner_name": "Identity Platform",
            "evidence_ids": ["case:identity"],
            "expires_at": now + 14 * 86400,
        },
    )
    assert renewal_response.status_code == 201, renewal_response.text
    renewal_approval = renewal_response.json()
    renewal_id = renewal_approval["resource_id"]
    original_after_request = ciso.get(f"/api/exceptions/{original_id}").json()

    revoke_response = ciso.post(
        f"/api/exceptions/{original_id}/revoke",
        json={
            "expected_version": original_after_request["version"],
            "rationale": "The legacy path was disabled.",
            "evidence_ids": ["case:identity-closed"],
        },
    )
    assert revoke_response.status_code == 200, revoke_response.text
    successor = ciso.get(f"/api/exceptions/{renewal_id}").json()
    cancelled_approval = ciso.get(
        f"/api/approvals/{renewal_approval['id']}"
    ).json()
    assert successor["status"] == "revoked"
    assert successor["events"][-1]["action"] == "exception.renewal_cancelled"
    assert cancelled_approval["status"] == "rejected"
    reopened = control_plane.ControlPlane(str(clients["base"]))
    reopened_successor = reopened.exception(renewal_id)
    assert reopened_successor["request_digest"] == successor["request_digest"]
    assert [event["action"] for event in reopened_successor["events"]] == [
        "exception.renewal_requested",
        "exception.renewal_cancelled",
    ]
    assert reopened.approval(renewal_approval["id"])["request_digest"] == successor[
        "request_digest"
    ]
    stale_decision = ciso.post(
        f"/api/approvals/{renewal_approval['id']}/decision",
        json={
            "expected_version": renewal_approval["version"],
            "decision": "approve",
            "rationale": "A cancelled renewal cannot be approved.",
        },
    )
    assert stale_decision.status_code == 412


def test_rejected_renewal_can_be_corrected_without_rewriting_history(clients):
    analyst = clients["analyst"]
    other_analyst = clients["other_analyst"]
    ciso = clients["ciso"]
    now = int(time.time())
    policy = _create_policy(ciso)
    approval = _request_exception(
        analyst, policy, scope="catalog/legacy", expires_at=now + 7 * 86400,
    )
    _approve(ciso, approval)
    original_id = approval["resource_id"]
    original = ciso.get(f"/api/exceptions/{original_id}").json()
    payload = {
        "expected_version": original["version"],
        "rationale": "First renewal requires correction.",
        "compensating_controls": "Isolation.",
        "owner": "catalog-platform",
        "evidence_ids": ["case:catalog"],
        "expires_at": now + 14 * 86400,
    }
    first_response = other_analyst.post(
        f"/api/exceptions/{original_id}/renew", json=payload,
    )
    assert first_response.status_code == 201, first_response.text
    first_approval = first_response.json()
    rejected = ciso.post(
        f"/api/approvals/{first_approval['id']}/decision",
        json={
            "expected_version": first_approval["version"],
            "decision": "reject",
            "rationale": "Add a stronger validation control.",
        },
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    current = ciso.get(f"/api/exceptions/{original_id}").json()
    corrected = other_analyst.post(
        f"/api/exceptions/{original_id}/renew",
        json={
            **payload,
            "expected_version": current["version"],
            "rationale": "Corrected renewal with stronger validation.",
            "compensating_controls": "Isolation and daily validation.",
            "evidence_ids": ["case:catalog", "review:catalog"],
        },
    )
    assert corrected.status_code == 201, corrected.text
    corrected_exception = ciso.get(
        f"/api/exceptions/{corrected.json()['resource_id']}"
    ).json()
    assert corrected_exception["renewal_number"] == 2
    assert corrected_exception["status"] == "pending"
    first_exception = ciso.get(
        f"/api/exceptions/{first_approval['resource_id']}"
    ).json()
    assert first_exception["status"] == "rejected"
    assert first_exception["renewal_number"] == 1
    reopened = control_plane.ControlPlane(str(clients["base"]))
    reopened_first = reopened.exception(first_exception["id"])
    assert reopened_first["request_digest"] == first_exception["request_digest"]
    assert [event["action"] for event in reopened_first["events"]] == [
        "exception.renewal_requested",
        "exception.rejected",
    ]


def test_governance_lifecycle_status_and_manual_reconcile_are_ciso_only(clients):
    assert clients["analyst"].get(
        "/api/governance/lifecycle/status"
    ).status_code == 403
    assert clients["developer"].post(
        "/api/governance/lifecycle/reconcile"
    ).status_code == 403
    reconciled = clients["ciso"].post("/api/governance/lifecycle/reconcile")
    assert reconciled.status_code == 200, reconciled.text
    body = reconciled.json()
    assert body["last_completed_at"] is not None
    assert body["last_error"] is None
    status = clients["ciso"].get("/api/governance/lifecycle/status")
    assert status.status_code == 200
    assert status.json()["last_counts"] == {
        "approvals_expired": 0,
        "exceptions_expired": 0,
    }


def test_exception_requester_cannot_revoke_their_own_approved_request(clients):
    ciso = clients["ciso"]
    other_ciso = clients["other_ciso"]
    policy = _create_policy(ciso)
    request = ciso.post(
        "/api/exceptions",
        json={
            "policy_id": policy["id"],
            "policy_version": policy["active_version"],
            "scope": "risk/temporary-control",
            "rationale": "A temporary control is required.",
            "compensating_controls": "Daily evidence review.",
            "owner": "risk-platform",
            "owner_name": "Risk Platform",
            "evidence_ids": ["case:risk"],
            "expires_at": int(time.time()) + 7 * 86400,
        },
    )
    assert request.status_code == 201, request.text
    approval = request.json()
    approved = _approve(other_ciso, approval)
    exception = ciso.get(f"/api/exceptions/{approved['resource_id']}").json()
    self_revoke = ciso.post(
        f"/api/exceptions/{exception['id']}/revoke",
        json={
            "expected_version": exception["version"],
            "rationale": "The requester must not terminate their own decision.",
            "evidence_ids": [],
        },
    )
    assert self_revoke.status_code == 409
    assert "cannot revoke" in self_revoke.json()["detail"]
    independent_revoke = other_ciso.post(
        f"/api/exceptions/{exception['id']}/revoke",
        json={
            "expected_version": exception["version"],
            "rationale": "Independent authority confirms early termination.",
            "evidence_ids": ["case:risk-closed"],
        },
    )
    assert independent_revoke.status_code == 200, independent_revoke.text
    assert independent_revoke.json()["status"] == "revoked"


def test_approved_exception_expiry_is_durable_across_restart(monkeypatch, tmp_path):
    clock = {"now": 1_900_000_000}
    monkeypatch.setattr(control_plane, "_now", lambda: clock["now"])
    store = control_plane.ControlPlane(str(tmp_path))
    policy = store.create_policy(
        name="Restart expiry policy",
        scope="production/*",
        severity_threshold="critical",
        denied_licenses=[],
        block_on_unknown=True,
        rationale="Test restart durability.",
        actor="alex@example.com",
        actor_name="Alex Morgan",
        actor_role="ciso",
        correlation_id="test-restart-policy",
    )
    exception, approval = store.create_exception(
        policy_id=policy["id"],
        policy_version=policy["active_version"],
        scope="payments/approved",
        rationale="Approved expiry test.",
        controls="Isolation.",
        owner="payments-platform",
        owner_name="Payments Platform",
        evidence_ids=["case:approved"],
        expires_at=clock["now"] + 100,
        actor="priya@example.com",
        actor_name="Priya Shah",
        actor_role="analyst",
        correlation_id="test-approved-expiry",
    )
    store.decide_approval(
        approval["id"],
        expected_version=approval["version"],
        decision="approve",
        rationale="Approve the bounded test window.",
        actor="alex@example.com",
        actor_name="Alex Morgan",
        actor_role="ciso",
        correlation_id="test-approved-decision",
    )

    clock["now"] += 100
    assert store.reconcile_governance_expiry(now=clock["now"]) == {
        "approvals_expired": 0,
        "exceptions_expired": 1,
    }
    expired = store.exception(exception["id"])
    assert expired["status"] == "expired"
    assert expired["events"][-1]["action"] == "exception.expired"

    reopened = control_plane.ControlPlane(str(tmp_path))
    after_restart = reopened.exception(exception["id"])
    assert after_restart["status"] == "expired"
    assert len(after_restart["events"]) == 3
    assert reopened.reconcile_governance_expiry(now=clock["now"] + 1) == {
        "approvals_expired": 0,
        "exceptions_expired": 0,
    }
    assert len(reopened.exception(exception["id"])["events"]) == 3


def test_free_text_owner_cannot_become_a_governance_notification_recipient(tmp_path):
    store = control_plane.ControlPlane(str(tmp_path))
    policy = store.create_policy(
        name="Notification isolation policy",
        scope="production/*",
        severity_threshold="high",
        denied_licenses=[],
        block_on_unknown=True,
        rationale="Verify notification authority.",
        actor="alex@example.com",
        actor_name="Alex Morgan",
        actor_role="ciso",
        correlation_id="test-notification-policy",
    )
    exception, approval = store.create_exception(
        policy_id=policy["id"],
        policy_version=policy["active_version"],
        scope="payments/unverified-owner",
        rationale="A bounded exception is required.",
        controls="Isolation.",
        owner="maya@example.com",
        owner_name="Maya Developer",
        evidence_ids=["case:notification"],
        expires_at=int(time.time()) + 86400,
        actor="priya@example.com",
        actor_name="Priya Shah",
        actor_role="analyst",
        correlation_id="test-notification-request",
    )
    store.decide_approval(
        approval["id"],
        expected_version=approval["version"],
        decision="approve",
        rationale="The controls are measurable.",
        actor="alex@example.com",
        actor_name="Alex Morgan",
        actor_role="ciso",
        correlation_id="test-notification-decision",
    )
    developer_notifications = store.notifications(
        subject="maya@example.com", role="developer",
    )
    requester_notifications = store.notifications(
        subject="priya@example.com", role="analyst",
    )
    assert not any(
        item["resource_id"] == exception["id"]
        for item in developer_notifications
    )
    assert any(
        item["resource_id"] == exception["id"]
        and item["kind"] == "exception.approved"
        for item in requester_notifications
    )
