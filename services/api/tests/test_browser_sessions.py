from __future__ import annotations

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
