from __future__ import annotations

import copy
import sqlite3
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


@pytest.mark.parametrize(
    "path",
    ["/home/maya/secret.py", "../secret.py", "src/../secret.py",
     "C:/Users/Maya/secret.py", r"src\secret.py", "src//secret.py"],
)
def test_file_activity_requires_canonical_repository_relative_path(path):
    event = {
        "event_id": "evt_pathvalidation000001",
        "source_event_id": "source-path",
        "sequence": 2,
        "occurred_at_ms": int(time.time() * 1000),
        "type": "file.changed",
        "payload": {"path": path, "operation": "update", "code": "x = 1\n"},
    }
    with pytest.raises(ValidationError, match="repository-relative POSIX path"):
        ActivityBatchRequest.model_validate({"events": [event]})


def test_session_list_total_is_not_truncated_by_limit(store):
    for index in range(3):
        body = start_body(f"ses_totalcount{index:016d}")
        body["source_session_id"] = f"source-total-{index}"
        body["source_event_id"] = f"source-total-start-{index}"
        store.create(SessionStartRequest.model_validate(body), MAYA)
    result = store.list(MAYA, limit=1)
    assert len(result.sessions) == 1
    assert result.total == 3


def test_engine_run_correlation_uses_opaque_session_id_across_repositories(
    store, tmp_path, monkeypatch,
):
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "engine-correlation"))
    gateway = engine_gateway.EngineGateway()
    run_ids: list[str] = []
    for index in range(2):
        body = start_body(f"ses_correlation{index:016d}")
        body["repository"] = {
            "id": f"repo-correlation-{index}",
            "name": f"repository-{index}",
        }
        session, _ = store.create(SessionStartRequest.model_validate(body), MAYA)
        main.session_projection.project_pending(store, gateway, session, MAYA)
        projected = store.get(session.id, MAYA)
        assert projected.run_id is not None
        run_ids.append(projected.run_id)
    assert len(set(run_ids)) == 2


def test_projection_retry_migration_preserves_existing_run_correlation(tmp_path):
    base = tmp_path / "legacy-ledger"
    base.mkdir()
    database = base / developer_sessions.DATABASE
    with sqlite3.connect(database) as conn:
        conn.executescript(
            (developer_sessions.MIGRATIONS / "001_developer_sessions.sql").read_text()
        )
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at_ms) VALUES (1, 1)"
        )
        conn.execute(
            """INSERT INTO developer_sessions (
                id, owner_subject, owner_name, adapter, adapter_version,
                source_session_id, repository_id, repository_name, task, status,
                started_at_ms, last_seen_at_ms, next_sequence,
                last_acked_sequence, run_id, verified, created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "ses_legacyupgrade000001", MAYA.subject, MAYA.name, "cursor", "1",
                "legacy-native-session", "repo-legacy", "legacy", "upgrade",
                "active", 1, 1, 2, 1, "legacy-run", 1, 1, 1,
            ),
        )
        conn.execute(
            """INSERT INTO developer_sessions (
                id, owner_subject, owner_name, adapter, adapter_version,
                source_session_id, repository_id, repository_name, task, status,
                started_at_ms, last_seen_at_ms, next_sequence,
                last_acked_sequence, verified, created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "ses_legacyattempt000001", MAYA.subject, MAYA.name, "cursor", "1",
                "legacy-attempt-session", "repo-legacy-attempt", "legacy-attempt",
                "upgrade attempt", "starting", 1, 1, 2, 1, 1, 1, 1,
            ),
        )
        conn.execute(
            """INSERT INTO activity_events (
                event_id, source_event_id, session_id, sequence, event_type,
                occurred_at_ms, received_at_ms, payload_json, payload_sha256,
                projection_status, projection_attempts, created_at_ms
            ) VALUES (?, ?, ?, 1, 'session.started', 1, 1, '{}', 'legacy-sha',
                      'failed', 1, 1)""",
            (
                "evt_legacyattempt000001", "legacy-attempt-start",
                "ses_legacyattempt000001",
            ),
        )

    migrated = developer_sessions.Store(str(base))
    assert migrated.projection_session_key("ses_legacyupgrade000001") == (
        "legacy-native-session"
    )
    assert migrated.projection_session_key("ses_legacyattempt000001") == (
        "legacy-attempt-session"
    )
    with sqlite3.connect(database) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(activity_events)")
        }
    assert "projection_last_attempt_at_ms" in columns
    assert "projection_next_attempt_at_ms" in columns


