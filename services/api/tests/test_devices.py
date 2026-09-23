"""Device tokens: identity for a hook that has no browser.

The interesting tests here are the refusals. A device token is a secret that
lives in a file on a laptop, so what matters is not that recording works --
that is one assertion -- but that everything else stays shut when somebody
else ends up holding it.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app import auth, devices, main
from app.auth import AuthError, Principal

CISO = {auth.DEV_USER: "alex@example.com", auth.DEV_ROLE: "ciso"}
ANALYST = {auth.DEV_USER: "priya@example.com", auth.DEV_ROLE: "analyst"}
DEV = {auth.DEV_USER: "sam@example.com", auth.DEV_ROLE: "developer"}


@pytest.fixture
def store(tmp_path):
    return devices.load(str(tmp_path))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client whose device store is this test's, not the process's."""
    monkeypatch.setattr(main, "device_store", devices.load(str(tmp_path)))
    return TestClient(app=main.app, headers=DEV)


def person(subject="sam@example.com", role="developer", verified=True):
    return Principal(subject=subject, name=subject, email=subject,
                     role=role, verified=verified)


def paired(store, who=None) -> str:
    """A live token for `who`, through the real pairing flow."""
    device_code, user_code = store.start("laptop")
    store.approve(user_code, who or person())
    got = store.claim(device_code)
    assert got is not None
    return got[1]


# -- the pairing flow ----------------------------------------------------------

def test_a_pairing_mints_nothing_until_a_human_approves_it(store):
    """The code is printed to a terminal before anyone has agreed to
    anything. If it were sufficient on its own, shoulder-surfing would be a
    complete attack."""
    device_code, _ = store.start("laptop")
    assert store.claim(device_code) is None
    assert store.devices == {}


def test_an_approved_pairing_yields_a_token_that_records_as_the_approver(store):
    token = paired(store, person("priya@example.com", "analyst"))
    who = store.verify(token)
    assert who.subject == "priya@example.com"
    assert who.device


def test_a_device_code_is_single_use(store):
    """A poll captured in transit should not be replayable into a second
    credential for the same machine."""
    device_code, user_code = store.start("laptop")
    store.approve(user_code, person())
    assert store.claim(device_code) is not None
    with pytest.raises(AuthError):
        store.claim(device_code)


def test_a_pairing_nobody_approves_expires(store):
    """An abandoned code must not sit there indefinitely being phishable."""
    device_code, user_code = store.start("laptop")
    store.pairings[user_code].started_at -= devices.PAIRING_TTL + 1
    with pytest.raises(AuthError):
        store.claim(device_code)


def test_a_device_token_cannot_approve_another_device(store):
    """Otherwise a leaked recording token mints a fresh one on demand and
    outlives its own revocation."""
    token = paired(store)
    machine = store.verify(token)
    _, user_code = store.start("second laptop")
    with pytest.raises(AuthError):
        store.approve(user_code, machine)


# -- holding one ---------------------------------------------------------------

def test_the_secret_is_never_written_to_disk(store, tmp_path):
    """The store holds hashes. Leaking this file should not hand over
    working tokens."""
    token = paired(store)
    secret = token.split(".", 1)[1]
    assert secret not in (tmp_path / devices.STORE).read_text()


def test_the_store_is_not_world_readable(store, tmp_path):
    paired(store)
    mode = os.stat(tmp_path / devices.STORE).st_mode & 0o777
    assert mode == 0o600


def test_a_tampered_secret_is_refused(store):
    token = paired(store)
    device_id = token[len(devices.PREFIX):].split(".", 1)[0]
    with pytest.raises(AuthError):
        store.verify(f"{devices.PREFIX}{device_id}.not-the-secret")


def test_an_unknown_device_and_a_wrong_secret_fail_identically(store):
    """Distinguishing them would enumerate which developers have registered
    machines."""
    token = paired(store)
    device_id = token[len(devices.PREFIX):].split(".", 1)[0]
    wrong = pytest.raises(AuthError)
    with wrong as a:
        store.verify(f"{devices.PREFIX}{device_id}.wrong")
    with pytest.raises(AuthError) as b:
        store.verify(f"{devices.PREFIX}0000000000000000.wrong")
    assert str(a.value) == str(b.value)


def test_revoking_takes_effect_immediately(store):
    """No token lifetime to wait out. That is the reason for keeping hashes
    here rather than issuing self-contained JWTs."""
    token = paired(store)
    device = next(iter(store.devices.values()))
    store.revoke(device.id, person())
    with pytest.raises(AuthError):
        store.verify(token)


def test_a_developer_cannot_revoke_a_colleagues_device(store):
    token = paired(store, person("sam@example.com"))
    device = next(iter(store.devices.values()))
    with pytest.raises(AuthError):
        store.revoke(device.id, person("mallory@example.com"))
    assert store.verify(token).subject == "sam@example.com"


