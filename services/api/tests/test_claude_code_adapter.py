"""The Claude Code adapter: translating a real agent's hook payloads.

The payload shapes here are the ones in the Claude Code hooks reference, not
shapes invented to suit the adapter. That is the point of the test: the
adapter has to survive an agent nobody here controls.

What matters most is what it refuses to do. It does not invent a reason for
code, it does not guess a package version, and it never fails in a way that
would stall the developer's editor.
"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

_HOOK = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                     "adapters", "claude-code", "meshagent_hook.py")


def _load():
    spec = importlib.util.spec_from_file_location("meshagent_hook",
                                                  os.path.abspath(_HOOK))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An opted-in repository, with the adapter's state kept out of $HOME."""
    monkeypatch.setenv("MESHAGENT_HOOK_HOME", str(tmp_path / "state"))
    (tmp_path / ".meshagent.json").write_text(json.dumps(
        {"record": True, "exclude": ["secrets/*", "*.pem"]}))
    return tmp_path


CFG = {"record": True, "exclude": ["secrets/*", "*.pem"]}


# -- opting in -----------------------------------------------------------------

def test_a_repository_that_has_not_opted_in_records_nothing(tmp_path):
    """Absent means absent. A developer who has not agreed to ship their
    source anywhere should not discover that they are doing it."""
    assert hook.config(str(tmp_path)) is None


def test_opting_in_requires_saying_so_not_merely_having_the_file(tmp_path):
    (tmp_path / ".meshagent.json").write_text(json.dumps({"exclude": []}))
    assert hook.config(str(tmp_path)) is None


def test_an_opted_in_repository_is_read(repo):
    assert hook.config(str(repo))["exclude"] == ["secrets/*", "*.pem"]


# -- the session ---------------------------------------------------------------

def test_the_first_prompt_opens_the_session_with_what_the_developer_asked(repo):
    events = hook.on_prompt({
        "session_id": "abc123",
        "cwd": str(repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "Write a shard loader for the training pipeline",
    }, CFG)
    assert events == [{"type": "session", "agent": "claude-code",
                       "task": "Write a shard loader for the training pipeline"}]


def test_later_prompts_do_not_reopen_or_get_filed_as_agent_decisions(repo):
    """A prompt is the developer speaking. Recording it as a decision would
    attribute their words to the agent."""
    hook.write_session("abc123", {"opened": True, "task": "first"})
    events = hook.on_prompt({
        "session_id": "abc123", "cwd": str(repo),
        "prompt": "now add tests",
    }, CFG)
    assert events == []


def test_session_end_closes_a_session_that_was_opened(repo):
    hook.write_session("abc123", {"opened": True, "task": "build a loader"})
    events = hook.on_session_end(
        {"session_id": "abc123", "cwd": str(repo), "reason": "other"}, CFG)
    assert events == [{"type": "session", "agent": "claude-code",
                       "task": "build a loader", "ends": True}]


def test_session_end_for_a_session_never_opened_says_nothing(repo):
    events = hook.on_session_end(
        {"session_id": "never", "cwd": str(repo), "reason": "other"}, CFG)
    assert events == []


# -- code ----------------------------------------------------------------------

def test_a_write_records_the_file_as_it_now_stands_on_disk(repo):
    """Read back rather than reconstructed from the tool arguments: Edit
    sends a diff, and the record should be the file the developer has."""
    target = repo / "src" / "loader.py"
    target.parent.mkdir()
    target.write_text("import pickle\n")

    events = hook.on_tool({
        "session_id": "abc123",
        "cwd": str(repo),
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(target), "content": "stale"},
        "tool_response": {"filePath": str(target), "type": "create"},
    }, CFG)

    assert events == [{"type": "code", "module": "src/loader.py",
                       "code": "import pickle\n"}]


def test_code_carries_no_rationale_because_the_hook_never_saw_one(repo):
    """The load-bearing omission. MeshAgent records the absence; this adapter
    must not fill it in."""
    target = repo / "loader.py"
    target.write_text("x = 1\n")
    events = hook.on_tool({
        "cwd": str(repo), "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
    }, CFG)
    assert "because" not in events[0]


def test_an_excluded_path_is_not_recorded(repo):
    secret = repo / "secrets" / "keys.py"
    secret.parent.mkdir()
    secret.write_text("TOKEN = 'hunter2'\n")
    events = hook.on_tool({
        "cwd": str(repo), "tool_name": "Write",
        "tool_input": {"file_path": str(secret)},
    }, CFG)
    assert events == []


