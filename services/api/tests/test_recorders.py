from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app import dpop, recorders
from app.auth import AuthError, Principal
from app.recorder_models import EnrollmentRequest, HeartbeatRequest


@pytest.fixture
def store(tmp_path):
    opened = recorders.load(str(tmp_path))
    yield opened
    opened.close()


def test_existing_recorder_database_gains_reported_configuration_column(tmp_path):
    migration = Path(recorders.__file__).with_name("recorder_migrations") / "001_recorder_control_plane.sql"
    old_schema = migration.read_text().replace(
        "  reported_config_version INTEGER NOT NULL DEFAULT 0,\n", "")
    database = tmp_path / "recorder-control.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(old_schema)
    opened = recorders.load(str(tmp_path))
    try:
        columns = {
            row[1] for row in opened._db.execute(
                "PRAGMA table_info(recorder_devices)"
            ).fetchall()
        }
        assert "reported_config_version" in columns
    finally:
        opened.close()


def admin() -> Principal:
    return Principal("admin-1", "Avery Admin", "avery@example.com", "platform_admin")


def developer() -> Principal:
    return Principal("maya-1", "Maya", "maya@example.com", "developer")


def request(key) -> EnrollmentRequest:
    return EnrollmentRequest(
        label="Maya work laptop", deployment="acme-prod", platform="darwin",
        platform_version="15.0", architecture="arm64", recorder_version="1.1.0",
        public_jwk=dpop.public_jwk(key.public_key()), attestation_format="none",
    )


def test_unverified_platform_attestation_is_rejected():
    key = ec.generate_private_key(ec.SECP256R1())
    body = request(key).model_dump()
    body["attestation_format"] = "linux_tpm"
    body["attestation"] = "client-asserted-evidence"
    with pytest.raises(ValueError, match="server verifier"):
        EnrollmentRequest.model_validate(body)


def test_pending_enrollment_capacity_is_bounded(store, monkeypatch):
    monkeypatch.setattr(recorders, "MAX_PENDING_ENROLLMENTS", 1)
    first_key = ec.generate_private_key(ec.SECP256R1())
    store.start_enrollment(request(first_key), "https://mesh.example/enroll")
    second_key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(recorders.Conflict, match="capacity"):
        store.start_enrollment(request(second_key), "https://mesh.example/enroll")


def test_enrollment_must_match_server_deployment(tmp_path):
    scoped = recorders.Store(str(tmp_path), deployment_id="customer-prod")
    try:
        key = ec.generate_private_key(ec.SECP256R1())
        with pytest.raises(recorders.Conflict, match="another deployment"):
            scoped.start_enrollment(request(key), "https://mesh.example/enroll")
    finally:
        scoped.close()


def make_proof(key, target, token=None, jti=None):
    claims = {
        "htm": "POST", "htu": target, "iat": int(time.time()),
        "jti": jti or str(uuid.uuid4()),
    }
    if token is not None:
        claims["ath"] = dpop.access_token_hash(token)
    return jwt.encode(
        claims, key, algorithm="ES256",
        headers={"typ": "dpop+jwt", "jwk": dpop.public_jwk(key.public_key())},
    )


def enroll(store):
    key = ec.generate_private_key(ec.SECP256R1())
    started = store.start_enrollment(request(key), "https://mesh.example/enroll")
    store.approve(started.user_code, developer())
    target = "https://mesh.example/api/v2/recorders/token"
    token = store.claim(
        started.device_code, proof=make_proof(key, target), method="POST", target=target,
    )
    return key, token


def test_enrollment_binds_approved_subject_and_p256_key(store):
    key, token = enroll(store)
    assert token.expires_in == 600
    assert token.trust_state == "active"
    detail = store.detail(token.device_id)
    assert detail.subject == "maya-1"
    assert detail.credential_kind == "dpop"
    assert detail.key_thumbprint == dpop.thumbprint(dpop.public_jwk(key.public_key()))


def test_dpop_proof_is_durable_single_use(store):
    key, issued = enroll(store)
    target = "https://mesh.example/api/v1/developer/sessions"
    jti = str(uuid.uuid4())
    made = make_proof(key, target, issued.access_token, jti)
    who = store.authenticate(token=issued.access_token, proof=made, method="POST", target=target)
    assert who.subject == "maya-1"
    with pytest.raises(AuthError):
        store.authenticate(token=issued.access_token, proof=made, method="POST", target=target)


def test_claim_rejects_a_key_other_than_the_enrolled_key(store):
    enrolled_key = ec.generate_private_key(ec.SECP256R1())
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    started = store.start_enrollment(request(enrolled_key), "https://mesh.example/enroll")
    store.approve(started.user_code, developer())
    target = "https://mesh.example/api/v2/recorders/token"
    with pytest.raises(AuthError):
        store.claim(
            started.device_code,
            proof=make_proof(attacker_key, target),
            method="POST",
            target=target,
        )


