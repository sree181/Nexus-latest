"""The MCP server: where an agent volunteers the why.

The hooks record what happened and cannot record why, so everything they
send arrives explicitly unexplained. This is the other half, and the tests
that matter are about the join between them -- because the tempting
implementation, attaching the most recent decision to the next file written,
would be a guess wearing a provenance record's clothing.
"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

_MCP = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                    "adapters", "mcp", "meshagent_mcp.py")
_HOOK = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                     "adapters", "claude-code", "meshagent_hook.py")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, os.path.abspath(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mcp = _load(_MCP, "meshagent_mcp")
hook = _load(_HOOK, "meshagent_hook_for_mcp")


@pytest.fixture
def state(tmp_path, monkeypatch):
    """An adapter home with one recording session already open."""
    monkeypatch.setenv("MESHAGENT_HOOK_HOME", str(tmp_path))
    (tmp_path / "session-abc.json").write_text(json.dumps(
        {"opened": True, "task": "Add a shard loader.", "run_id": "r1"}))
    return tmp_path


@pytest.fixture
def answers(monkeypatch):
    """Capture what the server would have posted, and reply for it."""
    sent: list[tuple[str, dict]] = []

    def call(path, body):
        sent.append((path, body))
        if path == "/recorder":
            return {"run_id": "r1", "recorded": 1, "refused": []}, ""
        return {"verdict": "allow", "package": body.get("package"),
                "version": body.get("version"), "reasons": ["nothing known"],
                "policy": "high and above refused", "fleet_agents": 0}, ""

    monkeypatch.setattr(mcp, "call", call)
    return sent


# -- the protocol --------------------------------------------------------------

def test_it_speaks_the_handshake():
    got = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert got["result"]["serverInfo"]["name"] == "meshagent"
    assert "tools" in got["result"]["capabilities"]


def test_a_notification_gets_no_reply():
    """Replying to a notification is a protocol error, and some clients
    close the connection over it."""
    assert mcp.handle({"jsonrpc": "2.0",
                       "method": "notifications/initialized"}) is None


def test_the_tools_are_listed_with_schemas():
    tools = mcp.handle({"jsonrpc": "2.0", "id": 2,
                        "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in tools} == {
        # the agent volunteering something
        "record_decision", "check_package", "session_status",
        # the agent consulting memory it did not write
        "why", "rewind", "findings", "sbom"}
    assert all(t["inputSchema"]["type"] == "object" for t in tools)


def test_an_unknown_tool_is_an_error_not_a_crash():
    got = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "telepathy", "arguments": {}}})
    assert got["error"]["code"] == -32601


def test_a_failing_tool_does_not_take_the_server_down(monkeypatch):
    monkeypatch.setitem(mcp.HANDLERS, "check_package",
                        lambda a: 1 / 0)
    got = mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                      "params": {"name": "check_package", "arguments": {}}})
    assert "failed" in got["result"]["content"][0]["text"]


# -- recording a reason --------------------------------------------------------

def test_a_decision_is_recorded_against_the_live_session(state, answers):
    mcp.tool_record_decision({"id": "use-parquet", "statement": "columnar",
                              "modules": ["src/loader.py"]})
    path, body = answers[0]
    assert path == "/recorder"
    assert body["session"] == "abc"
    assert body["events"][0]["id"] == "use-parquet"


def test_naming_files_is_what_makes_them_explained(state, answers):
    mcp.tool_record_decision({"id": "use-parquet", "statement": "columnar",
                              "modules": ["src/loader.py"]})
    claims = json.loads((state / "session-abc.json").read_text())["claims"]
    assert claims == {"src/loader.py": "use-parquet"}


def test_a_decision_naming_nothing_explains_nothing_and_says_so(state, answers):
    """It said something, but not about anything in particular, and the
    coverage number should not move because of it."""
    got = mcp.tool_record_decision({"id": "vague",
                                    "statement": "it seemed best"})
    assert "nothing became explained" in got
    assert "claims" not in json.loads((state / "session-abc.json").read_text())


def test_nothing_is_recorded_when_no_session_is_being_recorded(tmp_path,
                                                               monkeypatch,
                                                               answers):
    """Opening a second run would file the reasoning against work the
    developer's code is not in, splitting one task across two records."""
    monkeypatch.setenv("MESHAGENT_HOOK_HOME", str(tmp_path))
    got = mcp.tool_record_decision({"id": "d", "statement": "why"})
    assert "No editor session" in got
    assert answers == []


