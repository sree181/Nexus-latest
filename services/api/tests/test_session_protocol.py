from __future__ import annotations

import importlib.util
import json
import os
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[3] / "cli"
if str(CLI) not in sys.path:
    sys.path.insert(0, str(CLI))

from meshagent_cli import protocol, state  # noqa: E402

_ENTRY_SPEC = importlib.util.spec_from_file_location(
    "meshagent_cli_entry", CLI / "meshagent.py"
)
assert _ENTRY_SPEC is not None and _ENTRY_SPEC.loader is not None
meshagent_cli_entry = importlib.util.module_from_spec(_ENTRY_SPEC)
_ENTRY_SPEC.loader.exec_module(meshagent_cli_entry)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    root.mkdir()
    monkeypatch.setenv("MESHAGENT_HOOK_HOME", str(tmp_path / "meshagent-home"))
    return str(root)


def reply(item: dict) -> dict:
    if item["kind"] == "start":
        return {
            "id": item["session_id"], "run_id": "run-1",
            "last_acked_sequence": 1,
        }
    sequence = item["body"]["events"][-1]["sequence"]
    return {"run_id": "run-1", "acknowledged_through": sequence}


def test_offline_opener_replays_before_activity_with_reserved_sequence(repo):
    posted: list[dict] = []
    assert protocol.send(
        agent="cursor", native_session="native-1",
        events=[{"type": "session", "task": "add retries"}],
        repository=repo, headers={}, timeout=0.1,
        post_fn=lambda item: None,
    ) is None

    protocol.send(
        agent="cursor", native_session="native-1",
        events=[{"type": "code", "module": "retry.py", "code": "x = 1\n"}],
        repository=repo, headers={}, timeout=0.1,
        post_fn=lambda item: posted.append(item) or reply(item),
    )

    assert [item["kind"] for item in posted] == ["start", "events"]
    assert posted[1]["body"]["events"][0]["sequence"] == 2
    assert state.drain(repository=repo, editor="cursor", session_id="native-1") == []


def test_unacknowledged_opener_is_reconstructed_before_later_activity(repo):
    assert protocol.send(
        agent="cursor", native_session="native-reconstruct",
        events=[{"type": "session", "task": "recover opener"}],
        repository=repo, headers={}, timeout=0.1,
        post_fn=lambda item: None,
    ) is None
    # Simulate operator error or local queue loss. The exact opening request is
    # retained in session state until the server acknowledges sequence 1.
    assert state.drain(
        repository=repo, editor="cursor", session_id="native-reconstruct"
    )

    posted: list[dict] = []
    result = protocol.send(
        agent="cursor", native_session="native-reconstruct",
        events=[{"type": "tool", "name": "pytest"}],
        repository=repo, headers={}, timeout=0.1,
        post_fn=lambda item: posted.append(item) or reply(item),
    )

    assert result is not None
    assert [item["kind"] for item in posted] == ["start", "events"]
    assert posted[1]["body"]["events"][0]["sequence"] == 2


def test_identical_file_saves_are_distinct_ordered_occurrences(repo):
    posted: list[dict] = []
    protocol.send(
        agent="cursor", native_session="native-2",
        events=[{"type": "session", "task": "save twice"}],
        repository=repo, headers={}, timeout=0.1,
        post_fn=lambda item: reply(item),
    )
    for _ in range(2):
        protocol.send(
            agent="cursor", native_session="native-2",
            events=[{"type": "code", "module": "same.py", "code": "x = 1\n"}],
            repository=repo, headers={}, timeout=0.1,
            post_fn=lambda item: posted.append(item) or reply(item),
        )

    events = [item["body"]["events"][0] for item in posted]
    assert [event["sequence"] for event in events] == [2, 3]
    assert events[0]["event_id"] != events[1]["event_id"]


