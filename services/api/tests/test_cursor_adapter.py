"""The Cursor adapter: the second editor, and so the test of the claim.

The point of the recorder protocol was that an adapter is a thin translator
and the engine never learns which editor it is talking to. That is only a
claim until there are two, so the test that matters most here is the one
asserting both adapters produce the same events from the same developer
behaviour.

Payload shapes are Cursor's documented ones -- `conversation_id` rather than
`session_id`, `workspace_roots` rather than an always-present `cwd`, an
`afterFileEdit` carrying a diff rather than a file. Getting those wrong is
exactly the failure a second adapter exists to catch.
"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

_HERE = os.path.dirname(__file__)
_CURSOR = os.path.join(_HERE, "..", "..", "..", "adapters", "cursor",
                       "meshagent_hook.py")
_CLAUDE = os.path.join(_HERE, "..", "..", "..", "adapters", "claude-code",
                       "meshagent_hook.py")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, os.path.abspath(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cursor = _load(_CURSOR, "meshagent_cursor")
claude = _load(_CLAUDE, "meshagent_claude_for_cursor")

CFG = {"record": True, "exclude": ["secrets/*", "*.pem"]}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHAGENT_HOOK_HOME", str(tmp_path / "state"))
    (tmp_path / ".meshagent.json").write_text(json.dumps(CFG))
    return tmp_path


def base(event: str, **extra) -> dict:
    """Cursor's base envelope, with the fields the docs say are always there."""
    return {"hook_event_name": event, "conversation_id": "conv-1",
            "generation_id": "gen-1", "model": "claude", **extra}


# -- the fields Cursor actually sends ------------------------------------------

def test_the_run_is_keyed_on_conversation_id_not_session_id(repo):
    """`conversation_id` is stable across turns; `session_id` appears only
    on sessionStart and sessionEnd. Keying on the wrong one would split one
    piece of work across several runs."""
    assert cursor.conversation(base("beforeSubmitPrompt")) == "conv-1"


def test_session_id_is_accepted_where_conversation_id_is_absent(repo):
    assert cursor.conversation({"session_id": "s-9"}) == "s-9"


def test_workspace_roots_locate_the_repository_when_cwd_is_absent(repo):
    """`cwd` is documented for the shell and tool hooks and not for
    afterFileEdit. Falling back to this process's own cwd would file the
    file under wherever Cursor was launched from."""
    payload = base("afterFileEdit", workspace_roots=["/repo/one", "/repo/two"])
    assert cursor.workspace(payload) == "/repo/one"


def test_cwd_wins_where_cursor_sends_it(repo):
    payload = base("afterShellExecution", cwd="/actual",
                   workspace_roots=["/other"])
    assert cursor.workspace(payload) == "/actual"


# -- translation ---------------------------------------------------------------

def test_the_first_prompt_opens_the_run_with_what_was_asked(repo):
    got = cursor.on_prompt(base("beforeSubmitPrompt",
                                prompt="Write a shard loader."), CFG)
    assert got == [{"type": "session", "agent": "cursor",
                    "task": "Write a shard loader."}]


def test_later_prompts_are_activity_without_reopening_the_run(repo):
    cursor.write_session("conv-1", {"opened": True, "task": "first"})
    assert cursor.on_prompt(base("beforeSubmitPrompt", prompt="and now"), CFG) == [{
        "type": "prompt", "prompt": "and now", "turn_id": "gen-1",
    }]


def test_prompt_text_is_bounded_to_the_server_contract(repo):
    cursor.write_session("conv-1", {"opened": True, "task": "first"})
    event = cursor.on_prompt(
        base("beforeSubmitPrompt", prompt="x" * 5000), CFG,
    )[0]
    assert len(event["prompt"]) == 4096


def test_an_edit_records_the_file_as_it_now_stands_not_the_diff(repo):
    """Cursor sends `edits` as old_string/new_string pairs. Reconstructing
    the file from those would record this adapter's idea of the result
    rather than what the developer actually has."""
    src = repo / "loader.py"
    src.write_text("class Loader:\n    pass\n")
    got = cursor.on_file_edit(base(
        "afterFileEdit", file_path=str(src), workspace_roots=[str(repo)],
        edits=[{"old_string": "x", "new_string": "y"}]), CFG)
    assert got[0]["code"] == "class Loader:\n    pass\n"
    assert got[0]["module"] == "loader.py"


def test_code_carries_no_reason_because_the_hook_never_saw_one(repo):
    src = repo / "loader.py"
    src.write_text("class L:\n    pass\n")
    got = cursor.on_file_edit(base("afterFileEdit", file_path=str(src),
                                   workspace_roots=[str(repo)]), CFG)
    assert "because" not in got[0]


def test_a_reason_the_agent_volunteered_about_this_file_is_attached(repo):
    cursor.write_session("conv-1", {"opened": True,
                                    "claims": {"loader.py": "use-parquet"}})
    src = repo / "loader.py"
    src.write_text("class L:\n    pass\n")
    got = cursor.on_file_edit(base("afterFileEdit", file_path=str(src),
                                   workspace_roots=[str(repo)]), CFG)
    assert got[0]["because"] == "use-parquet"


def test_an_excluded_path_is_not_recorded(repo):
    secret = repo / "secrets"
    secret.mkdir()
    (secret / "key.py").write_text("TOKEN = 'x'\n")
    assert cursor.on_file_edit(base("afterFileEdit",
                                    file_path=str(secret / "key.py"),
                                    workspace_roots=[str(repo)]), CFG) == []


def test_a_shell_command_becomes_a_tool_event(repo):
    got = cursor.on_shell_done(base("afterShellExecution",
                                    command="pytest -q", output=""), CFG)
    assert got == [{"type": "tool", "name": "Shell", "detail": "pytest -q"}]


