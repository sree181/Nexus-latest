from __future__ import annotations

import copy
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import developer_sessions, engine_gateway, main
from app.auth import Principal
from app.developer_session_models import ActivityBatchRequest, SessionStartRequest


MAYA = Principal(
    subject="maya@company.com", name="Maya", email="maya@company.com",
    role="developer",
    verified=True, device="device-maya",
)
OTHER = Principal(
    subject="other@company.com", name="Other", email="other@company.com",
    role="developer",
    verified=True, device="device-other",
)
HEADERS = {
    "X-MeshAgent-User": MAYA.subject,
    "X-MeshAgent-Name": MAYA.name,
    "X-MeshAgent-Role": "developer",
}


def start_body(session_id: str = "ses_0123456789abcdef") -> dict:
    return {
        "id": session_id,
        "source_session_id": "cursor-conversation-42",
        "source_event_id": "cursor-session-start-42",
        "adapter": "cursor",
        "adapter_version": "1.0.0",
        "repository": {"id": "repo-payments-123", "name": "payments-api"},
        "task": "Add retry-safe payment capture",
        "started_at_ms": int(time.time() * 1000),
        "sequence": 1,
    }


def activity_batch(*, start: int = 2, include_end: bool = False) -> dict:
    events = [
        {
            "event_id": "evt_1111111111111111",
            "source_event_id": "cursor-decision-1",
            "sequence": start,
            "occurred_at_ms": int(time.time() * 1000),
            "type": "decision.recorded",
            "payload": {
                "decision_id": "retry-policy",
                "statement": "Retry only idempotent capture requests",
            },
        },
        {
            "event_id": "evt_2222222222222222",
            "source_event_id": "cursor-file-1",
            "sequence": start + 1,
            "occurred_at_ms": int(time.time() * 1000),
            "type": "file.changed",
            "payload": {
                "path": "src/payments.py",
                "operation": "update",
                "code": "def capture():\n    return 'ok'\n",
                "because": "retry-policy",
            },
        },
        {
            "event_id": "evt_3333333333333333",
            "source_event_id": "cursor-policy-1",
            "sequence": start + 2,
            "occurred_at_ms": int(time.time() * 1000),
            "type": "policy.evaluated",
            "payload": {
                "package": "httpx",
                "version": "0.28.1",
                "verdict": "allow",
                "reasons": ["no matching advisory"],
                "policy": "block high and critical reachable advisories",
                "advisories": [],
            },
        },
    ]
    if include_end:
        events.append(
            {
                "event_id": "evt_4444444444444444",
                "source_event_id": "cursor-session-end-42",
                "sequence": start + 3,
                "occurred_at_ms": int(time.time() * 1000),
                "type": "session.ended",
                "payload": {"reason": "normal"},
            }
        )
    return {"events": events}


@pytest.fixture
def store(tmp_path):
    return developer_sessions.Store(str(tmp_path / "ledger"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    ledger = developer_sessions.Store(str(tmp_path / "api-ledger"))
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "engine"))
    gateway = engine_gateway.EngineGateway()
    monkeypatch.setattr(main, "developer_session_store", ledger)
    monkeypatch.setattr(main, "gateway", gateway)
    return TestClient(main.app), ledger


def test_session_open_is_replay_safe_and_owner_scoped(store):
    original = start_body()
    req = SessionStartRequest.model_validate(original)
    first, replayed = store.create(req, MAYA)
    second, replayed_again = store.create(req, MAYA)
    assert replayed is False
    assert replayed_again is True
    assert first.id == second.id
    assert first.next_sequence == 2
    with pytest.raises(developer_sessions.SessionAccessDenied):
        store.get(first.id, OTHER)

    for divergent in (
        {"task": "different immutable task"},
        {"adapter_version": "9.9.9"},
        {"source_event_id": "cursor-session-start-other"},
        {"started_at_ms": original["started_at_ms"] + 1},
        {"repository": {**original["repository"], "name": "other-name"}},
        {"repository": {**original["repository"], "remote": "ssh://other"}},
        {"repository": {**original["repository"], "branch": "other-branch"}},
        {"repository": {**original["repository"], "commit": "def456"}},
    ):
        changed = {**original, **divergent}
        with pytest.raises(developer_sessions.ReplayConflict):
            store.create(SessionStartRequest.model_validate(changed), MAYA)

    with pytest.raises(developer_sessions.SessionAccessDenied):
        store.create(req, OTHER)


