from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from app import control_plane, engine_seed
from app.engine_gateway import EngineGateway
from meshagent.codegraph import CodeGraphRecorder


def _store(tmp_path) -> control_plane.ControlPlane:
    return control_plane.ControlPlane(str(tmp_path / "state"))


def _policy(store: control_plane.ControlPlane) -> dict:
    return store.create_policy(
        name="Dependency safety",
        scope="python services",
        severity_threshold="high",
        denied_licenses=["GPL-3.0"],
        block_on_unknown=True,
        rationale="Protect production dependencies.",
        actor="alex@company.com",
        actor_name="Alex",
        actor_role="ciso",
        correlation_id="corr-policy",
    )


def test_policy_and_exception_lifecycle_enqueue_canonical_evidence(tmp_path):
    store = _store(tmp_path)
    policy = _policy(store)
    policy = store.create_policy_version(
        policy["id"],
        expected_version=policy["version"],
        severity_threshold="medium",
        denied_licenses=["GPL-3.0", "AGPL-3.0"],
        block_on_unknown=True,
        rationale="Expand the governed boundary.",
        actor="alex@company.com",
        actor_name="Alex",
        actor_role="ciso",
        correlation_id="corr-version",
    )
    policy = store.submit_policy_version(
        policy["id"], 2,
        expected_version=policy["version"],
        rationale="Ready for independent review.",
        actor="alex@company.com",
        actor_name="Alex",
        actor_role="ciso",
        correlation_id="corr-submit",
    )
    policy = store.activate_policy_version(
        policy["id"], 2,
        expected_version=policy["version"],
        rationale="Approved after review.",
        actor="morgan@company.com",
        actor_name="Morgan",
        actor_role="ciso",
        correlation_id="corr-activate",
        require_independent_approver=True,
    )
    exception, approval = store.create_exception(
        policy_id=policy["id"],
        policy_version=2,
        scope="payments-service",
        rationale="Temporary compatibility window.",
        controls="Restricted network and increased monitoring.",
        owner="maya@company.com",
        owner_name="Maya",
        evidence_ids=["review:unknown-review", "case:unknown-case"],
        expires_at=control_plane._now() + 3600,
        actor="priya@company.com",
        actor_name="Priya",
        actor_role="analyst",
        correlation_id="corr-exception",
    )
    store.decide_approval(
        approval["id"],
        expected_version=approval["version"],
        decision="approve",
        rationale="Controls are sufficient for the stated window.",
        actor="alex@company.com",
        actor_name="Alex",
        actor_role="ciso",
        correlation_id="corr-decision",
    )
    exception = store.exception(exception["id"])
    store.revoke_exception(
        exception["id"],
        expected_version=exception["version"],
        rationale="The compatibility work completed early.",
        evidence_ids=["case:unknown-case"],
        actor="morgan@company.com",
        actor_name="Morgan",
        actor_role="ciso",
        correlation_id="corr-revoke",
    )

    with sqlite3.connect(store.path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM governance_projection_outbox ORDER BY created_at,projection_id"
        ).fetchall()
    kinds = {str(row["relation_kind"]) for row in rows}
    assert {
        "policy_version", "policy_activation", "policy_supersession",
        "exception_request", "exception_decision", "exception_revocation",
    } <= kinds
    assert len(rows) == len({str(row["projection_id"]) for row in rows})
    for row in rows:
        payload = json.loads(row["payload_json"])
        assert hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest() == row["payload_sha256"]
        assert payload["event_id"] == row["event_id"]
        assert payload["relation_kind"] == row["relation_kind"]
    request_payload = next(
        json.loads(row["payload_json"])
        for row in rows
        if row["resource_id"] == exception["id"]
        and row["relation_kind"] == "exception_request"
    )
    assert request_payload["exception"]["policy_digest"] == policy["current"]["content_digest"]
    assert request_payload["exception"]["request_digest"]
    assert all(not item["resolved"] for item in request_payload["resolved_evidence"])


def test_projection_integrity_retry_and_restart_backfill_are_idempotent(tmp_path):
    base = tmp_path / "state"
    store = control_plane.ControlPlane(str(base))
    policy = _policy(store)
    work = store.recoverable_governance_projections()[0]
    projection_id = work["projection_id"]
    store.mark_governance_projection_started(projection_id)
    store.mark_governance_projection_failed(projection_id, "engine unavailable")
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE governance_projection_outbox SET next_attempt_at=0 WHERE projection_id=?",
            (projection_id,),
        )
        before = conn.execute(
            "SELECT COUNT(*) FROM governance_projection_outbox"
        ).fetchone()[0]
    assert store.recoverable_governance_projections()

    reopened = control_plane.ControlPlane(str(base))
    with sqlite3.connect(reopened.path) as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM governance_projection_outbox"
        ).fetchone()[0]
        conn.execute(
            "UPDATE governance_projection_outbox SET payload_json='{}' "
            "WHERE projection_id=?", (projection_id,),
        )
    assert after == before
    with pytest.raises(control_plane.StoreError, match="payload digest mismatch"):
        reopened.verify_governance_projection(projection_id)
    with pytest.raises(control_plane.Missing):
        reopened.governance_evidence_projection("policy", policy["id"])


