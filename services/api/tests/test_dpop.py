from __future__ import annotations

import hashlib
import os
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app import dpop
from app.auth import AuthError


def proof(key, *, method="POST", target="https://mesh.example/api/v2/recorders/token", token=None, jti=None, now=None):
    public = dpop.public_jwk(key.public_key())
    claims = {
        "htm": method,
        "htu": target,
        "iat": int(time.time()) if now is None else now,
        "jti": jti or str(uuid.uuid4()),
    }
    if token is not None:
        claims["ath"] = dpop.access_token_hash(token)
    return jwt.encode(claims, key, algorithm="ES256", headers={"typ": "dpop+jwt", "jwk": public})


def test_thumbprint_is_stable_and_public_only():
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = dpop.public_jwk(key.public_key())
    assert dpop.thumbprint(jwk) == dpop.thumbprint(dict(reversed(list(jwk.items()))))
    assert len(dpop.thumbprint(jwk)) == 43
    assert "d" not in jwk


def test_valid_proof_binds_method_target_and_access_token():
    key = ec.generate_private_key(ec.SECP256R1())
    token = "short-lived-token"
    made = proof(key, token=token)
    verified = dpop.verify_proof(
        made, method="POST", target="https://mesh.example/api/v2/recorders/token",
        access_token=token, expected_thumbprint=dpop.thumbprint(dpop.public_jwk(key.public_key())),
    )
    assert verified.key_thumbprint


@pytest.mark.parametrize("method,target,token", [
    ("GET", "https://mesh.example/api/v2/recorders/token", "short-lived-token"),
    ("POST", "https://mesh.example/api/v2/recorders/config", "short-lived-token"),
    ("POST", "https://mesh.example/api/v2/recorders/token", "other-token"),
])
def test_proof_rejects_binding_mismatch(method, target, token):
    key = ec.generate_private_key(ec.SECP256R1())
    made = proof(key, token="short-lived-token")
    with pytest.raises(AuthError, match="proof rejected"):
        dpop.verify_proof(made, method=method, target=target, access_token=token)


def test_proof_rejects_stale_issued_at():
    key = ec.generate_private_key(ec.SECP256R1())
    made = proof(key, now=int(time.time()) - dpop.PROOF_SKEW - 1)
    with pytest.raises(AuthError):
        dpop.verify_proof(
            made, method="POST", target="https://mesh.example/api/v2/recorders/token",
            access_token=None,
        )


def test_access_token_hash_is_base64url_sha256():
    expected = dpop._b64(hashlib.sha256(b"abc").digest())
    assert dpop.access_token_hash("abc") == expected


def test_production_signing_key_requires_private_regular_file(tmp_path, monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    body = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    insecure = tmp_path / "insecure.pem"
    insecure.write_bytes(body)
    insecure.chmod(0o644)
    monkeypatch.setenv("MESHAGENT_ENV", "production")
    monkeypatch.setenv("MESHAGENT_RECORDER_SIGNING_KEY_FILE", str(insecure))
    with pytest.raises(RuntimeError, match="group- or world-readable"):
        dpop.SigningAuthority.load(str(tmp_path))

    secure = tmp_path / "secure.pem"
    secure.write_bytes(body)
    secure.chmod(0o600)
    monkeypatch.setenv("MESHAGENT_RECORDER_SIGNING_KEY_FILE", str(secure))
    assert dpop.SigningAuthority.load(str(tmp_path)).kid

    link = tmp_path / "link.pem"
    os.symlink(secure, link)
    monkeypatch.setenv("MESHAGENT_RECORDER_SIGNING_KEY_FILE", str(link))
    with pytest.raises(RuntimeError, match="symbolic link"):
        dpop.SigningAuthority.load(str(tmp_path))