def test_a_pinned_install_also_records_the_package(repo):
    got = cursor.on_shell_done(base("afterShellExecution",
                                    command="pip install numpy==1.26.4"), CFG)
    assert {
        "type": "package", "package": "numpy", "version": "1.26.4",
        "ecosystem": "PyPI", "command": "pip install numpy==1.26.4",
    } in got


def test_an_unpinned_install_records_no_version(repo):
    got = cursor.on_shell_done(base("afterShellExecution",
                                    command="pip install requests"), CFG)
    assert all(e["type"] != "package" for e in got)


def test_a_failed_install_never_becomes_installed_package_evidence(repo):
    got = cursor.on_shell_done(base(
        "afterShellExecution", command="pip install numpy==1.26.4",
        exit_code=1,
    ), CFG)
    assert got[0]["failed"] is True
    assert all(event["type"] != "package" for event in got)


# -- the trap: silence means refusal under Cursor ------------------------------

def test_the_permission_hook_always_answers_out_loud(repo, monkeypatch):
    """Cursor treats unparseable output from a permission hook as a denial
    even with failClosed off. An adapter that stayed quiet would block every
    shell command in the editor."""
    monkeypatch.setattr(cursor, "ask_gate", lambda *a: None)
    got = cursor.on_before_shell(base("beforeShellExecution",
                                      command="pytest -q", cwd=str(repo)), CFG)
    assert got == {"permission": "allow"}


def test_unparseable_input_still_prints_an_allow(monkeypatch, capsys):
    """Nothing was understood, so nothing can be judged -- and under Cursor
    saying nothing is not the neutral option."""
    monkeypatch.setattr("sys.stdin", _Stdin("not json"))
    assert cursor.main() == 0
    assert json.loads(capsys.readouterr().out) == {"permission": "allow"}


def test_a_repository_that_never_opted_in_still_answers_the_permission_hook(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(base(
        "beforeShellExecution", command="pip install numpy==1.0",
        cwd=str(tmp_path)))))
    assert cursor.main() == 0
    assert json.loads(capsys.readouterr().out)["permission"] == "allow"


# -- refusing -------------------------------------------------------------------

def test_a_blocked_install_is_denied_with_a_message_for_both_audiences(
        repo, monkeypatch):
    """The developer needs to know why their command did not run; the agent
    needs enough to pick a different version rather than retry the same."""
    monkeypatch.setattr(cursor, "ask_gate", lambda *a: {
        "verdict": "block", "reasons": ["CVE-2018-18074 (high)"],
        "policy": "high and above refused"})
    got = cursor.on_before_shell(base(
        "beforeShellExecution", command="pip install requests==2.19.0",
        cwd=str(repo)), CFG)
    assert got["permission"] == "deny"
    assert "CVE-2018-18074" in got["user_message"]
    assert "different version" in got["agent_message"]


@pytest.mark.parametrize("verdict", ["allow", "warn", "unknown", "nonsense"])
def test_only_a_block_denies(repo, monkeypatch, verdict):
    monkeypatch.setattr(cursor, "ask_gate",
                        lambda *a: {"verdict": verdict, "reasons": []})
    got = cursor.on_before_shell(base(
        "beforeShellExecution", command="pip install numpy==1.0",
        cwd=str(repo)), CFG)
    assert got["permission"] == "allow"


def test_a_repository_may_keep_the_recorder_and_refuse_the_gate(repo,
                                                                monkeypatch):
    monkeypatch.setattr(cursor, "ask_gate",
                        lambda *a: {"verdict": "block", "reasons": ["x"]})
    got = cursor.on_before_shell(
        base("beforeShellExecution", command="pip install numpy==1.0",
             cwd=str(repo)), {"record": True, "gate": False})
    assert got["permission"] == "allow"


# -- the claim this adapter exists to test -------------------------------------

def test_both_editors_produce_the_same_events_for_the_same_work(repo):
    """The engine must not be able to tell which editor it is talking to,
    beyond the `agent` label. If this ever fails, one of the two adapters
    has started interpreting rather than translating."""
    src = repo / "loader.py"
    src.write_text("class Loader:\n    pass\n")

    from_cursor = cursor.on_file_edit(base(
        "afterFileEdit", file_path=str(src), workspace_roots=[str(repo)]), CFG)
    from_claude = claude._code_events({"file_path": str(src)}, str(repo),
                                      CFG, "conv-1")
    assert from_cursor == from_claude

    cursor_shell = cursor.on_shell_done(
        base("afterShellExecution", command="pip install numpy==1.26.4"), CFG)
    claude_shell = claude._command_events(
        {"command": "pip install numpy==1.26.4"})
    # only the tool's own name differs, because the editors call it different
    # things; every governed fact either side records is identical
    assert [e for e in cursor_shell if e["type"] == "package"] == \
           [e for e in claude_shell if e["type"] == "package"]


def test_both_adapters_agree_on_what_counts_as_an_install(repo):
    for command in ("pip install numpy==1.26.4", "pip install requests",
                    "sudo pip3 install --upgrade numpy==1.0 flask",
                    "pytest -q"):
        assert cursor.installs(command) == claude.installs(command), command


def test_both_adapters_fail_open_when_local_telemetry_state_fails(repo, monkeypatch):
    def fail(**kwargs):
        raise OSError("state volume is unavailable")

    monkeypatch.setattr(cursor.session_protocol, "send", fail)
    event = [{"type": "tool", "name": "Shell", "detail": "pytest -q"}]
    assert cursor.send("conv-fail-open", event, str(repo)) is None
    assert claude.send("conv-fail-open", event, str(repo)) is None


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text
