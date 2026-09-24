"""Verified identity, closed role mapping, and server-owned capabilities.

MeshAgent has three production personas with deliberately different authority:
Developers operate on their own runs, Analysts investigate fleet evidence and
own cases, and CISOs set policy and approve governed changes. Browsers may use
roles to choose a landing page, but every API action is authorized here from
verified claims. Unknown or conflicting privileged mappings fail closed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal, cast

import httpx

Role = Literal["developer", "analyst", "ciso"]
Environment = Literal["development", "test", "production"]
Capability = Literal[
    "run.own",
    "run.create",
    "recorder.write",
    "package.gate",
    "device.own",
    "review.own",
    "review.read",
    "review.write",
    "fleet.read",
    "evidence.read",
    "case.read",
    "case.write",
    "audit.read",
    "exception.request",
    "policy.read",
    "policy.write",
    "policy.activate",
    "exception.read",
    "exception.renew",
    "exception.approve",
    "exception.revoke",
    "recommendation.apply",
    "remediation.write",
    "report.generate",
    "device.fleet",
]

ENVIRONMENTS = frozenset({"development", "test", "production"})
CAPABILITIES: dict[Role, frozenset[str]] = {
    "developer": frozenset({
        "run.own", "run.create", "recorder.write", "package.gate", "device.own",
        "review.own",
    }),
    "analyst": frozenset({
        "fleet.read", "evidence.read", "case.read", "case.write", "audit.read",
        "exception.request", "exception.renew", "policy.read", "device.own", "review.read",
        "review.write",
    }),
    "ciso": frozenset({
        "fleet.read", "evidence.read", "case.read", "case.write", "audit.read",
        "exception.request", "exception.renew", "policy.read", "policy.write",
        "policy.activate", "exception.read", "exception.approve",
        "exception.revoke", "recommendation.apply", "remediation.write",
        "report.generate", "device.fleet", "device.own",
        "review.read", "review.write",
    }),
}


def environment() -> Environment:
    value = os.environ.get("MESHAGENT_ENV", "development")
    if value not in ENVIRONMENTS:
        raise RuntimeError(
            "MESHAGENT_ENV must be one of development, test, or production")
    return cast(Environment, value)


def is_production() -> bool:
    return environment() == "production"


def allows_local_asserted_identities() -> bool:
    return environment() in ("development", "test")


ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384"]
DISCOVERY = "/.well-known/openid-configuration"


class AuthError(Exception):
    """The caller could not be identified. Surfaces as 401."""


class Forbidden(Exception):
    """The caller is known, and this is not theirs. Surfaces as 403."""


@dataclass(frozen=True)
class Principal:
    subject: str
    name: str
    email: str
    role: Role
    verified: bool = True
    device: str | None = None

    @property
    def capabilities(self) -> frozenset[str]:
        return CAPABILITIES[self.role]

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    @property
    def analyst(self) -> bool:
        """Compatibility name for cross-fleet investigative access."""
        return self.role in ("analyst", "ciso")

    @property
    def ciso(self) -> bool:
        return self.role == "ciso"


@dataclass(frozen=True)
class Config:
    issuer: str = ""
    audience: str = ""
    jwks_url: str = ""
    role_claim: str = "roles"
    analyst_groups: tuple[str, ...] = ()
    ciso_groups: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.issuer)


def _split(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def config() -> Config:
    return Config(
        issuer=os.environ.get("MESHAGENT_OIDC_ISSUER", "").strip().rstrip("/"),
        audience=os.environ.get("MESHAGENT_OIDC_AUDIENCE", "").strip(),
        jwks_url=os.environ.get("MESHAGENT_OIDC_JWKS_URL", "").strip(),
        role_claim=os.environ.get("MESHAGENT_OIDC_ROLE_CLAIM", "roles").strip(),
        analyst_groups=_split(os.environ.get("MESHAGENT_ANALYST_GROUPS", "")),
        ciso_groups=_split(os.environ.get("MESHAGENT_CISO_GROUPS", "")),
    )


def discover_jwks(cfg: Config, *, timeout: float = 5.0) -> str:
    if cfg.jwks_url:
        return cfg.jwks_url
    try:
        doc = httpx.get(cfg.issuer + DISCOVERY, timeout=timeout).json()
    except Exception as exc:
        raise AuthError(f"identity provider unreachable: {exc}") from exc
    url = doc.get("jwks_uri")
    if not url:
        raise AuthError("identity provider published no jwks_uri")
    return str(url)


def _held_roles(claims: dict[str, Any], cfg: Config) -> set[str]:
    raw = claims.get(cfg.role_claim)
    if raw is None and cfg.role_claim != "groups":
        raw = claims.get("groups")
    if raw is None:
        return set()
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list) and all(isinstance(value, str) for value in raw):
        return set(raw)
    raise AuthError(f"token carries a malformed {cfg.role_claim} claim")


def role_of(claims: dict[str, Any], cfg: Config) -> Role:
    """Map signed group claims onto one non-composable role.

    CISO and Analyst are intentionally not additive. Membership in both groups
    is a provisioning error, so access is refused instead of silently selecting
    the more privileged role. An authenticated person in neither privileged
    group remains a Developer.
    """
    held = _held_roles(claims, cfg)
    analyst = bool(held & set(cfg.analyst_groups))
    ciso = bool(held & set(cfg.ciso_groups))
    if analyst and ciso:
        raise AuthError("identity maps to conflicting Analyst and CISO groups")
    if ciso:
        return "ciso"
    if analyst:
        return "analyst"
    return "developer"


def principal_of(claims: dict[str, Any], cfg: Config) -> Principal:
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        raise AuthError("token carries no subject")
    email = str(claims.get("email") or claims.get("preferred_username") or "")
    return Principal(
        subject=subject,
        name=str(claims.get("name") or email or subject),
        email=email,
        role=role_of(claims, cfg),
    )


class Verifier:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._keys: Any = None

    def _client(self) -> Any:
        if self._keys is None:
            from jwt import PyJWKClient
            self._keys = PyJWKClient(discover_jwks(self._cfg), cache_keys=True)
        return self._keys

    def verify(self, token: str) -> Principal:
        import jwt

        cfg = self._cfg
        if is_production() and not cfg.audience:
            raise AuthError("production OIDC configuration requires an audience")
        try:
            key = self._client().get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=cfg.audience or None,
                issuer=cfg.issuer or None,
                options={
                    "require": ["exp", "iss", "sub"],
                    "verify_aud": bool(cfg.audience),
                },
            )
        except AuthError:
            raise
        except Exception as exc:
            raise AuthError(f"token rejected: {exc}") from exc
        return principal_of(claims, cfg)


DEV_USER = "X-MeshAgent-User"
DEV_ROLE = "X-MeshAgent-Role"


def local_principal(user: str | None, role: str | None) -> Principal:
    who = (user or "dev@localhost").strip() or "dev@localhost"
    raw = (role or "developer").strip().lower()
    wanted: Role = raw if raw in ("developer", "analyst", "ciso") else "developer"  # type: ignore[assignment]
    return Principal(
        subject=who, name=who, email=who, role=wanted, verified=False
    )


BEARER = "meshagent.bearer"
LOCAL = "meshagent.local"


def from_subprotocols(offered: list[str]) -> tuple[str | None, str | None, str | None]:
    if not offered:
        return None, None, None
    kind = offered[0]
    if kind == BEARER:
        return (offered[1] if len(offered) > 1 else None), None, None
    if kind == LOCAL and len(offered) > 1:
        import base64
        raw = offered[1]
        try:
            user = base64.urlsafe_b64decode(
                raw + "=" * (-len(raw) % 4)
            ).decode()
        except (ValueError, UnicodeDecodeError):
            return None, None, None
        return None, user, (offered[2] if len(offered) > 2 else None)
    return None, None, None