def test_ledger_enforces_sequence_replay_and_terminal_lifecycle(store):
    session, _ = store.create(SessionStartRequest.model_validate(start_body()), MAYA)
    batch = ActivityBatchRequest.model_validate(activity_batch(include_end=True))
    _, accepted, duplicates = store.ingest(session.id, batch, MAYA)
    assert (accepted, duplicates) == (4, 0)

    _, accepted, duplicates = store.ingest(session.id, batch, MAYA)
    assert (accepted, duplicates) == (0, 4)

    events = store.events(session.id, MAYA).events
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
    policies = store.policy_evaluations(session.id, MAYA)
    assert policies.total == 1
    assert policies.evaluations[0].verdict == "allow"

    store.mark_projected(events[-1].event_id, run_id="abcd")
    assert store.get(session.id, MAYA).status == "completed"

    late = activity_batch(start=6)
    late["events"] = [late["events"][0]]
    late["events"][0]["event_id"] = "evt_5555555555555555"
    late["events"][0]["source_event_id"] = "late-decision"
    with pytest.raises(developer_sessions.LifecycleConflict):
        store.ingest(session.id, ActivityBatchRequest.model_validate(late), MAYA)


def test_sequence_gap_and_divergent_event_replay_are_conflicts(store):
    session, _ = store.create(SessionStartRequest.model_validate(start_body()), MAYA)
    gap = activity_batch(start=3)
    gap["events"] = [gap["events"][0]]
    with pytest.raises(developer_sessions.SequenceConflict) as caught:
        store.ingest(session.id, ActivityBatchRequest.model_validate(gap), MAYA)
    assert caught.value.expected == 2

    first = activity_batch()
    first["events"] = [first["events"][0]]
    store.ingest(session.id, ActivityBatchRequest.model_validate(first), MAYA)
    for mutate in (
        lambda event: event["payload"].update(statement="different content"),
        lambda event: event.update(source_event_id="different-source"),
        lambda event: event.update(event_id="evt_9999999999999999"),
        lambda event: event.update(occurred_at_ms=event["occurred_at_ms"] + 1),
    ):
        changed = copy.deepcopy(first)
        mutate(changed["events"][0])
        with pytest.raises(developer_sessions.ReplayConflict):
            store.ingest(
                session.id, ActivityBatchRequest.model_validate(changed), MAYA
            )


def test_activity_batch_rejects_more_than_two_mebibytes():
    events = [
        {
            "event_id": f"evt_oversized{i:016d}",
            "source_event_id": f"source-{i}",
            "sequence": i + 2,
            "occurred_at_ms": int(time.time() * 1000),
            "type": "file.changed",
            "payload": {
                "path": f"src/generated-{i}.py",
                "operation": "update",
                "code": "x" * 200_000,
            },
        }
        for i in range(11)
    ]
    with pytest.raises(ValidationError, match="activity batch exceeds 2 MiB"):
        ActivityBatchRequest.model_validate({"events": events})


def test_api_projects_ordered_activity_and_exposes_policy_history(client):
    http, _ = client
    opened = http.post("/api/v1/developer/sessions", json=start_body(), headers=HEADERS)
    assert opened.status_code == 201, opened.text
    session_id = opened.json()["id"]
    assert opened.json()["run_id"]

    batch = activity_batch(include_end=True)
    recorded = http.post(
        f"/api/v1/developer/sessions/{session_id}/events",
        json=batch, headers=HEADERS,
    )
    assert recorded.status_code == 200, recorded.text
    receipt = recorded.json()
    assert receipt["accepted"] == 4
    assert receipt["acknowledged_through"] == 5
    assert receipt["next_sequence"] == 6
    assert receipt["session"]["status"] == "completed"

    replay = http.post(
        f"/api/v1/developer/sessions/{session_id}/events",
        json=batch, headers=HEADERS,
    )
    assert replay.status_code == 200
    assert replay.json()["accepted"] == 0
    assert replay.json()["duplicates"] == 4

    history = http.get(
        f"/api/v1/developer/sessions/{session_id}/events", headers=HEADERS,
    )
    assert history.status_code == 200
    assert [event["projection_status"] for event in history.json()["events"]] == [
        "projected", "projected", "projected", "projected", "projected",
    ]

    policies = http.get(
        f"/api/v1/developer/sessions/{session_id}/policy-evaluations",
        headers=HEADERS,
    )
    assert policies.status_code == 200
    assert policies.json()["total"] == 1

    hidden = http.get(
        f"/api/v1/developer/sessions/{session_id}",
        headers={
            "X-MeshAgent-User": OTHER.subject,
            "X-MeshAgent-Role": "developer",
        },
    )
    assert hidden.status_code == 404


def test_api_returns_expected_sequence_on_a_gap(client):
    http, _ = client
    session_id = http.post(
        "/api/v1/developer/sessions", json=start_body("ses_abcdef0123456789"),
        headers=HEADERS,
    ).json()["id"]
    gap = activity_batch(start=4)
    gap["events"] = [gap["events"][0]]
    response = http.post(
        f"/api/v1/developer/sessions/{session_id}/events",
        json=gap, headers=HEADERS,
    )
    assert response.status_code == 409
    assert response.json()["expected_sequence"] == 2
    assert response.json()["received_sequence"] == 4
