from __future__ import annotations

import hashlib
import sqlite3
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import auth, browser_sessions, main


def test_login_transaction_is_one_time_and_state_bound(monkeypatch, tmp_path):
    store = browser_sessions.Store(tmp_path)
    transaction, state, verifier = store.begin("/ciso/overview?period=30d")

    pending = store.consume(transaction, state)
    assert pending.return_to == "/ciso/overview?period=30d"
    assert pending.verifier == verifier
    with pytest.raises(auth.AuthError):
        store.consume(transaction, state)


def test_wrong_state_consumes_the_transaction(tmp_path):
    store = browser_sessions.Store(tmp_path)
    transaction, state, _ = store.begin("/analyst/queue")

    with pytest.raises(auth.AuthError):
        store.consume(transaction, state + "tampered")
    with pytest.raises(auth.AuthError):
        store.consume(transaction, state)


def test_browser_session_is_opaque_resolvable_and_revocable(tmp_path):
    store = browser_sessions.Store(tmp_path)
    principal = auth.Principal(
        subject="alex-1", name="Alex", email="alex@example.com",
        role="ciso", verified=True,
    )
    cookie, expires_at = store.create(principal, expires_in=600)

    assert "alex" not in cookie
    session = store.resolve(cookie)
    assert session is not None
    assert session.principal == principal
    assert session.expires_at == expires_at
    store.revoke(cookie)
    assert store.resolve(cookie) is None


