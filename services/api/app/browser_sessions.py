"""Server-owned OIDC browser transactions and HttpOnly application sessions.

The browser never receives an access or refresh token.  It receives only opaque,
random cookies whose hashes are stored in the durable control-plane directory.
Bearer-token API clients remain supported by :mod:`app.auth`.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

from . import auth
from .auth import AuthError, Principal

DISCOVERY = "/.well-known/openid-configuration"
TRANSACTION_TTL = 10 * 60
DEFAULT_SESSION_TTL = 60 * 60
MAX_SESSION_TTL = 8 * 60 * 60


def session_cookie_name() -> str:
    return "__Host-meshagent_session" if auth.is_production() else "meshagent_session"


def transaction_cookie_name() -> str:
    return "__Host-meshagent_oidc_tx" if auth.is_production() else "meshagent_oidc_tx"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base64url(size: int = 32) -> str:
    return secrets.token_urlsafe(size)


def _safe_return_to(value: str | None) -> str:
    target = (value or "/").strip()
    if len(target) > 2048 or not target.startswith("/") or target.startswith("//"):
        raise AuthError("invalid return target")
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or "\\" in target or any(ord(char) < 32 for char in target):
        raise AuthError("invalid return target")
    return target


def _callback_url() -> str:
    base = os.environ.get("MESHAGENT_WEB_URL", "http://localhost:5173").rstrip("/")
    return base + "/auth/callback"


def _client_id() -> str:
    value = os.environ.get("MESHAGENT_OIDC_CLIENT_ID", "").strip()
    if not value:
        raise AuthError("browser OIDC client is not configured")
    return value


def _scope() -> str:
    return os.environ.get("MESHAGENT_OIDC_SCOPE", "openid profile email").strip()


def discover(cfg: auth.Config, *, timeout: float = 5.0) -> dict[str, str]:
    try:
        response = httpx.get(cfg.issuer + DISCOVERY, timeout=timeout)
        response.raise_for_status()
        document = response.json()
    except Exception as exc:
        raise AuthError("identity provider is unavailable") from exc
    authorization_endpoint = str(document.get("authorization_endpoint") or "")
    token_endpoint = str(document.get("token_endpoint") or "")
    if not authorization_endpoint or not token_endpoint:
        raise AuthError("identity provider discovery is incomplete")
    return {
        "authorization_endpoint": authorization_endpoint,
        "token_endpoint": token_endpoint,
    }


@dataclass(frozen=True)
class LoginTransaction:
    verifier: str
    return_to: str


@dataclass(frozen=True)
class BrowserSession:
    principal: Principal
    expires_at: int


class Store:
    """SQLite-backed one-time login transactions and browser sessions."""

    def __init__(self, base: Path | str) -> None:
        base = Path(base)
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / "browser_sessions.sqlite3"
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS oidc_transactions (
                  transaction_hash TEXT PRIMARY KEY,
                  state_hash TEXT NOT NULL,
                  verifier TEXT NOT NULL,
                  return_to TEXT NOT NULL,
                  expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS browser_sessions (
                  session_hash TEXT PRIMARY KEY,
                  subject TEXT NOT NULL,
                  name TEXT NOT NULL,
                  email TEXT NOT NULL,
                  role TEXT NOT NULL CHECK(role IN ('developer','analyst','ciso')),
                  verified INTEGER NOT NULL CHECK(verified IN (0,1)),
                  created_at INTEGER NOT NULL,
                  expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS browser_sessions_expiry
                  ON browser_sessions(expires_at);
                """
            )

    def _purge(self, connection: sqlite3.Connection, now: int) -> None:
        connection.execute("DELETE FROM oidc_transactions WHERE expires_at <= ?", (now,))
        connection.execute("DELETE FROM browser_sessions WHERE expires_at <= ?", (now,))

    def begin(self, return_to: str | None) -> tuple[str, str, str]:
        now = int(time.time())
        transaction = _base64url()
        state = _base64url(24)
        verifier = _base64url(48)
        with self._lock, self._connect() as connection:
            self._purge(connection, now)
            connection.execute(
                "INSERT INTO oidc_transactions VALUES (?, ?, ?, ?, ?)",
                (_digest(transaction), _digest(state), verifier, _safe_return_to(return_to), now + TRANSACTION_TTL),
            )
        return transaction, state, verifier

    def consume(self, transaction: str | None, state: str | None) -> LoginTransaction:
        if not transaction or not state:
            raise AuthError("sign-in transaction could not be verified")
        now = int(time.time())
        with self._lock, self._connect() as connection:
            self._purge(connection, now)
            row = connection.execute(
                "SELECT state_hash, verifier, return_to, expires_at FROM oidc_transactions WHERE transaction_hash = ?",
                (_digest(transaction),),
            ).fetchone()
            connection.execute(
                "DELETE FROM oidc_transactions WHERE transaction_hash = ?", (_digest(transaction),)
            )
        if row is None or int(row[3]) <= now or not hmac.compare_digest(str(row[0]), _digest(state)):
            raise AuthError("sign-in transaction could not be verified")
        return LoginTransaction(verifier=str(row[1]), return_to=str(row[2]))

    def create(self, principal: Principal, *, expires_in: int | None) -> tuple[str, int]:
        now = int(time.time())
        requested = expires_in if isinstance(expires_in, int) and expires_in > 0 else DEFAULT_SESSION_TTL
        expires_at = now + min(requested, MAX_SESSION_TTL)
        session = _base64url(48)
        with self._lock, self._connect() as connection:
            self._purge(connection, now)
            connection.execute(
                "INSERT INTO browser_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_digest(session), principal.subject, principal.name, principal.email,
                 principal.role, int(principal.verified), now, expires_at),
            )
        return session, expires_at

    def resolve(self, session: str | None) -> BrowserSession | None:
        if not session:
            return None
        now = int(time.time())
        with self._lock, self._connect() as connection:
            self._purge(connection, now)
            row = connection.execute(
                "SELECT subject, name, email, role, verified, expires_at FROM browser_sessions WHERE session_hash = ?",
                (_digest(session),),
            ).fetchone()
        if row is None:
            return None
        role = str(row[3])
        if role not in ("developer", "analyst", "ciso"):
            return None
        principal = Principal(
            subject=str(row[0]), name=str(row[1]), email=str(row[2]),
            role=role, verified=bool(row[4]),  # type: ignore[arg-type]
        )
        return BrowserSession(principal=principal, expires_at=int(row[5]))

    def revoke(self, session: str | None) -> None:
        if not session:
            return
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM browser_sessions WHERE session_hash = ?", (_digest(session),)
            )


