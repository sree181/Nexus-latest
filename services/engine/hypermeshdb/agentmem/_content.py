"""
Project:     HyperMesh DB
File:        hypermeshdb/agentmem/_content.py
Description: Content sidecar for agent memory. The engine's property blob
             is capped at 256 bytes, so memory payloads (an episode record,
             a fact statement, a skill document) live in SQLite keyed by
             ULID. The graph commits to each payload through its sha256 in
             the CSHA column, which makes the sidecar tamper-evident and
             lets a tombstone redact the payload while the hash, and with
             it audit-chain integrity, is retained.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time

_DB_NAME = "agentmem_content.db"


def sha256_of(payload: dict) -> str:
    """Canonical sha256 of a JSON payload (sorted keys, compact)."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ContentStore:
    """ULID-keyed payload store with redaction that preserves the hash."""

    def __init__(self, db_dir: str) -> None:
        self._path = os.path.join(db_dir, _DB_NAME)
        os.makedirs(db_dir, exist_ok=True)
        self._local = threading.local()
        conn = self._conn()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS content ("
            " ulid TEXT PRIMARY KEY,"
            " sha TEXT NOT NULL,"
            " payload TEXT,"           # NULL after redaction
            " created_ts INTEGER NOT NULL,"
            " redacted_ts INTEGER)"
        )
        conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def put(self, ulid: str, payload: dict) -> str:
        """Store *payload* under *ulid*; returns the sha256 the graph must
        carry. Refuses to overwrite: content is append-only like the graph."""
        sha = sha256_of(payload)
        conn = self._conn()
        cur = conn.execute("SELECT sha FROM content WHERE ulid = ?", (ulid,))
        if cur.fetchone() is not None:
            raise ValueError(f"content for {ulid} already exists; append-only")
        conn.execute(
            "INSERT INTO content (ulid, sha, payload, created_ts) "
            "VALUES (?, ?, ?, ?)",
            (
                ulid,
                sha,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                int(time.time()),
            ),
        )
        conn.commit()
        return sha

    def get(self, ulid: str) -> tuple[dict | None, str | None, bool]:
        """Return (payload, sha, redacted). Payload is None when absent or
        redacted; the sha survives redaction."""
        cur = self._conn().execute(
            "SELECT payload, sha, redacted_ts FROM content WHERE ulid = ?",
            (ulid,),
        )
        row = cur.fetchone()
        if row is None:
            return None, None, False
        payload_txt, sha, redacted_ts = row
        redacted = redacted_ts is not None
        payload = json.loads(payload_txt) if payload_txt is not None else None
        return payload, str(sha), redacted

    def redact(self, ulid: str) -> bool:
        """Drop the payload, keep the hash. Returns True if a payload was
        removed, False if there was nothing to redact."""
        conn = self._conn()
        cur = conn.execute(
            "UPDATE content SET payload = NULL, redacted_ts = ? "
            "WHERE ulid = ? AND payload IS NOT NULL",
            (int(time.time()), ulid),
        )
        conn.commit()
        return cur.rowcount > 0

    def verify(self, ulid: str) -> bool | None:
        """Recompute the stored payload's sha against the recorded one.
        None when absent or redacted (nothing to verify)."""
        payload, sha, redacted = self.get(ulid)
        if payload is None or redacted:
            return None
        return sha256_of(payload) == sha

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