def test_the_ciso_can_revoke_anyones_device(store):
    paired(store, person("sam@example.com"))
    device = next(iter(store.devices.values()))
    store.revoke(device.id, person("alex@example.com", "ciso"))
    assert not device.active


def test_an_analyst_cannot_revoke_a_colleagues_device(store):
    token = paired(store, person("sam@example.com"))
    device = next(iter(store.devices.values()))
    with pytest.raises(AuthError):
        store.revoke(device.id, person("priya@example.com", "analyst"))
    assert store.verify(token).subject == "sam@example.com"


def test_a_token_is_never_more_trustworthy_than_who_minted_it(store):
    """Local mode has no identity provider, so the approving human was
    asserted. A bearer secret does not turn that into a proven name."""
    token = paired(store, person(verified=False))
    assert store.verify(token).verified is False


def test_an_unreadable_store_fails_closed(tmp_path):
    """Every token stops working and developers log in again. Guessing at
    half-read records would be guessing about who may write to memory."""
    (tmp_path / devices.STORE).write_text("{ this is not json")
    assert devices.load(str(tmp_path)).devices == {}


def test_a_device_survives_a_restart(store, tmp_path):
    token = paired(store)
    assert devices.load(str(tmp_path)).verify(token).subject == "sam@example.com"


# -- what it reaches, over HTTP ------------------------------------------------

def test_a_device_token_can_record(client):
    token = paired(main.device_store)
    res = client.post("/api/recorder", headers={
        "Authorization": f"Bearer {token}", auth.DEV_USER: ""}, json={
        "agent": "claude-code", "session": "dev-1",
        "events": [{"type": "session", "agent": "claude-code",
                    "task": "Add a loader."}],
    })
    assert res.status_code == 200


def test_a_device_token_can_ask_the_gate(client, monkeypatch):
    """The hook asking permission before an install is the reason the gate
    exists, and it holds nothing but this token."""
    monkeypatch.setenv("MESHAGENT_FEEDS", "0")
    token = paired(main.device_store)
    res = client.post("/api/gate/package",
                      json={"package": "numpy", "version": "1.26.4"},
                      headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/runs"),
    ("GET", "/api/me"),
    ("GET", "/api/fleet/overview"),
    ("GET", "/api/fleet/coverage"),
    ("GET", "/api/audit"),
    ("GET", "/api/devices"),
    ("GET", "/api/recommendations"),
    ("POST", "/api/runs"),
])
def test_a_device_token_reaches_nothing_but_the_recorder(client, method, path):
    """The containment story for a credential sitting in a file on a laptop.
    Checked route by route rather than trusted to a code comment, because
    the failure mode is silent."""
    token = paired(main.device_store)
    res = client.request(method, path, json={"task": "x"},
                         headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403, f"{method} {path} let a device token in"


def test_the_pairing_route_needs_no_credential(client):
    """It cannot: the whole problem is that the caller has none yet. It
    grants nothing until a human approves."""
    res = client.post("/api/devices/pair", json={"label": "laptop"},
                      headers={auth.DEV_USER: ""})
    assert res.status_code == 200
    assert res.json()["user_code"]
    assert main.device_store.devices == {}


def test_approving_over_http_names_the_human_in_the_audit_log(client, tmp_path,
                                                              monkeypatch):
    log = __import__("app.audit", fromlist=["audit"]).Log(base=str(tmp_path))
    monkeypatch.setattr(main, "audit_log", log)
    start = client.post("/api/devices/pair", json={"label": "laptop"}).json()
    client.post("/api/devices/approve", json={"user_code": start["user_code"]})
    got = client.post("/api/devices/token",
                      json={"device_code": start["device_code"]}).json()
    assert got["status"] == "granted"
    actions = [e.action for e in log.read()]
    assert "device.approve" in actions and "device.mint" in actions


def test_the_token_is_returned_once_and_only_once(client):
    start = client.post("/api/devices/pair", json={"label": "laptop"}).json()
    client.post("/api/devices/approve", json={"user_code": start["user_code"]})
    body = {"device_code": start["device_code"]}
    assert client.post("/api/devices/token", json=body).json()["token"]
    assert client.post("/api/devices/token", json=body).status_code == 401


def test_the_grants_are_stated_on_the_wire(client):
    """A credential whose holder cannot say what it permits is one nobody
    can reason about storing."""
    start = client.post("/api/devices/pair", json={"label": "laptop"}).json()
    assert start["grants"] == list(devices.SCOPE)
    assert "recorder.write" in start["grants"]
    assert not any(g.startswith("run.") for g in start["grants"])


def test_a_developer_only_sees_their_own_devices(client):
    paired(main.device_store, person("sam@example.com"))
    paired(main.device_store, person("mallory@example.com"))
    mine = client.get("/api/devices").json()
    assert [d["subject"] for d in mine] == ["sam@example.com"]
    analyst = client.get("/api/devices", headers=ANALYST).json()
    assert analyst == []
    every = client.get("/api/devices", headers=CISO).json()
    assert len(every) == 2
