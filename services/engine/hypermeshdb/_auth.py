"""
_auth.py — API key authentication for HyperMesh DB.

Keys are stored as SHA-256 hashes in the ``api_keys`` table inside
``schema.db`` of the database directory.  The plaintext key is returned
exactly once at creation time and is never persisted.

Key format
----------
``hmdb_<32-hex-chars>``   (total 37 characters)

Roles (ordered by privilege)
-----------------------------
readonly    MATCH queries only
readwrite   MATCH + INSERT / DELETE / UPDATE / compact
admin       readwrite + DDL (CREATE/DROP TABLE, INDEX) + backup + key mgmt

Environment variables
---------------------
HMDB_API_KEY        If set and the key store is empty, an admin key with
                    this value is automatically provisioned on first open.
HMDB_AUTH_DISABLED  Set to "1" to skip auth entirely (dev/test mode).

Usage in FastAPI
----------------
::

    from hypermeshdb._auth import require_role

    @router.post("/v1/hyperedges")
    async def insert(req: InsertRequest, _: str = Depends(require_role("readwrite")), ...):
        ...
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_KEY_PREFIX  = "hmdb_"
_KEY_LEN     = 32          # hex chars after prefix → 16 random bytes
_HASH_ALG    = "sha256"
_SCHEMA_VER  = 1

_ROLES        = ("readonly", "readwrite", "admin")
_ROLE_ORDER   = {r: i for i, r in enumerate(_ROLES)}

# FastAPI security scheme (shows the lock icon in /docs)
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# ── Data structures ───────────────────────────────────────────────────────────

@dataclass(slots=True)
class ApiKeyRecord:
    key_id:          str
    description:     str
    role:            str
    created_at:      int
    last_used_at:    int | None
    expires_at:      int | None = None
    workspace_id:    str | None = None        # which workspace this key is scoped to
    table_patterns:  list[str] = field(default_factory=list)  # e.g. ["LTM_*"], [] = unrestricted

    def is_expired(self, now: int | None = None) -> bool:
        if self.expires_at is None:
            return False
        return int(now if now is not None else time.time()) >= self.expires_at

    def allows_table(self, table: str) -> bool:
        """Return True if this key is allowed to access the given table."""
        if not self.table_patterns:
            return True  # empty = unrestricted (admin behaviour)
        return any(
            fnmatch.fnmatch(table.upper(), p.upper())
            for p in self.table_patterns
        )


# ── AuthStore ─────────────────────────────────────────────────────────────────

class AuthStore:
    """
    Thin wrapper around the ``api_keys`` table in ``schema.db``.

    All methods are synchronous and hold the SQLite connection open for the
    lifetime of the store.  Thread safety is provided by SQLite's own
    serialised write mode (``check_same_thread=False`` + WAL mode).
    """

    def __init__(self, db_dir: str) -> None:
        self._path = os.path.join(db_dir, "schema.db")
        self._con  = sqlite3.connect(self._path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._migrate()
        self._maybe_bootstrap()

    # ── Schema migration ──────────────────────────────────────────────────────

    def _migrate(self) -> None:
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key_id        TEXT NOT NULL PRIMARY KEY,
                key_hash      TEXT NOT NULL,
                description   TEXT NOT NULL DEFAULT '',
                role          TEXT NOT NULL DEFAULT 'readonly'
                                   CHECK (role IN ('readonly','readwrite','admin')),
                created_at    INTEGER NOT NULL,
                last_used_at  INTEGER,
                workspace_id  TEXT,
                table_patterns TEXT NOT NULL DEFAULT '[]'
            );
        """)
        # Idempotent column additions for existing DBs
        for col, definition in [
            ("workspace_id",   "TEXT"),
            ("table_patterns", "TEXT NOT NULL DEFAULT '[]'"),
            ("expires_at",     "INTEGER"),
        ]:
            try:
                self._con.execute(f"ALTER TABLE api_keys ADD COLUMN {col} {definition}")
                self._con.commit()
            except sqlite3.OperationalError:
                pass  # column already exists


    # ── Bootstrap from environment ────────────────────────────────────────────

    def _maybe_bootstrap(self) -> None:
        """
        If ``HMDB_API_KEY`` is set and the key table is empty, create an
        admin key with that value so the server is immediately usable.
        """
        env_key = os.environ.get("HMDB_API_KEY", "").strip()
        if not env_key:
            return
        row = self._con.execute("SELECT COUNT(*) FROM api_keys").fetchone()
        if row[0] > 0:
            return
        # Validate format or accept any string as the fixed plaintext key
        if not env_key.startswith(_KEY_PREFIX):
            env_key = _KEY_PREFIX + env_key[:_KEY_LEN].ljust(_KEY_LEN, "0")
        key_id   = f"env_{env_key[-8:]}"
        key_hash = _hash_key(env_key)
        self._con.execute(
            "INSERT INTO api_keys (key_id, key_hash, description, role, created_at)"
            " VALUES (?, ?, ?, 'admin', ?)",
            (key_id, key_hash, "Auto-provisioned from HMDB_API_KEY", int(time.time())),
        )
        self._con.commit()
        log.info("auth: bootstrapped admin key %s from HMDB_API_KEY", key_id)

    # ── Public API ────────────────────────────────────────────────────────────

    def create_key(
        self,
        description:    str = "",
        role:           str = "readonly",
        workspace_id:   str | None = None,
        table_patterns: list[str] | None = None,
        expires_at:     int | None = None,
        expires_in_days: int | None = None,
        expires_in_seconds: int | None = None,
    ) -> tuple[str, "ApiKeyRecord"]:
        """
        Generate a new key.  Returns ``(plaintext_key, record)``.
        The plaintext is never stored; this is the only time it is returned.
        """
        if role not in _ROLE_ORDER:
            raise ValueError(f"Invalid role '{role}'. Must be one of: {_ROLES}")
        if expires_in_seconds is not None:
            expires_at = int(time.time()) + int(expires_in_seconds)
        elif expires_in_days is not None:
            expires_at = int(time.time()) + int(expires_in_days) * 86400
        plaintext  = _KEY_PREFIX + secrets.token_hex(_KEY_LEN // 2)
        key_id     = f"key_{secrets.token_hex(4)}"
        key_hash   = _hash_key(plaintext)
        now        = int(time.time())
        patterns   = table_patterns or []
        self._con.execute(
            "INSERT INTO api_keys"
            " (key_id, key_hash, description, role, created_at, workspace_id, table_patterns, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (key_id, key_hash, description, role, now,
             workspace_id, json.dumps(patterns), expires_at),
        )
        self._con.commit()
        log.info("auth: created key %s role=%s workspace=%s", key_id, role, workspace_id)
        rec = ApiKeyRecord(
            key_id=key_id, description=description, role=role,
            created_at=now, last_used_at=None, expires_at=expires_at,
            workspace_id=workspace_id, table_patterns=patterns,
        )
        return plaintext, rec

    def verify(self, plaintext: str) -> ApiKeyRecord | None:
        """Return the record if the key is valid, else None."""
        key_hash = _hash_key(plaintext)
        row = self._con.execute(
            "SELECT key_id, description, role, created_at, last_used_at,"
            "       workspace_id, table_patterns, expires_at"
            " FROM api_keys WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["table_patterns"] = json.loads(d.get("table_patterns") or "[]")
        rec = ApiKeyRecord(**d)
        if rec.is_expired():
            log.info("auth: expired key %s attempted", rec.key_id)
            return None
        self._con.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE key_hash = ?",
            (int(time.time()), key_hash),
        )
        self._con.commit()
        return rec

    def list_keys(self) -> list[ApiKeyRecord]:
        rows = self._con.execute(
            "SELECT key_id, description, role, created_at, last_used_at, expires_at"
            " FROM api_keys ORDER BY created_at"
        ).fetchall()
        return [ApiKeyRecord(**dict(r)) for r in rows]

    def revoke(self, key_id: str) -> bool:
        """Delete a key by key_id.  Returns True if a row was deleted."""
        cur = self._con.execute("DELETE FROM api_keys WHERE key_id = ?", (key_id,))
        self._con.commit()
        deleted = cur.rowcount > 0
        if deleted:
            log.info("auth: revoked key %s", key_id)
        return deleted

    def revoke_expired(self, now: int | None = None) -> list[str]:
        """Delete all expired keys. Returns revoked key_ids."""
        ts = int(now if now is not None else time.time())
        rows = self._con.execute(
            "SELECT key_id FROM api_keys"
            " WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (ts,),
        ).fetchall()
        key_ids = [str(r["key_id"]) for r in rows]
        if not key_ids:
            return []
        self._con.execute(
            "DELETE FROM api_keys WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (ts,),
        )
        self._con.commit()
        for key_id in key_ids:
            log.info("auth: revoked expired key %s", key_id)
        return key_ids

    def count(self) -> int:
        return self._con.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0]

    def close(self) -> None:
        self._con.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


