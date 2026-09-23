"""Who says this dangerous call can actually be reached?

The product's most consequential claim is `exploitable`, and the honest
answer has three states rather than two: an analyser traced a path, an
analyser looked and found none, or nobody has looked. The third is the one a
boolean silently reports as the second, and these tests exist to keep those
apart. They also hold the precedence rule: an accredited scanner outranks our
own AST walk, and a scanner declining to corroborate is visible without being
treated as a refutation.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import auth, engine_gateway, engine_seed, main

pytest.importorskip("hypermeshdb")

RUN = engine_seed.RUN_ID


def _gateway():
    try:
        return engine_gateway.EngineGateway()
    except (ImportError, OSError) as exc:  # pragma: no cover - environment
        pytest.skip(f"HyperMesh engine unavailable: {exc}")


@pytest.fixture
def gw():
    """Own gateway per test: ingesting a scan writes to memory."""
    return _gateway()


def semgrep(results: list[dict[str, Any]],
            files: list[str] | None = None) -> dict[str, Any]:
    """A Semgrep-shaped SARIF document. Semgrep often omits `artifacts`, so
    coverage has to be recoverable from the results' own locations -- which
    is exactly the case worth testing."""
    doc: dict[str, Any] = {
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "Semgrep", "rules": [
                {"id": "python.lang.security.unsafe-deserialization",
                 "properties": {"tags": ["security", "external/cwe/cwe-502"],
                                "security-severity": "9.1"}},
            ]}},
            "results": results,
        }],
    }
    if files is not None:
        doc["runs"][0]["artifacts"] = [
            {"location": {"uri": f}} for f in files]
    return doc


def hit(*, uri: str, line: int, with_flow: bool,
        rule: str = "python.lang.security.unsafe-deserialization"
        ) -> dict[str, Any]:
    """One SARIF result. `with_flow` decides whether the scanner is claiming
    reachability or merely presence -- the whole distinction this rests on."""
    loc = {"physicalLocation": {"artifactLocation": {"uri": uri},
                                "region": {"startLine": line}}}
    res: dict[str, Any] = {"ruleId": rule, "locations": [loc],
                           "message": {"text": "unsafe deserialization"}}
    if with_flow:
        entry = {"physicalLocation": {
            "artifactLocation": {"uri": uri},
            "region": {"startLine": 1}}}
        res["codeFlows"] = [{"threadFlows": [{"locations": [
            {"location": entry}, {"location": loc},
        ]}]}]
    return res


def _find(gw, sink: str):
    for f in gw.run_findings(RUN).findings:
        if f.sink == sink:
            return f
    raise AssertionError(f"no finding for {sink}")


# -- the scanner's flow is the reachability evidence ---------------------------

def test_a_traced_flow_makes_a_finding_reachable_and_names_the_scanner(gw):
    gw.ingest_scan(RUN, semgrep([hit(uri="shards.py", line=22, with_flow=True)]))
    f = _find(gw, "pickle.load")
    assert f.reachability == "reachable"
    assert f.asserted_by == "semgrep"
    assert f.exploitable is True, "the derived wire field must still agree"


def test_a_result_without_a_flow_claims_nothing(gw):
    """Semgrep reporting a sink exists is not Semgrep proving it reachable.
    Only a code flow is evidence of a path."""
    before = _find(gw, "subprocess.run").reachability
    gw.ingest_scan(RUN, semgrep(
        [hit(uri="shards.py", line=30, with_flow=False,
             rule="python.lang.security.subprocess")]))
    assert _find(gw, "subprocess.run").reachability == before


# -- the distinction the boolean used to hide -----------------------------------

def test_a_sink_no_scanner_has_read_is_not_assessed_not_clean(gw):
    """The whole point. Nothing has scanned this run, so nothing may be
    described as cleared."""
    out = gw.run_findings(RUN)
    unproven = [f for f in out.findings if f.reachability != "reachable"]
    assert unproven, "expected at least one unproven sink to reason about"
    assert all(f.reachability == "not-assessed" for f in unproven)
    assert out.not_assessed == len(unproven)
    assert out.not_reachable == 0


def test_a_scanner_that_read_the_module_clears_what_it_did_not_flag(gw):
    """Coverage is what licenses a negative: same sink, different verdict,
    purely because a scanner actually looked at the file this time."""
    module = gw.run_code(RUN).modules[0].name
    assert _find(gw, "subprocess.run").reachability == "not-assessed"

    gw.ingest_scan(RUN, semgrep(
        [hit(uri=f"{module}.py", line=22, with_flow=True)],
        files=[f"{module}.py"]))

    f = _find(gw, "subprocess.run")
    assert f.reachability == "not-reachable"
    assert f.asserted_by == "semgrep"


def test_a_scan_of_other_files_clears_nothing_here(gw):
    """A scanner reading somebody else's module tells us nothing about this
    one, and must not quietly turn unassessed into clean."""
    gw.ingest_scan(RUN, semgrep(
        [hit(uri="unrelated.py", line=3, with_flow=False)],
        files=["unrelated.py"]))
    assert gw.run_findings(RUN).not_reachable == 0


# -- precedence between analysers ----------------------------------------------

def test_the_scanner_outranks_our_own_walk_on_the_same_sink(gw):
    """Our AST walk already calls pickle.load reachable in the seeded run.
    When Semgrep traces the same sink, the account a reader can verify is
    the scanner's."""
    assert _find(gw, "pickle.load").asserted_by == "builtin"
    gw.ingest_scan(RUN, semgrep([hit(uri="shards.py", line=22, with_flow=True)]))
    f = _find(gw, "pickle.load")
    assert f.reachability == "reachable"
    assert f.asserted_by == "semgrep"