def test_stolen_token_without_the_device_key_is_rejected(store):
    _, issued = enroll(store)
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    target = "https://mesh.example/api/v1/developer/sessions"
    with pytest.raises(AuthError):
        store.authenticate(
            token=issued.access_token,
            proof=make_proof(attacker_key, target, issued.access_token),
            method="POST",
            target=target,
        )


def test_expired_access_token_is_rejected_and_refresh_rotates_token(store):
    key, issued = enroll(store)
    row = store._device_row(issued.device_id)
    expired = store.authority.mint_access_token(
        device_id=row["id"], subject=row["subject"], name=row["name"],
        email=row["email"], verified=bool(row["verified"]),
        key_thumbprint=row["key_thumbprint"], now=int(time.time()) - 1_000,
    )
    target = "https://mesh.example/api/v1/developer/sessions"
    with pytest.raises(AuthError):
        store.authenticate(
            token=expired, proof=make_proof(key, target, expired),
            method="POST", target=target,
        )
    refresh_target = "https://mesh.example/api/v2/recorders/token"
    refreshed = store.refresh_token(
        issued.device_id,
        proof=make_proof(key, refresh_target),
        method="POST", target=refresh_target,
    )
    assert refreshed.access_token != issued.access_token
    assert refreshed.expires_in == 600


def test_signed_configuration_and_content_free_heartbeat(store, monkeypatch):
    _, issued = enroll(store)
    config = store.signed_config(issued.device_id)
    assert config.kid == issued.config_signing_kid
    assert config.payload.device_id == issued.device_id
    assert len(config.digest_sha256) == 64
    assert config.payload.expires_at - config.payload.issued_at == 86_400
    repeated = store.signed_config(issued.device_id)
    assert repeated == config
    monkeypatch.setattr(recorders, "_now", lambda: config.payload.expires_at - 299)
    renewed = store.signed_config(issued.device_id)
    assert renewed.payload.version == config.payload.version + 1
    assert renewed.digest_sha256 != config.digest_sha256
    receipt = store.heartbeat(issued.device_id, HeartbeatRequest(
        recorder_version="1.1.0", platform="darwin", platform_version="15.0",
        architecture="arm64", config_version=1, adapter_state="ready",
        queue_batches=3, queue_bytes=4096, oldest_queued_age_seconds=12,
        delivery_state="healthy",
    ))
    assert receipt.trust_state == "active"
    detail = store.detail(issued.device_id)
    assert detail.queue_batches == 3
    assert detail.config_version == 2
    assert detail.reported_config_version == 1
    assert detail.config_status == "stale"
    assert store.list().action_required == 1
    with pytest.raises(recorders.Conflict):
        store.heartbeat(issued.device_id, HeartbeatRequest(
            recorder_version="1.1.0", platform="darwin", platform_version="15.0",
            architecture="arm64", config_version=3, adapter_state="ready",
            queue_batches=3, queue_bytes=4096, oldest_queued_age_seconds=12,
            delivery_state="healthy",
        ))
    assert not hasattr(detail, "repository")
    assert not hasattr(detail, "prompt")


def test_trust_mutation_is_versioned_idempotent_and_immediate(store):
    key, issued = enroll(store)
    first = store.transition(
        device_id=issued.device_id, action="quarantine", expected_version=1,
        reason="Unexpected recorder binary", actor=admin(), idempotency_key="idem-12345678",
        correlation_id="corr-1",
    )
    replay = store.transition(
        device_id=issued.device_id, action="quarantine", expected_version=1,
        reason="Unexpected recorder binary", actor=admin(), idempotency_key="idem-12345678",
        correlation_id="corr-other",
    )
    assert replay.receipt_id == first.receipt_id
    with pytest.raises(recorders.Conflict):
        store.transition(
            device_id=issued.device_id, action="quarantine", expected_version=1,
            reason="Different reason", actor=admin(), idempotency_key="idem-12345678",
            correlation_id="corr-2",
        )
    with pytest.raises(recorders.TrustDenied):
        store.authenticate(
            token=issued.access_token,
            proof=make_proof(key, "https://mesh.example/api/v1/developer/sessions", issued.access_token),
            method="POST", target="https://mesh.example/api/v1/developer/sessions",
        )
    revoked = store.transition(
        device_id=issued.device_id, action="revoke", expected_version=2,
        reason="Device retired", actor=admin(), idempotency_key="idem-87654321",
        correlation_id="corr-3",
    )
    assert revoked.next_state == "revoked"


def test_stale_version_is_rejected_without_mutation(store):
    _, issued = enroll(store)
    with pytest.raises(recorders.VersionConflict):
        store.transition(
            device_id=issued.device_id, action="quarantine", expected_version=9,
            reason="Stale browser", actor=admin(), idempotency_key="idem-stale-01",
            correlation_id="corr",
        )
    assert store.detail(issued.device_id).trust_state == "active"