def test_exclusions_match_a_bare_filename_as_well_as_a_path(repo):
    key = repo / "deploy" / "server.pem"
    key.parent.mkdir()
    key.write_text("-----BEGIN-----\n")
    events = hook.on_tool({
        "cwd": str(repo), "tool_name": "Write",
        "tool_input": {"file_path": str(key)},
    }, CFG)
    assert events == []


def test_a_file_that_cannot_be_read_as_text_is_passed_over_silently(repo):
    blob = repo / "model.bin"
    blob.write_bytes(b"\x00\x81\xfe")
    events = hook.on_tool({
        "cwd": str(repo), "tool_name": "Write",
        "tool_input": {"file_path": str(blob)},
    }, CFG)
    assert events == []


def test_a_windows_path_resolves_to_the_same_module_name():
    """Claude Code delivers native separators, and a forward-slash comparison
    against a backslash path silently matches nothing."""
    assert hook.module_name(r"C:\project\src\index.ts", r"C:\project") \
        == "src/index.ts"


# -- commands and packages -----------------------------------------------------

def test_a_shell_command_is_recorded_as_a_tool_event(repo):
    events = hook.on_tool({
        "cwd": str(repo), "tool_name": "Bash",
        "tool_input": {"command": "pytest -q"},
    }, CFG)
    assert events == [{"type": "tool", "name": "Bash", "detail": "pytest -q"}]


def test_a_pinned_install_also_records_the_package(repo):
    events = hook.on_tool({
        "cwd": str(repo), "tool_name": "Bash",
        "tool_input": {"command": "pip install numpy==1.26.4"},
    }, CFG)
    assert events[1] == {"type": "package", "package": "numpy",
                         "version": "1.26.4"}


def test_an_unpinned_install_records_the_command_and_guesses_no_version(repo):
    """`pip install requests` resolves to whatever the index serves that
    minute. A guessed version would be indistinguishable in the graph from
    one that was read."""
    events = hook.on_tool({
        "cwd": str(repo), "tool_name": "Bash",
        "tool_input": {"command": "pip install requests"},
    }, CFG)
    assert [e["type"] for e in events] == ["tool"]


def test_a_read_only_tool_records_nothing(repo):
    assert hook.on_tool({
        "cwd": str(repo), "tool_name": "Read",
        "tool_input": {"file_path": str(repo / "anything.py")},
    }, CFG) == []


# -- never getting in the developer's way --------------------------------------

def test_an_unreachable_meshagent_queues_the_batch_rather_than_losing_it(
        repo, monkeypatch):
    monkeypatch.setattr(hook, "post", lambda batch: None)
    assert hook.send("abc123", [{"type": "tool", "name": "Bash"}]) is None
    assert len(hook.drain()) == 1


def test_the_queue_is_delivered_in_order_once_meshagent_returns(
        repo, monkeypatch):
    """A code event that arrived before the session event that opens its run
    would be refused, so the order the developer worked in has to survive."""
    monkeypatch.setattr(hook, "post", lambda batch: None)
    hook.send("abc123", [{"type": "session", "agent": "claude-code",
                          "task": "first"}])
    hook.send("abc123", [{"type": "code", "module": "a.py", "code": "x = 1\n"}])

    sent: list[dict] = []
    monkeypatch.setattr(
        hook,
        "post",
        lambda batch: sent.append(batch) or (
            {
                "id": batch["session_id"],
                "run_id": "r1",
                "last_acked_sequence": 1,
            }
            if batch["kind"] == "start"
            else {
                "run_id": "r1",
                "acknowledged_through": batch["body"]["events"][-1]["sequence"],
            }
        ),
    )
    hook.send("abc123", [{"type": "tool", "name": "Bash"}])

    assert [b["kind"] for b in sent] == ["start", "events", "events"]
    assert sent[0]["body"]["adapter"] == "claude-code"
    assert [b["body"]["events"][0]["type"] for b in sent[1:]] == [
        "file.changed", "tool.completed",
    ]
    assert [b["body"]["events"][0]["sequence"] for b in sent[1:]] == [2, 3]
    assert hook.drain() == []


def test_draining_the_queue_empties_it_so_a_second_hook_cannot_resend(repo):
    hook.enqueue({"agent": "claude-code", "session": "s", "events": []})
    assert len(hook.drain()) == 1
    assert hook.drain() == []


def test_unparseable_input_is_not_an_error_the_developer_has_to_see(
        monkeypatch, capsys):
    """Every path exits 0. A governance tool that stalls somebody's editor
    gets uninstalled, and an uninstalled recorder records nothing."""
    monkeypatch.setattr("sys.stdin", _Stdin("not json at all"))
    assert hook.main() == 0


def test_an_event_this_adapter_does_not_handle_exits_cleanly(monkeypatch):
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(
        {"hook_event_name": "PreCompact", "session_id": "s"})))
    assert hook.main() == 0


