"""Who the API thinks you are, and what that lets you see.

Two things are being tested. First that a token is actually verified: an
expired one, one signed by the wrong key, or one meant for another audience
must all be refused, because the analyst's attribution is worth nothing if a
subject can be asserted. Second that the two roles are genuinely separated:
a developer reaches their own runs and nobody else's, and the fleet views
belong to the security office.

Entirely offline. A throwaway RSA key stands in for the provider's, so the
signature checking is real without anything leaving the machine.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import auth, main
from app.auth import AuthError

ISSUER = "https://login.example.com"
AUDIENCE = "meshagent-api"


@pytest.fixture(scope="module")
def keys():
    """One RSA key: the private half signs, the public half verifies."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture
def oidc(monkeypatch, keys):
    """A deployment with a provider configured, whose key set is the local
    one rather than a network fetch."""
    private, public = keys
    monkeypatch.setenv("MESHAGENT_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("MESHAGENT_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("MESHAGENT_ANALYST_GROUPS", "sec-office,appsec-leads")
    monkeypatch.setenv("MESHAGENT_CISO_GROUPS", "ciso-office,security-leadership")

    class _Key:
        key = public

    class _Client:
        def get_signing_key_from_jwt(self, _token):
            return _Key()

    monkeypatch.setattr(auth.Verifier, "_client", lambda self: _Client())
    monkeypatch.setattr(main, "_verifier", None)
    return private


def _b64(obj) -> bytes:
    """One JWT segment: compact JSON, base64url, no padding."""
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def mint(private, **overrides):
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": AUDIENCE, "sub": "maya-1234",
        "email": "maya@example.com", "name": "Maya Okonkwo",
        "iat": now, "exp": now + 600,
    }
    claims.update(overrides)
    for k in [k for k, v in claims.items() if v is None]:
        del claims[k]
    return jwt.encode(claims, private, algorithm="RS256")


# -- verifying the token ------------------------------------------------------

def test_a_properly_signed_token_identifies_its_holder(oidc):
    who = auth.Verifier(auth.config()).verify(mint(oidc))
    assert who.subject == "maya-1234"
    assert who.email == "maya@example.com"
    assert who.name == "Maya Okonkwo"
    assert who.verified is True


def test_an_expired_token_is_refused(oidc):
    now = int(time.time())
    stale = mint(oidc, iat=now - 7200, exp=now - 3600)
    with pytest.raises(AuthError):
        auth.Verifier(auth.config()).verify(stale)


def test_a_token_for_another_audience_is_refused(oidc):
    """An access token minted for a different app in the same tenant is a
    valid token; it is just not for us."""
    with pytest.raises(AuthError):
        auth.Verifier(auth.config()).verify(mint(oidc, aud="some-other-api"))


def test_a_token_from_another_issuer_is_refused(oidc):
    with pytest.raises(AuthError):
        auth.Verifier(auth.config()).verify(mint(oidc, iss="https://evil.example"))


def test_a_token_signed_by_the_wrong_key_is_refused(oidc):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthError):
        auth.Verifier(auth.config()).verify(mint(other))


def test_an_unsigned_token_is_refused(oidc):
    """`alg: none` is the oldest trick against a JWT verifier."""
    forged = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "intruder",
                         "exp": int(time.time()) + 600},
                        key="", algorithm="none")
    with pytest.raises(AuthError):
        auth.Verifier(auth.config()).verify(forged)


def test_a_symmetrically_signed_token_is_refused(oidc, keys):
    """Algorithm confusion: signing with HS256 using the provider's public
    key as the shared secret. Accepted only by a verifier that trusts the
    algorithm named in the token."""
    _, public = keys
    from cryptography.hazmat.primitives import serialization

    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    # assembled by hand: PyJWT refuses to *mint* this, which is no help
    # because a real attacker is not using PyJWT
    body = {"iss": ISSUER, "aud": AUDIENCE, "sub": "intruder",
            "exp": int(time.time()) + 600}
    signing_input = b".".join([
        _b64({"alg": "HS256", "typ": "JWT"}), _b64(body)])
    mac = hmac.new(pem, signing_input, hashlib.sha256).digest()
    forged = (signing_input + b"." + base64.urlsafe_b64encode(mac).rstrip(b"=")
              ).decode()

    with pytest.raises(AuthError):
        auth.Verifier(auth.config()).verify(forged)


def test_a_token_with_no_subject_is_refused(oidc):
    """There would be nothing to attribute a run to."""
    with pytest.raises(AuthError):
        auth.Verifier(auth.config()).verify(mint(oidc, sub=None))


# -- reading the role off the claims ------------------------------------------

