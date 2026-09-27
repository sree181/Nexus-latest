"""DPoP and signing primitives for enterprise recorder credentials.

The proof key belongs to the recorder. The signing key belongs to the control
plane. They are deliberately separate trust domains.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .auth import AuthError

TOKEN_TTL = 600
PROOF_SKEW = 120
TOKEN_AUDIENCE = "meshagent-recorder"
TOKEN_ISSUER = "meshagent-control-plane"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _decode_b64(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise AuthError("recorder proof rejected") from exc


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()


def public_key_from_jwk(jwk: dict[str, Any]) -> ec.EllipticCurvePublicKey:
    if set(jwk) - {"kty", "crv", "x", "y", "kid", "use", "alg"}:
        raise AuthError("recorder proof rejected")
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise AuthError("recorder proof rejected")
    x = _decode_b64(str(jwk.get("x", "")))
    y = _decode_b64(str(jwk.get("y", "")))
    if len(x) != 32 or len(y) != 32:
        raise AuthError("recorder proof rejected")
    try:
        numbers = ec.EllipticCurvePublicNumbers(
            int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
        )
        return numbers.public_key()
    except ValueError as exc:
        raise AuthError("recorder proof rejected") from exc


def public_jwk(key: ec.EllipticCurvePublicKey) -> dict[str, str]:
    numbers = key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64(numbers.x.to_bytes(32, "big")),
        "y": _b64(numbers.y.to_bytes(32, "big")),
    }


def thumbprint(jwk: dict[str, Any]) -> str:
    public_key_from_jwk(jwk)
    canonical = {
        "crv": "P-256", "kty": "EC", "x": str(jwk["x"]), "y": str(jwk["y"]),
    }
    return _b64(hashlib.sha256(canonical_json(canonical)).digest())


def access_token_hash(token: str) -> str:
    return _b64(hashlib.sha256(token.encode()).digest())


def normalize_target(raw: str) -> str:
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if not scheme or not host:
        raise AuthError("recorder proof rejected")
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        authority = f"{host}:{port}"
    else:
        authority = host
    path = parsed.path or "/"
    return urlunsplit((scheme, authority, path, parsed.query, ""))


@dataclass(frozen=True)
class VerifiedProof:
    key_thumbprint: str
    jti: str
    issued_at: int


def verify_proof(
    proof: str,
    *,
    method: str,
    target: str,
    access_token: str | None,
    expected_thumbprint: str | None = None,
    now: int | None = None,
) -> VerifiedProof:
    rejected = AuthError("recorder proof rejected")
    try:
        header = jwt.get_unverified_header(proof)
    except Exception as exc:
        raise rejected from exc
    if header.get("typ") != "dpop+jwt" or header.get("alg") != "ES256":
        raise rejected
    raw_jwk = header.get("jwk")
    if not isinstance(raw_jwk, dict) or "d" in raw_jwk:
        raise rejected
    key_thumbprint = thumbprint(raw_jwk)
    if expected_thumbprint and not secrets.compare_digest(
        expected_thumbprint, key_thumbprint
    ):
        raise rejected
    key = public_key_from_jwk(raw_jwk)
    try:
        claims = jwt.decode(
            proof,
            key,
            algorithms=["ES256"],
            options={
                "verify_aud": False,
                "verify_exp": False,
                "require": ["htm", "htu", "iat", "jti"],
            },
        )
    except Exception as exc:
        raise rejected from exc
    issued_at = claims.get("iat")
    jti = claims.get("jti")
    if not isinstance(issued_at, int) or not isinstance(jti, str) or not (8 <= len(jti) <= 128):
        raise rejected
    clock = int(time.time()) if now is None else now
    if abs(clock - issued_at) > PROOF_SKEW:
        raise rejected
    if claims.get("htm") != method.upper():
        raise rejected
    try:
        if normalize_target(str(claims.get("htu", ""))) != normalize_target(target):
            raise rejected
    except (ValueError, AuthError) as exc:
        raise rejected from exc
    if access_token is not None:
        ath = claims.get("ath")
        if not isinstance(ath, str) or not secrets.compare_digest(
            ath, access_token_hash(access_token)
        ):
            raise rejected
    elif "ath" in claims:
        raise rejected
    return VerifiedProof(key_thumbprint=key_thumbprint, jti=jti, issued_at=issued_at)


class SigningAuthority:
    def __init__(self, key: ec.EllipticCurvePrivateKey) -> None:
        self._key = key
        self.public = public_jwk(key.public_key())
        self.kid = thumbprint(self.public)

    @classmethod
    def load(cls, base: str) -> "SigningAuthority":
        configured = os.environ.get("MESHAGENT_RECORDER_SIGNING_KEY_FILE", "").strip()
        path = Path(configured) if configured else Path(base) / "recorder-signing-key.pem"
        if path.exists():
            metadata = path.lstat()
            if path.is_symlink():
                raise RuntimeError("recorder signing key must not be a symbolic link")
            if os.environ.get("MESHAGENT_ENV", "development") == "production":
                if metadata.st_mode & 0o077:
                    raise RuntimeError("recorder signing key must not be group- or world-readable")
                if hasattr(os, "geteuid") and metadata.st_uid not in (0, os.geteuid()):
                    raise RuntimeError("recorder signing key has an unexpected owner")
            try:
                key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            except Exception as exc:
                raise RuntimeError("recorder signing key could not be loaded") from exc
            if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
                key.curve, ec.SECP256R1
            ):
                raise RuntimeError("recorder signing key must be a P-256 private key")
            return cls(key)
        if os.environ.get("MESHAGENT_ENV", "development") == "production":
            raise RuntimeError("production requires MESHAGENT_RECORDER_SIGNING_KEY_FILE")
        path.parent.mkdir(parents=True, exist_ok=True)
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(pem)
            handle.flush()
            os.fsync(handle.fileno())
        return cls(key)

    def mint_access_token(
        self,
        *,
        device_id: str,
        subject: str,
        name: str,
        email: str,
        verified: bool,
        key_thumbprint: str,
        now: int | None = None,
    ) -> str:
        issued = int(time.time()) if now is None else now
        claims = {
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
            "sub": subject,
            "name": name,
            "email": email,
            "verified": verified,
            "device_id": device_id,
            "scope": "recorder.write gate.check",
            "cnf": {"jkt": key_thumbprint},
            "iat": issued,
            "exp": issued + TOKEN_TTL,
            "jti": f"rat_{secrets.token_hex(16)}",
        }
        return jwt.encode(
            claims, self._key, algorithm="ES256",
            headers={"typ": "at+jwt", "kid": self.kid},
        )

    def verify_access_token(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("typ") != "at+jwt" or header.get("alg") != "ES256":
                raise AuthError("recorder credential rejected")
            claims = jwt.decode(
                token,
                self._key.public_key(),
                algorithms=["ES256"],
                audience=TOKEN_AUDIENCE,
                issuer=TOKEN_ISSUER,
                options={"require": ["exp", "iat", "sub", "device_id", "cnf", "scope"]},
            )
        except AuthError:
            raise
        except Exception as exc:
            raise AuthError("recorder credential rejected") from exc
        if claims.get("scope") != "recorder.write gate.check":
            raise AuthError("recorder credential rejected")
        cnf = claims.get("cnf")
        if not isinstance(cnf, dict) or not isinstance(cnf.get("jkt"), str):
            raise AuthError("recorder credential rejected")
        return claims

    def sign_configuration(self, payload: dict[str, Any]) -> str:
        return jwt.encode(
            payload, self._key, algorithm="ES256",
            headers={"typ": "meshagent-recorder-config+jwt", "kid": self.kid},
        )