def test_a_scanner_declining_to_corroborate_is_shown_but_does_not_overturn(gw):
    """Semgrep read the module and did not flag the sink our walk flagged.
    Its silence is not a refutation -- its rules may not cover that sink --
    so the claim stands and the reader is told who declined to second it."""
    module = gw.run_code(RUN).modules[0].name
    gw.ingest_scan(RUN, semgrep(
        [hit(uri="elsewhere.py", line=2, with_flow=False)],
        files=[f"{module}.py", "elsewhere.py"]))

    f = _find(gw, "pickle.load")
    assert f.reachability == "reachable", "silence must not clear a finding"
    assert f.asserted_by == "builtin"
    assert f.disputed_by == "semgrep"


# -- the ingest itself ----------------------------------------------------------

def test_the_same_scan_twice_adds_nothing(gw):
    doc = semgrep([hit(uri="shards.py", line=22, with_flow=True)])
    first = gw.ingest_scan(RUN, doc)
    second = gw.ingest_scan(RUN, doc)
    assert first.reachable == 1
    assert second.reachable == 0, "re-uploading a scan must not duplicate it"
    assert gw.run_findings(RUN).reachable == 1


def test_an_ingested_scan_never_claims_to_be_sample_data(gw):
    """The counterpart of the curated gateway saying it discarded the document.
    Here it landed, so the receipt must not carry the flag that says otherwise
    -- an uploader told their evidence was ignored would upload it again."""
    got = gw.ingest_scan(RUN, semgrep([hit(uri="shards.py", line=22,
                                           with_flow=True)]))
    assert got.sample is False
    assert gw.run_findings(RUN).scans[-1].sample is False


def test_the_scan_is_recorded_with_what_it_covered(gw):
    gw.ingest_scan(RUN, semgrep(
        [hit(uri="src/pipeline.py", line=9, with_flow=True)]))
    scans = gw.run_findings(RUN).scans
    assert [s.tool for s in scans] == ["semgrep"]
    assert "pipeline" in scans[0].modules, (
        "a path must be reduced to the module name the graph uses")


# -- over the wire ---------------------------------------------------------------

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A gateway on its own directory, so fleet-wide counts do not depend on
    what other tests in this file have already uploaded."""
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "store"))
    return _gateway()


@pytest.fixture
def client():
    return TestClient(main.app, headers={auth.DEV_USER: "priya@example.com",
                                         auth.DEV_ROLE: "analyst"})


def test_the_whole_wire_carries_the_three_states(client):
    body = client.get(f"/api/runs/{RUN}/findings").json()
    assert set(body) >= {"reachable", "not_reachable", "not_assessed", "scans"}
    assert body["present"] == (body["reachable"] + body["not_reachable"]
                               + body["not_assessed"])
    for f in body["findings"]:
        assert f["reachability"] in {"reachable", "not-reachable",
                                     "not-assessed"}
        assert f["exploitable"] is (f["reachability"] == "reachable")


def test_uploading_a_scan_over_the_wire_leaves_an_audit_entry(client):
    res = client.post(f"/api/runs/{RUN}/scan",
                      json=semgrep([hit(uri="shards.py", line=22,
                                        with_flow=True)]))
    assert res.status_code == 200
    assert res.json()["tool"] == "semgrep"

    log = client.get("/api/audit").json()["entries"]
    assert any(e["action"] == "scan.ingest" for e in log)


# -- the fleet view must not read as healthy merely because nobody looked ------

def test_the_fleet_counts_what_nobody_has_checked(client):
    """A low exploitable count beside a high unassessed count is not a safe
    fleet, and the headline numbers have to be able to say which it is."""
    body = client.get("/api/fleet/overview").json()
    assert body["unassessed_findings"] > 0
    assert body["runs_unscanned"] <= body["runs_with_code"]


def test_scanning_a_run_moves_it_out_of_the_unscanned_count(isolated, monkeypatch):
    """Own store: an earlier test in this module scans the shared one, and a
    coverage count that depends on test order is worse than no count."""
    monkeypatch.setattr(main, "gateway", isolated)
    client = TestClient(main.app, headers={auth.DEV_USER: "priya@example.com",
                                           auth.DEV_ROLE: "analyst"})
    before = client.get("/api/fleet/overview").json()
    assert before["runs_unscanned"] >= 1

    client.post(f"/api/runs/{RUN}/scan",
                json=semgrep([hit(uri="main.py", line=22, with_flow=True)],
                             files=["main.py"]))

    after = client.get("/api/fleet/overview").json()
    assert after["runs_unscanned"] == before["runs_unscanned"] - 1
    assert after["unassessed_findings"] < before["unassessed_findings"]