def test_membership_of_an_analyst_group_means_the_security_office(oidc):
    who = auth.Verifier(auth.config()).verify(
        mint(oidc, roles=["engineering", "sec-office"]))
    assert who.role == "analyst"
    assert who.analyst


def test_membership_of_a_ciso_group_grants_governance_capabilities(oidc):
    who = auth.Verifier(auth.config()).verify(
        mint(oidc, roles=["engineering", "ciso-office"]))
    assert who.role == "ciso"
    assert who.has("policy.write")
    assert who.has("report.generate")


def test_conflicting_privileged_groups_are_refused(oidc):
    with pytest.raises(AuthError, match="conflicting"):
        auth.Verifier(auth.config()).verify(
            mint(oidc, roles=["sec-office", "ciso-office"]))


def test_malformed_role_claim_is_refused(oidc):
    with pytest.raises(AuthError, match="malformed"):
        auth.Verifier(auth.config()).verify(mint(oidc, roles={"bad": True}))


def test_anyone_else_is_a_developer(oidc):
    who = auth.Verifier(auth.config()).verify(mint(oidc, roles=["engineering"]))
    assert who.role == "developer"


def test_a_single_group_claim_is_read_as_well_as_a_list(oidc):
    who = auth.Verifier(auth.config()).verify(mint(oidc, roles="sec-office"))
    assert who.role == "analyst"


def test_providers_that_spell_it_groups_are_understood(oidc):
    """Entra says `roles`, Okta and Keycloak commonly say `groups`."""
    who = auth.Verifier(auth.config()).verify(mint(oidc, groups=["sec-office"]))
    assert who.role == "analyst"


def test_no_role_claim_at_all_is_a_developer_not_an_analyst(oidc):
    """The safe direction: a misconfigured claim must not hand out
    fleet-wide visibility."""
    who = auth.Verifier(auth.config()).verify(mint(oidc))
    assert who.role == "developer"


# -- the local, unauthenticated mode ------------------------------------------

def test_without_a_provider_the_identity_is_asserted_and_says_so():
    who = main.identify(None, "maya@example.com", "developer")
    assert who.subject == "maya@example.com"
    assert who.verified is False, (
        "an asserted identity must not present itself as a verified one")


def test_only_the_three_explicit_environment_values_are_accepted(monkeypatch):
    monkeypatch.setenv("MESHAGENT_ENV", "staging")
    with pytest.raises(RuntimeError, match="development, test, or production"):
        auth.environment()


def test_production_startup_rejects_every_missing_security_prerequisite(monkeypatch):
    for key in ("MESHAGENT_OIDC_ISSUER", "MESHAGENT_OIDC_AUDIENCE",
                "MESHAGENT_OIDC_CLIENT_ID",
                "MESHAGENT_ANALYST_GROUPS", "MESHAGENT_CISO_GROUPS",
                "MESHAGENT_DB_DIR",
                "MESHAGENT_STRICT_AUDIT", "MESHAGENT_WEB_URL",
                "MESHAGENT_ENGINE", "MESHAGENT_CORS_ORIGINS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MESHAGENT_ENV", "production")

    with pytest.raises(RuntimeError) as exc:
        main.validate_startup()
    message = str(exc.value)
    for required in ("MESHAGENT_OIDC_ISSUER", "MESHAGENT_OIDC_AUDIENCE",
                     "MESHAGENT_OIDC_CLIENT_ID",
                     "MESHAGENT_ANALYST_GROUPS", "MESHAGENT_CISO_GROUPS",
                     "MESHAGENT_DB_DIR",
                     "MESHAGENT_STRICT_AUDIT", "MESHAGENT_WEB_URL",
                     "MESHAGENT_ENGINE", "MESHAGENT_CORS_ORIGINS"):
        assert required in message


