"""
Project:     HyperMesh DB
File:        hypermeshdb/agentmem/_registry.py
Description: SQLite-backed entity registry for agent memory. Maps string
             entity names (``"edge:01H..."``, ``"tool:web_search"``) to the
             integer node ids the engine addresses, safely under concurrent
             use. Replaces the JSON-file registry pattern for memory tables.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

import os
import sqlite3
import threading

_DB_NAME = "agentmem_entities.db"


class SqliteEntityRegistry:
    """Persistent, concurrency-safe name-to-id registry for one database dir.

    Ids are allocated by SQLite AUTOINCREMENT, so they are unique and stable
    across processes. Names follow the ``type:value`` convention used by the
    MCP registry (a bare name gets type ``entity``).
    """

    def __init__(self, db_dir: str) -> None:
        self._path = os.path.join(db_dir, _DB_NAME)
        os.makedirs(db_dir, exist_ok=True)
        self._local = threading.local()
        conn = self._conn()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS entities ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT NOT NULL UNIQUE)"
        )
        conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def get_or_create(self, name: str) -> int:
        """Return the id for *name*, allocating one on first sight."""
        name = name.strip()
        if not name:
            raise ValueError("entity name must be non-empty")
        conn = self._conn()
        cur = conn.execute("SELECT id FROM entities WHERE name = ?", (name,))
        row = cur.fetchone()
        if row is not None:
            return int(row[0])
        cur = conn.execute(
            "INSERT INTO entities (name) VALUES (?) "
            "ON CONFLICT(name) DO UPDATE SET name = name RETURNING id",
            (name,),
        )
        row = cur.fetchone()
        conn.commit()
        return int(row[0])

    def get(self, name: str) -> int | None:
        cur = self._conn().execute(
            "SELECT id FROM entities WHERE name = ?", (name.strip(),)
        )
        row = cur.fetchone()
        return int(row[0]) if row is not None else None

    def name_of(self, node_id: int) -> str | None:
        cur = self._conn().execute(
            "SELECT name FROM entities WHERE id = ?", (int(node_id),)
        )
        row = cur.fetchone()
        return str(row[0]) if row is not None else None

    def names_of(self, node_ids: list[int]) -> dict[int, str]:
        if not node_ids:
            return {}
        marks = ",".join("?" for _ in node_ids)
        cur = self._conn().execute(
            f"SELECT id, name FROM entities WHERE id IN ({marks})",
            [int(n) for n in node_ids],
        )
        return {int(i): str(n) for i, n in cur.fetchall()}

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
