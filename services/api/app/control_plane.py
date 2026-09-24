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
                  assignee TEXT,
                  assignee_name TEXT,
                  sla_due_at INTEGER,
                  escalated_case_id TEXT,
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
                CREATE TABLE IF NOT EXISTS work_comments (
                  id TEXT PRIMARY KEY,
                  resource_kind TEXT NOT NULL CHECK(resource_kind IN ('review','case')),
                  resource_id TEXT NOT NULL,
                  actor TEXT NOT NULL,
                  actor_name TEXT NOT NULL,
                  actor_role TEXT NOT NULL,
                  message TEXT NOT NULL,
                  mentions_json TEXT NOT NULL DEFAULT '[]',
                  created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS work_comments_resource
                  ON work_comments(resource_kind,resource_id,created_at,id);
                CREATE TABLE IF NOT EXISTS saved_work_views (
                  id TEXT PRIMARY KEY,
                  owner_subject TEXT NOT NULL,
                  name TEXT NOT NULL,
                  filters_json TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  UNIQUE(owner_subject,name)
                );
                CREATE TABLE IF NOT EXISTS work_notifications (
                  id TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  title TEXT NOT NULL,
                  message TEXT NOT NULL,
                  resource_kind TEXT NOT NULL CHECK(resource_kind IN ('review','case')),
                  resource_id TEXT NOT NULL,
                  recipient_subject TEXT,
                  recipient_role TEXT,
                  not_before INTEGER NOT NULL,
                  created_at INTEGER NOT NULL,
                  dedupe_key TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS work_notifications_ready
                  ON work_notifications(not_before,recipient_subject,recipient_role);
                CREATE TABLE IF NOT EXISTS work_notification_reads (
                  notification_id TEXT NOT NULL REFERENCES work_notifications(id)
                    ON DELETE CASCADE,
                  subject TEXT NOT NULL,
                  read_at INTEGER NOT NULL,
                  PRIMARY KEY(notification_id,subject)
                );
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
                    ("assignee",
                     "ALTER TABLE review_requests ADD COLUMN assignee TEXT"),
                    ("assignee_name",
                     "ALTER TABLE review_requests ADD COLUMN assignee_name TEXT"),
                    ("sla_due_at",
                     "ALTER TABLE review_requests ADD COLUMN sla_due_at INTEGER"),
                    ("escalated_case_id",
                     "ALTER TABLE review_requests ADD COLUMN escalated_case_id TEXT"),
                ):
                    if column not in existing:
                        conn.execute(ddl)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS review_requests_assignment "
                    "ON review_requests(assignee,state,sla_due_at,updated_at DESC)"
                )
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
            self._notify(
                conn, kind="work.assigned", title="Case assigned to you",
                message=current["title"], resource_kind="case",
                resource_id=case_id, recipient_subject=assignee,
                dedupe_key=f"case-assigned:{case_id}:{expected_version + 1}",
            )
            if sla_due_at:
                self._notify(
                    conn, kind="sla.due", title="Case SLA is due soon",
                    message=current["title"], resource_kind="case",
                    resource_id=case_id, recipient_subject=assignee,
                    not_before=max(now, sla_due_at - 86_400),
                    dedupe_key=f"case-sla:{case_id}:{sla_due_at}",
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
    def _notify(
        conn: sqlite3.Connection, *, kind: str, title: str, message: str,
        resource_kind: str, resource_id: str, dedupe_key: str,
        recipient_subject: str | None = None,
        recipient_role: str | None = None, not_before: int | None = None,
    ) -> None:
        now = _now()
        conn.execute(
            """INSERT OR IGNORE INTO work_notifications
            (id,kind,title,message,resource_kind,resource_id,recipient_subject,
             recipient_role,not_before,created_at,dedupe_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _id("not"), kind, title, message, resource_kind, resource_id,
                recipient_subject, recipient_role, not_before or now, now,
                dedupe_key,
            ),
        )

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
        body["overdue"] = bool(
            body.get("sla_due_at")
            and body["sla_due_at"] < _now()
            and body["state"] not in ("verified", "false_positive", "not_approved")
        )
        assignment_weight = 10 if not body.get("assignee") else 0
        overdue_weight = 25 if body["overdue"] else 0
        body["priority"] = (
            severity_weight + verdict_weight + state_weight + file_weight
            + min(15, age_days) + assignment_weight + overdue_weight
        )
        reasons = [f"{body['severity']} severity", f"{body['state'].replace('_', ' ')}"]
        if body["code_entities"]:
            reasons.append(f"{len(body['code_entities'])} linked code item(s)")
        if age_days:
            reasons.append(f"waiting {age_days} day(s)")
        if not body.get("assignee"):
            reasons.append("unassigned")
        if body["overdue"]:
            reasons.append("SLA overdue")
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
            self._notify(
                conn, kind="review.created", title="Developer review ready",
                message=(
                    f"{actor_name} asked for help with {snapshot['package']}"
                    f"@{snapshot.get('version') or 'unpinned'}."
                ),
                resource_kind="review", resource_id=request_id,
                recipient_role="analyst", dedupe_key=f"review-created:{request_id}",
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

    def assign_review_request(
        self, request_id: str, *, expected_version: int, assignee: str,
        assignee_name: str, sla_due_at: int | None, actor: str,
        actor_name: str, actor_role: str,
    ) -> dict[str, Any]:
        now = _now()
        with self._write() as conn:
            current = conn.execute(
                "SELECT * FROM review_requests WHERE id=?", (request_id,),
            ).fetchone()
            if current is None:
                raise Missing("unknown review request")
            if current["version_counter"] != expected_version:
                raise VersionConflict("review request changed; reload before assigning it")
            if current["state"] in ("verified", "false_positive", "not_approved"):
                raise StoreError("terminal review requests cannot be reassigned")
            event_id = _id("rve")
            conn.execute(
                """UPDATE review_requests SET assignee=?,assignee_name=?,sla_due_at=?,
                version_counter=version_counter+1,updated_at=? WHERE id=?""",
                (assignee, assignee_name, sla_due_at, now, request_id),
            )
            rationale = f"Assigned to {assignee_name}."
            conn.execute(
                """INSERT INTO review_events
                (id,request_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at)
                VALUES (?,?,?,?,?,'review.assigned',?,?,?,'[]',?)""",
                (
                    event_id, request_id, actor, actor_name, actor_role,
                    current["state"], current["state"], rationale, now,
                ),
            )
            snapshot = _loads(current["evidence_snapshot_json"], {})
            self._enqueue_review_projection(
                conn, event_id=event_id, request_id=request_id,
                run_id=current["run_id"], at=now,
                payload={
                    "request_id": request_id, "event_id": event_id,
                    "action": "review.assigned", "to_state": current["state"],
                    "actor": actor, "actor_role": actor_role, "at": now,
                    "snapshot": snapshot,
                    "evidence_digest": current["evidence_digest"],
                },
            )
            self._notify(
                conn, kind="work.assigned", title="Review assigned to you",
                message=f"{current['package']}@{current['package_version']} needs review.",
                resource_kind="review", resource_id=request_id,
                recipient_subject=assignee,
                dedupe_key=f"review-assigned:{request_id}:{expected_version + 1}",
            )
            if sla_due_at:
                self._notify(
                    conn, kind="sla.due", title="Review SLA is due soon",
                    message=f"The review for {current['package']} is nearing its due time.",
                    resource_kind="review", resource_id=request_id,
                    recipient_subject=assignee,
                    not_before=max(now, sla_due_at - 86_400),
                    dedupe_key=f"review-sla:{request_id}:{sla_due_at}",
                )
        return self.review_request(request_id)

    def escalate_review_to_case(
        self, request_id: str, *, expected_version: int, title: str,
        rationale: str, assignee: str | None, assignee_name: str | None,
        sla_due_at: int | None, actor: str, actor_name: str,
        actor_role: str, correlation_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        now = _now()
        with self._write() as conn:
            current = conn.execute(
                "SELECT * FROM review_requests WHERE id=?", (request_id,),
            ).fetchone()
            if current is None:
                raise Missing("unknown review request")
            if current["version_counter"] != expected_version:
                raise VersionConflict("review request changed; reload before escalating it")
            if current["state"] in ("verified", "false_positive", "not_approved"):
                raise StoreError("terminal review requests cannot be escalated")
            if current["escalated_case_id"]:
                case_row = conn.execute(
                    "SELECT * FROM cases WHERE id=?", (current["escalated_case_id"],),
                ).fetchone()
                assert case_row is not None
                return self._review(current), self._case(case_row)
            if not current["run_id"]:
                raise StoreError("review evidence has not reached HyperMesh")
            case_id, case_event_id, review_event_id = _id("case"), _id("evt"), _id("rve")
            conn.execute(
                """INSERT INTO cases
                (id,finding_id,run_id,title,severity,state,assignee,assignee_name,
                 sla_due_at,version,created_at,updated_at,created_by,origin)
                VALUES (?,?,?,?,?,'open',?,?,?,1,?,?,?,'live')""",
                (
                    case_id, f"review:{request_id}", current["run_id"], title,
                    current["severity"], assignee, assignee_name, sla_due_at,
                    now, now, actor,
                ),
            )
            conn.execute(
                """INSERT INTO case_events
                (id,case_id,actor,actor_name,action,from_state,to_state,rationale,
                 evidence_ids,at,correlation_id)
                VALUES (?,?,?,?,?,'','open',?,?,?,?)""",
                (
                    case_event_id, case_id, actor, actor_name,
                    "case.created_from_review", rationale,
                    _json([request_id, current["policy_evaluation_id"]]), now,
                    correlation_id,
                ),
            )
            conn.execute(
                """UPDATE review_requests SET state='escalated',
                escalated_case_id=?,assignee=COALESCE(?,assignee),
                assignee_name=COALESCE(?,assignee_name),
                sla_due_at=COALESCE(?,sla_due_at),version_counter=version_counter+1,
                updated_at=? WHERE id=?""",
                (case_id, assignee, assignee_name, sla_due_at, now, request_id),
            )
            conn.execute(
                """INSERT INTO review_events
                (id,request_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at)
                VALUES (?,?,?,?,?,'review.escalated_to_case',?,'escalated',?,?,?)""",
                (
                    review_event_id, request_id, actor, actor_name, actor_role,
                    current["state"], rationale, _json([case_id]), now,
                ),
            )
            snapshot = _loads(current["evidence_snapshot_json"], {})
            snapshot["escalated_case_id"] = case_id
            self._enqueue_review_projection(
                conn, event_id=review_event_id, request_id=request_id,
                run_id=current["run_id"], at=now,
                payload={
                    "request_id": request_id, "event_id": review_event_id,
                    "action": "review.escalated_to_case", "to_state": "escalated",
                    "actor": actor, "actor_role": actor_role, "at": now,
                    "snapshot": snapshot,
                    "evidence_digest": current["evidence_digest"],
                },
            )
            self._notify(
                conn, kind="case.created", title="Review escalated to a case",
                message=title, resource_kind="case", resource_id=case_id,
                recipient_subject=assignee, recipient_role=None if assignee else "analyst",
                dedupe_key=f"review-case:{request_id}",
            )
            review_row = conn.execute(
                "SELECT * FROM review_requests WHERE id=?", (request_id,),
            ).fetchone()
            case_row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        assert review_row is not None and case_row is not None
        return self._review(review_row), self._case(case_row)

    def work_queue(
        self, *, kind: str | None = None, state: str | None = None,
        assignee: str | None = None, repository: str | None = None,
        severity: str | None = None, query: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        cases = self.list_cases(limit=100)
        reviews = self.list_review_requests(limit=500)
        values: list[dict[str, Any]] = []
        for item in reviews:
            values.append({
                "id": item["id"], "kind": "review",
                "title": f"{item['package']}@{item['version'] or 'unpinned'}",
                "subtitle": item["kind"].replace("_", " "),
                "severity": item["severity"], "state": item["state"],
                "priority": item["priority"],
                "priority_reasons": item["priority_reasons"],
                "version": item["version_counter"],
                "assignee": item.get("assignee"),
                "assignee_name": item.get("assignee_name"),
                "sla_due_at": item.get("sla_due_at"),
                "overdue": item.get("overdue", False),
                "repository_name": item["repository_name"],
                "developer_name": item["owner_name"],
                "route": f"/analyst/reviews/{item['id']}",
                "created_at": item["created_at"], "updated_at": item["updated_at"],
            })
        for item in cases:
            values.append({
                "id": item["id"], "kind": "case", "title": item["title"],
                "subtitle": item["finding_id"], "severity": item["severity"],
                "state": item["state"], "priority": item["priority"],
                "priority_reasons": item["priority_reasons"],
                "version": item["version"],
                "assignee": item.get("assignee"),
                "assignee_name": item.get("assignee_name"),
                "sla_due_at": item.get("sla_due_at"),
                "overdue": item.get("overdue", False),
                "repository_name": None, "developer_name": None,
                "route": f"/analyst/cases/{item['id']}",
                "created_at": item["created_at"], "updated_at": item["updated_at"],
            })
        needle = (query or "").strip().lower()
        filtered = [item for item in values if (
            (not kind or item["kind"] == kind)
            and (not state or item["state"] == state)
            and (
                not assignee
                or (assignee == "__unassigned__" and not item["assignee"])
                or item["assignee"] == assignee
            )
            and (not repository or item.get("repository_name") == repository)
            and (not severity or item["severity"] == severity)
            and (not needle or needle in " ".join(str(item.get(key) or "") for key in (
                "id", "title", "subtitle", "repository_name", "developer_name",
                "assignee_name",
            )).lower())
        )]
        filtered.sort(key=lambda item: (-item["priority"], -item["updated_at"], item["id"]))
        counts = {
            "all": len(values),
            "unassigned": sum(not item.get("assignee") for item in values),
            "overdue": sum(bool(item.get("overdue")) for item in values),
            "reviews": sum(item["kind"] == "review" for item in values),
            "cases": sum(item["kind"] == "case" for item in values),
        }
        return {"items": filtered[:max(1, min(limit, 500))], "total": len(filtered), "counts": counts}

    def add_work_comment(
        self, *, resource_kind: str, resource_id: str, message: str,
        mentions: list[str], actor: str, actor_name: str, actor_role: str,
    ) -> dict[str, Any]:
        now, comment_id = _now(), _id("cmt")
        unique_mentions = list(dict.fromkeys(value.strip() for value in mentions if value.strip()))[:20]
        with self._write() as conn:
            if resource_kind == "review":
                resource = conn.execute(
                    "SELECT owner_subject,assignee FROM review_requests WHERE id=?",
                    (resource_id,),
                ).fetchone()
            else:
                resource = conn.execute(
                    "SELECT created_by AS owner_subject,assignee FROM cases WHERE id=?",
                    (resource_id,),
                ).fetchone()
            if resource is None:
                raise Missing(f"unknown {resource_kind}")
            conn.execute(
                """INSERT INTO work_comments
                (id,resource_kind,resource_id,actor,actor_name,actor_role,message,
                 mentions_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    comment_id, resource_kind, resource_id, actor, actor_name,
                    actor_role, message, _json(unique_mentions), now,
                ),
            )
            recipients = set(unique_mentions)
            if resource["assignee"] and resource["assignee"] != actor:
                recipients.add(resource["assignee"])
            if resource["owner_subject"] != actor:
                recipients.add(resource["owner_subject"])
            for recipient in recipients:
                self._notify(
                    conn, kind="work.comment", title="New work note",
                    message=f"{actor_name}: {message[:180]}",
                    resource_kind=resource_kind, resource_id=resource_id,
                    recipient_subject=recipient,
                    dedupe_key=f"comment:{comment_id}:{recipient}",
                )
        return self.work_comments(resource_kind=resource_kind, resource_id=resource_id)[-1]

    def work_comments(self, *, resource_kind: str, resource_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            table = "review_requests" if resource_kind == "review" else "cases"
            resource = conn.execute(
                f"SELECT 1 FROM {table} WHERE id=?", (resource_id,),
            ).fetchone()
            if resource is None:
                raise Missing(f"unknown {resource_kind}")
            rows = conn.execute(
                "SELECT * FROM work_comments WHERE resource_kind=? AND resource_id=? "
                "ORDER BY created_at,id", (resource_kind, resource_id),
            ).fetchall()
        values = []
        for row in rows:
            item = dict(row)
            item["mentions"] = _loads(item.pop("mentions_json"), [])
            values.append(item)
        return values

    def save_work_view(
        self, *, owner_subject: str, name: str, filters: dict[str, str],
    ) -> dict[str, Any]:
        now, view_id = _now(), _id("view")
        clean = {str(key)[:64]: str(value)[:256] for key, value in filters.items() if value}
        with self._write() as conn:
            existing = conn.execute(
                "SELECT id,created_at FROM saved_work_views WHERE owner_subject=? AND name=?",
                (owner_subject, name),
            ).fetchone()
            if existing:
                view_id = existing["id"]
                conn.execute(
                    "UPDATE saved_work_views SET filters_json=?,updated_at=? WHERE id=?",
                    (_json(clean), now, view_id),
                )
            else:
                conn.execute(
                    "INSERT INTO saved_work_views VALUES (?,?,?,?,?,?)",
                    (view_id, owner_subject, name, _json(clean), now, now),
                )
        return self.work_view(view_id, owner_subject=owner_subject)

    def work_view(self, view_id: str, *, owner_subject: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM saved_work_views WHERE id=? AND owner_subject=?",
                (view_id, owner_subject),
            ).fetchone()
        if row is None:
            raise Missing("unknown saved view")
        item = dict(row)
        item["filters"] = _loads(item.pop("filters_json"), {})
        return item

    def work_views(self, *, owner_subject: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            ids = [row["id"] for row in conn.execute(
                "SELECT id FROM saved_work_views WHERE owner_subject=? ORDER BY updated_at DESC,id",
                (owner_subject,),
            ).fetchall()]
        return [self.work_view(view_id, owner_subject=owner_subject) for view_id in ids]

    def delete_work_view(self, view_id: str, *, owner_subject: str) -> None:
        with self._write() as conn:
            result = conn.execute(
                "DELETE FROM saved_work_views WHERE id=? AND owner_subject=?",
                (view_id, owner_subject),
            )
            if result.rowcount != 1:
                raise Missing("unknown saved view")

    def notifications(
        self, *, subject: str, role: str, limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT n.*,CASE WHEN r.notification_id IS NULL THEN 0 ELSE 1 END AS read
                FROM work_notifications n
                LEFT JOIN work_notification_reads r
                  ON r.notification_id=n.id AND r.subject=?
                WHERE n.not_before<=? AND
                  (n.recipient_subject=? OR n.recipient_role=?)
                ORDER BY n.created_at DESC,n.id DESC LIMIT ?""",
                (subject, _now(), subject, role, max(1, min(limit, 200))),
            ).fetchall()
        values = []
        for row in rows:
            item = dict(row)
            item["read"] = bool(item["read"])
            item["route"] = (
                f"/analyst/reviews/{item['resource_id']}"
                if item["resource_kind"] == "review"
                else f"/analyst/cases/{item['resource_id']}"
            )
            for key in ("recipient_subject", "recipient_role", "dedupe_key"):
                item.pop(key, None)
            values.append(item)
        return values

    def read_notification(self, notification_id: str, *, subject: str, role: str) -> None:
        with self._write() as conn:
            visible = conn.execute(
                "SELECT 1 FROM work_notifications WHERE id=? AND "
                "(recipient_subject=? OR recipient_role=?)",
                (notification_id, subject, role),
            ).fetchone()
            if visible is None:
                raise Missing("unknown notification")
            conn.execute(
                "INSERT OR IGNORE INTO work_notification_reads VALUES (?,?,?)",
                (notification_id, subject, _now()),
            )

    def work_activity(
        self, *, query: str | None = None, actor: str | None = None,
        action: str | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            review_rows = conn.execute(
                "SELECT id,request_id,actor,actor_name,action,rationale,at FROM review_events"
            ).fetchall()
            case_rows = conn.execute(
                "SELECT id,case_id,actor,actor_name,action,rationale,at FROM case_events"
            ).fetchall()
            comment_rows = conn.execute(
                "SELECT id,resource_kind,resource_id,actor,actor_name,message,created_at "
                "FROM work_comments"
            ).fetchall()
        values = [{
            "id": row["id"], "resource_kind": "review",
            "resource_id": row["request_id"], "actor": row["actor"],
            "actor_name": row["actor_name"], "action": row["action"],
            "message": row["rationale"], "at": row["at"],
            "route": f"/analyst/reviews/{row['request_id']}",
        } for row in review_rows]
        values += [{
            "id": row["id"], "resource_kind": "case",
            "resource_id": row["case_id"], "actor": row["actor"],
            "actor_name": row["actor_name"], "action": row["action"],
            "message": row["rationale"], "at": row["at"],
            "route": f"/analyst/cases/{row['case_id']}",
        } for row in case_rows]
        values += [{
            "id": row["id"], "resource_kind": row["resource_kind"],
            "resource_id": row["resource_id"], "actor": row["actor"],
            "actor_name": row["actor_name"], "action": "work.comment",
            "message": row["message"], "at": row["created_at"],
            "route": (
                f"/analyst/reviews/{row['resource_id']}"
                if row["resource_kind"] == "review"
                else f"/analyst/cases/{row['resource_id']}"
            ),
        } for row in comment_rows]
        needle = (query or "").strip().lower()
        values = [item for item in values if (
            (not actor or item["actor"] == actor)
            and (not action or item["action"] == action)
            and (not needle or needle in " ".join(str(item[key]) for key in (
                "resource_id", "actor_name", "action", "message",
            )).lower())
        )]
        values.sort(key=lambda item: (-item["at"], item["id"]))
        return values[:max(1, min(limit, 500))]

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
            self._notify(
                conn, kind="review.decision", title="Your review was updated",
                message=f"{actor_name}: {rationale[:180]}",
                resource_kind="review", resource_id=request_id,
                recipient_subject=current["owner_subject"],
                dedupe_key=f"review-decision:{event_id}",
            )
            if decision == "escalate":
                self._notify(
                    conn, kind="review.escalated", title="Review needs CISO attention",
                    message=f"{current['package']} was escalated by {actor_name}.",
                    resource_kind="review", resource_id=request_id,
                    recipient_role="ciso", dedupe_key=f"review-ciso:{event_id}",
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
                self._notify(
                    conn, kind="review.verified", title="Package fix verified",
                    message=f"MeshAgent confirmed {package}@{version} is clean.",
                    resource_kind="review", resource_id=row["id"],
                    recipient_subject=owner_subject,
                    dedupe_key=f"review-verified:{row['id']}:{evidence_id}",
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