def test_complete_production_security_configuration_passes_startup_validation(
        monkeypatch, tmp_path):
    monkeypatch.setenv("MESHAGENT_ENV", "production")
    monkeypatch.setenv("MESHAGENT_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("MESHAGENT_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("MESHAGENT_OIDC_CLIENT_ID", "meshagent-web")
    monkeypatch.setenv("MESHAGENT_ANALYST_GROUPS", "sec-office")
    monkeypatch.setenv("MESHAGENT_CISO_GROUPS", "ciso-office")
    monkeypatch.setenv("MESHAGENT_DB_DIR", "/var/lib/meshagent-test")
    monkeypatch.setenv("MESHAGENT_ENGINE", "1")
    monkeypatch.setenv("MESHAGENT_STRICT_AUDIT", "true")
    monkeypatch.setenv("MESHAGENT_WEB_URL", "https://meshagent.example.com")
    monkeypatch.setenv("MESHAGENT_CORS_ORIGINS", "https://meshagent.example.com")

    main.validate_startup()


def test_production_rejects_overlapping_analyst_and_ciso_groups(monkeypatch):
    monkeypatch.setenv("MESHAGENT_ENV", "production")
    monkeypatch.setenv("MESHAGENT_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("MESHAGENT_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("MESHAGENT_OIDC_CLIENT_ID", "meshagent-web")
    monkeypatch.setenv("MESHAGENT_ANALYST_GROUPS", "security-office")
    monkeypatch.setenv("MESHAGENT_CISO_GROUPS", "security-office")
    monkeypatch.setenv("MESHAGENT_DB_DIR", "/var/lib/meshagent-test")
    monkeypatch.setenv("MESHAGENT_ENGINE", "1")
    monkeypatch.setenv("MESHAGENT_STRICT_AUDIT", "true")
    monkeypatch.setenv("MESHAGENT_WEB_URL", "https://meshagent.example.com")
    monkeypatch.setenv("MESHAGENT_CORS_ORIGINS", "https://meshagent.example.com")

    with pytest.raises(RuntimeError, match="must not overlap"):
        main.validate_startup()


def test_production_rejects_temporary_storage_and_wildcard_cors(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHAGENT_ENV", "production")
    monkeypatch.setenv("MESHAGENT_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("MESHAGENT_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("MESHAGENT_OIDC_CLIENT_ID", "meshagent-web")
    monkeypatch.setenv("MESHAGENT_ANALYST_GROUPS", "sec-office")
    monkeypatch.setenv("MESHAGENT_CISO_GROUPS", "ciso-office")
    monkeypatch.setenv("MESHAGENT_ENGINE", "1")
    monkeypatch.setenv("MESHAGENT_STRICT_AUDIT", "true")
    monkeypatch.setenv("MESHAGENT_WEB_URL", "https://meshagent.example.com")
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MESHAGENT_CORS_ORIGINS", "*")

    with pytest.raises(RuntimeError) as exc:
        main.validate_startup()

    assert "non-temporary" in str(exc.value)
    assert "exact HTTPS web origin" in str(exc.value)


def test_production_refuses_a_locally_asserted_identity(monkeypatch):
    monkeypatch.setenv("MESHAGENT_ENV", "production")
    monkeypatch.delenv("MESHAGENT_OIDC_ISSUER", raising=False)
    with pytest.raises(AuthError, match="local asserted identities"):
        main.identify(None, "intruder@example.com", "analyst")


def test_production_oidc_verification_requires_an_audience(oidc, monkeypatch):
    monkeypatch.setenv("MESHAGENT_ENV", "production")
    monkeypatch.delenv("MESHAGENT_OIDC_AUDIENCE")
    with pytest.raises(AuthError, match="requires an audience"):
        auth.Verifier(auth.config()).verify(mint(oidc))


def test_with_a_provider_configured_the_development_headers_are_ignored(oidc):
    """Otherwise a deployment could be talked out of authenticating by
    sending a header."""
    with pytest.raises(AuthError):
        main.identify(None, "intruder@example.com", "analyst")


# -- what each role can reach -------------------------------------------------

def _as(user: str, role: str) -> TestClient:
    return TestClient(main.app, headers={auth.DEV_USER: user,
                                         auth.DEV_ROLE: role})


@pytest.fixture
def maya():
    return _as("maya@example.com", "developer")


@pytest.fixture
def raj():
    return _as("raj@example.com", "developer")


@pytest.fixture
def priya():
    return _as("priya@example.com", "analyst")


def test_me_reports_the_caller_and_whether_it_was_checked(maya):
    body = maya.get("/api/me").json()
    assert body["subject"] == "maya@example.com"
    assert body["role"] == "developer"
    assert body["primary_role"] == "developer"
    assert "run.create" in body["capabilities"]
    assert "fleet.read" not in body["capabilities"]
    assert body["verified"] is False


def test_a_new_run_is_attributed_to_the_developer_who_started_it(maya):
    created = maya.post("/api/runs", json={"task": "Summarise the export"})
    assert created.status_code == 201
    assert created.json()["owner"] == "maya@example.com"


def test_a_developer_sees_their_own_runs_and_not_a_colleagues(maya, raj):
    mine = maya.post("/api/runs", json={"task": "Mine"}).json()["id"]
    theirs = raj.post("/api/runs", json={"task": "Theirs"}).json()["id"]

    visible = {r["id"] for r in maya.get("/api/runs").json()}
    assert mine in visible
    assert theirs not in visible, "a developer could list a colleague's run"


def test_a_developer_cannot_open_a_colleagues_run(maya, raj):
    theirs = raj.post("/api/runs", json={"task": "Theirs"}).json()["id"]
    # 404 rather than 403: whether a colleague has a run by that id is not
    # something to confirm either
    assert maya.get(f"/api/runs/{theirs}/findings").status_code == 404
    assert maya.get(f"/api/runs/{theirs}/code").status_code == 404


def test_a_developer_cannot_forget_a_colleagues_memory(maya, raj):
    """The destructive verb. Memory is evidence, and one developer must not
    be able to destroy another's."""
    theirs = raj.post("/api/runs", json={"task": "Theirs"}).json()["id"]
    gone = maya.post(f"/api/runs/{theirs}/forget",
                     json={"node": "source:poisoned-mirror", "reason": "x"})
    assert gone.status_code == 404


def test_the_fleet_views_belong_to_the_security_office(maya):
    assert maya.get("/api/fleet/overview").status_code == 403
    assert maya.get("/api/recommendations").status_code == 403
    assert maya.post("/api/fleet/query", json={"query": "numpy"}).status_code == 403
    assert maya.get("/api/cve/CVE-2021-41496/impact").status_code == 403


def test_the_analyst_sees_every_developers_runs(maya, raj, priya):
    mine = maya.post("/api/runs", json={"task": "Mine"}).json()["id"]
    theirs = raj.post("/api/runs", json={"task": "Theirs"}).json()["id"]

    visible = {r["id"] for r in priya.get("/api/runs").json()}
    assert {mine, theirs} <= visible
    assert priya.get("/api/fleet/overview").status_code == 200


def test_the_analyst_can_tell_which_developer_a_run_belongs_to(maya, priya):
    """Her whole job is routing a finding back to a person, so the run has
    to carry one."""
    mine = maya.post("/api/runs", json={"task": "Mine"}).json()["id"]
    run = next(r for r in priya.get("/api/runs").json() if r["id"] == mine)
    assert run["owner"] == "maya@example.com"


def test_a_developer_cannot_stream_a_colleagues_run(maya, raj):
    """The socket is a way into a run's memory like any other, and it was
    briefly not checked at all."""
    theirs = raj.post("/api/runs", json={"task": "Theirs"}).json()["id"]
    with maya.websocket_connect(f"/api/runs/{theirs}/stream") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "error"


def test_a_browser_identifies_itself_over_the_subprotocol(raj):
    """A browser cannot set headers on a WebSocket, so the identity rides in
    the subprotocol list instead. Its own run must be reachable that way."""
    mine = raj.post("/api/runs", json={"task": "Mine"}).json()["id"]
    who = base64.urlsafe_b64encode(b"raj@example.com").rstrip(b"=").decode()

    plain = TestClient(main.app)        # no identity headers at all
    with plain.websocket_connect(
        f"/api/runs/{mine}/stream",
        subprotocols=[auth.LOCAL, who, "developer"],
    ) as ws:
        frame = ws.receive_json()
    assert frame["type"] != "error", frame


def test_the_subprotocol_does_not_let_a_developer_reach_another_run(maya, raj):
    theirs = raj.post("/api/runs", json={"task": "Theirs"}).json()["id"]
    someone_else = base64.urlsafe_b64encode(b"maya@example.com").rstrip(b"=").decode()

    plain = TestClient(main.app)
    with plain.websocket_connect(
        f"/api/runs/{theirs}/stream",
        subprotocols=[auth.LOCAL, someone_else, "developer"],
    ) as ws:
        frame = ws.receive_json()
    assert frame["type"] == "error"


def test_the_seeded_reference_build_is_published_to_everyone(maya, priya):
    """It belongs to nobody because the deployment seeded it, so it is not one
    developer's work to keep from another. Withholding it left a developer with
    an empty workspace, every tab disabled, and no way to see what the screens
    are for before their own first run."""
    from app import sample

    seeded = sample.RUN_ID
    for who in (maya, priya):
        listed = who.get("/api/runs").json()
        entry = next(r for r in listed if r["id"] == seeded)
        assert entry["seeded"] is True, (
            "it has to say so, or it reads as the caller's own work")
        assert who.get(f"/api/runs/{seeded}/findings").status_code == 200


def test_publishing_the_seeded_run_did_not_open_anybody_elses(maya, raj):
    """The boundary the seeded exception was cut into. It turns on the flag and
    not on the run having no owner, so a colleague's run is as unreachable as
    it was -- and still 404, because whether they have one is not something to
    confirm from here either."""
    theirs = raj.post("/api/runs", json={"task": "Theirs"}).json()["id"]
    assert theirs not in {r["id"] for r in maya.get("/api/runs").json()}

    for view in ("findings", "code", "sbom", "graph", "hypergraph"):
        assert maya.get(f"/api/runs/{theirs}/{view}").status_code == 404, view
    assert maya.get(f"/api/runs/{theirs}/why?node=class:Any").status_code == 404
    assert maya.post(f"/api/runs/{theirs}/scan", json={}).status_code == 404