def _event(policy_id: str, event_id: str) -> dict:
    payload = {
        "schema_version": 1,
        "event_id": event_id,
        "resource_kind": "policy",
        "resource_id": policy_id,
        "relation_kind": "policy_activation",
        "action": "policy.version_activated",
        "from_state": "v1:active",
        "to_state": "v2:active",
        "actor": "morgan@company.com",
        "actor_name": "Morgan",
        "actor_role": "ciso",
        "rationale": "Independent review complete.",
        "evidence_ids": [],
        "occurred_at": 1_800_000_000,
        "correlation_id": f"corr-{event_id}",
        "policy": {
            "id": policy_id,
            "name": "Dependency safety",
            "scope": "python services",
            "version": 2,
            "version_state": "active",
            "content_digest": "a" * 64,
            "severity_threshold": "high",
            "denied_licenses": ["GPL-3.0"],
            "block_on_unknown": True,
            "version_rationale": "Tighten controls.",
            "created_by": "alex@company.com",
            "submitted_by": "alex@company.com",
            "activated_by": "morgan@company.com",
            "effective_from": 1_800_000_000,
            "effective_until": None,
            "predecessor_version": 1,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        **payload,
        "canonical_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "projection_id": f"{event_id}:policy_activation",
    }


def _exception_event(policy_id: str, exception_id: str, event_id: str) -> dict:
    payload = {
        "schema_version": 1,
        "event_id": event_id,
        "resource_kind": "exception",
        "resource_id": exception_id,
        "relation_kind": "exception_request",
        "action": "exception.requested",
        "from_state": None,
        "to_state": "pending",
        "actor": "priya@company.com",
        "actor_name": "Priya",
        "actor_role": "analyst",
        "rationale": "Temporary compatibility window.",
        "evidence_ids": ["review:rev-private"],
        "occurred_at": 1_800_000_100,
        "correlation_id": f"corr-{event_id}",
        "exception": {
            "id": exception_id,
            "policy_id": policy_id,
            "policy_version": 2,
            "policy_digest": "a" * 64,
            "request_digest": "c" * 64,
            "scope": "private-service",
            "owner": "maya@company.com",
            "owner_name": "Maya",
            "compensating_controls": "Restricted network.",
            "expires_at": 1_800_003_600,
            "requested_by": "priya@company.com",
            "approved_by": None,
            "decision_rationale": None,
            "predecessor_exception_id": None,
            "superseded_by_exception_id": None,
            "renewal_number": 0,
        },
        "resolved_evidence": [{
            "kind": "review", "resource_id": "rev-private",
            "native_ulid": None, "resolved": False,
        }],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        **payload,
        "canonical_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "projection_id": f"{event_id}:exception_request",
    }


def test_native_governance_projection_is_idempotent_and_resource_scoped(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "engine-state"))
    gateway = object.__new__(EngineGateway)
    gateway._governance = engine_seed.new_store("governance-test")
    gateway._codegraph_recorder = CodeGraphRecorder

    first = _event("pol-one", "pev-one")
    second = _event("pol-two", "pev-two")
    first_ulid = gateway.project_governance_event(event=first)
    assert gateway.project_governance_event(event=first) == first_ulid
    second_ulid = gateway.project_governance_event(event=second)
    assert second_ulid != first_ulid
    exception_ulid = gateway.project_governance_event(
        event=_exception_event("pol-one", "exc-private", "eev-private")
    )

    graph = gateway.governance_evidence_graph("policy", "pol-one")
    assert first_ulid in {relation.id for relation in graph.relations}
    assert second_ulid not in {relation.id for relation in graph.relations}
    assert exception_ulid not in {relation.id for relation in graph.relations}
    members = {member for relation in graph.relations for member in relation.members}
    assert "policy:pol-one" in members
    assert "policy:pol-two" not in members
    assert "exception:exc-private" not in members
    assert "scope:private-service" not in members
    assert "review:rev-private" not in members
    assert {relation.kind for relation in graph.relations} == {"policy_activation"}
    assert gateway._governance.get(first_ulid) is not None

    exception_graph = gateway.governance_evidence_graph(
        "exception", "exc-private"
    )
    assert exception_ulid in {
        relation.id for relation in exception_graph.relations
    }
    assert first_ulid in {relation.id for relation in exception_graph.relations}


def test_native_governance_graph_rejects_payload_tamper(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "engine-state"))
    gateway = object.__new__(EngineGateway)
    gateway._governance = engine_seed.new_store("governance-tamper")
    gateway._codegraph_recorder = CodeGraphRecorder
    event = _event("pol-tamper", "pev-tamper")
    event["canonical_sha256"] = "b" * 64
    with pytest.raises(Exception, match="digest verification"):
        gateway.project_governance_event(event=event)
