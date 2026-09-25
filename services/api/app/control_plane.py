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


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    value = "\0".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:24]}"


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
                  version INTEGER NOT NULL DEFAULT 1,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  created_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS policy_versions (
                  policy_id TEXT NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
                  version INTEGER NOT NULL,
                  state TEXT NOT NULL DEFAULT 'active',
                  severity_threshold TEXT NOT NULL,
                  denied_licenses TEXT NOT NULL,
                  block_on_unknown INTEGER NOT NULL,
                  rationale TEXT NOT NULL,
                  content_digest TEXT,
                  effective_from INTEGER,
                  effective_until INTEGER,
                  created_at INTEGER NOT NULL,
                  created_by TEXT NOT NULL,
                  created_by_name TEXT,
                  submitted_by TEXT,
                  submitted_by_name TEXT,
                  submitted_at INTEGER,
                  activated_by TEXT,
                  activated_by_name TEXT,
                  activated_at INTEGER,
                  withdrawn_by TEXT,
                  withdrawn_by_name TEXT,
                  withdrawn_at INTEGER,
                  PRIMARY KEY(policy_id, version)
                );
                CREATE TABLE IF NOT EXISTS policy_events (
                  id TEXT PRIMARY KEY,
                  policy_id TEXT NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
                  actor TEXT NOT NULL,
                  actor_name TEXT NOT NULL,
                  actor_role TEXT NOT NULL,
                  action TEXT NOT NULL,
                  from_state TEXT,
                  to_state TEXT NOT NULL,
                  rationale TEXT NOT NULL,
                  evidence_ids TEXT NOT NULL DEFAULT '[]',
                  at INTEGER NOT NULL,
                  correlation_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS policy_events_policy
                  ON policy_events(policy_id,at,id);
                CREATE TABLE IF NOT EXISTS exceptions (
                  id TEXT PRIMARY KEY,
                  policy_id TEXT NOT NULL,
                  policy_version INTEGER NOT NULL DEFAULT 1,
                  policy_digest TEXT,
                  scope TEXT NOT NULL,
                  rationale TEXT NOT NULL,
                  compensating_controls TEXT NOT NULL,
                  owner TEXT NOT NULL,
                  owner_name TEXT,
                  evidence_ids TEXT NOT NULL DEFAULT '[]',
                  request_digest TEXT,
                  expires_at INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  requested_by TEXT NOT NULL,
                  requested_by_name TEXT,
                  approved_by TEXT,
                  approved_by_name TEXT,
                  decision_rationale TEXT,
                  decided_at INTEGER,
                  revoked_by TEXT,
                  revoked_by_name TEXT,
                  revoked_at INTEGER,
                  revocation_rationale TEXT,
                  predecessor_exception_id TEXT,
                  renewal_number INTEGER NOT NULL DEFAULT 0,
                  superseded_by_exception_id TEXT,
                  superseded_at INTEGER,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  FOREIGN KEY(policy_id,policy_version)
                    REFERENCES policy_versions(policy_id,version)
                );
                CREATE TABLE IF NOT EXISTS exception_events (
                  id TEXT PRIMARY KEY,
                  exception_id TEXT NOT NULL REFERENCES exceptions(id) ON DELETE CASCADE,
                  actor TEXT NOT NULL,
                  actor_name TEXT NOT NULL,
                  actor_role TEXT NOT NULL,
                  action TEXT NOT NULL,
                  from_state TEXT,
                  to_state TEXT NOT NULL,
                  rationale TEXT NOT NULL,
                  evidence_ids TEXT NOT NULL DEFAULT '[]',
                  at INTEGER NOT NULL,
                  correlation_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS exception_events_exception
                  ON exception_events(exception_id,at,id);
                CREATE TABLE IF NOT EXISTS approvals (
                  id TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  resource_id TEXT NOT NULL,
                  resource_version INTEGER NOT NULL DEFAULT 1,
                  request_digest TEXT,
                  evidence_ids TEXT NOT NULL DEFAULT '[]',
                  requester TEXT NOT NULL,
                  requester_name TEXT NOT NULL,
                  status TEXT NOT NULL,
                  rationale TEXT NOT NULL,
                  approver TEXT,
                  approver_name TEXT,
                  decision_rationale TEXT,
                  version INTEGER NOT NULL,
                  expires_at INTEGER NOT NULL,
                  created_at INTEGER NOT NULL,
                  decided_at INTEGER,
                  expired_at INTEGER
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
                CREATE TABLE IF NOT EXISTS governance_notifications (
                  id TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  title TEXT NOT NULL,
                  message TEXT NOT NULL,
                  resource_kind TEXT NOT NULL CHECK(resource_kind IN ('policy','exception','approval')),
                  resource_id TEXT NOT NULL,
                  recipient_subject TEXT,
                  recipient_role TEXT,
                  not_before INTEGER NOT NULL,
                  created_at INTEGER NOT NULL,
                  dedupe_key TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS governance_notifications_ready
                  ON governance_notifications(not_before,recipient_subject,recipient_role);
                CREATE TABLE IF NOT EXISTS governance_notification_reads (
                  notification_id TEXT NOT NULL REFERENCES governance_notifications(id)
                    ON DELETE CASCADE,
                  subject TEXT NOT NULL,
                  read_at INTEGER NOT NULL,
                  PRIMARY KEY(notification_id,subject)
                );
                CREATE TABLE IF NOT EXISTS governance_reconciler_state (
                  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                  last_started_at INTEGER,
                  last_completed_at INTEGER,
                  last_error TEXT,
                  last_counts TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS governance_projection_outbox (
                  projection_id TEXT PRIMARY KEY,
                  event_id TEXT NOT NULL,
                  resource_kind TEXT NOT NULL
                    CHECK(resource_kind IN ('policy','exception')),
                  resource_id TEXT NOT NULL,
                  relation_kind TEXT NOT NULL CHECK(relation_kind IN (
                    'policy_version','policy_activation','policy_supersession',
                    'exception_request','exception_decision','exception_expiry',
                    'exception_revocation'
                  )),
                  payload_json TEXT NOT NULL,
                  payload_sha256 TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','projecting','failed','projected')),
                  attempts INTEGER NOT NULL DEFAULT 0,
                  next_attempt_at INTEGER,
                  native_ulid TEXT,
                  error TEXT,
                  created_at INTEGER NOT NULL,
                  projected_at INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS governance_projection_event_kind
                  ON governance_projection_outbox(event_id,relation_kind);
                CREATE INDEX IF NOT EXISTS governance_projection_ready
                  ON governance_projection_outbox(status,next_attempt_at,created_at);
                CREATE INDEX IF NOT EXISTS governance_projection_resource
                  ON governance_projection_outbox(resource_kind,resource_id,created_at);
                """
                )
                # All additive upgrades and deterministic backfills below commit
                # together. Closing the connection after an exception rolls this
                # transaction back, so a process stop cannot expose half a
                # governance lifecycle migration.
                conn.execute("BEGIN IMMEDIATE")
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

                def table_columns(table: str) -> set[str]:
                    return {
                        str(row[1]) for row in conn.execute(
                            f"PRAGMA table_info({table})"
                        ).fetchall()
                    }

                governance_upgrades: dict[str, tuple[tuple[str, str], ...]] = {
                    "policies": (
                        ("version", "ALTER TABLE policies ADD COLUMN version INTEGER NOT NULL DEFAULT 1"),
                    ),
                    "policy_versions": (
                        ("state", "ALTER TABLE policy_versions ADD COLUMN state TEXT NOT NULL DEFAULT 'active'"),
                        ("content_digest", "ALTER TABLE policy_versions ADD COLUMN content_digest TEXT"),
                        ("effective_from", "ALTER TABLE policy_versions ADD COLUMN effective_from INTEGER"),
                        ("effective_until", "ALTER TABLE policy_versions ADD COLUMN effective_until INTEGER"),
                        ("created_by_name", "ALTER TABLE policy_versions ADD COLUMN created_by_name TEXT"),
                        ("submitted_by", "ALTER TABLE policy_versions ADD COLUMN submitted_by TEXT"),
                        ("submitted_by_name", "ALTER TABLE policy_versions ADD COLUMN submitted_by_name TEXT"),
                        ("submitted_at", "ALTER TABLE policy_versions ADD COLUMN submitted_at INTEGER"),
                        ("activated_by", "ALTER TABLE policy_versions ADD COLUMN activated_by TEXT"),
                        ("activated_by_name", "ALTER TABLE policy_versions ADD COLUMN activated_by_name TEXT"),
                        ("activated_at", "ALTER TABLE policy_versions ADD COLUMN activated_at INTEGER"),
                        ("withdrawn_by", "ALTER TABLE policy_versions ADD COLUMN withdrawn_by TEXT"),
                        ("withdrawn_by_name", "ALTER TABLE policy_versions ADD COLUMN withdrawn_by_name TEXT"),
                        ("withdrawn_at", "ALTER TABLE policy_versions ADD COLUMN withdrawn_at INTEGER"),
                    ),
                    "exceptions": (
                        ("policy_version", "ALTER TABLE exceptions ADD COLUMN policy_version INTEGER NOT NULL DEFAULT 1"),
                        ("policy_digest", "ALTER TABLE exceptions ADD COLUMN policy_digest TEXT"),
                        ("owner_name", "ALTER TABLE exceptions ADD COLUMN owner_name TEXT"),
                        ("evidence_ids", "ALTER TABLE exceptions ADD COLUMN evidence_ids TEXT NOT NULL DEFAULT '[]'"),
                        ("request_digest", "ALTER TABLE exceptions ADD COLUMN request_digest TEXT"),
                        ("requested_by_name", "ALTER TABLE exceptions ADD COLUMN requested_by_name TEXT"),
                        ("approved_by_name", "ALTER TABLE exceptions ADD COLUMN approved_by_name TEXT"),
                        ("decision_rationale", "ALTER TABLE exceptions ADD COLUMN decision_rationale TEXT"),
                        ("decided_at", "ALTER TABLE exceptions ADD COLUMN decided_at INTEGER"),
                        ("revoked_by", "ALTER TABLE exceptions ADD COLUMN revoked_by TEXT"),
                        ("revoked_by_name", "ALTER TABLE exceptions ADD COLUMN revoked_by_name TEXT"),
                        ("revoked_at", "ALTER TABLE exceptions ADD COLUMN revoked_at INTEGER"),
                        ("revocation_rationale", "ALTER TABLE exceptions ADD COLUMN revocation_rationale TEXT"),
                        ("predecessor_exception_id", "ALTER TABLE exceptions ADD COLUMN predecessor_exception_id TEXT"),
                        ("renewal_number", "ALTER TABLE exceptions ADD COLUMN renewal_number INTEGER NOT NULL DEFAULT 0"),
                        ("superseded_by_exception_id", "ALTER TABLE exceptions ADD COLUMN superseded_by_exception_id TEXT"),
                        ("superseded_at", "ALTER TABLE exceptions ADD COLUMN superseded_at INTEGER"),
                    ),
                    "approvals": (
                        ("resource_version", "ALTER TABLE approvals ADD COLUMN resource_version INTEGER NOT NULL DEFAULT 1"),
                        ("request_digest", "ALTER TABLE approvals ADD COLUMN request_digest TEXT"),
                        ("evidence_ids", "ALTER TABLE approvals ADD COLUMN evidence_ids TEXT NOT NULL DEFAULT '[]'"),
                        ("approver_name", "ALTER TABLE approvals ADD COLUMN approver_name TEXT"),
                        ("expired_at", "ALTER TABLE approvals ADD COLUMN expired_at INTEGER"),
                    ),
                }
                for table, upgrades in governance_upgrades.items():
                    present = table_columns(table)
                    for column, ddl in upgrades:
                        if column not in present:
                            conn.execute(ddl)

                conn.execute(
                    "CREATE INDEX IF NOT EXISTS policy_versions_state "
                    "ON policy_versions(policy_id,state,version DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS exceptions_policy_status "
                    "ON exceptions(policy_id,status,expires_at,updated_at DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS exceptions_expiry "
                    "ON exceptions(status,expires_at,id)"
                )
                conn.execute("DROP INDEX IF EXISTS exception_one_successor")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS exception_one_active_successor "
                    "ON exceptions(predecessor_exception_id) "
                    "WHERE predecessor_exception_id IS NOT NULL "
                    "AND status IN ('pending','approved')"
                )
                conn.execute(
                    "INSERT OR IGNORE INTO governance_reconciler_state "
                    "(singleton,last_counts) VALUES (1,'{}')"
                )

                legacy_policies = conn.execute("SELECT * FROM policies").fetchall()
                for policy in legacy_policies:
                    versions = conn.execute(
                        "SELECT * FROM policy_versions WHERE policy_id=? ORDER BY version",
                        (policy["id"],),
                    ).fetchall()
                    for version in versions:
                        canonical = {
                            "policy_id": policy["id"],
                            "version": version["version"],
                            "severity_threshold": version["severity_threshold"],
                            "denied_licenses": _loads(version["denied_licenses"], []),
                            "block_on_unknown": bool(version["block_on_unknown"]),
                            "rationale": version["rationale"],
                        }
                        state = (
                            "active" if version["version"] == policy["active_version"]
                            else "superseded"
                        )
                        if not version["content_digest"]:
                            conn.execute(
                                """UPDATE policy_versions SET state=?,content_digest=?,
                                effective_from=COALESCE(effective_from,created_at),
                                effective_until=CASE WHEN ?='active' THEN NULL
                                    ELSE COALESCE(effective_until,?) END,
                                created_by_name=COALESCE(created_by_name,created_by)
                                WHERE policy_id=? AND version=?""",
                                (
                                    state, _digest(canonical), state, policy["updated_at"],
                                    policy["id"], version["version"],
                                ),
                            )
                        elif not version["created_by_name"]:
                            conn.execute(
                                """UPDATE policy_versions SET created_by_name=created_by
                                WHERE policy_id=? AND version=?""",
                                (policy["id"], version["version"]),
                            )
                        effective_state = state if not version["content_digest"] else version["state"]
                        if effective_state in (
                            "in_review", "active", "superseded", "retired",
                        ) and not version["submitted_by"]:
                            conn.execute(
                                """UPDATE policy_versions SET submitted_by=created_by,
                                submitted_by_name=COALESCE(created_by_name,created_by),
                                submitted_at=COALESCE(submitted_at,created_at)
                                WHERE policy_id=? AND version=?""",
                                (policy["id"], version["version"]),
                            )
                        if effective_state in (
                            "active", "superseded", "retired",
                        ) and not version["activated_by"]:
                            conn.execute(
                                """UPDATE policy_versions SET activated_by=created_by,
                                activated_by_name=COALESCE(created_by_name,created_by),
                                activated_at=COALESCE(activated_at,effective_from,created_at)
                                WHERE policy_id=? AND version=?""",
                                (policy["id"], version["version"]),
                            )
                        if effective_state == "withdrawn" and not version["withdrawn_by"]:
                            conn.execute(
                                """UPDATE policy_versions SET withdrawn_by=created_by,
                                withdrawn_by_name=COALESCE(created_by_name,created_by),
                                withdrawn_at=COALESCE(withdrawn_at,created_at)
                                WHERE policy_id=? AND version=?""",
                                (policy["id"], version["version"]),
                            )
                    has_policy_events = conn.execute(
                        "SELECT 1 FROM policy_events WHERE policy_id=? LIMIT 1",
                        (policy["id"],),
                    ).fetchone()
                    if has_policy_events is None:
                        conn.execute(
                            """INSERT INTO policy_events
                            (id,policy_id,actor,actor_name,actor_role,action,from_state,
                             to_state,rationale,evidence_ids,at,correlation_id)
                            VALUES (?,?,?,?,?,'policy.created',NULL,?,?, '[]',?,?)""",
                            (
                                _stable_id("pev", policy["id"], "created"), policy["id"],
                                policy["created_by"], policy["created_by"], "ciso",
                                policy["status"], "Migrated durable policy baseline.",
                                policy["created_at"], "migration:priority5a",
                            ),
                        )

                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS policy_one_active_version "
                    "ON policy_versions(policy_id) WHERE state='active'"
                )

                legacy_exceptions = conn.execute("SELECT * FROM exceptions").fetchall()
                for exception in legacy_exceptions:
                    policy_row = conn.execute(
                        "SELECT active_version FROM policies WHERE id=?",
                        (exception["policy_id"],),
                    ).fetchone()
                    if not exception["policy_digest"] and policy_row is not None:
                        policy_version = int(policy_row["active_version"])
                    else:
                        policy_version = int(exception["policy_version"] or 1)
                    version = conn.execute(
                        "SELECT content_digest FROM policy_versions WHERE policy_id=? AND version=?",
                        (exception["policy_id"], policy_version),
                    ).fetchone()
                    policy_digest = (
                        str(version["content_digest"]) if version and version["content_digest"]
                        else _digest({"policy_id": exception["policy_id"], "version": policy_version})
                    )
                    evidence_ids = _loads(exception["evidence_ids"], [])
                    canonical = {
                        "policy_id": exception["policy_id"],
                        "policy_version": policy_version,
                        "policy_digest": policy_digest,
                        "scope": exception["scope"],
                        "rationale": exception["rationale"],
                        "compensating_controls": exception["compensating_controls"],
                        "owner": exception["owner"],
                        "expires_at": exception["expires_at"],
                        "evidence_ids": evidence_ids,
                    }
                    if exception["predecessor_exception_id"]:
                        canonical["predecessor_exception_id"] = exception[
                            "predecessor_exception_id"
                        ]
                        canonical["renewal_number"] = int(
                            exception["renewal_number"] or 0
                        )
                    request_digest = exception["request_digest"] or _digest(canonical)
                    approval = conn.execute(
                        "SELECT * FROM approvals WHERE kind='exception' AND resource_id=? "
                        "ORDER BY created_at,id LIMIT 1", (exception["id"],),
                    ).fetchone()
                    approver = exception["approved_by"] or (
                        approval["approver"] if approval is not None else None
                    )
                    decided_at = (
                        approval["decided_at"] if approval is not None else None
                    )
                    decision_rationale = (
                        approval["decision_rationale"] if approval is not None else None
                    )
                    conn.execute(
                        """UPDATE exceptions SET
                        policy_version=CASE WHEN policy_digest IS NULL OR policy_digest=''
                          THEN ? ELSE policy_version END,
                        policy_digest=COALESCE(NULLIF(policy_digest,''),?),
                        owner_name=COALESCE(owner_name,owner),
                        request_digest=COALESCE(NULLIF(request_digest,''),?),
                        requested_by_name=COALESCE(requested_by_name,requested_by),
                        approved_by_name=COALESCE(approved_by_name,?),
                        revoked_by_name=COALESCE(revoked_by_name,revoked_by),
                        decision_rationale=COALESCE(decision_rationale,?),
                        decided_at=COALESCE(decided_at,?) WHERE id=?""",
                        (
                            policy_version, policy_digest, request_digest, approver,
                            decision_rationale, decided_at, exception["id"],
                        ),
                    )
                    requested_event = conn.execute(
                        """SELECT 1 FROM exception_events
                        WHERE exception_id=? AND action IN
                          ('exception.requested','exception.renewal_requested') LIMIT 1""",
                        (exception["id"],),
                    ).fetchone()
                    if requested_event is None:
                        conn.execute(
                            """INSERT INTO exception_events
                            (id,exception_id,actor,actor_name,actor_role,action,from_state,
                             to_state,rationale,evidence_ids,at,correlation_id)
                            VALUES (?,?,?,?,?,'exception.requested',NULL,'pending',?,?,?,?)""",
                            (
                                _stable_id("eev", exception["id"], "requested"),
                                exception["id"], exception["requested_by"],
                                exception["requested_by_name"] or exception["requested_by"],
                                "analyst", exception["rationale"], _json(evidence_ids),
                                exception["created_at"], "migration:priority5a",
                            ),
                        )
                    if exception["status"] != "pending":
                        terminal_action = f"exception.{exception['status']}"
                        terminal_actions = [terminal_action]
                        if exception["status"] == "expired":
                            terminal_actions.append("exception.approval_expired")
                        if exception["status"] == "revoked":
                            terminal_actions.append("exception.renewal_cancelled")
                        placeholders = ",".join("?" for _ in terminal_actions)
                        terminal_event = conn.execute(
                            f"""SELECT 1 FROM exception_events
                            WHERE exception_id=? AND action IN ({placeholders}) LIMIT 1""",
                            (exception["id"], *terminal_actions),
                        ).fetchone()
                        if terminal_event is None:
                            conn.execute(
                                """INSERT INTO exception_events
                                (id,exception_id,actor,actor_name,actor_role,action,from_state,
                                 to_state,rationale,evidence_ids,at,correlation_id)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (
                                    _stable_id("eev", exception["id"], exception["status"]),
                                    exception["id"], approver or "migration",
                                    approver or "Migration", "ciso", terminal_action,
                                    "pending", exception["status"],
                                    decision_rationale or exception["rationale"],
                                    _json(evidence_ids), decided_at or exception["updated_at"],
                                    "migration:priority5a",
                                ),
                            )
                    if approval is not None:
                        conn.execute(
                            """UPDATE approvals SET
                            request_digest=COALESCE(NULLIF(request_digest,''),?),
                            evidence_ids=CASE WHEN evidence_ids IS NULL OR evidence_ids=''
                              THEN ? ELSE evidence_ids END,
                            approver_name=COALESCE(approver_name,approver)
                            WHERE id=?""",
                            (request_digest, _json(evidence_ids), approval["id"]),
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
                for event in conn.execute(
                    "SELECT id FROM policy_events ORDER BY at,id"
                ).fetchall():
                    self._enqueue_governance_event(
                        conn, resource_kind="policy", event_id=str(event["id"]),
                    )
                for event in conn.execute(
                    "SELECT id FROM exception_events ORDER BY at,id"
                ).fetchall():
                    self._enqueue_governance_event(
                        conn, resource_kind="exception", event_id=str(event["id"]),
                    )
                conn.execute("COMMIT")
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

    @staticmethod
    def _policy_version(row: sqlite3.Row) -> dict[str, Any]:
        body = dict(row)
        body["denied_licenses"] = _loads(body["denied_licenses"], [])
        body["block_on_unknown"] = bool(body["block_on_unknown"])
        return body

    @staticmethod
    def _governance_event(row: sqlite3.Row, resource_kind: str) -> dict[str, Any]:
        body = dict(row)
        body["resource_kind"] = resource_kind
        body["resource_id"] = body.pop(f"{resource_kind}_id")
        body["evidence_ids"] = _loads(body["evidence_ids"], [])
        return body

    def create_policy(self, *, name: str, scope: str, severity_threshold: str,
                      denied_licenses: list[str], block_on_unknown: bool,
                      rationale: str, actor: str, actor_name: str,
                      actor_role: str, correlation_id: str,
                      require_independent_approver: bool = False) -> dict[str, Any]:
        policy_id, event_id, now = _id("pol"), _id("pev"), _now()
        licenses = list(dict.fromkeys(value.strip() for value in denied_licenses if value.strip()))
        canonical = {
            "policy_id": policy_id, "version": 1,
            "severity_threshold": severity_threshold,
            "denied_licenses": licenses,
            "block_on_unknown": block_on_unknown,
            "rationale": rationale,
        }
        digest = _digest(canonical)
        initial_status = "pending" if require_independent_approver else "active"
        initial_state = "in_review" if require_independent_approver else "active"
        effective_from = None if require_independent_approver else now
        with self._write() as conn:
            conn.execute(
                """INSERT INTO policies
                (id,name,scope,status,active_version,version,created_at,updated_at,created_by)
                VALUES (?,?,?,?,1,1,?,?,?)""",
                (policy_id, name, scope, initial_status, now, now, actor),
            )
            conn.execute(
                """INSERT INTO policy_versions
                (policy_id,version,state,severity_threshold,denied_licenses,
                 block_on_unknown,rationale,content_digest,effective_from,
                 effective_until,created_at,created_by,created_by_name)
                VALUES (?,1,?,?,?,?,?,?,?,NULL,?,?,?)""",
                (
                    policy_id, initial_state, severity_threshold, _json(licenses),
                    int(block_on_unknown), rationale, digest, effective_from, now,
                    actor, actor_name,
                ),
            )
            if require_independent_approver:
                conn.execute(
                    """UPDATE policy_versions SET submitted_by=?,submitted_by_name=?,
                    submitted_at=? WHERE policy_id=? AND version=1""",
                    (actor, actor_name, now, policy_id),
                )
            else:
                conn.execute(
                    """UPDATE policy_versions SET submitted_by=?,submitted_by_name=?,
                    submitted_at=?,activated_by=?,activated_by_name=?,activated_at=?
                    WHERE policy_id=? AND version=1""",
                    (actor, actor_name, now, actor, actor_name, now, policy_id),
                )
            conn.execute(
                """INSERT INTO policy_events
                (id,policy_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at,correlation_id)
                VALUES (?,?,?,?,?,'policy.created',NULL,?,?,?,?,?)""",
                (
                    event_id, policy_id, actor, actor_name, actor_role,
                    initial_state, rationale,
                    _json([f"policy-version:{policy_id}@1"]), now,
                    correlation_id,
                ),
            )
            self._enqueue_governance_event(
                conn, resource_kind="policy", event_id=event_id,
            )
            if require_independent_approver:
                self._governance_notify(
                    conn, kind="policy.review_requested",
                    title="New policy ready for review",
                    message=f"{name} version 1 awaits independent activation.",
                    resource_kind="policy", resource_id=policy_id,
                    recipient_role="ciso",
                    dedupe_key=f"policy-review:{policy_id}:1",
                )
        return self.policy(policy_id)

    def create_policy_version(
        self, policy_id: str, *, expected_version: int, severity_threshold: str,
        denied_licenses: list[str], block_on_unknown: bool, rationale: str,
        actor: str, actor_name: str, actor_role: str, correlation_id: str,
    ) -> dict[str, Any]:
        now, event_id = _now(), _id("pev")
        licenses = list(dict.fromkeys(value.strip() for value in denied_licenses if value.strip()))
        with self._write() as conn:
            current = conn.execute(
                "SELECT * FROM policies WHERE id=?", (policy_id,),
            ).fetchone()
            if current is None:
                raise Missing("unknown policy")
            if current["version"] != expected_version:
                raise VersionConflict("policy changed; reload before creating a version")
            if current["status"] != "active":
                raise StoreError("retired policies cannot receive new versions")
            pending = conn.execute(
                "SELECT 1 FROM policy_versions WHERE policy_id=? "
                "AND state IN ('draft','in_review')", (policy_id,),
            ).fetchone()
            if pending is not None:
                raise StoreError("policy already has an unfinished version")
            next_version = int(conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM policy_versions WHERE policy_id=?",
                (policy_id,),
            ).fetchone()[0])
            canonical = {
                "policy_id": policy_id, "version": next_version,
                "severity_threshold": severity_threshold,
                "denied_licenses": licenses,
                "block_on_unknown": block_on_unknown,
                "rationale": rationale,
            }
            conn.execute(
                """INSERT INTO policy_versions
                (policy_id,version,state,severity_threshold,denied_licenses,
                 block_on_unknown,rationale,content_digest,effective_from,
                 effective_until,created_at,created_by,created_by_name)
                VALUES (?,?,'draft',?,?,?,?,?,NULL,NULL,?,?,?)""",
                (
                    policy_id, next_version, severity_threshold, _json(licenses),
                    int(block_on_unknown), rationale, _digest(canonical), now,
                    actor, actor_name,
                ),
            )
            conn.execute(
                "UPDATE policies SET version=version+1,updated_at=? WHERE id=?",
                (now, policy_id),
            )
            conn.execute(
                """INSERT INTO policy_events
                (id,policy_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at,correlation_id)
                VALUES (?,?,?,?,?,'policy.version_created','active','draft',?,?,?,?)""",
                (
                    event_id, policy_id, actor, actor_name, actor_role,
                    rationale, _json([f"policy-version:{policy_id}@{next_version}"]),
                    now, correlation_id,
                ),
            )
            self._enqueue_governance_event(
                conn, resource_kind="policy", event_id=event_id,
            )
        return self.policy(policy_id)

    def submit_policy_version(
        self, policy_id: str, version: int, *, expected_version: int,
        rationale: str, actor: str, actor_name: str, actor_role: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        return self._transition_policy_version(
            policy_id, version, expected_version=expected_version,
            expected_state="draft", target_state="in_review",
            action="policy.version_submitted", rationale=rationale,
            actor=actor, actor_name=actor_name, actor_role=actor_role,
            correlation_id=correlation_id,
        )

    def withdraw_policy_version(
        self, policy_id: str, version: int, *, expected_version: int,
        rationale: str, actor: str, actor_name: str, actor_role: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        now, event_id = _now(), _id("pev")
        with self._write() as conn:
            current = conn.execute(
                "SELECT * FROM policies WHERE id=?", (policy_id,),
            ).fetchone()
            if current is None:
                raise Missing("unknown policy")
            if current["version"] != expected_version:
                raise VersionConflict("policy changed; reload before withdrawing a version")
            if current["status"] != "active":
                raise StoreError("retired policies cannot withdraw versions")
            candidate = conn.execute(
                "SELECT state FROM policy_versions WHERE policy_id=? AND version=?",
                (policy_id, version),
            ).fetchone()
            if candidate is None:
                raise Missing("unknown policy version")
            if candidate["state"] not in ("draft", "in_review"):
                raise StoreError("only a draft or in-review policy version can be withdrawn")
            prior_state = str(candidate["state"])
            conn.execute(
                """UPDATE policy_versions SET state='withdrawn',withdrawn_by=?,
                withdrawn_by_name=?,withdrawn_at=?
                WHERE policy_id=? AND version=?""",
                (actor, actor_name, now, policy_id, version),
            )
            conn.execute(
                "UPDATE policies SET version=version+1,updated_at=? WHERE id=?",
                (now, policy_id),
            )
            conn.execute(
                """INSERT INTO policy_events
                (id,policy_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at,correlation_id)
                VALUES (?,?,?,?,?,'policy.version_withdrawn',?,?,?,?,?,?)""",
                (
                    event_id, policy_id, actor, actor_name, actor_role,
                    f"v{version}:{prior_state}", f"v{version}:withdrawn",
                    rationale, _json([f"policy-version:{policy_id}@{version}"]),
                    now, correlation_id,
                ),
            )
            self._enqueue_governance_event(
                conn, resource_kind="policy", event_id=event_id,
            )
        return self.policy(policy_id)

    def activate_policy_version(
        self, policy_id: str, version: int, *, expected_version: int,
        rationale: str, actor: str, actor_name: str, actor_role: str,
        correlation_id: str, require_independent_approver: bool = False,
    ) -> dict[str, Any]:
        now, event_id = _now(), _id("pev")
        with self._write() as conn:
            current = conn.execute(
                "SELECT * FROM policies WHERE id=?", (policy_id,),
            ).fetchone()
            if current is None:
                raise Missing("unknown policy")
            if current["version"] != expected_version:
                raise VersionConflict("policy changed; reload before activating a version")
            if current["status"] not in ("active", "pending"):
                raise StoreError("retired policies cannot activate versions")
            candidate = conn.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND version=?",
                (policy_id, version),
            ).fetchone()
            if candidate is None:
                raise Missing("unknown policy version")
            if candidate["state"] != "in_review":
                raise StoreError("only a policy version in review can be activated")
            if require_independent_approver and actor in {
                candidate["created_by"], candidate["submitted_by"],
            }:
                raise SeparationConflict(
                    "the policy author or submitter cannot activate this version"
                )
            previous = None if current["status"] == "pending" else current["active_version"]
            if previous is not None:
                conn.execute(
                    """UPDATE policy_versions SET state='superseded',effective_until=?
                    WHERE policy_id=? AND version=? AND state='active'""",
                    (now, policy_id, previous),
                )
            conn.execute(
                """UPDATE policy_versions SET state='active',effective_from=?,
                effective_until=NULL,activated_by=?,activated_by_name=?,activated_at=?
                WHERE policy_id=? AND version=?""",
                (now, actor, actor_name, now, policy_id, version),
            )
            conn.execute(
                """UPDATE policies SET status='active',active_version=?,version=version+1,
                updated_at=? WHERE id=?""", (version, now, policy_id),
            )
            conn.execute(
                """INSERT INTO policy_events
                (id,policy_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at,correlation_id)
                VALUES (?,?,?,?,?,'policy.version_activated',?,?,?,?,?,?)""",
                (
                    event_id, policy_id, actor, actor_name, actor_role,
                    (
                        f"v{previous}:active"
                        if previous is not None
                        else f"v{version}:in_review"
                    ),
                    f"v{version}:active", rationale,
                    _json([f"policy-version:{policy_id}@{version}"]), now,
                    correlation_id,
                ),
            )
            self._enqueue_governance_event(
                conn, resource_kind="policy", event_id=event_id,
            )
            if candidate["created_by"] != actor:
                self._governance_notify(
                    conn, kind="policy.version_activated",
                    title="Policy version activated",
                    message=f"{current['name']} version {version} is now active.",
                    resource_kind="policy", resource_id=policy_id,
                    recipient_subject=candidate["created_by"],
                    dedupe_key=f"policy-activated:{policy_id}:{version}",
                )
        return self.policy(policy_id)

    def _transition_policy_version(
        self, policy_id: str, version: int, *, expected_version: int,
        expected_state: str, target_state: str, action: str, rationale: str,
        actor: str, actor_name: str, actor_role: str, correlation_id: str,
    ) -> dict[str, Any]:
        now, event_id = _now(), _id("pev")
        with self._write() as conn:
            current = conn.execute(
                "SELECT * FROM policies WHERE id=?", (policy_id,),
            ).fetchone()
            if current is None:
                raise Missing("unknown policy")
            if current["version"] != expected_version:
                raise VersionConflict("policy changed; reload before updating its lifecycle")
            candidate = conn.execute(
                "SELECT state FROM policy_versions WHERE policy_id=? AND version=?",
                (policy_id, version),
            ).fetchone()
            if candidate is None:
                raise Missing("unknown policy version")
            if candidate["state"] != expected_state:
                raise StoreError(
                    f"policy version must be {expected_state} before it can become {target_state}"
                )
            conn.execute(
                "UPDATE policy_versions SET state=? WHERE policy_id=? AND version=?",
                (target_state, policy_id, version),
            )
            if action == "policy.version_submitted":
                conn.execute(
                    """UPDATE policy_versions SET submitted_by=?,submitted_by_name=?,
                    submitted_at=? WHERE policy_id=? AND version=?""",
                    (actor, actor_name, now, policy_id, version),
                )
            conn.execute(
                "UPDATE policies SET version=version+1,updated_at=? WHERE id=?",
                (now, policy_id),
            )
            conn.execute(
                """INSERT INTO policy_events
                (id,policy_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at,correlation_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id, policy_id, actor, actor_name, actor_role,
                    action, expected_state, target_state, rationale,
                    _json([f"policy-version:{policy_id}@{version}"]), now,
                    correlation_id,
                ),
            )
            self._enqueue_governance_event(
                conn, resource_kind="policy", event_id=event_id,
            )
            if action == "policy.version_submitted":
                self._governance_notify(
                    conn, kind="policy.review_requested",
                    title="Policy version ready for review",
                    message=f"{current['name']} version {version} awaits activation.",
                    resource_kind="policy", resource_id=policy_id,
                    recipient_role="ciso",
                    dedupe_key=f"policy-review:{policy_id}:{version}",
                )
        return self.policy(policy_id)

    def retire_policy(
        self, policy_id: str, *, expected_version: int, rationale: str,
        actor: str, actor_name: str, actor_role: str, correlation_id: str,
    ) -> dict[str, Any]:
        now, event_id = _now(), _id("pev")
        with self._write() as conn:
            current = conn.execute(
                "SELECT * FROM policies WHERE id=?", (policy_id,),
            ).fetchone()
            if current is None:
                raise Missing("unknown policy")
            if current["version"] != expected_version:
                raise VersionConflict("policy changed; reload before retiring it")
            if current["status"] != "active":
                raise StoreError("policy is already retired")
            unfinished = conn.execute(
                "SELECT 1 FROM policy_versions WHERE policy_id=? "
                "AND state IN ('draft','in_review')", (policy_id,),
            ).fetchone()
            if unfinished is not None:
                raise StoreError("finish or discard the pending policy version before retirement")
            conn.execute(
                """UPDATE policy_versions SET state='retired',effective_until=?
                WHERE policy_id=? AND version=?""",
                (now, policy_id, current["active_version"]),
            )
            conn.execute(
                """UPDATE policies SET status='retired',version=version+1,
                updated_at=? WHERE id=?""", (now, policy_id),
            )
            conn.execute(
                """INSERT INTO policy_events
                (id,policy_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at,correlation_id)
                VALUES (?,?,?,?,?,'policy.retired','active','retired',?,?,?,?)""",
                (
                    event_id, policy_id, actor, actor_name, actor_role,
                    rationale,
                    _json([f"policy-version:{policy_id}@{current['active_version']}"]),
                    now, correlation_id,
                ),
            )
            self._enqueue_governance_event(
                conn, resource_kind="policy", event_id=event_id,
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
            versions = conn.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? ORDER BY version DESC",
                (policy_id,),
            ).fetchall()
            events = conn.execute(
                "SELECT * FROM policy_events WHERE policy_id=? ORDER BY at,id",
                (policy_id,),
            ).fetchall()
        body = dict(row)
        values = [self._policy_version(value) for value in versions]
        current = next(
            (value for value in values if value["version"] == body["active_version"]),
            None,
        )
        assert current is not None
        body["current"] = current
        body["versions"] = values
        body["events"] = [self._governance_event(value, "policy") for value in events]
        return body

    @staticmethod
    def _exception(row: sqlite3.Row, events: list[sqlite3.Row] | None = None) -> dict[str, Any]:
        body = dict(row)
        body["evidence_ids"] = _loads(body["evidence_ids"], [])
        body["expired"] = body["status"] == "expired"
        body["events"] = [
            ControlPlane._governance_event(value, "exception")
            for value in (events or [])
        ]
        return body

    @staticmethod
    def _approval(row: sqlite3.Row) -> dict[str, Any]:
        body = dict(row)
        body["evidence_ids"] = _loads(body["evidence_ids"], [])
        body["expired"] = body["status"] == "expired"
        return body

    def create_exception(
        self, *, policy_id: str, policy_version: int | None, scope: str,
        rationale: str, controls: str, owner: str, owner_name: str | None,
        evidence_ids: list[str], expires_at: int, actor: str, actor_name: str,
        actor_role: str, correlation_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        now = _now()
        if expires_at <= now:
            raise StoreError("exception expiry must be in the future")
        exception_id, approval_id, event_id = _id("exc"), _id("apr"), _id("eev")
        evidence = list(dict.fromkeys(value.strip() for value in evidence_ids if value.strip()))
        with self._write() as conn:
            policy = conn.execute(
                "SELECT * FROM policies WHERE id=?", (policy_id,),
            ).fetchone()
            if policy is None:
                raise Missing("unknown policy")
            if policy["status"] != "active":
                raise StoreError("exceptions cannot target a retired policy")
            bound_version = policy_version or policy["active_version"]
            version = conn.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND version=?",
                (policy_id, bound_version),
            ).fetchone()
            if version is None:
                raise Missing("unknown policy version")
            if version["state"] != "active" or bound_version != policy["active_version"]:
                raise StoreError("exceptions must target the active policy version")
            duplicate = conn.execute(
                """SELECT 1 FROM exceptions WHERE policy_id=? AND scope=?
                AND status IN ('pending','approved') AND expires_at>?""",
                (policy_id, scope, now),
            ).fetchone()
            if duplicate is not None:
                raise StoreError("an active exception already exists for this policy scope")
            policy_digest = str(version["content_digest"])
            canonical = {
                "policy_id": policy_id, "policy_version": bound_version,
                "policy_digest": policy_digest, "scope": scope,
                "rationale": rationale, "compensating_controls": controls,
                "owner": owner, "expires_at": expires_at,
                "evidence_ids": evidence,
            }
            request_digest = _digest(canonical)
            resolved_owner_name = owner_name or owner
            conn.execute(
                """INSERT INTO exceptions
                (id,policy_id,policy_version,policy_digest,scope,rationale,
                 compensating_controls,owner,owner_name,evidence_ids,request_digest,
                 expires_at,status,version,requested_by,requested_by_name,
                 approved_by,approved_by_name,decision_rationale,decided_at,
                 revoked_by,revoked_at,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'pending',1,?,?,NULL,NULL,NULL,NULL,
                        NULL,NULL,?,?)""",
                (
                    exception_id, policy_id, bound_version, policy_digest, scope,
                    rationale, controls, owner, resolved_owner_name, _json(evidence),
                    request_digest, expires_at, actor, actor_name, now, now,
                ),
            )
            conn.execute(
                """INSERT INTO approvals
                (id,kind,resource_id,resource_version,request_digest,evidence_ids,
                 requester,requester_name,status,rationale,approver,approver_name,
                 decision_rationale,version,expires_at,created_at,decided_at)
                VALUES (?,'exception',?,?,?, ?,?,?,'pending',?,NULL,NULL,NULL,1,?,?,NULL)""",
                (
                    approval_id, exception_id, 1, request_digest, _json(evidence),
                    actor, actor_name, rationale, min(expires_at, now + 604800), now,
                ),
            )
            conn.execute(
                """INSERT INTO exception_events
                (id,exception_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at,correlation_id)
                VALUES (?,?,?,?,?,'exception.requested',NULL,'pending',?,?,?,?)""",
                (
                    event_id, exception_id, actor, actor_name, actor_role,
                    rationale, _json(evidence), now, correlation_id,
                ),
            )
            self._enqueue_governance_event(
                conn, resource_kind="exception", event_id=event_id,
            )
            self._governance_notify(
                conn, kind="exception.approval_requested",
                title="Exception approval ready",
                message=f"{actor_name} requested an exception for {scope}.",
                resource_kind="approval", resource_id=approval_id,
                recipient_role="ciso",
                dedupe_key=f"exception-approval:{exception_id}:1",
            )
        return self.exception(exception_id), self.approval(approval_id)

    def renew_exception(
        self, exception_id: str, *, expected_version: int, rationale: str,
        controls: str, owner: str, owner_name: str | None,
        evidence_ids: list[str], expires_at: int, actor: str, actor_name: str,
        actor_role: str, correlation_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create a new approval-bound exception without extending the old row."""
        now = _now()
        if expires_at <= now:
            raise StoreError("renewal expiry must be in the future")
        evidence = list(dict.fromkeys(
            value.strip() for value in evidence_ids if value.strip()
        ))
        successor_id, approval_id = _id("exc"), _id("apr")
        predecessor_event_id, successor_event_id = _id("eev"), _id("eev")
        with self._write() as conn:
            current = conn.execute(
                "SELECT * FROM exceptions WHERE id=?", (exception_id,),
            ).fetchone()
            if current is None:
                raise Missing("unknown exception")
            if current["version"] != expected_version:
                raise VersionConflict("exception changed; reload before renewing it")
            if current["requested_by"] == actor:
                raise SeparationConflict(
                    "the exception requester cannot renew the same request"
                )
            if current["status"] != "approved" or current["expires_at"] <= now:
                raise StoreError("only a currently approved exception can be renewed")
            existing = conn.execute(
                """SELECT 1 FROM exceptions WHERE predecessor_exception_id=?
                AND status IN ('pending','approved')""",
                (exception_id,),
            ).fetchone()
            if existing is not None:
                raise StoreError("this exception already has an active renewal request")
            policy = conn.execute(
                "SELECT * FROM policies WHERE id=?", (current["policy_id"],),
            ).fetchone()
            if policy is None or policy["status"] != "active":
                raise StoreError("renewals require an active policy")
            policy_version = int(policy["active_version"])
            version = conn.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND version=?",
                (current["policy_id"], policy_version),
            ).fetchone()
            if version is None or version["state"] != "active":
                raise StoreError("renewals require an active policy version")
            renewal_number = int(conn.execute(
                """SELECT COALESCE(MAX(renewal_number),0)+1 FROM exceptions
                WHERE predecessor_exception_id=?""",
                (exception_id,),
            ).fetchone()[0])
            policy_digest = str(version["content_digest"])
            canonical = {
                "policy_id": current["policy_id"],
                "policy_version": policy_version,
                "policy_digest": policy_digest,
                "scope": current["scope"],
                "rationale": rationale,
                "compensating_controls": controls,
                "owner": owner,
                "expires_at": expires_at,
                "evidence_ids": evidence,
                "predecessor_exception_id": exception_id,
                "renewal_number": renewal_number,
            }
            request_digest = _digest(canonical)
            resolved_owner_name = owner_name or owner
            conn.execute(
                """INSERT INTO exceptions
                (id,policy_id,policy_version,policy_digest,scope,rationale,
                 compensating_controls,owner,owner_name,evidence_ids,request_digest,
                 expires_at,status,version,requested_by,requested_by_name,
                 approved_by,approved_by_name,decision_rationale,decided_at,
                 revoked_by,revoked_by_name,revoked_at,revocation_rationale,
                 predecessor_exception_id,renewal_number,superseded_by_exception_id,
                 superseded_at,created_at,updated_at)
                VALUES (:id,:policy_id,:policy_version,:policy_digest,:scope,:rationale,
                        :controls,:owner,:owner_name,:evidence,:digest,:expires_at,
                        'pending',1,:actor,:actor_name,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
                        NULL,:predecessor,:renewal_number,NULL,NULL,:now,:now)""",
                {
                    "id": successor_id, "policy_id": current["policy_id"],
                    "policy_version": policy_version, "policy_digest": policy_digest,
                    "scope": current["scope"], "rationale": rationale,
                    "controls": controls, "owner": owner,
                    "owner_name": resolved_owner_name, "evidence": _json(evidence),
                    "digest": request_digest, "expires_at": expires_at,
                    "actor": actor, "actor_name": actor_name,
                    "predecessor": exception_id,
                    "renewal_number": renewal_number, "now": now,
                },
            )
            approval_expires = min(expires_at, current["expires_at"], now + 604800)
            conn.execute(
                """INSERT INTO approvals
                (id,kind,resource_id,resource_version,request_digest,evidence_ids,
                 requester,requester_name,status,rationale,approver,approver_name,
                 decision_rationale,version,expires_at,created_at,decided_at,expired_at)
                VALUES (?,'exception',?,?,?, ?,?,?,'pending',?,NULL,NULL,NULL,1,?,?,NULL,NULL)""",
                (
                    approval_id, successor_id, 1, request_digest, _json(evidence),
                    actor, actor_name, rationale, approval_expires, now,
                ),
            )
            conn.execute(
                """UPDATE exceptions SET version=version+1,updated_at=? WHERE id=?""",
                (now, exception_id),
            )
            conn.execute(
                """INSERT INTO exception_events
                (id,exception_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at,correlation_id)
                VALUES (?,?,?,?,?,'exception.renewal_started','approved','approved',?,?,?,?)""",
                (
                    predecessor_event_id, exception_id, actor, actor_name, actor_role,
                    rationale, _json([successor_id, *evidence]), now, correlation_id,
                ),
            )
            self._enqueue_governance_event(
                conn, resource_kind="exception", event_id=predecessor_event_id,
            )
            conn.execute(
                """INSERT INTO exception_events
                (id,exception_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at,correlation_id)
                VALUES (?,?,?,?,?,'exception.renewal_requested',NULL,'pending',?,?,?,?)""",
                (
                    successor_event_id, successor_id, actor, actor_name, actor_role,
                    rationale, _json([exception_id, *evidence]), now, correlation_id,
                ),
            )
            self._enqueue_governance_event(
                conn, resource_kind="exception", event_id=successor_event_id,
            )
            self._governance_notify(
                conn, kind="exception.renewal_requested",
                title="Exception renewal ready",
                message=f"{actor_name} requested renewal for {current['scope']}.",
                resource_kind="approval", resource_id=approval_id,
                recipient_role="ciso",
                dedupe_key=f"exception-renewal:{successor_id}:1",
            )
        return self.exception(successor_id), self.approval(approval_id)

    def revoke_exception(
        self, exception_id: str, *, expected_version: int, rationale: str,
        evidence_ids: list[str], actor: str, actor_name: str, actor_role: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        """End an approved exception and cancel any pending renewal atomically."""
        now, event_id = _now(), _id("eev")
        evidence = list(dict.fromkeys(
            value.strip() for value in evidence_ids if value.strip()
        ))
        with self._write() as conn:
            current = conn.execute(
                "SELECT * FROM exceptions WHERE id=?", (exception_id,),
            ).fetchone()
            if current is None:
                raise Missing("unknown exception")
            if current["version"] != expected_version:
                raise VersionConflict("exception changed; reload before revoking it")
            if current["requested_by"] == actor:
                raise SeparationConflict(
                    "the exception requester cannot revoke the same request"
                )
            if current["status"] != "approved" or current["expires_at"] <= now:
                raise StoreError("only a currently approved exception can be revoked")
            conn.execute(
                """UPDATE exceptions SET status='revoked',revoked_by=?,
                revoked_by_name=?,revoked_at=?,revocation_rationale=?,
                version=version+1,updated_at=? WHERE id=?""",
                (actor, actor_name, now, rationale, now, exception_id),
            )
            conn.execute(
                """INSERT INTO exception_events
                (id,exception_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at,correlation_id)
                VALUES (?,?,?,?,?,'exception.revoked','approved','revoked',?,?,?,?)""",
                (
                    event_id, exception_id, actor, actor_name, actor_role,
                    rationale, _json(evidence), now, correlation_id,
                ),
            )
            self._enqueue_governance_event(
                conn, resource_kind="exception", event_id=event_id,
            )
            successors = conn.execute(
                """SELECT * FROM exceptions WHERE predecessor_exception_id=?
                AND status='pending'""", (exception_id,),
            ).fetchall()
            for successor in successors:
                cancellation = "Renewal cancelled because the prior exception was revoked."
                cancellation_event_id = _id("eev")
                conn.execute(
                    """UPDATE exceptions SET status='revoked',revoked_by=?,
                    revoked_by_name=?,revoked_at=?,revocation_rationale=?,
                    version=version+1,updated_at=? WHERE id=?""",
                    (actor, actor_name, now, cancellation, now, successor["id"]),
                )
                conn.execute(
                    """UPDATE approvals SET status='rejected',approver=?,approver_name=?,
                    decision_rationale=?,version=version+1,decided_at=?
                    WHERE kind='exception' AND resource_id=? AND status='pending'""",
                    (actor, actor_name, cancellation, now, successor["id"]),
                )
                conn.execute(
                    """INSERT INTO exception_events
                    (id,exception_id,actor,actor_name,actor_role,action,from_state,
                     to_state,rationale,evidence_ids,at,correlation_id)
                    VALUES (?,?,?,?,?,'exception.renewal_cancelled','pending','revoked',?,?,?,?)""",
                    (
                        cancellation_event_id, successor["id"], actor, actor_name,
                        actor_role,
                        cancellation, _json([exception_id]), now, correlation_id,
                    ),
                )
                self._enqueue_governance_event(
                    conn, resource_kind="exception",
                    event_id=cancellation_event_id,
                )
            recipients = {current["requested_by"]} - {actor}
            for recipient in recipients:
                self._governance_notify(
                    conn, kind="exception.revoked", title="Exception revoked",
                    message=f"The exception for {current['scope']} is no longer active.",
                    resource_kind="exception", resource_id=exception_id,
                    recipient_subject=recipient,
                    dedupe_key=f"exception-revoked:{exception_id}:{recipient}",
                )
        return self.exception(exception_id)

    def exceptions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            ids = [row["id"] for row in conn.execute(
                "SELECT id FROM exceptions ORDER BY updated_at DESC,id DESC"
            ).fetchall()]
        return [self.exception(exception_id) for exception_id in ids]

    def exception(self, exception_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM exceptions WHERE id=?", (exception_id,)).fetchone()
            if row is None:
                raise Missing("unknown exception")
            events = conn.execute(
                "SELECT * FROM exception_events WHERE exception_id=? ORDER BY at,id",
                (exception_id,),
            ).fetchall()
        return self._exception(row, events)

    def approvals(self, status: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM approvals", []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC,id DESC"
        with self._connect() as conn:
            return [self._approval(row) for row in conn.execute(sql, args).fetchall()]

    def approval(self, approval_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            raise Missing("unknown approval")
        return self._approval(row)

    def decide_approval(
        self, approval_id: str, *, expected_version: int, decision: str,
        rationale: str, actor: str, actor_name: str, actor_role: str,
        correlation_id: str,
    ) -> dict[str, Any]:
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
            if current["kind"] != "exception":
                raise StoreError("unsupported approval kind")
            exception = conn.execute(
                "SELECT * FROM exceptions WHERE id=?", (current["resource_id"],),
            ).fetchone()
            if exception is None:
                raise Missing("unknown exception")
            if exception["version"] != current["resource_version"]:
                raise VersionConflict("exception changed; reload before deciding")
            if exception["status"] != "pending" or exception["expires_at"] <= now:
                raise StoreError("exception is no longer pending")
            if exception["request_digest"] != current["request_digest"]:
                raise StoreError("approval no longer matches the exception request")
            predecessor = None
            if status == "approved" and exception["predecessor_exception_id"]:
                predecessor = conn.execute(
                    "SELECT * FROM exceptions WHERE id=?",
                    (exception["predecessor_exception_id"],),
                ).fetchone()
                if (
                    predecessor is None
                    or predecessor["status"] != "approved"
                    or predecessor["expires_at"] <= now
                ):
                    raise StoreError(
                        "the prior exception is no longer active; submit a new request"
                    )
            conn.execute(
                """UPDATE approvals SET status=?,approver=?,approver_name=?,
                decision_rationale=?,version=?,decided_at=? WHERE id=?""",
                (
                    status, actor, actor_name, rationale,
                    expected_version + 1, now, approval_id,
                ),
            )
            conn.execute(
                """UPDATE exceptions SET status=?,approved_by=?,approved_by_name=?,
                decision_rationale=?,decided_at=?,version=version+1,updated_at=?
                WHERE id=?""",
                (
                    status, actor if status == "approved" else None,
                    actor_name if status == "approved" else None,
                    rationale, now, now, current["resource_id"],
                ),
            )
            decision_event_id = _id("eev")
            conn.execute(
                """INSERT INTO exception_events
                (id,exception_id,actor,actor_name,actor_role,action,from_state,
                 to_state,rationale,evidence_ids,at,correlation_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision_event_id, current["resource_id"], actor, actor_name,
                    actor_role, f"exception.{status}", "pending", status,
                    rationale, current["evidence_ids"], now, correlation_id,
                ),
            )
            self._enqueue_governance_event(
                conn, resource_kind="exception", event_id=decision_event_id,
            )
            if predecessor is not None:
                conn.execute(
                    """UPDATE exceptions SET status='superseded',
                    superseded_by_exception_id=?,superseded_at=?,version=version+1,
                    updated_at=? WHERE id=?""",
                    (exception["id"], now, now, predecessor["id"]),
                )
                supersession_event_id = _id("eev")
                conn.execute(
                    """INSERT INTO exception_events
                    (id,exception_id,actor,actor_name,actor_role,action,from_state,
                     to_state,rationale,evidence_ids,at,correlation_id)
                    VALUES (?,?,?,?,?,'exception.superseded','approved','superseded',?,?,?,?)""",
                    (
                        supersession_event_id, predecessor["id"], actor, actor_name,
                        actor_role, rationale, _json([exception["id"]]), now,
                        correlation_id,
                    ),
                )
                self._enqueue_governance_event(
                    conn, resource_kind="exception",
                    event_id=supersession_event_id,
                )
            recipients = {exception["requested_by"]} - {actor}
            for recipient in recipients:
                self._governance_notify(
                    conn, kind=f"exception.{status}",
                    title=f"Exception {status}",
                    message=f"The request for {exception['scope']} was {status}.",
                    resource_kind="exception", resource_id=exception["id"],
                    recipient_subject=recipient,
                    dedupe_key=f"exception-{status}:{exception['id']}:{recipient}",
                )
            if status == "approved":
                for lead, label in ((604800, "7 days"), (86400, "24 hours")):
                    for recipient in {exception["requested_by"]}:
                        self._governance_notify(
                            conn, kind="exception.expiry_due",
                            title="Exception expiry approaching",
                            message=(
                                f"The exception for {exception['scope']} expires in {label}."
                            ),
                            resource_kind="exception", resource_id=exception["id"],
                            recipient_subject=recipient,
                            not_before=max(now, int(exception["expires_at"]) - lead),
                            dedupe_key=(
                                f"exception-expiry:{exception['id']}:{lead}:{recipient}"
                            ),
                        )
                    self._governance_notify(
                        conn, kind="exception.expiry_due",
                        title="Exception expiry approaching",
                        message=(
                            f"The exception for {exception['scope']} expires in {label}."
                        ),
                        resource_kind="exception", resource_id=exception["id"],
                        recipient_role="ciso",
                        not_before=max(now, int(exception["expires_at"]) - lead),
                        dedupe_key=f"exception-expiry:{exception['id']}:{lead}:ciso",
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
    def _governance_notify(
        conn: sqlite3.Connection, *, kind: str, title: str, message: str,
        resource_kind: str, resource_id: str, dedupe_key: str,
        recipient_subject: str | None = None,
        recipient_role: str | None = None, not_before: int | None = None,
    ) -> None:
        now = _now()
        conn.execute(
            """INSERT OR IGNORE INTO governance_notifications
            (id,kind,title,message,resource_kind,resource_id,recipient_subject,
             recipient_role,not_before,created_at,dedupe_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _id("gnt"), kind, title, message, resource_kind, resource_id,
                recipient_subject, recipient_role, not_before or now, now,
                dedupe_key,
            ),
        )

    def reconcile_governance_expiry(
        self, *, now: int | None = None,
    ) -> dict[str, int]:
        """Materialize due approval and exception transitions exactly once."""
        at = int(now if now is not None else _now())
        counts = {"approvals_expired": 0, "exceptions_expired": 0}
        try:
            with self._write() as conn:
                conn.execute(
                    """UPDATE governance_reconciler_state
                    SET last_started_at=?,last_error=NULL WHERE singleton=1""",
                    (at,),
                )
                approvals = conn.execute(
                    """SELECT * FROM approvals
                    WHERE status='pending' AND expires_at<=? ORDER BY expires_at,id""",
                    (at,),
                ).fetchall()
                for approval in approvals:
                    conn.execute(
                        """UPDATE approvals SET status='expired',version=version+1,
                        expired_at=? WHERE id=? AND status='pending'""",
                        (at, approval["id"]),
                    )
                    counts["approvals_expired"] += 1
                    if approval["kind"] != "exception":
                        continue
                    exception = conn.execute(
                        "SELECT * FROM exceptions WHERE id=?",
                        (approval["resource_id"],),
                    ).fetchone()
                    if exception is not None and exception["status"] == "pending":
                        conn.execute(
                            """UPDATE exceptions SET status='expired',
                            version=version+1,updated_at=? WHERE id=?""",
                            (at, exception["id"]),
                        )
                        expiry_event_id = _id("eev")
                        conn.execute(
                            """INSERT INTO exception_events
                            (id,exception_id,actor,actor_name,actor_role,action,
                             from_state,to_state,rationale,evidence_ids,at,correlation_id)
                            VALUES (?,?,?,?,?,'exception.approval_expired','pending',
                                    'expired',?,?,?,'system:governance-expiry')""",
                            (
                                expiry_event_id, exception["id"], "meshagent-system",
                                "MeshAgent", "system",
                                "The approval window expired without a decision.",
                                approval["evidence_ids"], at,
                            ),
                        )
                        self._enqueue_governance_event(
                            conn, resource_kind="exception",
                            event_id=expiry_event_id,
                        )
                        counts["exceptions_expired"] += 1
                        for recipient in {exception["requested_by"]}:
                            self._governance_notify(
                                conn, kind="exception.expired",
                                title="Exception request expired",
                                message=(
                                    f"The approval window for {exception['scope']} expired."
                                ),
                                resource_kind="exception",
                                resource_id=exception["id"],
                                recipient_subject=recipient,
                                dedupe_key=(
                                    f"exception-expired:{exception['id']}:{recipient}"
                                ),
                            )
                        self._governance_notify(
                            conn, kind="exception.expired",
                            title="Exception request expired",
                            message=(
                                f"The approval window for {exception['scope']} expired."
                            ),
                            resource_kind="exception",
                            resource_id=exception["id"], recipient_role="ciso",
                            dedupe_key=f"exception-expired:{exception['id']}:ciso",
                        )

                active_exceptions = conn.execute(
                    """SELECT * FROM exceptions
                    WHERE status='approved' AND expires_at<=?
                    ORDER BY expires_at,id""", (at,),
                ).fetchall()
                for exception in active_exceptions:
                    conn.execute(
                        """UPDATE exceptions SET status='expired',version=version+1,
                        updated_at=? WHERE id=? AND status='approved'""",
                        (at, exception["id"]),
                    )
                    expiry_event_id = _id("eev")
                    conn.execute(
                        """INSERT INTO exception_events
                        (id,exception_id,actor,actor_name,actor_role,action,
                         from_state,to_state,rationale,evidence_ids,at,correlation_id)
                        VALUES (?,?,?,?,?,'exception.expired','approved','expired',
                                ?,?,?, 'system:governance-expiry')""",
                        (
                            expiry_event_id, exception["id"], "meshagent-system",
                            "MeshAgent", "system",
                            "The governed exception reached its expiry time.",
                            exception["evidence_ids"], at,
                        ),
                    )
                    self._enqueue_governance_event(
                        conn, resource_kind="exception",
                        event_id=expiry_event_id,
                    )
                    counts["exceptions_expired"] += 1
                    for recipient in {exception["requested_by"]}:
                        self._governance_notify(
                            conn, kind="exception.expired",
                            title="Exception expired",
                            message=(
                                f"The exception for {exception['scope']} is no longer active."
                            ),
                            resource_kind="exception",
                            resource_id=exception["id"],
                            recipient_subject=recipient,
                            dedupe_key=f"exception-expired:{exception['id']}:{recipient}",
                        )
                    self._governance_notify(
                        conn, kind="exception.expired", title="Exception expired",
                        message=(
                            f"The exception for {exception['scope']} is no longer active."
                        ),
                        resource_kind="exception", resource_id=exception["id"],
                        recipient_role="ciso",
                        dedupe_key=f"exception-expired:{exception['id']}:ciso",
                    )
                conn.execute(
                    """UPDATE governance_reconciler_state
                    SET last_completed_at=?,last_error=NULL,last_counts=?
                    WHERE singleton=1""", (at, _json(counts)),
                )
        except Exception as exc:
            try:
                with self._write() as conn:
                    conn.execute(
                        """UPDATE governance_reconciler_state SET last_error=?
                        WHERE singleton=1""", (str(exc)[:1000],),
                    )
            finally:
                raise
        return counts

    def governance_lifecycle_status(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM governance_reconciler_state WHERE singleton=1"
            ).fetchone()
        assert row is not None
        body = dict(row)
        body.pop("singleton", None)
        body["last_counts"] = _loads(body["last_counts"], {})
        return body

    @staticmethod
    def _governance_relation_kinds(
        action: str, *, from_state: str | None = None,
    ) -> list[str]:
        if action == "policy.version_activated":
            kinds = ["policy_activation"]
            if from_state and from_state.endswith(":active"):
                kinds.append("policy_supersession")
            return kinds
        if action.startswith("policy."):
            return ["policy_version"]
        if action in (
            "exception.requested", "exception.renewal_requested",
            "exception.renewal_started",
        ):
            return ["exception_request"]
        if action in (
            "exception.approved", "exception.rejected", "exception.superseded",
        ):
            return ["exception_decision"]
        if action in ("exception.expired", "exception.approval_expired"):
            return ["exception_expiry"]
        if action in ("exception.revoked", "exception.renewal_cancelled"):
            return ["exception_revocation"]
        return []

    @staticmethod
    def _policy_event_version(
        conn: sqlite3.Connection, event: sqlite3.Row,
    ) -> sqlite3.Row | None:
        policy_id, action, at = event["policy_id"], event["action"], event["at"]
        match: sqlite3.Row | None = None
        for value in _loads(event["evidence_ids"], []):
            prefix = f"policy-version:{policy_id}@"
            if str(value).startswith(prefix) and str(value)[len(prefix):].isdigit():
                return conn.execute(
                    "SELECT * FROM policy_versions WHERE policy_id=? AND version=?",
                    (policy_id, int(str(value)[len(prefix):])),
                ).fetchone()
        if action == "policy.created":
            match = conn.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND version=1",
                (policy_id,),
            ).fetchone()
        elif action == "policy.version_created":
            match = conn.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND created_at=? "
                "ORDER BY version DESC LIMIT 1", (policy_id, at),
            ).fetchone()
        elif action == "policy.version_submitted":
            match = conn.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND submitted_at=? "
                "ORDER BY version DESC LIMIT 1", (policy_id, at),
            ).fetchone()
        elif action == "policy.version_withdrawn":
            match = conn.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND withdrawn_at=? "
                "ORDER BY version DESC LIMIT 1", (policy_id, at),
            ).fetchone()
        elif action == "policy.version_activated":
            match = conn.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND activated_at=? "
                "ORDER BY version DESC LIMIT 1", (policy_id, at),
            ).fetchone()
        elif action == "policy.retired":
            match = conn.execute(
                "SELECT * FROM policy_versions WHERE policy_id=? AND state='retired' "
                "ORDER BY version DESC LIMIT 1", (policy_id,),
            ).fetchone()
        if match is not None:
            return match
        return conn.execute(
            "SELECT * FROM policy_versions WHERE policy_id=? "
            "ORDER BY ABS(created_at-?),version DESC LIMIT 1", (policy_id, at),
        ).fetchone()

    @staticmethod
    def _resolved_governance_evidence(
        conn: sqlite3.Connection, evidence_ids: list[str],
    ) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        for evidence_id in dict.fromkeys(evidence_ids):
            kind: str | None = None
            resource_id = ""
            if evidence_id.startswith("review:"):
                kind, resource_id = "review", evidence_id.split(":", 1)[1]
                row = conn.execute(
                    "SELECT evidence_root_ulid FROM review_requests WHERE id=?",
                    (resource_id,),
                ).fetchone()
            elif evidence_id.startswith("case:"):
                kind, resource_id = "case", evidence_id.split(":", 1)[1]
                row = conn.execute(
                    "SELECT evidence_root_ulid FROM review_requests "
                    "WHERE escalated_case_id=? ORDER BY created_at LIMIT 1",
                    (resource_id,),
                ).fetchone()
            else:
                continue
            native_ulid = (
                str(row["evidence_root_ulid"])
                if row is not None and row["evidence_root_ulid"] else None
            )
            resolved.append({
                "kind": kind, "resource_id": resource_id,
                "native_ulid": native_ulid, "resolved": bool(native_ulid),
            })
        return resolved

    @classmethod
    def _governance_snapshot(
        cls, conn: sqlite3.Connection, *, resource_kind: str,
        event: sqlite3.Row, relation_kind: str,
    ) -> dict[str, Any]:
        evidence_ids = list(_loads(event["evidence_ids"], []))
        payload: dict[str, Any] = {
            "schema_version": 1,
            "event_id": event["id"],
            "resource_kind": resource_kind,
            "resource_id": event[f"{resource_kind}_id"],
            "relation_kind": relation_kind,
            "action": event["action"],
            "from_state": event["from_state"],
            "to_state": event["to_state"],
            "actor": event["actor"],
            "actor_name": event["actor_name"],
            "actor_role": event["actor_role"],
            "rationale": event["rationale"],
            "evidence_ids": evidence_ids,
            "occurred_at": event["at"],
            "correlation_id": event["correlation_id"],
        }
        if resource_kind == "policy":
            policy = conn.execute(
                "SELECT * FROM policies WHERE id=?", (event["policy_id"],),
            ).fetchone()
            version = cls._policy_event_version(conn, event)
            if policy is None or version is None:
                raise Missing("governance projection source is unavailable")
            predecessor_version: int | None = None
            if relation_kind == "policy_supersession" and event["from_state"]:
                raw = str(event["from_state"]).split(":", 1)[0]
                if raw.startswith("v") and raw[1:].isdigit():
                    predecessor_version = int(raw[1:])
            payload["policy"] = {
                "id": policy["id"], "name": policy["name"],
                "scope": policy["scope"], "version": int(version["version"]),
                "version_state": version["state"],
                "content_digest": version["content_digest"],
                "severity_threshold": version["severity_threshold"],
                "denied_licenses": _loads(version["denied_licenses"], []),
                "block_on_unknown": bool(version["block_on_unknown"]),
                "version_rationale": version["rationale"],
                "created_by": version["created_by"],
                "submitted_by": version["submitted_by"],
                "activated_by": version["activated_by"],
                "effective_from": version["effective_from"],
                "effective_until": version["effective_until"],
                "predecessor_version": predecessor_version,
            }
        else:
            exception = conn.execute(
                "SELECT * FROM exceptions WHERE id=?", (event["exception_id"],),
            ).fetchone()
            if exception is None:
                raise Missing("governance projection source is unavailable")
            declared = list(dict.fromkeys(
                [*evidence_ids, *_loads(exception["evidence_ids"], [])]
            ))
            payload["evidence_ids"] = declared
            payload["exception"] = {
                "id": exception["id"], "policy_id": exception["policy_id"],
                "policy_version": int(exception["policy_version"]),
                "policy_digest": exception["policy_digest"],
                "request_digest": exception["request_digest"],
                "scope": exception["scope"], "owner": exception["owner"],
                "owner_name": exception["owner_name"],
                "compensating_controls": exception["compensating_controls"],
                "expires_at": int(exception["expires_at"]),
                "requested_by": exception["requested_by"],
                "approved_by": exception["approved_by"],
                "decision_rationale": exception["decision_rationale"],
                "predecessor_exception_id": exception["predecessor_exception_id"],
                "superseded_by_exception_id": exception["superseded_by_exception_id"],
                "renewal_number": int(exception["renewal_number"] or 0),
            }
            payload["resolved_evidence"] = cls._resolved_governance_evidence(
                conn, declared,
            )
        return payload

    @classmethod
    def _enqueue_governance_event(
        cls, conn: sqlite3.Connection, *, resource_kind: str,
        event_id: str,
    ) -> int:
        table = "policy_events" if resource_kind == "policy" else "exception_events"
        event = conn.execute(
            f"SELECT * FROM {table} WHERE id=?", (event_id,),
        ).fetchone()
        if event is None:
            raise Missing("unknown governance event")
        inserted = 0
        for relation_kind in cls._governance_relation_kinds(
            str(event["action"]), from_state=event["from_state"],
        ):
            payload = cls._governance_snapshot(
                conn, resource_kind=resource_kind, event=event,
                relation_kind=relation_kind,
            )
            canonical = _json(payload)
            projection_id = f"{event_id}:{relation_kind}"
            result = conn.execute(
                """INSERT OR IGNORE INTO governance_projection_outbox
                (projection_id,event_id,resource_kind,resource_id,relation_kind,
                 payload_json,payload_sha256,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    projection_id, event_id, resource_kind,
                    event[f"{resource_kind}_id"], relation_kind, canonical,
                    hashlib.sha256(canonical.encode()).hexdigest(), event["at"],
                ),
            )
            inserted += int(result.rowcount)
        return inserted

    def reset_interrupted_governance_projections(self) -> int:
        with self._write() as conn:
            result = conn.execute(
                "UPDATE governance_projection_outbox SET status='pending', "
                "error='projection interrupted before acknowledgement', "
                "next_attempt_at=NULL WHERE status='projecting'"
            )
            return int(result.rowcount)

    def recoverable_governance_projections(
        self, *, limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM governance_projection_outbox WHERE "
                "status IN ('pending','failed') AND "
                "(next_attempt_at IS NULL OR next_attempt_at<=?) "
                "ORDER BY created_at,projection_id LIMIT ?",
                (_now(), max(1, min(limit, 500))),
            ).fetchall()
        values: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = _loads(item["payload_json"], {})
            values.append(item)
        return values

    def mark_governance_projection_started(self, projection_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE governance_projection_outbox SET status='projecting', "
                "attempts=attempts+1,error=NULL,next_attempt_at=NULL "
                "WHERE projection_id=? AND status IN ('pending','failed')",
                (projection_id,),
            )

    @staticmethod
    def _verify_governance_projection_row(
        conn: sqlite3.Connection, row: sqlite3.Row,
    ) -> dict[str, Any]:
        payload = _loads(row["payload_json"], {})
        canonical_sha = hashlib.sha256(_json(payload).encode()).hexdigest()
        if canonical_sha != row["payload_sha256"]:
            raise StoreError("governance projection payload digest mismatch")
        if row["resource_kind"] == "policy":
            policy = payload.get("policy") or {}
            source = conn.execute(
                "SELECT content_digest FROM policy_versions "
                "WHERE policy_id=? AND version=?",
                (policy.get("id"), policy.get("version")),
            ).fetchone()
            if source is None or source["content_digest"] != policy.get("content_digest"):
                raise StoreError("policy version digest no longer matches projection")
        else:
            exception = payload.get("exception") or {}
            source = conn.execute(
                "SELECT request_digest,policy_digest FROM exceptions WHERE id=?",
                (exception.get("id"),),
            ).fetchone()
            if (
                source is None
                or source["request_digest"] != exception.get("request_digest")
                or source["policy_digest"] != exception.get("policy_digest")
            ):
                raise StoreError("exception digest no longer matches projection")
        return payload

    def verify_governance_projection(self, projection_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM governance_projection_outbox WHERE projection_id=?",
                (projection_id,),
            ).fetchone()
            if row is None:
                raise Missing("unknown governance projection")
            return self._verify_governance_projection_row(conn, row)

    def mark_governance_projection_complete(
        self, projection_id: str, native_ulid: str,
    ) -> None:
        with self._write() as conn:
            row = conn.execute(
                "SELECT * FROM governance_projection_outbox WHERE projection_id=?",
                (projection_id,),
            ).fetchone()
            if row is None:
                raise Missing("unknown governance projection")
            self._verify_governance_projection_row(conn, row)
            conn.execute(
                "UPDATE governance_projection_outbox SET status='projected', "
                "native_ulid=?,projected_at=?,error=NULL,next_attempt_at=NULL "
                "WHERE projection_id=?",
                (native_ulid, _now(), projection_id),
            )

    def mark_governance_projection_failed(
        self, projection_id: str, error: str,
    ) -> None:
        with self._write() as conn:
            row = conn.execute(
                "SELECT attempts FROM governance_projection_outbox "
                "WHERE projection_id=?", (projection_id,),
            ).fetchone()
            attempts = int(row["attempts"]) if row is not None else 1
            delay = min(300, 2 ** min(max(attempts - 1, 0), 8))
            conn.execute(
                "UPDATE governance_projection_outbox SET status='failed',error=?, "
                "next_attempt_at=? WHERE projection_id=?",
                (error[:512], _now() + delay, projection_id),
            )

    def governance_projection_ulids(
        self, resource_kind: str, resource_id: str,
    ) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT native_ulid FROM governance_projection_outbox "
                "WHERE resource_kind=? AND resource_id=? AND status='projected' "
                "AND native_ulid IS NOT NULL ORDER BY created_at,projection_id",
                (resource_kind, resource_id),
            ).fetchall()
        return [str(row["native_ulid"]) for row in rows]

    def governance_evidence_projection(
        self, resource_kind: str, resource_id: str,
    ) -> dict[str, Any]:
        if resource_kind not in ("policy", "exception"):
            raise Missing("unknown governance evidence kind")
        with self._connect() as conn:
            table = "policies" if resource_kind == "policy" else "exceptions"
            if conn.execute(
                f"SELECT 1 FROM {table} WHERE id=?", (resource_id,),
            ).fetchone() is None:
                raise Missing(f"unknown {resource_kind}")
            rows = conn.execute(
                "SELECT * FROM governance_projection_outbox "
                "WHERE resource_kind=? AND resource_id=? "
                "ORDER BY created_at,projection_id",
                (resource_kind, resource_id),
            ).fetchall()
            if not rows or any(
                row["status"] != "projected" or not row["native_ulid"]
                for row in rows
            ):
                raise Missing(
                    f"native {resource_kind} evidence is not fully projected"
                )
            for row in rows:
                self._verify_governance_projection_row(conn, row)
        return {
            "resource_kind": resource_kind,
            "resource_id": resource_id,
            "projection_count": len(rows),
            "native_ulids": [str(row["native_ulid"]) for row in rows],
            "payload_sha256": [str(row["payload_sha256"]) for row in rows],
        }

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
        bounded = max(1, min(limit, 200))
        now = _now()
        with self._connect() as conn:
            work_rows = conn.execute(
                """SELECT n.*,CASE WHEN r.notification_id IS NULL THEN 0 ELSE 1 END AS read
                FROM work_notifications n
                LEFT JOIN work_notification_reads r
                  ON r.notification_id=n.id AND r.subject=?
                WHERE n.not_before<=? AND
                  (n.recipient_subject=? OR n.recipient_role=?)
                ORDER BY n.created_at DESC,n.id DESC LIMIT ?""",
                (subject, now, subject, role, bounded),
            ).fetchall()
            governance_rows = conn.execute(
                """SELECT n.*,CASE WHEN r.notification_id IS NULL THEN 0 ELSE 1 END AS read
                FROM governance_notifications n
                LEFT JOIN governance_notification_reads r
                  ON r.notification_id=n.id AND r.subject=?
                WHERE n.not_before<=? AND
                  (n.recipient_subject=? OR n.recipient_role=?)
                ORDER BY n.created_at DESC,n.id DESC LIMIT ?""",
                (subject, now, subject, role, bounded),
            ).fetchall()
        values = []
        for row in [*work_rows, *governance_rows]:
            item = dict(row)
            item["read"] = bool(item["read"])
            item["route"] = {
                "review": f"/analyst/reviews/{item['resource_id']}",
                "case": f"/analyst/cases/{item['resource_id']}",
                "policy": "/ciso/policies",
                "exception": (
                    "/ciso/policies" if role == "ciso" else "/analyst/queue"
                ),
                "approval": (
                    "/ciso/approvals" if role == "ciso" else "/analyst/queue"
                ),
            }[item["resource_kind"]]
            for key in ("recipient_subject", "recipient_role", "dedupe_key"):
                item.pop(key, None)
            values.append(item)
        values.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return values[:bounded]

    def read_notification(self, notification_id: str, *, subject: str, role: str) -> None:
        with self._write() as conn:
            visible = conn.execute(
                "SELECT 'work' AS source FROM work_notifications WHERE id=? AND "
                "not_before<=? AND (recipient_subject=? OR recipient_role=?) "
                "UNION ALL SELECT 'governance' AS source FROM governance_notifications "
                "WHERE id=? AND not_before<=? AND "
                "(recipient_subject=? OR recipient_role=?)",
                (
                    notification_id, _now(), subject, role,
                    notification_id, _now(), subject, role,
                ),
            ).fetchone()
            if visible is None:
                raise Missing("unknown notification")
            table = (
                "work_notification_reads"
                if visible["source"] == "work"
                else "governance_notification_reads"
            )
            conn.execute(
                f"INSERT OR IGNORE INTO {table} VALUES (?,?,?)",
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
