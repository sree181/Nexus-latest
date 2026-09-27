from __future__ import annotations

import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app import auth, devices, dpop, main, recorders

DEV = {auth.DEV_USER: "maya@example.com", auth.DEV_ROLE: "developer"}
ADMIN = {auth.DEV_USER: "avery@example.com", auth.DEV_ROLE: "platform_admin"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    registry = recorders.load(str(tmp_path))
    monkeypatch.setattr(main, "recorder_store", registry)
    monkeypatch.setattr(main, "device_store", devices.load(str(tmp_path)))
    with TestClient(main.app) as opened:
        yield opened
    registry.close()


def proof(key, target, token=None, jti=None, method="POST"):
    claims = {
        "htm": method, "htu": target, "iat": int(time.time()),
        "jti": jti or str(uuid.uuid4()),
    }
    if token:
        claims["ath"] = dpop.access_token_hash(token)
    return jwt.encode(
        claims, key, algorithm="ES256",
        headers={"typ": "dpop+jwt", "jwk": dpop.public_jwk(key.public_key())},
    )


def enroll(client):
    key = ec.generate_private_key(ec.SECP256R1())
    body = {
        "label": "Maya work laptop", "deployment": "acme-prod",
        "platform": "darwin", "platform_version": "15.0",
        "architecture": "arm64", "recorder_version": "1.1.0",
        "public_jwk": dpop.public_jwk(key.public_key()),
        "attestation_format": "none",
    }
    started = client.post("/api/v2/recorders/enrollments", json=body)
    assert started.status_code == 201, started.text
    start = started.json()
    assert start["verify_url"] == (
        "http://localhost:5173/connect/recorder?code=" + start["user_code"]
    )
    waiting_target = "http://testserver/api/v2/recorders/token"
    waiting = client.post(
        "/api/v2/recorders/token",
        headers={"DPoP": proof(key, waiting_target)},
        json={"device_code": start["device_code"]},
    )
    assert waiting.status_code == 409
    assert client.get(
        f"/api/v2/recorders/enrollments/{start['user_code']}", headers=ADMIN,
    ).status_code == 403
    assert client.post(
        "/api/v2/recorders/enrollments/approve",
        headers=ADMIN, json={"user_code": start["user_code"]},
    ).status_code == 403
    pending = client.get(
        f"/api/v2/recorders/enrollments/{start['user_code']}", headers=DEV,
    )
    assert pending.status_code == 200
    approved = client.post(
        "/api/v2/recorders/enrollments/approve",
        headers=DEV, json={"user_code": start["user_code"]},
    )
    assert approved.status_code == 200
    target = "http://testserver/api/v2/recorders/token"
    claimed = client.post(
        "/api/v2/recorders/token",
        headers={"DPoP": proof(key, target)},
        json={"device_code": start["device_code"]},
    )
    assert claimed.status_code == 200, claimed.text
    return key, claimed.json()


def dpop_headers(key, token, target, method="POST"):
    return {
        "Authorization": f"DPoP {token}",
        "DPoP": proof(key, target, token, method=method),
    }


def test_enrollment_configuration_heartbeat_and_content_free_admin_detail(client):
    key, issued = enroll(client)
    config = client.get(
        "/api/v2/recorders/config",
        headers=dpop_headers(key, issued["access_token"], "http://testserver/api/v2/recorders/config", method="GET"),
    )
    assert config.status_code == 200, config.text
    assert config.json()["kid"] == issued["config_signing_kid"]
    heartbeat = client.post(
        "/api/v2/recorders/heartbeat",
        headers=dpop_headers(key, issued["access_token"], "http://testserver/api/v2/recorders/heartbeat"),
        json={
            "recorder_version": "1.1.0", "platform": "darwin",
            "platform_version": "15.0", "architecture": "arm64",
            "config_version": 1, "adapter_state": "ready",
            "queue_batches": 0, "queue_bytes": 0,
            "oldest_queued_age_seconds": 0, "delivery_state": "healthy",
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    detail = client.get(f"/api/v2/recorders/{issued['device_id']}", headers=ADMIN)
    assert detail.status_code == 200
    assert detail.json()["config_version"] == 1
    assert detail.json()["reported_config_version"] == 1
    assert detail.json()["config_status"] == "current"
    serialized = detail.text.lower()
    for forbidden in ("prompt", "repository", "source_path", "command", "package_name", "session_id"):
        assert forbidden not in serialized


def test_platform_admin_isolated_and_quarantine_denies_the_next_write(client):
    key, issued = enroll(client)
    identity = client.get("/api/me", headers=ADMIN)
    assert identity.status_code == 200
    assert identity.json()["role"] == "platform_admin"
    assert identity.json()["capabilities"] == [
        "recorder.admin.read", "recorder.trust.write",
    ]
    assert client.get("/api/v2/recorders", headers=ADMIN).status_code == 200
    assert client.get("/api/fleet/overview", headers=ADMIN).status_code == 403
    assert client.post(
        "/api/v1/developer/sessions",
        headers=ADMIN,
        json={
            "protocol": "meshagent.session.v1",
            "session_id": "ses_platform_admin_denied",
            "adapter": "cursor",
            "adapter_version": "1.0.0",
            "source_session_id": "platform-admin-denied",
            "repository": {"id": "repo_denied", "name": "denied"},
            "task": "must not be recorded",
            "started_at_ms": 1,
        },
    ).status_code == 403
    for role in (DEV, {auth.DEV_USER: "priya@example.com", auth.DEV_ROLE: "analyst"},
                 {auth.DEV_USER: "alex@example.com", auth.DEV_ROLE: "ciso"}):
        assert client.get("/api/v2/recorders", headers=role).status_code == 403
    action = client.post(
        f"/api/v2/recorders/{issued['device_id']}/quarantine",
        headers={**ADMIN, "Idempotency-Key": "quarantine-demo-01"},
        json={"expected_version": 1, "reason": "Unexpected recorder binary"},
    )
    assert action.status_code == 200, action.text
    assert action.json()["next_state"] == "quarantined"
    denied = client.post(
        "/api/v2/recorders/heartbeat",
        headers=dpop_headers(key, issued["access_token"], "http://testserver/api/v2/recorders/heartbeat"),
        json={
            "recorder_version": "1.1.0", "platform": "darwin",
            "platform_version": "15.0", "architecture": "arm64",
            "config_version": 1, "adapter_state": "ready",
            "queue_batches": 1, "queue_bytes": 100,
            "oldest_queued_age_seconds": 1, "delivery_state": "blocked",
        },
    )
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == "DPoP"


def test_platform_admin_route_matrix_is_content_free(client):
    pairing = client.post("/api/devices/pair", json={"label": "legacy laptop"})
    assert pairing.status_code == 200
    user_code = pairing.json()["user_code"]
    denied = (
        client.get("/api/runs", headers=ADMIN),
        client.post("/api/runs", headers=ADMIN, json={"task": "must not exist"}),
        client.get("/api/runs/nonexistent", headers=ADMIN),
        client.get("/api/runs/nonexistent/graph", headers=ADMIN),
        client.get("/api/v1/developer/sessions", headers=ADMIN),
        client.get("/api/v1/developer/sessions/nonexistent", headers=ADMIN),
        client.get("/api/v1/developer/sessions/nonexistent/events", headers=ADMIN),
        client.get("/api/v1/developer/sessions/nonexistent/policy-evaluations", headers=ADMIN),
        client.get("/api/devices", headers=ADMIN),
        client.get(f"/api/devices/pending/{user_code}", headers=ADMIN),
        client.post("/api/devices/approve", headers=ADMIN, json={"user_code": user_code}),
        client.get("/api/operations/work", headers=ADMIN),
        client.get("/api/cases", headers=ADMIN),
        client.get("/api/reviews", headers=ADMIN),
        client.get("/api/approvals", headers=ADMIN),
        client.get("/api/notifications", headers=ADMIN),
        client.post("/api/notifications/nonexistent/read", headers=ADMIN),
    )
    assert [response.status_code for response in denied] == [403] * len(denied)


def test_revoked_recorder_fails_its_next_content_write_and_stale_admin_write(client):
    key, issued = enroll(client)
    device_id = issued["device_id"]
    stale = client.post(
        f"/api/v2/recorders/{device_id}/quarantine",
        headers={**ADMIN, "Idempotency-Key": "stale-version-01"},
        json={"expected_version": 9, "reason": "Stale administrator view"},
    )
    assert stale.status_code == 412
    revoked = client.post(
        f"/api/v2/recorders/{device_id}/revoke",
        headers={**ADMIN, "Idempotency-Key": "revoke-device-01"},
        json={"expected_version": 1, "reason": "Device retired"},
    )
    assert revoked.status_code == 200
    target = "http://testserver/api/v1/developer/sessions"
    denied = client.post(
        "/api/v1/developer/sessions",
        headers=dpop_headers(key, issued["access_token"], target),
        json={
            "id": "ses_1234567890abcdef",
            "source_session_id": "cursor-session-1",
            "source_event_id": "cursor-event-1",
            "adapter": "cursor",
            "adapter_version": "1",
            "repository": {"id": "repo-stage1b", "name": "service", "remote": None, "branch": "main", "commit": None},
            "task": "Verify trust revocation",
            "started_at_ms": 1_700_000_000_000,
            "sequence": 1,
        },
    )
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == "DPoP"


def test_production_dpop_target_uses_the_public_origin(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setenv("MESHAGENT_ENV", "production")
    monkeypatch.setenv("MESHAGENT_WEB_URL", "https://mesh.example")
    scope = {
        "type": "http", "method": "POST", "scheme": "http",
        "server": ("api", 8000), "path": "/api/v2/recorders/token",
        "raw_path": b"/api/v2/recorders/token", "query_string": b"",
        "headers": [(b"host", b"api:8000")],
    }
    assert main._dpop_target(Request(scope)) == "https://mesh.example/api/v2/recorders/token"