def authorization_url(store: Store, return_to: str | None) -> tuple[str, str]:
    cfg = auth.config()
    if not cfg.enabled:
        raise AuthError("identity provider is not configured")
    endpoints = discover(cfg)
    transaction, state, verifier = store.begin(return_to)
    import base64
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("utf-8")).digest()
    ).rstrip(b"=").decode("ascii")
    query = urlencode({
        "response_type": "code",
        "client_id": _client_id(),
        "redirect_uri": _callback_url(),
        "scope": _scope(),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{endpoints['authorization_endpoint']}?{query}", transaction


def exchange_code(store: Store, *, transaction: str | None, state: str | None,
                  code: str | None, verifier: auth.Verifier) -> tuple[str, int, str]:
    pending = store.consume(transaction, state)
    if not code:
        raise AuthError("identity provider returned no authorization code")
    cfg = auth.config()
    endpoint = discover(cfg)["token_endpoint"]
    try:
        response = httpx.post(
            endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": _client_id(),
                "redirect_uri": _callback_url(),
                "code_verifier": pending.verifier,
            },
            headers={"accept": "application/json"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise AuthError("identity provider rejected the sign-in") from exc
    token = str(payload.get("access_token") or "")
    if not token:
        raise AuthError("identity provider returned no access token")
    principal = verifier.verify(token)
    try:
        import jwt
        verified_exp = int(jwt.decode(
            token, options={"verify_signature": False, "verify_exp": False}
        ).get("exp", 0))
    except Exception as exc:
        raise AuthError("identity provider returned a malformed access token") from exc
    remaining = verified_exp - int(time.time())
    provider_ttl = payload.get("expires_in")
    if not isinstance(provider_ttl, int) or provider_ttl <= 0:
        provider_ttl = remaining
    session, expires_at = store.create(
        principal, expires_in=min(provider_ttl, remaining))
    return session, expires_at, pending.return_to
