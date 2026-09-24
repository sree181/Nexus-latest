"""Durable single-writer ledger for connected coding-agent sessions.

Activity is committed here before it is projected into HyperMesh. The SQLite
constraints are the ordering and replay authority; HyperMesh remains the
immutable evidence and analysis substrate.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .auth import Principal
from .developer_session_models import (
    ActivityBatchRequest,
    ActivityEventIn,
    ActivityEventOut,
    ActivityListOut,
    DeveloperSessionOut,
    PolicyEvaluationListOut,
    PolicyEvaluationOut,
    RepositoryContext,
    SessionListOut,
    SessionStartRequest,
)

DATABASE = "developer-sessions.sqlite3"
MIGRATIONS = Path(__file__).with_name("migrations")


class SessionStoreError(Exception):
    """Base class for expected session-ledger failures."""


class MissingSession(SessionStoreError):
    pass


class SessionAccessDenied(SessionStoreError):
    pass


class ReplayConflict(SessionStoreError):
    pass


class SequenceConflict(SessionStoreError):
    def __init__(self, *, expected: int, received: int) -> None:
        self.expected = expected
        self.received = received
        super().__init__(f"expected sequence {expected}, received {received}")


class LifecycleConflict(SessionStoreError):
    pass


def _now_ms() -> int:
    return int(time.time() * 1000)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _start_event_id(req: SessionStartRequest) -> str:
    material = f"{req.id}\x1f{req.source_event_id}\x1fsession.started"
    return "evt_" + hashlib.sha256(material.encode()).hexdigest()


def _policy_id(event_id: str) -> str:
    return "pol_" + hashlib.sha256(event_id.encode()).hexdigest()[:32]


class Store:
    """SQLite-backed session ledger with serialized transactional writes."""

    def __init__(self, base: str) -> None:
        self.base = os.path.abspath(base)
        self.path = os.path.join(self.base, DATABASE)
        self._lock = threading.RLock()
        self._projection_lock = threading.RLock()
        os.makedirs(self.base, mode=0o700, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def projecting(self) -> Iterator[None]:
        """Serialize ordered projection in this single-writer API process."""
        with self._projection_lock:
            yield

    def _migrate(self) -> None:
        with self._write() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at_ms INTEGER NOT NULL)"
            )
            applied = {
                int(row["version"])
                for row in conn.execute("SELECT version FROM schema_migrations")
            }
            for path in sorted(MIGRATIONS.glob("*.sql")):
                try:
                    version = int(path.name.split("_", 1)[0])
                except ValueError:
                    continue
                if version in applied:
                    continue
                # executescript commits implicitly, so execute statements inside
                # this already-serialized initialization one by one.
                statements = [part.strip() for part in path.read_text().split(";")]
                for statement in statements:
                    if statement:
                        conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at_ms) VALUES (?, ?)",
                    (version, _now_ms()),
                )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _session(row: sqlite3.Row) -> DeveloperSessionOut:
        repository = RepositoryContext(
            id=row["repository_id"],
            name=row["repository_name"],
            remote=row["repository_remote"],
            branch=row["repository_branch"],
            commit=row["repository_commit"],
        )
        return DeveloperSessionOut(
            id=row["id"], owner_subject=row["owner_subject"],
            owner_name=row["owner_name"], adapter=row["adapter"],
            adapter_version=row["adapter_version"],
            source_session_id=row["source_session_id"], repository=repository,
            task=row["task"], status=row["status"],
            started_at_ms=row["started_at_ms"],
            last_seen_at_ms=row["last_seen_at_ms"],
            ended_at_ms=row["ended_at_ms"], next_sequence=row["next_sequence"],
            last_acked_sequence=row["last_acked_sequence"], run_id=row["run_id"],
            device_id=row["device_id"], verified=bool(row["verified"]),
            failure_reason=row["failure_reason"],
        )

    @staticmethod
    def _event(row: sqlite3.Row) -> ActivityEventOut:
        return ActivityEventOut(
            event_id=row["event_id"], source_event_id=row["source_event_id"],
            session_id=row["session_id"], sequence=row["sequence"],
            type=row["event_type"], occurred_at_ms=row["occurred_at_ms"],
            received_at_ms=row["received_at_ms"],
            payload=json.loads(row["payload_json"]),
            payload_sha256=row["payload_sha256"],
            projection_status=row["projection_status"],
            projection_attempts=row["projection_attempts"],
            projected_at_ms=row["projected_at_ms"],
            projection_error=row["projection_error"], run_id=row["run_id"],
        )

    def create(self, req: SessionStartRequest, who: Principal) -> tuple[DeveloperSessionOut, bool]:
        now = _now_ms()
        payload = {
            "task": req.task,
            "adapter": req.adapter,
            "adapter_version": req.adapter_version,
            "repository": req.repository.model_dump(),
        }
        event_id = _start_event_id(req)
        with self._write() as conn:
            existing = conn.execute(
                "SELECT * FROM developer_sessions WHERE id = ?", (req.id,)
            ).fetchone()
            if existing is not None:
                if existing["owner_subject"] != who.subject:
                    raise SessionAccessDenied(
                        f"unknown developer session {req.id}"
                    )
                opening = conn.execute(
                    "SELECT source_event_id, payload_sha256 FROM activity_events "
                    "WHERE session_id = ? AND sequence = 1", (req.id,),
                ).fetchone()
                expected = (
                    existing["owner_subject"], existing["adapter"],
                    existing["adapter_version"], existing["source_session_id"],
                    existing["repository_id"], existing["repository_name"],
                    existing["repository_remote"], existing["repository_branch"],
                    existing["repository_commit"], existing["task"],
                    existing["started_at_ms"],
                    opening["source_event_id"] if opening is not None else None,
                    opening["payload_sha256"] if opening is not None else None,
                )
                supplied = (
                    who.subject, req.adapter, req.adapter_version,
                    req.source_session_id, req.repository.id,
                    req.repository.name, req.repository.remote,
                    req.repository.branch, req.repository.commit, req.task,
                    req.started_at_ms, req.source_event_id,
                    _sha({"type": "session.started", "payload": payload}),
                )
                if expected != supplied:
                    raise ReplayConflict(
                        "session id was already used with different immutable fields"
                    )
                return self._session(existing), True

            collision = conn.execute(
                "SELECT id FROM developer_sessions WHERE owner_subject = ? "
                "AND adapter = ? AND repository_id = ? AND source_session_id = ?",
                (who.subject, req.adapter, req.repository.id, req.source_session_id),
            ).fetchone()
            if collision is not None:
                raise ReplayConflict(
                    f"source session is already bound to {collision['id']}"
                )

            conn.execute(
                """INSERT INTO developer_sessions (
                    id, owner_subject, owner_name, adapter, adapter_version,
                    source_session_id, repository_id, repository_name,
                    repository_remote, repository_branch, repository_commit,
                    task, status, started_at_ms, last_seen_at_ms, next_sequence,
                    last_acked_sequence, device_id, verified, created_at_ms,
                    updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'starting', ?, ?, 2, 1, ?, ?, ?, ?)""",
                (
                    req.id, who.subject, who.name, req.adapter, req.adapter_version,
                    req.source_session_id, req.repository.id, req.repository.name,
                    req.repository.remote, req.repository.branch,
                    req.repository.commit, req.task, req.started_at_ms, now,
                    who.device, int(who.verified), now, now,
                ),
            )
            body = _canonical(payload)
            conn.execute(
                """INSERT INTO activity_events (
                    event_id, source_event_id, session_id, sequence, event_type,
                    occurred_at_ms, received_at_ms, payload_json, payload_sha256,
                    projection_status, created_at_ms
                ) VALUES (?, ?, ?, 1, 'session.started', ?, ?, ?, ?, 'pending', ?)""",
                (event_id, req.source_event_id, req.id, req.started_at_ms, now,
                 body, _sha({"type": "session.started", "payload": payload}), now),
            )
            row = conn.execute(
                "SELECT * FROM developer_sessions WHERE id = ?", (req.id,)
            ).fetchone()
            assert row is not None
            return self._session(row), False

    def ingest(
        self, session_id: str, batch: ActivityBatchRequest, who: Principal
    ) -> tuple[list[ActivityEventOut], int, int]:
        now = _now_ms()
        accepted = duplicates = 0
        inserted: list[str] = []
        with self._write() as conn:
            session = self._owned(conn, session_id, who)
            expected = int(session["next_sequence"])
            for event in batch.events:
                payload = event.payload.model_dump(mode="json")
                digest = _sha({"type": event.type, "payload": payload})
                duplicate = conn.execute(
                    "SELECT * FROM activity_events WHERE event_id = ? OR "
                    "(session_id = ? AND source_event_id = ?)",
                    (event.event_id, session_id, event.source_event_id),
                ).fetchone()
                if duplicate is not None:
                    if (
                        duplicate["event_id"] != event.event_id
                        or duplicate["source_event_id"] != event.source_event_id
                        or duplicate["session_id"] != session_id
                        or duplicate["sequence"] != event.sequence
                        or duplicate["event_type"] != event.type
                        or duplicate["occurred_at_ms"] != event.occurred_at_ms
                        or duplicate["payload_sha256"] != digest
                    ):
                        raise ReplayConflict(
                            f"event {event.event_id} was replayed with different content"
                        )
                    duplicates += 1
                    continue

                if session["status"] in ("completed", "failed"):
                    raise LifecycleConflict(
                        f"session {session_id} is {session['status']} and accepts no new events"
                    )

                if event.sequence != expected:
                    raise SequenceConflict(expected=expected, received=event.sequence)
                if session["status"] == "ending":
                    raise LifecycleConflict("session is ending and accepts no later events")

                body = _canonical(payload)
                conn.execute(
                    """INSERT INTO activity_events (
                        event_id, source_event_id, session_id, sequence, event_type,
                        occurred_at_ms, received_at_ms, payload_json, payload_sha256,
                        projection_status, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                    (event.event_id, event.source_event_id, session_id,
                     event.sequence, event.type, event.occurred_at_ms, now,
                     body, digest, now),
                )
                if event.type == "policy.evaluated":
                    self._insert_policy(conn, session, event, payload, now)
                inserted.append(event.event_id)
                accepted += 1
                expected += 1
                if event.type == "session.ended":
                    conn.execute(
                        "UPDATE developer_sessions SET status='ending', "
                        "end_sequence=?, ended_at_ms=?, last_seen_at_ms=?, "
                        "next_sequence=?, last_acked_sequence=?, updated_at_ms=? "
                        "WHERE id=?",
                        (event.sequence, event.occurred_at_ms, now, expected,
                         event.sequence, now, session_id),
                    )
                else:
                    conn.execute(
                        "UPDATE developer_sessions SET status='active', "
                        "last_seen_at_ms=?, next_sequence=?, "
                        "last_acked_sequence=?, updated_at_ms=? WHERE id=?",
                        (now, expected, event.sequence, now, session_id),
                    )

        return self.events_by_ids(inserted), accepted, duplicates

    def _insert_policy(
        self, conn: sqlite3.Connection, session: sqlite3.Row,
        event: ActivityEventIn, payload: dict[str, Any], now: int,
    ) -> None:
        conn.execute(
            """INSERT INTO policy_evaluations (
                id, session_id, activity_event_id, package, version, verdict,
                reasons_json, advisories_json, worst, unavailable, policy,
                evaluated_at_ms, owner_subject, device_id, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _policy_id(event.event_id), session["id"], event.event_id,
                payload["package"], payload.get("version", ""), payload["verdict"],
                _canonical(payload.get("reasons", [])),
                _canonical(payload.get("advisories", [])), payload.get("worst"),
                payload.get("unavailable"), payload.get("policy", ""),
                event.occurred_at_ms, session["owner_subject"],
                session["device_id"], now,
            ),
        )

    @staticmethod
    def _owned(
        conn: sqlite3.Connection, session_id: str, who: Principal
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM developer_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise MissingSession(f"unknown developer session {session_id}")
        if row["owner_subject"] != who.subject:
            raise SessionAccessDenied(f"unknown developer session {session_id}")
        return row

    def get(self, session_id: str, who: Principal) -> DeveloperSessionOut:
        with self._read() as conn:
            return self._session(self._owned(conn, session_id, who))

    def list(self, who: Principal, *, limit: int = 100) -> SessionListOut:
        with self._read() as conn:
            total = int(conn.execute(
                "SELECT COUNT(*) AS count FROM developer_sessions "
                "WHERE owner_subject = ?", (who.subject,),
            ).fetchone()["count"])
            rows = conn.execute(
                "SELECT * FROM developer_sessions WHERE owner_subject = ? "
                "ORDER BY updated_at_ms DESC LIMIT ?", (who.subject, limit),
            ).fetchall()
        sessions = [self._session(row) for row in rows]
        return SessionListOut(sessions=sessions, total=total)

    def events(
        self, session_id: str, who: Principal, *, after_sequence: int = 0,
        limit: int = 200,
    ) -> ActivityListOut:
        with self._read() as conn:
            self._owned(conn, session_id, who)
            rows = conn.execute(
                "SELECT * FROM activity_events WHERE session_id = ? AND sequence > ? "
                "ORDER BY sequence ASC LIMIT ?",
                (session_id, after_sequence, limit + 1),
            ).fetchall()
        more = len(rows) > limit
        visible = rows[:limit]
        return ActivityListOut(
            events=[self._event(row) for row in visible],
            next_after_sequence=(visible[-1]["sequence"] if more and visible else None),
        )

    def events_by_ids(self, event_ids: Sequence[str]) -> list[ActivityEventOut]:
        if not event_ids:
            return []
        placeholders = ",".join("?" for _ in event_ids)
        with self._read() as conn:
            rows = conn.execute(
                f"SELECT * FROM activity_events WHERE event_id IN ({placeholders}) "
                "ORDER BY sequence", tuple(event_ids),
            ).fetchall()
        return [self._event(row) for row in rows]

    def projectable(self, session_id: str, *, limit: int = 100) -> list[ActivityEventOut]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM activity_events WHERE session_id = ? AND "
                "projection_status IN ('pending', 'failed') "
                "ORDER BY sequence ASC LIMIT ?", (session_id, limit),
            ).fetchall()
        return [self._event(row) for row in rows]

    def mark_projecting(self, event_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE activity_events SET projection_status='projecting', "
                "projection_attempts=projection_attempts+1, projection_error=NULL "
                "WHERE event_id=? AND projection_status IN ('pending','failed')",
                (event_id,),
            )

    def mark_projected(
        self, event_id: str, *, run_id: str, refused: str | None = None
    ) -> None:
        now = _now_ms()
        status = "refused" if refused else "projected"
        with self._write() as conn:
            event = conn.execute(
                "SELECT session_id, event_type FROM activity_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if event is None:
                raise MissingSession(f"unknown activity event {event_id}")
            conn.execute(
                "UPDATE activity_events SET projection_status=?, projected_at_ms=?, "
                "projection_error=?, run_id=? WHERE event_id=?",
                (status, now, refused, run_id, event_id),
            )
            end_status = "completed" if event["event_type"] == "session.ended" else "active"
            conn.execute(
                "UPDATE developer_sessions SET run_id=COALESCE(run_id, ?), "
                "status=CASE WHEN status='ending' AND ?='session.ended' THEN ? "
                "ELSE status END, updated_at_ms=? WHERE id=?",
                (run_id, event["event_type"], end_status, now, event["session_id"]),
            )

    def mark_projection_failed(self, event_id: str, error: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE activity_events SET projection_status='failed', "
                "projection_error=? WHERE event_id=?",
                (error[:512], event_id),
            )

    def bind_run(self, session_id: str, run_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE developer_sessions SET run_id=COALESCE(run_id, ?), "
                "updated_at_ms=? WHERE id=?", (run_id, _now_ms(), session_id),
            )

    def reset_interrupted_projections(self) -> int:
        with self._write() as conn:
            result = conn.execute(
                "UPDATE activity_events SET projection_status='pending', "
                "projection_error='projection interrupted before acknowledgement' "
                "WHERE projection_status='projecting'"
            )
            return int(result.rowcount)

    def recoverable_sessions(self, *, limit: int = 500) -> list[DeveloperSessionOut]:
        """Sessions that still have durable activity awaiting projection."""
        with self._read() as conn:
            rows = conn.execute(
                "SELECT DISTINCT s.* FROM developer_sessions s "
                "JOIN activity_events e ON e.session_id=s.id "
                "WHERE e.projection_status IN ('pending','failed') "
                "ORDER BY s.started_at_ms ASC LIMIT ?", (limit,),
            ).fetchall()
        return [self._session(row) for row in rows]

    def policy_evaluations(
        self, session_id: str, who: Principal, *, limit: int = 200
    ) -> PolicyEvaluationListOut:
        with self._read() as conn:
            self._owned(conn, session_id, who)
            rows = conn.execute(
                "SELECT * FROM policy_evaluations WHERE session_id=? "
                "ORDER BY evaluated_at_ms DESC LIMIT ?", (session_id, limit),
            ).fetchall()
        values = [
            PolicyEvaluationOut(
                id=row["id"], session_id=row["session_id"],
                activity_event_id=row["activity_event_id"], package=row["package"],
                version=row["version"], verdict=row["verdict"],
                reasons=json.loads(row["reasons_json"]),
                advisories=json.loads(row["advisories_json"]), worst=row["worst"],
                unavailable=row["unavailable"], policy=row["policy"],
                evaluated_at_ms=row["evaluated_at_ms"],
                owner_subject=row["owner_subject"], device_id=row["device_id"],
            )
            for row in rows
        ]
        return PolicyEvaluationListOut(evaluations=values, total=len(values))


def load(base: str) -> Store:
    return Store(base)