# ── FastAPI dependency factories ──────────────────────────────────────────────

def _get_auth_store(request: Request) -> AuthStore | None:
    """Retrieve AuthStore from app state; None if auth is disabled."""
    if getattr(request.app.state, "auth_disabled", True):
        return None
    return getattr(request.app.state, "auth_store", None)


def require_role(minimum_role: str = "readonly"):
    """
    Returns a FastAPI dependency that enforces a minimum role.

    Usage::

        @router.get("/v1/info")
        async def info(_: str = Depends(require_role("readonly")), ...):
            ...
    """
    async def _dep(
        request:  Request,
        api_key:  str | None = Security(_api_key_header),
    ) -> ApiKeyRecord | None:
        auth_store: AuthStore | None = _get_auth_store(request)

        # ── Auth disabled (dev / test mode) ───────────────────────────────
        if auth_store is None:
            return None

        # ── No key provided ───────────────────────────────────────────────
        if not api_key:
            # Allow Authorization: Bearer <key> as fallback
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header[7:].strip()
        if not api_key:
            raise HTTPException(
                status_code = status.HTTP_401_UNAUTHORIZED,
                detail      = "Missing API key. Provide X-API-Key header or Authorization: Bearer <key>.",
                headers     = {"WWW-Authenticate": "ApiKey"},
            )

        # ── Verify key ────────────────────────────────────────────────────
        record = auth_store.verify(api_key)
        if record is None:
            log.warning("auth: invalid key attempt from %s", request.client)
            raise HTTPException(
                status_code = status.HTTP_401_UNAUTHORIZED,
                detail      = "Invalid API key.",
            )

        # ── Check role ────────────────────────────────────────────────────
        if _ROLE_ORDER.get(record.role, -1) < _ROLE_ORDER.get(minimum_role, 999):
            log.warning(
                "auth: key %s (role=%s) tried to access %s (requires %s)",
                record.key_id, record.role, request.url.path, minimum_role,
            )
            raise HTTPException(
                status_code = status.HTTP_403_FORBIDDEN,
                detail      = f"This operation requires the '{minimum_role}' role. "
                              f"Key '{record.key_id}' has role '{record.role}'.",
            )

        return record

    return _dep


# ── Convenience aliases ───────────────────────────────────────────────────────

ReadOnlyDep  = Depends(require_role("readonly"))
ReadWriteDep = Depends(require_role("readwrite"))
AdminDep     = Depends(require_role("admin"))