def test_a_session_that_never_opened_does_not_count(tmp_path, monkeypatch,
                                                    answers):
    monkeypatch.setenv("MESHAGENT_HOOK_HOME", str(tmp_path))
    (tmp_path / "session-xyz.json").write_text(json.dumps({"opened": False}))
    assert "No editor session" in mcp.tool_record_decision(
        {"id": "d", "statement": "why"})


def test_a_stale_session_is_not_attached_to(tmp_path, monkeypatch, answers):
    """Attaching today's reasoning to last week's run would be worse than
    not attaching it."""
    monkeypatch.setenv("MESHAGENT_HOOK_HOME", str(tmp_path))
    old = tmp_path / "session-old.json"
    old.write_text(json.dumps({"opened": True}))
    os.utime(old, (0, 0))
    assert mcp.active_session() == (None, None)


def test_both_fields_are_required(state, answers):
    assert "required" in mcp.tool_record_decision({"id": "d"})
    assert answers == []


# -- the join: an agent's reason reaching the hook's code ----------------------

def test_the_hook_attaches_a_decision_the_agent_claimed(state, answers,
                                                        tmp_path):
    """The whole point of this layer. Without it the code is recorded
    unexplained and coverage never moves."""
    mcp.tool_record_decision({"id": "use-parquet", "statement": "columnar",
                              "modules": ["src/loader.py"]})
    src = tmp_path / "src"
    src.mkdir()
    (src / "loader.py").write_text("class Loader:\n    pass\n")

    events = hook._code_events({"file_path": str(src / "loader.py")},
                               str(tmp_path), {"record": True}, "abc")
    assert events[0]["because"] == "use-parquet"


def test_a_file_the_agent_did_not_claim_stays_unexplained(state, answers,
                                                          tmp_path):
    """No inference from timing. A decision was recorded moments ago and
    this file is still not covered by it, because nobody said it was."""
    mcp.tool_record_decision({"id": "use-parquet", "statement": "columnar",
                              "modules": ["src/loader.py"]})
    other = tmp_path / "helpers.py"
    other.write_text("class H:\n    pass\n")

    events = hook._code_events({"file_path": str(other)}, str(tmp_path),
                               {"record": True}, "abc")
    assert "because" not in events[0]


def test_with_no_mcp_server_at_all_the_hook_behaves_exactly_as_before(
        tmp_path, monkeypatch):
    monkeypatch.setenv("MESHAGENT_HOOK_HOME", str(tmp_path / "empty"))
    f = tmp_path / "loader.py"
    f.write_text("class L:\n    pass\n")
    events = hook._code_events({"file_path": str(f)}, str(tmp_path),
                               {"record": True}, "nosuch")
    assert "because" not in events[0]


# -- asking the gate -----------------------------------------------------------

def test_the_gate_answer_carries_the_policy(state, answers):
    got = mcp.tool_check_package({"package": "numpy", "version": "1.26.4"})
    assert "ALLOW" in got
    assert "Policy:" in got


def test_unknown_is_spelled_out_rather_than_left_to_be_inferred(state,
                                                               monkeypatch):
    """The model has to act on this word. Left to interpret `unknown` on its
    own, an agent reads it as 'no problems found'."""
    monkeypatch.setattr(mcp, "call", lambda p, b: (
        {"verdict": "unknown", "reasons": ["OSV was unreachable"],
         "policy": "x"}, ""))
    got = mcp.tool_check_package({"package": "numpy", "version": "1.0"})
    assert "NOT checked" in got
    assert "not treat it as approved" in got


def test_a_block_tells_the_agent_what_to_do(state, monkeypatch):
    monkeypatch.setattr(mcp, "call", lambda p, b: (
        {"verdict": "block", "reasons": ["CVE-2018-18074 (high)"],
         "policy": "x"}, ""))
    got = mcp.tool_check_package({"package": "requests", "version": "2.19.0"})
    assert "Do not install" in got


def test_an_unreachable_api_is_reported_not_swallowed(state, monkeypatch):
    monkeypatch.setattr(mcp, "call", lambda p, b: (None, "could not be reached"))
    assert "could not be reached" in mcp.tool_check_package(
        {"package": "numpy", "version": "1.0"})