def test_queue_is_removed_only_after_an_exact_server_acknowledgment(repo):
    assert protocol.send(
        agent="cursor", native_session="native-ack",
        events=[{"type": "session", "task": "verify acknowledgments"}],
        repository=repo, headers={}, timeout=0.1,
        post_fn=lambda item: {"id": item["session_id"]},
    ) is None
    queued = state.drain(
        repository=repo, editor="cursor", session_id="native-ack"
    )
    assert [item["kind"] for item in queued] == ["start"]

    state.enqueue(
        queued[0], repository=repo, editor="cursor", session_id="native-ack"
    )
    responses = iter((reply(queued[0]), {"acknowledged_through": 1}))
    assert protocol.send(
        agent="cursor", native_session="native-ack",
        events=[{"type": "tool", "name": "pytest"}],
        repository=repo, headers={}, timeout=0.1,
        post_fn=lambda item: next(responses),
    ) is None
    queued = state.drain(
        repository=repo, editor="cursor", session_id="native-ack"
    )
    assert [item["kind"] for item in queued] == ["events"]
    assert queued[0]["body"]["events"][0]["sequence"] == 2


def test_inflight_queue_is_recovered_before_newer_activity(repo):
    old = {"protocol": protocol.PROTOCOL, "kind": "start", "session": "native-3",
           "session_id": "ses_0123456789abcdef", "path": "/start", "body": {"id": "ses_0123456789abcdef"}}
    new = {"protocol": protocol.PROTOCOL, "kind": "events", "session": "native-3",
           "session_id": "ses_0123456789abcdef", "path": "/events", "body": {"events": []}}
    state.enqueue(old, repository=repo, editor="cursor", session_id="native-3")
    path = state.queue_path("native-3", repository=repo, editor="cursor")
    os.replace(path, path + ".inflight")
    state.enqueue(new, repository=repo, editor="cursor", session_id="native-3")

    with state.lease_queue(
        repository=repo, editor="cursor", session_id="native-3",
    ) as lease:
        assert [item["kind"] for item in lease.batches] == ["start", "events"]
        lease.remaining = []

    assert not os.path.exists(path + ".inflight")
    assert state.drain(repository=repo, editor="cursor", session_id="native-3") == []


def test_manual_replay_recovers_inflight_protocol_envelope(repo, monkeypatch):
    item = {
        "protocol": protocol.PROTOCOL,
        "kind": "start",
        "session": "native-cli-replay",
        "session_id": "ses_0123456789abcdef",
        "path": "/api/v1/developer/sessions",
        "body": {"id": "ses_0123456789abcdef"},
    }
    state.enqueue(
        item, repository=repo, editor="cursor", session_id="native-cli-replay"
    )
    path = state.queue_path(
        "native-cli-replay", repository=repo, editor="cursor"
    )
    os.replace(path, path + ".inflight")
    monkeypatch.setenv("MESHAGENT_TOKEN", "mesh_test.device-token")
    posted: list[tuple[dict, dict[str, str], float]] = []
    monkeypatch.setattr(
        meshagent_cli_entry.session_protocol,
        "post",
        lambda batch, *, headers, timeout: (
            posted.append((batch, headers, timeout))
            or {"id": batch["session_id"], "last_acked_sequence": 1}
        ),
    )

    assert path in state.queue_files()
    assert state.queue_summary() == (1, 1)
    assert meshagent_cli_entry.replay(SimpleNamespace(json=True)) == 0
    assert posted[0][0] == item
    assert posted[0][1]["Authorization"] == "Bearer mesh_test.device-token"
    assert not os.path.exists(path + ".inflight")
    assert path not in state.queue_files()


def test_bounded_queue_keeps_oldest_causal_prefix(repo, monkeypatch):
    monkeypatch.setenv("MESHAGENT_QUEUE_MAX_BATCHES", "2")
    opener = {"protocol": protocol.PROTOCOL, "kind": "start", "session": "native-4",
              "session_id": "ses_0123456789abcdef", "body": {"id": "ses_0123456789abcdef"}}
    state.enqueue(opener, repository=repo, editor="cursor", session_id="native-4")
    for sequence in range(2, 7):
        state.enqueue(
            {"protocol": protocol.PROTOCOL, "kind": "events", "session": "native-4",
             "session_id": "ses_0123456789abcdef",
             "body": {"events": [{"sequence": sequence}]}},
            repository=repo, editor="cursor", session_id="native-4",
        )

    queued = state.drain(repository=repo, editor="cursor", session_id="native-4")
    assert queued[0]["kind"] == "start"
    assert [item["body"]["events"][0]["sequence"] for item in queued[1:]] == [2]