def test_a_repository_that_never_opted_in_short_circuits_before_posting(
        tmp_path, monkeypatch):
    posted: list[dict] = []
    monkeypatch.setattr(hook, "post", lambda b: posted.append(b))
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps({
        "hook_event_name": "UserPromptSubmit", "session_id": "s",
        "cwd": str(tmp_path), "prompt": "do a thing",
    })))
    assert hook.main() == 0
    assert posted == []


# -- the gate: the one thing this adapter can refuse ---------------------------

def gate_says(verdict: str, **extra):
    """Stand in for the API's answer."""
    def ask(package, version, session):
        return {"verdict": verdict, "package": package, "version": version,
                "reasons": ["because the policy says so"],
                "policy": "advisories of high severity or above are refused",
                **extra}
    return ask


def bash(command: str) -> dict:
    return {"hook_event_name": "PreToolUse", "session_id": "s",
            "tool_name": "Bash", "tool_input": {"command": command}}


def test_a_blocked_install_is_denied_with_a_reason_the_developer_can_read(
        monkeypatch):
    """Being stopped by a rule you cannot read is how a governance tool
    becomes the thing everybody routes around."""
    monkeypatch.setattr(hook, "ask_gate", gate_says("block"))
    got = hook.on_pre_tool(bash("pip install numpy==1.26.4"), CFG)
    out = got["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert "numpy" in out["permissionDecisionReason"]
    assert "policy" in out["permissionDecisionReason"].lower()


@pytest.mark.parametrize("verdict", ["allow", "warn", "unknown"])
def test_only_a_block_blocks(monkeypatch, verdict):
    """`warn` and `unknown` are information, not refusals. The deployment
    decides what not-knowing means, and it has already decided by the time
    the verdict reaches this file."""
    monkeypatch.setattr(hook, "ask_gate", gate_says(verdict))
    assert hook.on_pre_tool(bash("pip install numpy==1.26.4"), CFG) is None


def test_an_unreachable_meshagent_does_not_block_the_install(monkeypatch):
    """The asymmetry that keeps this installed. A recorder that goes quiet
    during an outage loses some memory; a gate that starts refusing during
    an outage stops the company working."""
    monkeypatch.setattr(hook, "ask_gate", lambda *a: None)
    assert hook.on_pre_tool(bash("pip install numpy==1.26.4"), CFG) is None


def test_a_verdict_this_adapter_does_not_recognise_does_not_block(monkeypatch):
    monkeypatch.setattr(hook, "ask_gate", gate_says("quarantine"))
    assert hook.on_pre_tool(bash("pip install numpy==1.26.4"), CFG) is None


def test_a_command_that_installs_nothing_is_never_sent_to_the_gate(monkeypatch):
    asked: list = []
    monkeypatch.setattr(hook, "ask_gate",
                        lambda *a: asked.append(a) or gate_says("block")(*a))
    assert hook.on_pre_tool(bash("pytest -q"), CFG) is None
    assert asked == []


def test_a_repository_may_keep_the_recorder_and_refuse_the_gate(monkeypatch):
    """Observing and refusing are different asks, and a team may reasonably
    agree to the first without the second."""
    monkeypatch.setattr(hook, "ask_gate", gate_says("block"))
    assert hook.on_pre_tool(bash("pip install numpy==1.26.4"),
                            {"record": True, "gate": False}) is None


def test_the_gate_sees_unpinned_installs_too():
    """`pinned_packages` must not guess a version, because it records what
    landed. The gate asks a different question, and cannot answer one it was
    never given."""
    assert hook.installs("pip install requests") == [("requests", "")]
    assert hook.pinned_packages("pip install requests") == []


def test_the_gate_reads_a_pinned_version(): 
    assert hook.installs("pip install numpy==1.26.4") == [("numpy", "1.26.4")]


def test_install_parsing_skips_flags_and_subcommands(): 
    got = hook.installs("sudo pip3 install --upgrade numpy==1.26.4 requests")
    assert got == [("numpy", "1.26.4"), ("requests", "")]


def test_a_denied_install_still_exits_zero(monkeypatch, capsys):
    """Claude Code reads the refusal from stdout. A non-zero exit would mean
    the hook broke, which is not what happened."""
    monkeypatch.setattr(hook, "ask_gate", gate_says("block"))
    monkeypatch.setenv("MESHAGENT_HOOK_HOME", "/tmp/meshagent-test-home")
    payload = bash("pip install numpy==1.26.4")
    payload["cwd"] = os.path.dirname(_HOOK)
    monkeypatch.setattr(hook, "config", lambda cwd: CFG)
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(payload)))
    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"][
        "permissionDecision"] == "deny"


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text