def test_lifespan_retries_transient_projection_without_another_editor_event(
    tmp_path, monkeypatch,
):
    ledger = developer_sessions.Store(str(tmp_path / "retry-ledger"))
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "retry-engine"))
    monkeypatch.setenv("MESHAGENT_PROJECTION_RETRY_SECONDS", "0.05")
    monkeypatch.setenv("MESHAGENT_PROJECTION_RETRY_BASE_SECONDS", "0.05")
    gateway = engine_gateway.EngineGateway()
    original = gateway.record_events
    attempts = 0

    def transient(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary engine outage")
        return original(*args, **kwargs)

    monkeypatch.setattr(gateway, "record_events", transient)
    monkeypatch.setattr(main, "developer_session_store", ledger)
    monkeypatch.setattr(main, "gateway", gateway)

    with TestClient(main.app) as http:
        opened = http.post(
            "/api/v1/developer/sessions",
            json=start_body("ses_retryworker00000001"), headers=HEADERS,
        )
        assert opened.status_code == 201, opened.text
        assert opened.json()["run_id"] is None

        deadline = time.monotonic() + 3
        event = None
        while time.monotonic() < deadline:
            history = http.get(
                "/api/v1/developer/sessions/ses_retryworker00000001/events",
                headers=HEADERS,
            )
            event = history.json()["events"][0]
            if event["projection_status"] == "projected":
                break
            time.sleep(0.05)

        assert event is not None
        assert event["projection_status"] == "projected"
        assert event["projection_attempts"] == 2
        assert event["projection_last_attempt_at_ms"] is not None
        assert event["projection_next_attempt_at_ms"] is None
        session = http.get(
            "/api/v1/developer/sessions/ses_retryworker00000001",
            headers=HEADERS,
        ).json()
        assert session["run_id"] is not None
        assert attempts == 2


def test_lifespan_retries_terminal_projection_to_completed_run(
    tmp_path, monkeypatch,
):
    ledger = developer_sessions.Store(str(tmp_path / "terminal-retry-ledger"))
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "terminal-retry-engine"))
    monkeypatch.setenv("MESHAGENT_PROJECTION_RETRY_SECONDS", "0.05")
    monkeypatch.setenv("MESHAGENT_PROJECTION_RETRY_BASE_SECONDS", "0.05")
    gateway = engine_gateway.EngineGateway()
    original = gateway.record_events
    terminal_attempts = 0

    def transient_terminal(batch, *args, **kwargs):
        nonlocal terminal_attempts
        event = batch.events[0]
        if event.type == "session" and event.ends:
            terminal_attempts += 1
            if terminal_attempts == 1:
                raise RuntimeError("temporary terminal projection outage")
        return original(batch, *args, **kwargs)

    monkeypatch.setattr(gateway, "record_events", transient_terminal)
    monkeypatch.setattr(main, "developer_session_store", ledger)
    monkeypatch.setattr(main, "gateway", gateway)

    session_id = "ses_terminalretry000001"
    with TestClient(main.app) as http:
        opened = http.post(
            "/api/v1/developer/sessions",
            json=start_body(session_id), headers=HEADERS,
        )
        assert opened.status_code == 201, opened.text
        run_id = opened.json()["run_id"]
        assert run_id is not None

        recorded = http.post(
            f"/api/v1/developer/sessions/{session_id}/events",
            json=activity_batch(include_end=True), headers=HEADERS,
        )
        assert recorded.status_code == 200, recorded.text
        assert recorded.json()["session"]["status"] == "ending"

        deadline = time.monotonic() + 3
        session = recorded.json()["session"]
        while time.monotonic() < deadline:
            session = http.get(
                f"/api/v1/developer/sessions/{session_id}", headers=HEADERS,
            ).json()
            if session["status"] == "completed":
                break
            time.sleep(0.05)

        assert session["status"] == "completed"
        history = http.get(
            f"/api/v1/developer/sessions/{session_id}/events", headers=HEADERS,
        ).json()["events"]
        terminal = history[-1]
        assert terminal["type"] == "session.ended"
        assert terminal["projection_status"] == "projected"
        assert terminal["projection_attempts"] == 2
        assert terminal["projection_next_attempt_at_ms"] is None
        run = http.get(f"/api/runs/{run_id}", headers=HEADERS)
        assert run.status_code == 200
        assert run.json()["status"] == "complete"
        assert terminal_attempts == 2


def test_api_projects_ordered_activity_and_exposes_policy_history(client):
    http, _ = client
    opened = http.post("/api/v1/developer/sessions", json=start_body(), headers=HEADERS)
    assert opened.status_code == 201, opened.text
    session_id = opened.json()["id"]
    run_id = opened.json()["run_id"]
    assert run_id

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

    run = http.get(f"/api/runs/{run_id}", headers=HEADERS)
    assert run.status_code == 200
    assert run.json()["id"] == run_id

    hidden = http.get(
        f"/api/v1/developer/sessions/{session_id}",
        headers={
            "X-MeshAgent-User": OTHER.subject,
            "X-MeshAgent-Role": "developer",
        },
    )
    assert hidden.status_code == 404
    hidden_run = http.get(
        f"/api/runs/{run_id}",
        headers={
            "X-MeshAgent-User": OTHER.subject,
            "X-MeshAgent-Role": "developer",
        },
    )
    assert hidden_run.status_code == 404


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
