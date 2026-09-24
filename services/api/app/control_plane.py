"""Durable workflow state for Analyst and CISO outcomes.

HyperMesh remains the evidence graph of record. This module stores the mutable
control-plane workflow around that evidence: cases, immutable case events,
policies, exceptions, approvals, remediation work, reports, and posture
snapshots. SQLite is intentionally used in v1 with a single-process lease; it
provides transactional semantics and a portable production baseline. A future
multi-replica deployment can move these tables to PostgreSQL without changing
the API contracts.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator


def _now() -> int:
    return int(time.time())


def _id(prefix: str) -> str:
    # Sortable enough for v1 and opaque on the wire. IDs are never security
    # tokens; entropy protects against accidental collision, not authorization.
    return f"{prefix}_{int(time.time() * 1000):013d}_{secrets.token_hex(6)}"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    return json.loads(value)


class StoreError(Exception):
    """A control-plane domain or optimistic-concurrency conflict."""


class Missing(StoreError):
    """A resource does not exist or is not visible."""


class VersionConflict(StoreError):
    """The caller acted on an old resource version."""


class SeparationConflict(StoreError):
    """A requester attempted to approve their own request."""


@dataclass(frozen=True)
class Origin:
    data_origin: str = "live"
    source_time: int = 0
    seeded_count: int = 0
    coverage_known: int = 0
    coverage_unknown: int = 0
    coverage_basis: str = "authenticated evidence"

    def wire(self) -> dict[str, Any]:
        body = asdict(self)
        return {
            "data_origin": body["data_origin"],
            "source_time": body["source_time"] or _now(),
            "seeded_count": body["seeded_count"],
            "coverage": {
                "known": body["coverage_known"],
                "unknown": body["coverage_unknown"],
                "basis": body["coverage_basis"],
            },
        }


class ControlPlane:
    """Transactional durable workflow store.

    Each public method holds a process lock and uses ``BEGIN IMMEDIATE`` for
    writes. Production starts one API writer per state directory; the startup
    lease protects the file-backed HyperMesh stores from multi-process writes.
    """

    def __init__(self, base: str) -> None:
        Path(base).mkdir(parents=True, exist_ok=True)
        self.path = os.path.join(base, "control-plane.sqlite3")
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def _init(self) -> None:
        # sqlite3.executescript manages its own transaction boundary, so it must
        # not run inside ``_write``'s explicit BEGIN/COMMIT pair.
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                  id TEXT PRIMARY KEY,
                  finding_id TEXT NOT NULL,
                  run_id TEXT NOT NULL,
                  title TEXT NOT NULL,
                  severity TEXT NOT NULL,
                  state TEXT NOT NULL,
                  assignee TEXT,
                  assignee_name TEXT,
                  sla_due_at INTEGER,
                  disposition TEXT,
                  version INTEGER NOT NULL,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  created_by TEXT NOT NULL,
                  origin TEXT NOT NULL DEFAULT 'live'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_case_per_finding
                  ON cases(finding_id, run_id)
                  WHERE state NOT IN ('closed');
                CREATE INDEX IF NOT EXISTS cases_priority
                  ON cases(state, severity, sla_due_at, updated_at);
                CREATE TABLE IF NOT EXISTS case_events (
                  id TEXT PRIMARY KEY,
                  case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                  actor TEXT NOT NULL,
                  actor_name TEXT NOT NULL,
                  action TEXT NOT NULL,
                  from_state TEXT,
                  to_state TEXT,
                  rationale TEXT NOT NULL,
                  evidence_ids TEXT NOT NULL,
                  at INTEGER NOT NULL,
                  correlation_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS case_events_case
                  ON case_events(case_id, at, id);
                CREATE TABLE IF NOT EXISTS policies (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  scope TEXT NOT NULL,
                  status TEXT NOT NULL,
                  active_version INTEGER NOT NULL,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  created_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS policy_versions (
                  policy_id TEXT NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
                  version INTEGER NOT NULL,
                  severity_threshold TEXT NOT NULL,
                  denied_licenses TEXT NOT NULL,
                  block_on_unknown INTEGER NOT NULL,
                  rationale TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  created_by TEXT NOT NULL,
                  PRIMARY KEY(policy_id, version)
                );
                CREATE TABLE IF NOT EXISTS exceptions (
                  id TEXT PRIMARY KEY,
                  policy_id TEXT NOT NULL,
                  scope TEXT NOT NULL,
                  rationale TEXT NOT NULL,
                  compensating_controls TEXT NOT NULL,
                  owner TEXT NOT NULL,
                  expires_at INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  requested_by TEXT NOT NULL,
                  approved_by TEXT,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                  id TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  resource_id TEXT NOT NULL,
                  requester TEXT NOT NULL,
                  requester_name TEXT NOT NULL,
                  status TEXT NOT NULL,
                  rationale TEXT NOT NULL,
                  approver TEXT,
                  decision_rationale TEXT,
                  version INTEGER NOT NULL,
                  expires_at INTEGER NOT NULL,
                  created_at INTEGER NOT NULL,
                  decided_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS approvals_status
                  ON approvals(status, expires_at, created_at);
                CREATE TABLE IF NOT EXISTS remediations (
                  id TEXT PRIMARY KEY,
                  case_id TEXT NOT NULL,
                  title TEXT NOT NULL,
                  owner TEXT NOT NULL,
                  due_at INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  target_revision TEXT,
                  evidence_ids TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  created_by TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reports (
                  id TEXT PRIMARY KEY,
                  title TEXT NOT NULL,
                  period_start INTEGER NOT NULL,
                  period_end INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  requested_by TEXT NOT NULL,
                  manifest TEXT NOT NULL,
                  digest TEXT NOT NULL,
                  created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS posture_snapshots (
                  id TEXT PRIMARY KEY,
                  captured_at INTEGER NOT NULL,
                  metrics TEXT NOT NULL,
                  origin TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_requests (
                  id TEXT PRIMARY KEY,
                  owner_subject TEXT NOT NULL,
                  owner_name TEXT NOT NULL,
                  session_id TEXT NOT NULL,
                  run_id TEXT,
                  repository_id TEXT NOT NULL,
                  repository_name TEXT NOT NULL,
                  policy_evaluation_id TEXT NOT NULL,
                  package TEXT NOT NULL,
                  package_version TEXT NOT NULL,
                  ecosystem TEXT NOT NULL,
                  verdict TEXT NOT NULL,
                  severity TEXT NOT NULL,
                  advisories_json TEXT NOT NULL,
                  affected_files_json TEXT NOT NULL,
                  reasons_json TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  rationale TEXT NOT NULL,
                  state TEXT NOT NULL,
                  analyst_subject TEXT,
                  analyst_name TEXT,
                  decision_rationale TEXT,
                  recommended_version TEXT,
                  exception_expires_at INTEGER,
                  verification_evidence_id TEXT,
                  evidence_snapshot_json TEXT NOT NULL DEFAULT '{}',
                  evidence_digest TEXT,
                  evidence_root_ulid TEXT,
                  version_counter INTEGER NOT NULL,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  UNIQUE(owner_subject, policy_evaluation_id)
                );
                CREATE INDEX IF NOT EXISTS review_requests_queue
                  ON review_requests(state, severity, updated_at DESC);
                CREATE INDEX IF NOT EXISTS review_requests_owner
                  ON review_requests(owner_subject, updated_at DESC);
                CREATE INDEX IF NOT EXISTS review_requests_package
                  ON review_requests(owner_subject, repository_id, ecosystem,
                                     package, package_version, state);
                CREATE TABLE IF NOT EXISTS review_events (
                  id TEXT PRIMARY KEY,
                  request_id TEXT NOT NULL REFERENCES review_requests(id)
                    ON DELETE CASCADE,
                  actor TEXT NOT NULL,
                  actor_name TEXT NOT NULL,
                  actor_role TEXT NOT NULL,
                  action TEXT NOT NULL,
                  from_state TEXT,
                  to_state TEXT NOT NULL,
                  rationale TEXT NOT NULL,
                  evidence_ids TEXT NOT NULL,
                  at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS review_events_request
                  ON review_events(request_id, at, id);
                CREATE TABLE IF NOT EXISTS review_projection_outbox (
                  event_id TEXT PRIMARY KEY REFERENCES review_events(id)
                    ON DELETE CASCADE,
                  request_id TEXT NOT NULL REFERENCES review_requests(id)
                    ON DELETE CASCADE,
                  run_id TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  payload_sha256 TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending',
                  attempts INTEGER NOT NULL DEFAULT 0,
                  next_attempt_at INTEGER,
                  native_ulid TEXT,
                  error TEXT,
                  created_at INTEGER NOT NULL,
                  projected_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS review_projection_ready
                  ON review_projection_outbox(status,next_attempt_at,created_at);
                """
                )
                existing = {
                    str(row[1]) for row in conn.execute(
                        "PRAGMA table_info(review_requests)"
                    ).fetchall()
                }
                for column, ddl in (
                    ("evidence_snapshot_json",
                     "ALTER TABLE review_requests ADD COLUMN evidence_snapshot_json TEXT NOT NULL DEFAULT '{}'"),
                    ("evidence_digest",
                     "ALTER TABLE review_requests ADD COLUMN evidence_digest TEXT"),
                    ("evidence_root_ulid",
                     "ALTER TABLE review_requests ADD COLUMN evidence_root_ulid TEXT"),
                ):
                    if column not in existing:
                        conn.execute(ddl)
                legacy_reviews = conn.execute(
                    "SELECT * FROM review_requests WHERE evidence_digest IS NULL"
                ).fetchall()
                for review in legacy_reviews:
                    frozen = {
                        "session_id": review["session_id"],
                        "repository_id": review["repository_id"],
                        "policy_evaluation_id": review["policy_evaluation_id"],
                        "package": review["package"],
                        "version": review["package_version"],
                        "ecosystem": review["ecosystem"],
                        "advisory_ids": [
                            item["id"] for item in _loads(
                                review["advisories_json"], [],
                            ) if item.get("id")
                        ],
                        # Historical labels cannot be promoted to native entity
                        # IDs without inventing a relationship that was not stored.
                        "code_entity_ids": [],
                    }
                    frozen_json = _json(frozen)
                    digest = hashlib.sha256(frozen_json.encode()).hexdigest()
                    conn.execute(
                        "UPDATE review_requests SET evidence_snapshot_json=?, "
                        "evidence_digest=? WHERE id=?",
                        (frozen_json, digest, review["id"]),
                    )
                    if not review["run_id"]:
                        continue
                    events = conn.execute(
                        "SELECT * FROM review_events WHERE request_id=? ORDER BY at,id",
                        (review["id"],),
                    ).fetchall()
                    for event in events:
                        payload = {
                            "request_id": review["id"], "event_id": event["id"],
                            "action": event["action"], "to_state": event["to_state"],
                            "actor": event["actor"],
                            "actor_role": event["actor_role"], "at": event["at"],
                            "snapshot": frozen, "evidence_digest": digest,
                        }
                        canonical = _json(payload)
                        conn.execute(
                            """INSERT OR IGNORE INTO review_projection_outbox
                            (event_id,request_id,run_id,payload_json,payload_sha256,created_at)
                            VALUES (?,?,?,?,?,?)""",
                            (event["id"], review["id"], review["run_id"], canonical,
                             hashlib.sha256(canonical.encode()).hexdigest(), event["at"]),
                        )
            finally:
                conn.close()

    @staticmethod
    def _case(row: sqlite3.Row) -> dict[str, Any]:
        body = dict(row)
        now = _now()
        body["overdue"] = bool(
            body.get("sla_due_at")
            and body["sla_due_at"] < now
            and body["state"] not in ("resolved", "closed")
        )
        severity_weight = {"critical": 40, "high": 30, "medium": 20, "low": 10}.get(
            body["severity"], 0
        )
        state_weight = {"open": 20, "triaged": 12, "investigating": 8,
                        "remediation": 6, "resolved": -30, "closed": -40}.get(
            body["state"], 0
        )
        aging_days = max(0, (now - body["created_at"]) // 86400)
        score = severity_weight + state_weight + min(20, aging_days)
        reasons = [f"{body['severity']} severity", f"state: {body['state']}"]
        if not body.get("assignee"):
            score += 10
            reasons.append("unassigned")
        if body["overdue"]:
            score += 25
            reasons.append("SLA overdue")
        body["priority"] = score
        body["priority_reasons"] = reasons
        return body

    def create_case(self, *, finding_id: str, run_id: str, title: str,
                    severity: str, rationale: str, actor: str,
                    actor_name: str, correlation_id: str,
                    origin: str = "live") -> dict[str, Any]:
        now = _now()
        case_id, event_id = _id("case"), _id("evt")
        with self._write() as conn:
            existing = conn.execute(
                "SELECT * FROM cases WHERE finding_id=? AND run_id=? AND state NOT IN ('closed')",
                (finding_id, run_id),
            ).fetchone()
            if existing:
                return self._case(existing)
            conn.execute(
                """INSERT INTO cases
                (id,finding_id,run_id,title,severity,state,version,created_at,updated_at,
                 created_by,origin) VALUES (?,?,?,?,?,'open',1,?,?,?,?)""",
                (case_id, finding_id, run_id, title, severity, now, now, actor, origin),
            )
            conn.execute(
                """INSERT INTO case_events
                (id,case_id,actor,actor_name,action,from_state,to_state,rationale,
                 evidence_ids,at,correlation_id)
                VALUES (?,?,?,?,?,'','open',?,'[]',?,?)""",
                (event_id, case_id, actor, actor_name, "case.created", rationale,
                 now, correlation_id),
            )
            row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        assert row is not None
        return self._case(row)

    def list_cases(self, *, state: str | None = None, assignee: str | None = None,
                   limit: int = 100) -> list[dict[str, Any]]:
        clauses, args = [], []
        if state:
            clauses.append("state=?")
            args.append(state)
        if assignee:
            clauses.append("assignee=?")
            args.append(assignee)
        sql = "SELECT * FROM cases"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        args.append(max(1, min(limit, 100)))
        with self._connect() as conn:
            return [self._case(row) for row in conn.execute(sql, args).fetchall()]

    def case(self, case_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if row is None:
                raise Missing("unknown case")
            events = [dict(item) for item in conn.execute(
                "SELECT * FROM case_events WHERE case_id=? ORDER BY at,id", (case_id,)
            ).fetchall()]
        body = self._case(row)
        for event in events:
            event["evidence_ids"] = _loads(event["evidence_ids"], [])
        body["events"] = events
        return body

    def transition_case(self, case_id: str, *, expected_version: int,
                        to_state: str, disposition: str | None, rationale: str,
                        evidence_ids: list[str], actor: str, actor_name: str,
                        correlation_id: str) -> dict[str, Any]:
        allowed = {
            "open": {"triaged", "investigating", "closed"},
            "triaged": {"investigating", "remediation", "closed"},
            "investigating": {"remediation", "resolved", "closed"},
            "remediation": {"resolved", "investigating", "closed"},
            "resolved": {"reopened", "closed"},
            "reopened": {"investigating", "remediation", "closed"},
            "closed": {"reopened"},
        }
        now = _now()
        with self._write() as conn:
            current = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if current is None:
                raise Missing("unknown case")
            if current["version"] != expected_version:
                raise VersionConflict("case changed; reload before updating it")
            if to_state not in allowed.get(current["state"], set()):
                raise StoreError(f"cannot move a case from {current['state']} to {to_state}")
            if to_state in ("resolved", "closed") and (
                not disposition or not rationale.strip() or not evidence_ids
            ):
                raise StoreError(
                    "resolution requires disposition, rationale, and verification evidence"
                )
            normalized = "investigating" if to_state == "reopened" else to_state
            version = expected_version + 1
            conn.execute(
                """UPDATE cases SET state=?,disposition=?,version=?,updated_at=?
                   WHERE id=?""",
                (normalized, disposition, version, now, case_id),
            )
            conn.execute(
                """INSERT INTO case_events
                (id,case_id,actor,actor_name,action,from_state,to_state,rationale,
                 evidence_ids,at,correlation_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (_id("evt"), case_id, actor, actor_name, "case.transition",
                 current["state"], normalized, rationale, _json(evidence_ids), now,
                 correlation_id),
            )
        return self.case(case_id)

    def assign_case(self, case_id: str, *, expected_version: int, assignee: str,
                    assignee_name: str, sla_due_at: int | None, actor: str,
                    actor_name: str, correlation_id: str) -> dict[str, Any]:
        now = _now()
        with self._write() as conn:
            current = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if current is None:
                raise Missing("unknown case")
            if current["version"] != expected_version:
                raise VersionConflict("case changed; reload before updating it")
            conn.execute(
                """UPDATE cases SET assignee=?,assignee_name=?,sla_due_at=?,
                   version=?,updated_at=? WHERE id=?""",
                (assignee, assignee_name, sla_due_at, expected_version + 1, now, case_id),
            )
            conn.execute(
                """INSERT INTO case_events
                (id,case_id,actor,actor_name,action,from_state,to_state,rationale,
                 evidence_ids,at,correlation_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (_id("evt"), case_id, actor, actor_name, "case.assigned",
                 current["state"], current["state"], f"assigned to {assignee_name}",
                 "[]", now, correlation_id),
            )
        return self.case(case_id)

    def create_policy(self, *, name: str, scope: str, severity_threshold: str,
                      denied_licenses: list[str], block_on_unknown: bool,
                      rationale: str, actor: str) -> dict[str, Any]:
        policy_id, now = _id("pol"), _now()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO policies VALUES (?,?,?,'active',1,?,?,?)",
                (policy_id, name, scope, now, now, actor),
            )
            conn.execute(
                "INSERT INTO policy_versions VALUES (?,?,?,?,?,?,?,?)",
                (policy_id, 1, severity_threshold, _json(denied_licenses),
                 int(block_on_unknown), rationale, now, actor),
            )
        return self.policy(policy_id)

    def policies(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            ids = [row["id"] for row in conn.execute(
                "SELECT id FROM policies ORDER BY updated_at DESC,id DESC"
            ).fetchall()]
        return [self.policy(policy_id) for policy_id in ids]

    def policy(self, policy_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM policies WHERE id=?", (policy_id,)).fetchone()
            if row is None:
                raise Missing("unknown policy")
            version = conn.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND version=?",
                (policy_id, row["active_version"]),
            ).fetchone()
        body = dict(row)
        assert version is not None
        body["current"] = dict(version)
        body["current"]["denied_licenses"] = _loads(version["denied_licenses"], [])
        body["current"]["block_on_unknown"] = bool(version["block_on_unknown"])
        return body

    def create_exception(self, *, policy_id: str, scope: str, rationale: str,
                         controls: str, owner: str, expires_at: int,
                         actor: str, actor_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if expires_at <= _now():
            raise StoreError("exception expiry must be in the future")
        exception_id, approval_id, now = _id("exc"), _id("apr"), _now()
        with self._write() as conn:
            if conn.execute("SELECT 1 FROM policies WHERE id=?", (policy_id,)).fetchone() is None:
                raise Missing("unknown policy")
            conn.execute(
                """INSERT INTO exceptions VALUES
                (?,?,?,?,?,?,?,'pending',1,?,NULL,?,?)""",
                (exception_id, policy_id, scope, rationale, controls, owner,
                 expires_at, actor, now, now),
            )
            conn.execute(
                """INSERT INTO approvals VALUES
                (?,?,?, ?,?,'pending',?,NULL,NULL,1,?,?,NULL)""",
                (approval_id, "exception", exception_id, actor, actor_name,
                 rationale, min(expires_at, now + 604800), now),
            )
        return self.exception(exception_id), self.approval(approval_id)

    def exceptions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM exceptions ORDER BY updated_at DESC,id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def exception(self, exception_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM exceptions WHERE id=?", (exception_id,)).fetchone()
        if row is None:
            raise Missing("unknown exception")
        return dict(row)

    def approvals(self, status: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM approvals", []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC,id DESC"
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def approval(self, approval_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            raise Missing("unknown approval")
        return dict(row)

    def decide_approval(self, approval_id: str, *, expected_version: int,
                        decision: str, rationale: str, actor: str) -> dict[str, Any]:
        now = _now()
        with self._write() as conn:
            current = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if current is None:
                raise Missing("unknown approval")
            if current["version"] != expected_version:
                raise VersionConflict("approval changed; reload before deciding")
            if current["status"] != "pending" or current["expires_at"] <= now:
                raise StoreError("approval is no longer pending")
            if current["requester"] == actor:
                raise SeparationConflict("requesters cannot approve their own request")
            status = "approved" if decision == "approve" else "rejected"
            conn.execute(
                """UPDATE approvals SET status=?,approver=?,decision_rationale=?,
                   version=?,decided_at=? WHERE id=?""",
                (status, actor, rationale, expected_version + 1, now, approval_id),
            )
            if current["kind"] == "exception":
                conn.execute(
                    """UPDATE exceptions SET status=?,approved_by=?,version=version+1,
                       updated_at=? WHERE id=?""",
                    (status, actor if status == "approved" else None, now,
                     current["resource_id"]),
                )
        return self.approval(approval_id)

    def create_remediation(self, *, case_id: str, title: str, owner: str,
                           due_at: int, target_revision: str | None,
                           actor: str) -> dict[str, Any]:
        if due_at <= _now():
            raise StoreError("remediation due date must be in the future")
        remediation_id, now = _id("rem"), _now()
        with self._write() as conn:
            if conn.execute("SELECT 1 FROM cases WHERE id=?", (case_id,)).fetchone() is None:
                raise Missing("unknown case")
            conn.execute(
                """INSERT INTO remediations VALUES
                (?,?,?,?,?,'accepted',?,'[]',1,?,?,?)""",
                (remediation_id, case_id, title, owner, due_at,
                 target_revision, actor, now, now),
            )
        return self.remediation(remediation_id)

    def remediations(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM remediations ORDER BY updated_at DESC,id DESC"
            ).fetchall()
        return [self._remediation(row) for row in rows]

    def remediation(self, remediation_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM remediations WHERE id=?", (remediation_id,)).fetchone()
        if row is None:
            raise Missing("unknown remediation")
        return self._remediation(row)

    @staticmethod
    def _remediation(row: sqlite3.Row) -> dict[str, Any]:
        body = dict(row)
        if body["due_at"] < _now() and body["status"] not in (
            "verified_remediated", "failed", "exception_covered"
        ):
            body["status"] = "overdue"
        body["evidence_ids"] = _loads(body["evidence_ids"], [])
        return body

    @staticmethod
    def _enqueue_review_projection(
        conn: sqlite3.Connection, *, event_id: str, request_id: str,
        run_id: str | None, payload: dict[str, Any], at: int,
    ) -> str:
        canonical = _json(payload)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        if run_id:
            conn.execute(
                """INSERT INTO review_projection_outbox
                (event_id,request_id,run_id,payload_json,payload_sha256,created_at)
                VALUES (?,?,?,?,?,?)""",
                (event_id, request_id, run_id, canonical, digest, at),
            )
        return digest

    @staticmethod
    def _review(row: sqlite3.Row, events: list[sqlite3.Row] | None = None) -> dict[str, Any]:
        body = dict(row)
        body["version"] = body.pop("package_version")
        body["version_counter"] = body.pop("version_counter")
        body["advisories"] = _loads(body.pop("advisories_json"), [])
        body["code_entities"] = _loads(body.pop("affected_files_json"), [])
        body["reasons"] = _loads(body.pop("reasons_json"), [])
        body["evidence_snapshot"] = _loads(
            body.pop("evidence_snapshot_json", None), {},
        )
        severity_weight = {
            "critical": 50, "high": 38, "medium": 24, "low": 12,
            "unknown": 18,
        }.get(body["severity"], 0)
        verdict_weight = {"block": 30, "unknown": 22, "warn": 12, "allow": 0}.get(
            body["verdict"], 0,
        )
        state_weight = {
            "waiting": 18, "escalated": 25, "changes_requested": 8,
            "exception_approved": 5, "not_approved": -20,
            "false_positive": -35, "verified": -45,
        }.get(body["state"], 0)
        file_weight = min(15, len(body["code_entities"]) * 2)
        age_days = max(0, (_now() - body["created_at"]) // 86400)
        body["priority"] = severity_weight + verdict_weight + state_weight + file_weight + min(15, age_days)
        reasons = [f"{body['severity']} severity", f"{body['state'].replace('_', ' ')}"]
        if body["code_entities"]:
            reasons.append(f"{len(body['code_entities'])} linked code item(s)")
        if age_days:
            reasons.append(f"waiting {age_days} day(s)")
        body["priority_reasons"] = reasons
        body["events"] = []
        if events is not None:
            body["events"] = []
            for event in events:
                item = dict(event)
                item["evidence_ids"] = _loads(item["evidence_ids"], [])
                body["events"].append(item)
        return body

    def create_review_request(
        self, *, snapshot: dict[str, Any], kind: str, rationale: str,
        actor: str, actor_name: str,
    ) -> dict[str, Any]:
        now = _now()
        request_id, event_id = _id("rev"), _id("rve")
        frozen = {
            "session_id": snapshot["session_id"],
            "repository_id": snapshot["repository_id"],
            "policy_evaluation_id": snapshot["policy_evaluation_id"],
            "package": snapshot["package"],
            "version": snapshot.get("version", ""),
            "ecosystem": snapshot.get("ecosystem", "PyPI"),
            "advisory_ids": [item["id"] for item in snapshot.get("advisories", [])],
            "code_entity_ids": [item["id"] for item in snapshot.get("code_entity_refs", [])],
        }
        frozen_json = _json(frozen)
        evidence_digest = hashlib.sha256(frozen_json.encode()).hexdigest()
        with self._write() as conn:
            existing = conn.execute(
                "SELECT * FROM review_requests WHERE owner_subject=? "
                "AND policy_evaluation_id=?",
                (actor, snapshot["policy_evaluation_id"]),
            ).fetchone()
            if existing is not None:
                return self._review(existing)
            conn.execute(
                """INSERT INTO review_requests (
                  id,owner_subject,owner_name,session_id,run_id,repository_id,
                  repository_name,policy_evaluation_id,package,package_version,
                  ecosystem,verdict,severity,advisories_json,affected_files_json,
                  reasons_json,kind,rationale,evidence_snapshot_json,evidence_digest,
                  state,version_counter,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'waiting',1,?,?)""",
                (
                    request_id, actor, actor_name, snapshot["session_id"],
                    snapshot.get("run_id"), snapshot["repository_id"],
                    snapshot["repository_name"], snapshot["policy_evaluation_id"],
                    snapshot["package"], snapshot.get("version", ""),
                    snapshot.get("ecosystem", "PyPI"), snapshot["verdict"],
                    snapshot.get("severity") or "unknown",
                    _json(snapshot.get("advisories", [])),
                    _json(snapshot.get("code_entities", [])),
                    _json(snapshot.get("reasons", [])), kind, rationale,
                    frozen_json, evidence_digest, now, now,
                ),
            )
            conn.execute(
                """INSERT INTO review_events
                (id,request_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at)
                VALUES (?,?,?,?,?,'review.requested',NULL,'waiting',?,?,?)""",
                (
                    event_id, request_id, actor, actor_name, "developer", rationale,
                    _json([snapshot["policy_evaluation_id"]]), now,
                ),
            )
            self._enqueue_review_projection(
                conn, event_id=event_id, request_id=request_id,
                run_id=snapshot.get("run_id"), at=now,
                payload={
                    "request_id": request_id, "event_id": event_id,
                    "action": "review.requested", "to_state": "waiting",
                    "actor": actor, "actor_role": "developer", "at": now,
                    "snapshot": frozen, "evidence_digest": evidence_digest,
                },
            )
            row = conn.execute(
                "SELECT * FROM review_requests WHERE id=?", (request_id,),
            ).fetchone()
        assert row is not None
        return self._review(row)

    def review_request(self, request_id: str, *, owner_subject: str | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM review_requests WHERE id=?", (request_id,),
            ).fetchone()
            if row is None or (owner_subject is not None and row["owner_subject"] != owner_subject):
                raise Missing("unknown review request")
            events = conn.execute(
                "SELECT * FROM review_events WHERE request_id=? ORDER BY at,id",
                (request_id,),
            ).fetchall()
        return self._review(row, events)

    def review_for_evaluation(self, owner_subject: str, evaluation_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM review_requests WHERE owner_subject=? "
                "AND policy_evaluation_id=?",
                (owner_subject, evaluation_id),
            ).fetchone()
        return self._review(row) if row is not None else None

    def list_review_requests(
        self, *, owner_subject: str | None = None, state: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if owner_subject is not None:
            clauses.append("owner_subject=?")
            args.append(owner_subject)
        if state is not None:
            clauses.append("state=?")
            args.append(state)
        sql = "SELECT * FROM review_requests"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC,id DESC LIMIT ?"
        args.append(max(1, min(limit, 500)))
        with self._connect() as conn:
            values = [self._review(row) for row in conn.execute(sql, args).fetchall()]
        return sorted(values, key=lambda item: (-item["priority"], -item["updated_at"]))

    def decide_review_request(
        self, request_id: str, *, expected_version: int, decision: str,
        rationale: str, recommended_version: str | None,
        expires_at: int | None, actor: str, actor_name: str, actor_role: str,
    ) -> dict[str, Any]:
        states = {
            "request_changes": "changes_requested",
            "approve_exception": "exception_approved",
            "reject": "not_approved",
            "false_positive": "false_positive",
            "escalate": "escalated",
        }
        now = _now()
        if decision == "approve_exception" and (expires_at is None or expires_at <= now):
            raise StoreError("exception expiry must be in the future")
        with self._write() as conn:
            current = conn.execute(
                "SELECT * FROM review_requests WHERE id=?", (request_id,),
            ).fetchone()
            if current is None:
                raise Missing("unknown review request")
            if current["owner_subject"] == actor:
                raise SeparationConflict("developers cannot decide their own review request")
            if current["version_counter"] != expected_version:
                raise VersionConflict("review request changed; reload before deciding")
            if current["state"] in ("verified", "false_positive", "not_approved"):
                raise StoreError("review request is already terminal")
            target = states[decision]
            event_id = _id("rve")
            conn.execute(
                """UPDATE review_requests SET state=?,analyst_subject=?,
                analyst_name=?,decision_rationale=?,recommended_version=?,
                exception_expires_at=?,version_counter=version_counter+1,
                updated_at=? WHERE id=?""",
                (
                    target, actor, actor_name, rationale, recommended_version,
                    expires_at, now, request_id,
                ),
            )
            conn.execute(
                """INSERT INTO review_events
                (id,request_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at)
                VALUES (?,?,?,?,?,?,?,?,?,'[]',?)""",
                (
                    event_id, request_id, actor, actor_name, actor_role,
                    f"review.{decision}", current["state"], target, rationale, now,
                ),
            )
            snapshot = _loads(current["evidence_snapshot_json"], {})
            self._enqueue_review_projection(
                conn, event_id=event_id, request_id=request_id,
                run_id=current["run_id"], at=now,
                payload={
                    "request_id": request_id, "event_id": event_id,
                    "action": f"review.{decision}", "to_state": target,
                    "actor": actor, "actor_role": actor_role, "at": now,
                    "snapshot": snapshot,
                    "evidence_digest": current["evidence_digest"],
                },
            )
        return self.review_request(request_id)

    def verify_package_reviews(
        self, *, owner_subject: str, repository_id: str, ecosystem: str,
        package: str, version: str, evidence_id: str,
    ) -> list[str]:
        """Close matching actionable requests after a clean package check."""
        now = _now()
        verified: list[str] = []
        with self._write() as conn:
            rows = conn.execute(
                """SELECT * FROM review_requests
                WHERE owner_subject=? AND repository_id=? AND ecosystem=?
                  AND lower(package)=lower(?)
                  AND state IN ('waiting','changes_requested','exception_approved','escalated')""",
                (owner_subject, repository_id, ecosystem, package),
            ).fetchall()
            for row in rows:
                recommended = row["recommended_version"]
                if recommended and recommended != version:
                    continue
                event_id = _id("rve")
                conn.execute(
                    """UPDATE review_requests SET state='verified',
                    verification_evidence_id=?,version_counter=version_counter+1,
                    updated_at=? WHERE id=?""",
                    (evidence_id, now, row["id"]),
                )
                conn.execute(
                    """INSERT INTO review_events
                    (id,request_id,actor,actor_name,actor_role,action,from_state,
                     to_state,rationale,evidence_ids,at)
                    VALUES (?,?,?,?,?,'review.verified',?,'verified',?,?,?)""",
                    (
                        event_id, row["id"], "meshagent", "MeshAgent",
                        "system", row["state"],
                        f"A clean check confirmed {package}@{version}.",
                        _json([evidence_id]), now,
                    ),
                )
                snapshot = _loads(row["evidence_snapshot_json"], {})
                snapshot["verified_package"] = package
                snapshot["verified_version"] = version
                snapshot["verification_evidence_id"] = evidence_id
                self._enqueue_review_projection(
                    conn, event_id=event_id, request_id=row["id"],
                    run_id=row["run_id"], at=now,
                    payload={
                        "request_id": row["id"], "event_id": event_id,
                        "action": "review.verified", "to_state": "verified",
                        "actor": "meshagent", "actor_role": "system", "at": now,
                        "snapshot": snapshot,
                        "evidence_digest": row["evidence_digest"],
                    },
                )
                verified.append(row["id"])
        return verified

    def reset_interrupted_review_projections(self) -> int:
        with self._write() as conn:
            result = conn.execute(
                "UPDATE review_projection_outbox SET status='pending', "
                "error='projection interrupted before acknowledgement', "
                "next_attempt_at=NULL WHERE status='projecting'"
            )
            return int(result.rowcount)

    def recoverable_review_projections(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM review_projection_outbox WHERE "
                "status IN ('pending','failed') AND "
                "(next_attempt_at IS NULL OR next_attempt_at<=?) "
                "ORDER BY created_at,event_id LIMIT ?",
                (_now(), max(1, min(limit, 500))),
            ).fetchall()
        values: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = _loads(item.pop("payload_json"), {})
            values.append(item)
        return values

    def mark_review_projection_started(self, event_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE review_projection_outbox SET status='projecting', "
                "attempts=attempts+1,error=NULL,next_attempt_at=NULL "
                "WHERE event_id=? AND status IN ('pending','failed')",
                (event_id,),
            )

    def mark_review_projection_complete(self, event_id: str, native_ulid: str) -> None:
        now = _now()
        with self._write() as conn:
            row = conn.execute(
                "SELECT request_id,payload_json FROM review_projection_outbox "
                "WHERE event_id=?", (event_id,),
            ).fetchone()
            if row is None:
                raise Missing("unknown review projection")
            conn.execute(
                "UPDATE review_projection_outbox SET status='projected', "
                "native_ulid=?,projected_at=?,error=NULL,next_attempt_at=NULL "
                "WHERE event_id=?", (native_ulid, now, event_id),
            )
            payload = _loads(row["payload_json"], {})
            if payload.get("action") == "review.requested":
                conn.execute(
                    "UPDATE review_requests SET evidence_root_ulid=? WHERE id=?",
                    (native_ulid, row["request_id"]),
                )

    def mark_review_projection_failed(self, event_id: str, error: str) -> None:
        with self._write() as conn:
            row = conn.execute(
                "SELECT attempts FROM review_projection_outbox WHERE event_id=?",
                (event_id,),
            ).fetchone()
            attempts = int(row["attempts"]) if row is not None else 1
            delay = min(300, 2 ** min(max(attempts - 1, 0), 8))
            conn.execute(
                "UPDATE review_projection_outbox SET status='failed',error=?, "
                "next_attempt_at=? WHERE event_id=?",
                (error[:512], _now() + delay, event_id),
            )

    def review_projection_ulids(self, request_id: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT native_ulid FROM review_projection_outbox "
                "WHERE request_id=? AND status='projected' AND native_ulid IS NOT NULL "
                "ORDER BY created_at,event_id", (request_id,),
            ).fetchall()
        return [str(row["native_ulid"]) for row in rows]

    def create_report(self, *, title: str, period_start: int, period_end: int,
                      requested_by: str, manifest: dict[str, Any]) -> dict[str, Any]:
        if period_end <= period_start:
            raise StoreError("report period end must be after its start")
        report_id, now = _id("rpt"), _now()
        normalized = _json(manifest)
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO reports VALUES (?,?,?,?,'ready',?,?,?,?)",
                (report_id, title, period_start, period_end, requested_by,
                 normalized, digest, now),
            )
        return self.report(report_id)

    def reports(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,title,period_start,period_end,status,requested_by,digest,created_at "
                "FROM reports ORDER BY created_at DESC,id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def report(self, report_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if row is None:
            raise Missing("unknown report")
        body = dict(row)
        body["manifest"] = _loads(body["manifest"], {})
        return body

    def capture_posture(self, metrics: dict[str, Any], *, origin: str = "live") -> None:
        now = _now()
        with self._write() as conn:
            latest = conn.execute(
                "SELECT captured_at,metrics FROM posture_snapshots ORDER BY captured_at DESC LIMIT 1"
            ).fetchone()
            if latest and latest["captured_at"] > now - 300 and latest["metrics"] == _json(metrics):
                return
            conn.execute(
                "INSERT INTO posture_snapshots VALUES (?,?,?,?)",
                (_id("snap"), now, _json(metrics), origin),
            )

    def posture_trend(self, *, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM posture_snapshots ORDER BY captured_at DESC LIMIT ?",
                (max(1, min(limit, 90)),),
            ).fetchall()
        out = []
        for row in reversed(rows):
            body = dict(row)
            body["metrics"] = _loads(body["metrics"], {})
            out.append(body)
        return out

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) FROM approvals WHERE status='pending' AND expires_at>?",
                (_now(),),
            ).fetchone()[0]
            open_cases = conn.execute(
                "SELECT COUNT(*) FROM cases WHERE state NOT IN ('resolved','closed')"
            ).fetchone()[0]
            overdue = conn.execute(
                """SELECT COUNT(*) FROM cases WHERE sla_due_at IS NOT NULL
                   AND sla_due_at<? AND state NOT IN ('resolved','closed')""",
                (_now(),),
            ).fetchone()[0]
            active_exceptions = conn.execute(
                "SELECT COUNT(*) FROM exceptions WHERE status='approved' AND expires_at>?",
                (_now(),),
            ).fetchone()[0]
            remediation = conn.execute(
                "SELECT COUNT(*) FROM remediations WHERE status NOT IN ('verified_remediated','failed')"
            ).fetchone()[0]
        return {
            "open_cases": int(open_cases),
            "overdue_cases": int(overdue),
            "pending_approvals": int(pending),
            "active_exceptions": int(active_exceptions),
            "open_remediations": int(remediation),
        }


def load(base: str) -> ControlPlane:
    return ControlPlane(base)