# -- telling the agent whether any of this is working --------------------------

def test_status_says_when_nothing_is_being_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHAGENT_HOOK_HOME", str(tmp_path))
    assert "Not recording" in mcp.tool_session_status({})


def test_status_names_the_run_and_counts_what_is_already_explained(state,
                                                                   answers):
    mcp.tool_record_decision({"id": "d", "statement": "why",
                              "modules": ["a.py", "b.py"]})
    got = mcp.tool_session_status({})
    assert "r1" in got and "2 file(s)" in got


# -- reading memory back -------------------------------------------------------

@pytest.fixture
def reads(monkeypatch):
    """Stub the GET side. Returns the dict the tests fill in per route."""
    replies: dict[str, dict] = {}
    asked: list[tuple[str, dict | None]] = []

    def get(path, params=None):
        asked.append((path, params))
        for route, body in replies.items():
            if path.endswith(route):
                return body, ""
        return None, "MeshAgent refused this (404): unknown run"

    monkeypatch.setattr(mcp, "get", get)
    return replies, asked


def test_a_read_defaults_to_the_run_being_recorded(state, reads):
    """An agent asking why a line exists means the code in front of it. The
    session file already knows which run that is, so it is never guessed."""
    replies, asked = reads
    replies["/why"] = {"run_id": "r1", "chain": [
        {"statement": "because", "origin": "AGENT", "status": "VERIFIED"}]}

    mcp.tool_why({"node": "class:Loader"})

    assert asked[0][0] == "/runs/r1/why"
    assert asked[0][1] == {"node": "class:Loader"}


def test_a_read_with_no_session_and_no_run_refuses_rather_than_guessing(
        tmp_path, monkeypatch, reads):
    monkeypatch.setenv("MESHAGENT_HOOK_HOME", str(tmp_path))

    got = mcp.tool_why({"node": "class:Loader"})

    assert "No run to read" in got
    assert reads[1] == [], "it should not have called the API at all"


def test_an_explicit_run_overrides_the_session(state, reads):
    replies, asked = reads
    replies["/why"] = {"run_id": "other", "chain": [
        {"statement": "because", "origin": "AGENT", "status": "VERIFIED"}]}

    mcp.tool_why({"node": "class:Loader", "run": "other"})

    assert asked[0][0] == "/runs/other/why"


def test_a_why_chain_ending_in_unverified_external_input_is_called_out(
        state, reads):
    """The whole point of the verb. An agent skimming a list will not infer
    'this rests on something nobody checked' from two words in a bracket."""
    reads[0]["/why"] = {"run_id": "r1", "chain": [
        {"statement": "UnsafeLoader", "origin": "AGENT", "status": "VERIFIED"},
        {"statement": "use pickle", "origin": "AGENT",
         "status": "UNVERIFIED", "via": "DERIVED_FROM"},
        {"statement": "a docs mirror said so", "entity": "source:mirror",
         "origin": "EXTERNAL", "status": "UNVERIFIED", "via": "DERIVED_FROM"},
    ]}

    got = mcp.tool_why({"node": "class:UnsafeLoader"})

    assert "WARNING" in got and "source:mirror" in got
    assert "nobody checked" in got


def test_a_clean_why_chain_carries_no_warning(state, reads):
    reads[0]["/why"] = {"run_id": "r1", "chain": [
        {"statement": "Loader", "origin": "AGENT", "status": "VERIFIED"},
        {"statement": "the user asked", "origin": "USER",
         "status": "USER_STATED", "via": "DERIVED_FROM"},
    ]}

    assert "WARNING" not in mcp.tool_why({"node": "class:Loader"})


def test_rewind_with_no_instant_lists_the_moments_worth_asking_about(
        state, reads):
    """Better than letting a model invent an epoch second and read the empty
    answer as 'nothing was known then'."""
    reads[0]["/rewind"] = {"run_id": "r1", "milestones": [1790108098,
                                                          1790108102],
                           "memories": [], "held": 0, "now": 2}

    got = mcp.tool_rewind({})

    assert "1790108098" in got and "1790108102" in got
    assert "2026-09-22" in got, "the raw number alone is not readable"