def test_platform_admin_browser_session_survives_fresh_and_upgraded_stores(tmp_path):
    fresh = browser_sessions.Store(tmp_path / "fresh")
    admin = auth.Principal(
        subject="avery-1", name="Avery", email="avery@example.com",
        role="platform_admin", verified=True,
    )
    cookie, _ = fresh.create(admin, expires_in=600)
    assert fresh.resolve(cookie).principal == admin

    upgraded_base = tmp_path / "upgraded"
    upgraded_base.mkdir()
    path = upgraded_base / "browser_sessions.sqlite3"
    old_cookie = "old-session"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE browser_sessions (
              session_hash TEXT PRIMARY KEY,
              subject TEXT NOT NULL,
              name TEXT NOT NULL,
              email TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('developer','analyst','ciso')),
              verified INTEGER NOT NULL CHECK(verified IN (0,1)),
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL
            );
            CREATE INDEX browser_sessions_expiry ON browser_sessions(expires_at);
            """
        )
        connection.execute(
            "INSERT INTO browser_sessions VALUES(?,?,?,?,?,?,?,?)",
            (
                hashlib.sha256(old_cookie.encode()).hexdigest(), "analyst-1",
                "Priya", "priya@example.com", "analyst", 1,
                int(time.time()), int(time.time()) + 600,
            ),
        )
    upgraded = browser_sessions.Store(upgraded_base)
    assert upgraded.resolve(old_cookie).principal.role == "analyst"
    admin_cookie, _ = upgraded.create(admin, expires_in=600)
    assert upgraded.resolve(admin_cookie).principal.role == "platform_admin"


def test_oidc_callback_establishes_platform_admin_session(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHAGENT_OIDC_ISSUER", "https://identity.example")
    monkeypatch.setenv("MESHAGENT_WEB_URL", "http://testserver")
    store = browser_sessions.Store(tmp_path)
    monkeypatch.setattr(main, "browser_session_store", store)
    monkeypatch.setattr(main, "_verifier", object())
    transaction, state, _ = store.begin("/admin/onboarding/recorder")

    def exchange(active_store, *, transaction, state, code, verifier):
        pending = active_store.consume(transaction, state)
        session, expires_at = active_store.create(
            auth.Principal(
                subject="avery-oidc", name="Avery", email="avery@example.com",
                role="platform_admin", verified=True,
            ),
            expires_in=600,
        )
        return session, expires_at, pending.return_to

    monkeypatch.setattr(browser_sessions, "exchange_code", exchange)
    client = TestClient(main.app)
    client.cookies.set(browser_sessions.transaction_cookie_name(), transaction)
    response = client.get(
        f"/auth/callback?code=code&state={state}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/onboarding/recorder"
    identity = client.get("/api/me")
    assert identity.status_code == 200
    assert identity.json()["primary_role"] == "platform_admin"


def test_login_redirect_uses_server_pkce_and_httponly_transaction(
        monkeypatch, tmp_path):
    monkeypatch.setenv("MESHAGENT_OIDC_ISSUER", "https://identity.example")
    monkeypatch.setenv("MESHAGENT_OIDC_CLIENT_ID", "meshagent-web")
    monkeypatch.setenv("MESHAGENT_WEB_URL", "http://testserver")
    monkeypatch.setattr(
        browser_sessions, "discover",
        lambda _cfg: {
            "authorization_endpoint": "https://identity.example/authorize",
            "token_endpoint": "https://identity.example/token",
        },
    )
    monkeypatch.setattr(main, "browser_session_store", browser_sessions.Store(tmp_path))
    client = TestClient(main.app)

    response = client.get(
        "/auth/login?return_to=/analyst/queue", follow_redirects=False)

    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    query = parse_qs(location.query)
    assert location.netloc == "identity.example"
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://testserver/auth/callback"]
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "meshagent_oidc_tx=" in set_cookie
    assert "access_token" not in response.text


def test_production_discovery_rejects_insecure_or_credentialed_endpoints(monkeypatch):
    class Response:
        def __init__(self, authorization: str, token: str) -> None:
            self.authorization = authorization
            self.token = token

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "authorization_endpoint": self.authorization,
                "token_endpoint": self.token,
            }

    monkeypatch.setenv("MESHAGENT_ENV", "production")
    cfg = auth.Config(issuer="https://identity.example")
    for authorization, token in (
        ("http://identity.example/authorize", "https://identity.example/token"),
        ("https://identity.example/authorize", "http://identity.example/token"),
        ("https://user@identity.example/authorize", "https://identity.example/token"),
    ):
        monkeypatch.setattr(
            browser_sessions.httpx, "get",
            lambda *_args, authorization=authorization, token=token, **_kwargs:
                Response(authorization, token),
        )
        with pytest.raises(auth.AuthError):
            browser_sessions.discover(cfg)


def test_cookie_session_authenticates_api_without_bearer(
        monkeypatch, tmp_path):
    monkeypatch.setenv("MESHAGENT_OIDC_ISSUER", "https://identity.example")
    store = browser_sessions.Store(tmp_path)
    monkeypatch.setattr(main, "browser_session_store", store)
    cookie, _ = store.create(
        auth.Principal(
            subject="priya-1", name="Priya", email="priya@example.com",
            role="analyst", verified=True,
        ),
        expires_in=600,
    )
    client = TestClient(main.app)
    client.cookies.set(browser_sessions.session_cookie_name(), cookie)

    response = client.get("/api/me")

    assert response.status_code == 200
    assert response.json()["primary_role"] == "analyst"
    assert "case.write" in response.json()["capabilities"]


def test_logout_revokes_session(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHAGENT_OIDC_ISSUER", "https://identity.example")
    monkeypatch.setenv("MESHAGENT_WEB_URL", "http://testserver")
    store = browser_sessions.Store(tmp_path)
    monkeypatch.setattr(main, "browser_session_store", store)
    cookie, _ = store.create(
        auth.Principal(
            subject="maya-1", name="Maya", email="maya@example.com",
            role="developer", verified=True,
        ),
        expires_in=600,
    )
    client = TestClient(main.app)
    client.cookies.set(browser_sessions.session_cookie_name(), cookie)

    assert client.post(
        "/auth/logout", headers={"origin": "http://testserver"}).status_code == 200
    assert store.resolve(cookie) is None
    assert client.get("/api/me").status_code == 401


def test_cookie_mutations_require_the_canonical_browser_origin(
        monkeypatch, tmp_path):
    monkeypatch.setenv("MESHAGENT_OIDC_ISSUER", "https://identity.example")
    monkeypatch.setenv("MESHAGENT_WEB_URL", "https://meshagent.example")
    store = browser_sessions.Store(tmp_path)
    monkeypatch.setattr(main, "browser_session_store", store)
    cookie, _ = store.create(
        auth.Principal(
            subject="maya-1", name="Maya", email="maya@example.com",
            role="developer", verified=True,
        ),
        expires_in=600,
    )
    client = TestClient(main.app)
    client.cookies.set(browser_sessions.session_cookie_name(), cookie)

    assert client.post("/api/runs", json={"task": "unsafe"}).status_code == 403
    accepted = client.post(
        "/api/runs",
        json={"task": "safe"},
        headers={"origin": "https://meshagent.example"},
    )
    assert accepted.status_code == 201


def test_revoked_browser_session_closes_an_active_stream(
        monkeypatch, tmp_path):
    monkeypatch.setenv("MESHAGENT_OIDC_ISSUER", "https://identity.example")
    monkeypatch.setenv("MESHAGENT_WEB_URL", "http://testserver")
    monkeypatch.setenv("MESHAGENT_STREAM_DELAY", "0.01")
    store = browser_sessions.Store(tmp_path)
    monkeypatch.setattr(main, "browser_session_store", store)
    cookie, _ = store.create(
        auth.Principal(
            subject="maya-1", name="Maya", email="maya@example.com",
            role="developer", verified=True,
        ),
        expires_in=600,
    )
    client = TestClient(main.app)
    client.cookies.set(browser_sessions.session_cookie_name(), cookie)
    run = client.post(
        "/api/runs", json={"task": "stream"},
        headers={"origin": "http://testserver"},
    ).json()

    with client.websocket_connect(
        f"/api/runs/{run['id']}/stream",
        headers={"origin": "http://testserver"},
    ) as socket:
        socket.receive_json()
        store.revoke(cookie)
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                socket.receive_json()
        assert closed.value.code == 4401
