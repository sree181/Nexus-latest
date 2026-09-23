"""Who is asking, and which of the two jobs they are here to do.

MeshAgent has two users with genuinely different rights. A developer runs
agents and owns the memory those runs produce. An analyst in the security
office oversees every developer's runs and is accountable for what they
contain. Attribution is not decoration here: the analyst's whole job is to
say *which developer* a reachable finding belongs to, and `forget` destroys
evidence, so it matters that the caller is who they claim to be.

So identity is an OIDC access token from the company's own provider, verified
against its published keys -- issuer, audience, signature and expiry all
checked. Nothing about a role is taken from the client; it is read from the
claims the provider signed.

There is an unauthenticated mode for working locally, because a developer
cloning this repo should not need an identity provider to see it run. It is
reported in every response so no screen can quietly imply it is secure, and
it is refused outright once an issuer is configured -- a deployment that is
half-authenticated is worse than either.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

import httpx

Role = Literal["developer", "analyst"]

# Signature algorithms accepted from the provider. Deliberately asymmetric
# only: an HMAC algorithm here would let anyone holding the (shared) secret
# mint tokens, and `none` would let anyone mint them at all.
ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384"]

DISCOVERY = "/.well-known/openid-configuration"


class AuthError(Exception):
    """The caller could not be identified. Surfaces as 401."""


class Forbidden(Exception):
    """The caller is known, and this is not theirs. Surfaces as 403."""


@dataclass(frozen=True)
class Principal:
    """One authenticated human."""

    subject: str                # the IdP's stable id for them; owns runs
    name: str
    email: str
    role: Role
    # False when no provider is configured and this identity was simply
    # asserted by the client. Carried into /api/me so the UI can say so.
    verified: bool = True
    # Set when the caller is a machine holding a device token rather than a
    # person at a browser. Routes meant for people refuse it outright: a
    # token that lives in a file on a laptop should be able to record memory
    # and nothing else, whoever ends up reading that file.
    device: str | None = None

    @property
    def analyst(self) -> bool:
        return self.role == "analyst"


@dataclass(frozen=True)
class Config:
    """How this deployment identifies people."""

    issuer: str = ""
    audience: str = ""
    jwks_url: str = ""
    # Claim holding the groups or roles the provider assigns. Entra puts them
    # in `roles` or `groups`, Okta and Keycloak commonly in `groups`.
    role_claim: str = "roles"
    # Membership of any of these means the security office, not a developer.
    analyst_groups: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.issuer)


def _split(raw: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def config() -> Config:
    """Read the deployment's identity settings from the environment."""
    return Config(
        issuer=os.environ.get("MESHAGENT_OIDC_ISSUER", "").strip().rstrip("/"),
        audience=os.environ.get("MESHAGENT_OIDC_AUDIENCE", "").strip(),
        jwks_url=os.environ.get("MESHAGENT_OIDC_JWKS_URL", "").strip(),
        role_claim=os.environ.get("MESHAGENT_OIDC_ROLE_CLAIM", "roles").strip(),
        analyst_groups=_split(os.environ.get("MESHAGENT_ANALYST_GROUPS", "")),
    )


def discover_jwks(cfg: Config, *, timeout: float = 5.0) -> str:
    """The provider's signing-key URL, taken from its own discovery document
    rather than guessed from the issuer -- the path differs between Entra,
    Okta, Google and Keycloak."""
    if cfg.jwks_url:
        return cfg.jwks_url
    try:
        doc = httpx.get(cfg.issuer + DISCOVERY, timeout=timeout).json()
    except Exception as exc:                        # network, TLS, bad JSON
        raise AuthError(f"identity provider unreachable: {exc}") from exc
    url = doc.get("jwks_uri")
    if not url:
        raise AuthError("identity provider published no jwks_uri")
    return str(url)


def role_of(claims: dict[str, Any], cfg: Config) -> Role:
    """The caller's role, from the claims the provider signed.

    Anyone the security office has not put in an analyst group is a
    developer. Erring the other way would hand fleet-wide visibility to
    whoever happened to log in."""
    raw = claims.get(cfg.role_claim)
    if raw is None and cfg.role_claim != "groups":
        raw = claims.get("groups")          # the other common spelling
    if isinstance(raw, str):
        held = {raw}
    elif isinstance(raw, list):
        held = {str(v) for v in raw}
    else:
        held = set()
    return "analyst" if held & set(cfg.analyst_groups) else "developer"


def principal_of(claims: dict[str, Any], cfg: Config) -> Principal:
    """The verified claims, read as a person."""
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
    """Verifies access tokens against the provider's published keys.

    The key set is fetched once and cached by PyJWT, which re-fetches when a
    token arrives signed by a key it has not seen -- so provider key rotation
    does not need a restart."""

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
        try:
            key = self._client().get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key,
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
        except Exception as exc:        # expired, wrong audience, bad signature
            raise AuthError(f"token rejected: {exc}") from exc
        return principal_of(claims, cfg)


# -- the unauthenticated local mode -------------------------------------------

# Who you are when no provider is configured. The client says so, and the
# server believes it, which is exactly why every response marks the identity
# unverified and the UI has to show that.
DEV_USER = "X-MeshAgent-User"
DEV_ROLE = "X-MeshAgent-Role"


def local_principal(user: str | None, role: str | None) -> Principal:
    """An asserted, unverified identity for local work."""
    who = (user or "dev@localhost").strip() or "dev@localhost"
    wanted: Role = "analyst" if (role or "").strip() == "analyst" else "developer"
    return Principal(subject=who, name=who, email=who, role=wanted,
                     verified=False)


# WebSocket subprotocols, which is where a browser has to put identity
# because it cannot set an Authorization header on a socket.
BEARER = "meshagent.bearer"
LOCAL = "meshagent.local"


def from_subprotocols(offered: list[str]) -> tuple[str | None, str | None, str | None]:
    """Read (token, asserted user, asserted role) out of the subprotocol list.

    Subprotocol values are HTTP tokens and so cannot contain `@`, which is
    why the asserted user arrives base64url encoded. There is nothing secret
    about it -- in local mode there is nothing secret at all."""
    if not offered:
        return None, None, None
    kind = offered[0]
    if kind == BEARER:
        return (offered[1] if len(offered) > 1 else None), None, None
    if kind == LOCAL and len(offered) > 1:
        import base64
        raw = offered[1]
        try:
            user = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        except (ValueError, UnicodeDecodeError):
            return None, None, None
        return None, user, (offered[2] if len(offered) > 2 else None)
    return None, None, None