def test_a_memory_deleted_since_is_reported_as_deleted_not_as_absent(
        state, reads):
    """The single most important string in this adapter.

    An agent that reports an erased memory as one that never existed has
    turned a deletion into a lie. The content must not appear either."""
    reads[0]["/rewind"] = {
        "run_id": "r1", "at": 100, "held": 2, "now": 1, "redacted": 1,
        "milestones": [100],
        "memories": [
            {"ulid": "a", "entity": "source:poisoned", "statement": None,
             "redacted": True},
            {"ulid": "b", "entity": "class:Loader", "statement": "Loader",
             "origin": "AGENT", "status": "VERIFIED", "redacted": False},
        ]}

    got = mcp.tool_rewind({"at": 100})

    assert "DELETED" in got
    assert "source:poisoned" in got
    assert "cannot be recovered" in got
    assert "does not and must not reproduce" in got
    assert "None" not in got, "a null statement leaked into the text"


def test_rewind_before_a_run_began_says_so_rather_than_returning_nothing(
        state, reads):
    reads[0]["/rewind"] = {"run_id": "r1", "at": 5, "held": 0, "now": 3,
                           "memories": [], "milestones": [100]}

    got = mcp.tool_rewind({"at": 5})

    assert "held nothing" in got and "begins later" in got


def test_a_nonsense_instant_is_rejected_before_it_reaches_the_api(state, reads):
    assert "whole number" in mcp.tool_rewind({"at": "yesterday"})
    assert reads[1] == []


def test_an_unscanned_run_is_not_reported_as_clean(state, reads):
    """Zero findings means two different things, and only one of them is
    good news."""
    reads[0]["/findings"] = {"run_id": "r1", "scanned": 0, "present": 0,
                             "findings": []}

    got = mcp.tool_findings({})

    assert "not been scanned" in got
    assert "not a clean result" in got


def test_not_assessed_reachability_is_spelled_out_as_not_safe(state, reads):
    """The honesty rule the whole product is built on, at the one boundary
    where a model is the reader."""
    reads[0]["/findings"] = {
        "run_id": "r1", "scanned": 1, "present": 1, "reachable": 0,
        "not_reachable": 0, "not_assessed": 1,
        "findings": [{"sink": "subprocess.run", "owner": "Loader",
                      "cwe": "CWE-78", "reachability": "not-assessed"}]}

    got = mcp.tool_findings({})

    assert "NOT ASSESSED" in got
    assert "Do not read this as safe" in got


def test_a_disputed_finding_keeps_both_readings(state, reads):
    reads[0]["/findings"] = {
        "run_id": "r1", "scanned": 1, "present": 1, "reachable": 1,
        "findings": [{"sink": "pickle.load", "reachability": "reachable",
                      "asserted_by": "codeql", "disputed_by": "semgrep"}]}

    got = mcp.tool_findings({})

    assert "asserted by codeql" in got
    assert "DISPUTED" in got and "semgrep" in got


def test_an_unchecked_dependency_is_not_reported_as_having_no_advisories(
        state, reads):
    """An empty CVE list from a feed nobody reached is not a clean bill of
    health, and the difference has to survive into the text."""
    reads[0]["/sbom"] = {"run_id": "r1", "entries": [
        {"package": "numpy", "version": "1.26.4", "license": "BSD-3-Clause",
         "cves": [], "feed": None},
        {"package": "torch", "version": "2.1.0", "license": "Apache-2.0",
         "cves": [], "feed": "sample"},
    ]}

    got = mcp.tool_sbom({})

    assert "NOT CHECKED" in got
    assert "curated sample data" in got


def test_a_refusal_arrives_as_readable_text_not_a_traceback(state, reads):
    """Reading is scoped by owner, so 404 is a normal answer here."""
    got = mcp.tool_why({"node": "class:Loader", "run": "someone-elses"})

    assert "refused" in got and "404" in got


def test_the_read_tools_are_listed_with_schemas():
    names = {t["name"] for t in mcp.TOOLS}
    assert {"why", "rewind", "findings", "sbom"} <= names
    for tool in mcp.TOOLS:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["name"] in mcp.HANDLERS


def test_no_read_tool_can_destroy_memory():
    """`forget` is deliberately not here. A model that can erase governed
    memory unprompted makes the deletion certificate worthless, and the
    confirm-after-preview flow assumes a human read the preview."""
    assert "forget" not in mcp.HANDLERS
    assert not any("forget" in t["name"] for t in mcp.TOOLS)
