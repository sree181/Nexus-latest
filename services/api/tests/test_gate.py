"""The package gate: the one part of MeshAgent that refuses.

Everything else here observes, and a wrong observation wastes an afternoon.
A wrong refusal stops somebody working, and a wrong permission waves through
the thing the gate exists to catch. So these tests are mostly about the
boundary between "allowed" and "nobody looked", which is the distinction the
whole design turns on.

The advisory feed is injected throughout. A test for a component whose job is
to refuse things must not depend on what OSV happens to hold this week.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth, gate, main
from app.advisories import Advisory, Resolved


def feed(*advisories: Advisory, unavailable: str | None = None,
         license: str | None = None):
    """A stand-in for the advisory lookup, stating exactly what it knows."""
    def resolve(package: str, version: str) -> Resolved:
        return Resolved(package=package, project=package, version=version,
                        license=license, advisories=list(advisories),
                        unavailable=unavailable)
    return resolve


def advisory(severity: str, ident: str = "CVE-2024-0001") -> Advisory:
    return Advisory(id=ident, summary="a problem", severity=severity)


STRICT = gate.Policy(threshold="high")


# -- the four verdicts ---------------------------------------------------------

def test_a_clean_version_is_allowed(): 
    got = gate.check("numpy", "1.26.4", STRICT, feed())
    assert got.verdict == "allow"


def test_the_allow_reason_does_not_claim_more_than_silence(): 
    """OSV holding no advisory is the absence of a report, not a guarantee
    that the package is safe, and the wording has to keep those apart."""
    got = gate.check("numpy", "1.26.4", STRICT, feed())
    assert "not a guarantee" in " ".join(got.reasons)


def test_an_advisory_at_the_threshold_blocks(): 
    got = gate.check("numpy", "1.26.4", STRICT, feed(advisory("high")))
    assert got.verdict == "block"
    assert "CVE-2024-0001" in " ".join(got.reasons)


def test_an_advisory_below_the_threshold_warns_rather_than_blocks(): 
    got = gate.check("numpy", "1.26.4", STRICT, feed(advisory("medium")))
    assert got.verdict == "warn"
    assert got.worst == "medium"


def test_the_threshold_is_the_deployments_to_set(): 
    lenient = gate.Policy(threshold="critical")
    assert gate.check("numpy", "1.0", lenient, feed(advisory("high"))).verdict == "warn"
    assert gate.check("numpy", "1.0", lenient,
                      feed(advisory("critical"))).verdict == "block"


def test_the_worst_advisory_decides(): 
    got = gate.check("numpy", "1.0", STRICT,
                     feed(advisory("low", "A"), advisory("critical", "B")))
    assert got.verdict == "block"
    assert got.worst == "critical"


# -- not knowing is its own answer ---------------------------------------------

def test_an_unreachable_feed_is_unknown_and_never_allow(): 
    """The crux of the design. A gate that cannot see OSV and answers
    'allow' is worse than no gate: it manufactures the impression of a
    check that did not happen."""
    got = gate.check("numpy", "1.26.4", STRICT,
                     feed(unavailable="OSV was unreachable (ConnectError)"))
    assert got.verdict == "unknown"
    assert got.verdict != "allow"
    assert "not checked" in " ".join(got.reasons)


def test_a_deployment_may_choose_to_fail_closed(): 
    closed = gate.Policy(threshold="high", block_on_unknown=True)
    got = gate.check("numpy", "1.26.4", closed,
                     feed(unavailable="OSV was unreachable"))
    assert got.verdict == "block"


def test_an_unpinned_install_is_unknown_not_allowed(): 
    """Whatever resolves today is not what resolves in CI tomorrow, so there
    is nothing specific to have checked."""
    got = gate.check("requests", "", STRICT, feed())
    assert got.verdict == "unknown"
    assert "not pinned" in " ".join(got.reasons)


def test_an_unpinned_install_obeys_the_same_fail_closed_choice(): 
    closed = gate.Policy(threshold="high", block_on_unknown=True)
    assert gate.check("requests", "", closed, feed()).verdict == "block"


def test_a_known_advisory_still_blocks_when_the_feed_was_partly_unavailable(): 
    """Degrading to 'unknown' must not lose a refusal that was already
    earned."""
    got = gate.check("numpy", "1.0", STRICT,
                     feed(advisory("critical"), unavailable="OSV partial"))
    assert got.verdict == "block"


# -- licence policy ------------------------------------------------------------

def test_a_denied_licence_blocks(): 
    pol = gate.Policy(threshold="high", denied_licenses=("AGPL",))
    got = gate.check("something", "1.0", pol, feed(license="AGPL-3.0"))
    assert got.verdict == "block"
    assert "does not accept" in " ".join(got.reasons)


def test_no_licence_is_denied_by_default(): 
    """Which licences a company can accept is a legal question, and this
    file is not where it gets answered."""
    assert gate.policy().denied_licenses == ()


# -- the policy is legible -----------------------------------------------------

def test_the_policy_describes_itself_in_words(): 
    """A developer blocked by a rule they cannot read routes around the
    tool rather than arguing with it."""
    described = gate.Policy(threshold="high",
                            denied_licenses=("AGPL",)).described
    assert "high" in described and "AGPL" in described
    assert "lets it through" in described


def test_the_policy_comes_from_the_environment(monkeypatch): 
    monkeypatch.setenv("MESHAGENT_GATE_THRESHOLD", "medium")
    monkeypatch.setenv("MESHAGENT_DENIED_LICENSES", "AGPL, SSPL")
    monkeypatch.setenv("MESHAGENT_GATE_ON_UNKNOWN", "1")
    pol = gate.policy()
    assert pol.threshold == "medium"
    assert pol.denied_licenses == ("AGPL", "SSPL")
    assert pol.block_on_unknown is True


def test_a_nonsense_threshold_falls_back_rather_than_disabling_the_gate(monkeypatch): 
    """A typo in a deployment variable must not silently turn refusals off."""
    monkeypatch.setenv("MESHAGENT_GATE_THRESHOLD", "extremely-bad")
    assert gate.policy().threshold == gate.DEFAULT_THRESHOLD


# -- over HTTP -----------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MESHAGENT_FEEDS", "0")      # never call OSV in a test
    return TestClient(app=main.app, headers={
        auth.DEV_USER: "sam@example.com", auth.DEV_ROLE: "developer"})


def test_the_route_answers_with_a_verdict_and_the_policy_behind_it(client): 
    res = client.post("/api/gate/package",
                      json={"package": "numpy", "version": "1.26.4"})
    assert res.status_code == 200
    body = res.json()
    assert body["verdict"] in ("allow", "warn", "block", "unknown")
    assert body["policy"]


def test_disabled_feeds_report_unknown_rather_than_allow(client): 
    """An offline deployment has not checked anything, and must not imply
    it has."""
    body = client.post("/api/gate/package",
                       json={"package": "numpy", "version": "1.26.4"}).json()
    assert body["verdict"] == "unknown"
    assert "disabled" in " ".join(body["reasons"])


def test_refusals_are_audited(client, tmp_path, monkeypatch): 
    """A gate whose blocks are invisible cannot be argued with, and a policy
    nobody can see the effects of gets quietly switched off."""
    from app import audit
    log = audit.Log(base=str(tmp_path))
    monkeypatch.setattr(main, "audit_log", log)
    client.post("/api/gate/package", json={"package": "numpy", "version": "1.0"})
    assert any(e.action.startswith("gate.") for e in log.read())


def test_an_allowed_package_is_not_audited(client, tmp_path, monkeypatch): 
    """Only refusals and non-checks. Logging every allow would bury them."""
    from app import audit
    log = audit.Log(base=str(tmp_path))
    monkeypatch.setattr(main, "audit_log", log)
    monkeypatch.setattr(main.gateway, "check_package", lambda req: __import__(
        "app.models", fromlist=["models"]).GateDecision(
            package=req.package, version=req.version, verdict="allow"))
    client.post("/api/gate/package", json={"package": "numpy", "version": "1.0"})
    assert log.read() == []