def test_full_offline_queue_resumes_without_a_sequence_gap(repo, monkeypatch):
    monkeypatch.setenv("MESHAGENT_QUEUE_MAX_BATCHES", "2")
    offline = lambda item: None
    assert protocol.send(
        agent="cursor", native_session="native-5",
        events=[{"type": "session", "task": "bounded recovery"}],
        repository=repo, headers={}, timeout=0.1, post_fn=offline,
    ) is None
    for module in ("two.py", "dropped.py"):
        assert protocol.send(
            agent="cursor", native_session="native-5",
            events=[{"type": "code", "module": module, "code": "x = 1\n"}],
            repository=repo, headers={}, timeout=0.1, post_fn=offline,
        ) is None

    posted: list[dict] = []
    result = protocol.send(
        agent="cursor", native_session="native-5",
        events=[{"type": "code", "module": "recovered.py", "code": "x = 2\n"}],
        repository=repo, headers={}, timeout=0.1,
        post_fn=lambda item: posted.append(item) or reply(item),
    )

    assert result is not None
    assert [item["kind"] for item in posted] == ["start", "events", "events"]
    assert [
        item["body"]["events"][0]["sequence"]
        for item in posted if item["kind"] == "events"
    ] == [2, 3]
    assert posted[-1]["body"]["events"][0]["payload"]["path"] == "recovered.py"
    session = state.read_session("native-5", repository=repo, editor="cursor")
    assert session["queue_rejected_events"] == 1
    assert state.diagnostic()["queue_rejected_events"] == 1


def test_queue_write_before_sequence_state_crash_recovers_without_reuse(
    repo, monkeypatch,
):
    assert protocol.send(
        agent="cursor", native_session="native-reservation-crash",
        events=[{"type": "session", "task": "survive state crash"}],
        repository=repo, headers={}, timeout=0.1,
        post_fn=lambda item: reply(item),
    ) is not None

    original_update = state.update_session
    updates = 0

    def fail_after_queue(*args, **kwargs):
        nonlocal updates
        updates += 1
        if updates == 2:
            raise OSError("state write failed after queue admission")
        return original_update(*args, **kwargs)

    monkeypatch.setattr(state, "update_session", fail_after_queue)
    with pytest.raises(OSError, match="after queue admission"):
        protocol.send(
            agent="cursor", native_session="native-reservation-crash",
            events=[{"type": "code", "module": "queued.py", "code": "x = 1\n"}],
            repository=repo, headers={}, timeout=0.1,
            post_fn=lambda item: reply(item),
        )

    persisted = state.read_session(
        "native-reservation-crash", repository=repo, editor="cursor",
    )
    assert persisted["next_sequence"] == 2
    monkeypatch.setattr(state, "update_session", original_update)

    posted: list[dict] = []
    result = protocol.send(
        agent="cursor", native_session="native-reservation-crash",
        events=[{"type": "code", "module": "next.py", "code": "x = 2\n"}],
        repository=repo, headers={}, timeout=0.1,
        post_fn=lambda item: posted.append(item) or reply(item),
    )

    assert result is not None
    event_items = [item for item in posted if item["kind"] == "events"]
    assert [item["body"]["events"][0]["sequence"] for item in event_items] == [2, 3]
    assert [item["body"]["events"][0]["payload"]["path"] for item in event_items] == [
        "queued.py", "next.py",
    ]


def test_session_start_reuses_immutable_body_after_post_ack_crash(repo):
    posted: list[dict] = []
    first = protocol.send(
        agent="cursor", native_session="native-crash",
        events=[{"type": "session", "task": "original task"}],
        repository=repo, headers={}, timeout=0.1,
        post_fn=lambda item: posted.append(item) or reply(item),
    )
    assert first is not None

    # Simulate process death after the server accepted the opener but before an
    # editor-specific hook persisted its own `opened` bookkeeping.
    session = state.read_session(
        "native-crash", repository=repo, editor="cursor",
    )
    session["opened"] = False
    state.write_session(
        "native-crash", session, repository=repo, editor="cursor",
    )

    second = protocol.send(
        agent="cursor", native_session="native-crash",
        events=[{"type": "session", "task": "later prompt"}],
        repository=repo, headers={}, timeout=0.1,
        post_fn=lambda item: posted.append(item) or reply(item),
    )
    assert second is not None
    assert posted[0]["body"] == posted[1]["body"]
    persisted = state.read_session(
        "native-crash", repository=repo, editor="cursor",
    )
    assert persisted["opening_acknowledged"] is True
    assert persisted["opened"] is True
    assert persisted["task"] == "original task"
