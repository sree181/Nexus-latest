"""
schema_store.py — Persistent schema catalog for HyperMesh DB.

Backed by SQLite (WAL mode, same as node_store.py).
Stores the definitions of hyperedge and node tables independently of the
binary TPI/FMI index, so the schema survives restarts and compaction cycles.

Tables
──────
node_tables        — node table definitions  (name, description, created_at)
node_columns       — per-column definitions for each node table
                     (table_name, col_name, col_type, is_pk, nullable,
                      default_val, position)
hyperedge_tables   — hyperedge table defs   (name, member_tables JSON array,
                                              bucket_seconds, description, created_at)

Usage
──────
    from hypermesh_core.python.schema_store import SchemaStore, ColumnDef

    schema = SchemaStore("/tmp/hm_db/schema.db")
    schema.seed_defaults()          # idempotent; creates Drone + CoProximity

    # Define a custom node table for CISO machine data
    schema.create_node_table("Machine", description="Compromised endpoint nodes")
    schema.define_node_columns("Machine", [
        ColumnDef("machine_id", "INTEGER", is_pk=True),
        ColumnDef("hostname",   "TEXT"),
        ColumnDef("os",         "TEXT",    default_val="''"),
        ColumnDef("role",       "TEXT",    default_val="''"),
    ])

    schema.create_hyperedge_table(
        name="SensorProximity",
        member_tables=["Sensor", "Sensor"],
        bucket_seconds=30,
        description="Co-proximity hyperedge for sensor networks",
    )
    print(schema.list_hyperedge_tables())
    schema.drop_hyperedge_table("SensorProximity")
    schema.close()
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Column definition ─────────────────────────────────────────────────────────

@dataclass
class ColumnDef:
    """
    Definition of a single column in a node table.

    Parameters
    ----------
    name:
        Column name (case-insensitive; stored as provided).
    col_type:
        SQLite storage type: ``"INTEGER"``, ``"REAL"``, or ``"TEXT"``.
        Default: ``"TEXT"``.
    is_pk:
        Whether this column is the primary key.  Exactly one column per
        table must be the primary key.  Default: ``False``.
    nullable:
        Whether NULL is allowed.  Primary key columns are always NOT NULL.
        Default: ``True``.
    default_val:
        Raw SQL default expression (e.g. ``"''"`` for empty string,
        ``"0"`` for zero, ``"0.0"`` for zero float).
        ``None`` means no DEFAULT clause.  Default: ``None``.
    """
    name:        str
    col_type:    str  = "TEXT"
    is_pk:       bool = False
    nullable:    bool = True
    default_val: str | None = None

    def __post_init__(self) -> None:
        self.col_type = self.col_type.upper()
        if self.col_type not in ("INTEGER", "REAL", "TEXT"):
            raise ValueError(
                f"ColumnDef: unsupported col_type {self.col_type!r}. "
                f"Supported: 'INTEGER', 'REAL', 'TEXT'."
            )

    def ddl_fragment(self) -> str:
        """Return the SQLite column definition fragment, e.g. ``drone_id INTEGER PRIMARY KEY``."""
        parts = [self.name, self.col_type]
        if self.is_pk:
            parts.append("PRIMARY KEY")
        elif not self.nullable:
            parts.append("NOT NULL")
        if self.default_val is not None:
            parts.append(f"DEFAULT {self.default_val}")
        return " ".join(parts)

    def coerce(self, value: Any) -> Any:
        """
        Coerce *value* to the Python type that matches this column's type.
        Returns the column's default Python value (0, 0.0, or '') if value
        is None or a NaN float.
        """
        import math as _math
        # NaN of any numeric type → treat as missing
        if isinstance(value, float) and _math.isnan(value):
            return {"INTEGER": 0, "REAL": 0.0, "TEXT": ""}[self.col_type]
        if value is None:
            return {"INTEGER": 0, "REAL": 0.0, "TEXT": ""}[self.col_type]
        if self.col_type == "INTEGER":
            try:
                f = float(value)
                return 0 if _math.isnan(f) else int(f)
            except (TypeError, ValueError):
                return 0
        if self.col_type == "REAL":
            try:
                f = float(value)
                return 0.0 if _math.isnan(f) else f
            except (TypeError, ValueError):
                return 0.0
        # TEXT — NaN float already handled above
        return str(value)


# ── Built-in Drone node table column definitions ──────────────────────────────

DRONE_COLUMNS: list[ColumnDef] = [
    ColumnDef("drone_id",        "INTEGER", is_pk=True),
    ColumnDef("callsign",        "TEXT",    nullable=False, default_val="''"),
    ColumnDef("role",            "TEXT",    default_val="''"),
    ColumnDef("formation",       "TEXT",    default_val="''"),
    ColumnDef("mission",         "TEXT",    default_val="''"),
    ColumnDef("avg_battery",     "REAL",    default_val="0.0"),
    ColumnDef("avg_signal",      "REAL",    default_val="0.0"),
    ColumnDef("collision_events","INTEGER", default_val="0"),
]


class SchemaStore:
    """
    Persistent schema catalog backed by SQLite.
    Thread-safe for single-process use (WAL journal mode).
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    # ── Schema initialisation ─────────────────────────────────────────────

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS node_tables (
                name        TEXT    PRIMARY KEY COLLATE NOCASE,
                description TEXT    NOT NULL DEFAULT '',
                created_at  INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS node_columns (
                table_name  TEXT    NOT NULL COLLATE NOCASE
                                    REFERENCES node_tables(name) ON DELETE CASCADE,
                col_name    TEXT    NOT NULL,
                col_type    TEXT    NOT NULL DEFAULT 'TEXT',
                is_pk       INTEGER NOT NULL DEFAULT 0,
                nullable    INTEGER NOT NULL DEFAULT 1,
                default_val TEXT,
                position    INTEGER NOT NULL,
                PRIMARY KEY (table_name, col_name)
            );

            CREATE TABLE IF NOT EXISTS hyperedge_tables (
                name               TEXT    PRIMARY KEY COLLATE NOCASE,
                member_tables      TEXT    NOT NULL DEFAULT '[]',
                bucket_seconds     INTEGER NOT NULL DEFAULT 10,
                compact_threshold  INTEGER NOT NULL DEFAULT 0,
                description        TEXT    NOT NULL DEFAULT '',
                created_at         INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hyperedge_columns (
                table_name  TEXT    NOT NULL COLLATE NOCASE
                                    REFERENCES hyperedge_tables(name) ON DELETE CASCADE,
                col_name    TEXT    NOT NULL,
                col_type    TEXT    NOT NULL DEFAULT 'REAL',
                nullable    INTEGER NOT NULL DEFAULT 1,
                default_val TEXT,
                position    INTEGER NOT NULL,
                PRIMARY KEY (table_name, col_name)
            );

            CREATE TABLE IF NOT EXISTS hyperedge_indexes (
                table_name  TEXT    NOT NULL COLLATE NOCASE,
                col_name    TEXT    NOT NULL COLLATE NOCASE,
                created_at  INTEGER NOT NULL,
                PRIMARY KEY (table_name, col_name)
            );
        """)
        self._conn.commit()
        # Phase 8 migration: add compact_threshold column to existing databases
        # that were created before this column existed.  SQLite's ADD COLUMN is
        # idempotent when combined with the try/except guard below.
        try:
            self._conn.execute(
                "ALTER TABLE hyperedge_tables"
                " ADD COLUMN compact_threshold INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.commit()
        except Exception:
            pass  # column already exists — nothing to do

    # ── Default seeding (idempotent) ─────────────────────────────────────

    def seed_defaults(self) -> None:
        """
        Create the built-in node and hyperedge tables if they do not exist.
        Safe to call on every startup — uses INSERT OR IGNORE.
        """
        now = int(time.time())
        self._conn.execute(
            "INSERT OR IGNORE INTO node_tables (name, description, created_at)"
            " VALUES (?, ?, ?)",
            ("Drone", "Unmanned aerial platform node table", now),
        )
        self._conn.commit()
        # Seed Drone column definitions if not already present
        self._seed_node_columns("Drone", DRONE_COLUMNS)
        self._conn.execute(
            "INSERT OR IGNORE INTO hyperedge_tables"
            " (name, member_tables, bucket_seconds, compact_threshold,"
            "  description, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                "CoProximity",
                json.dumps(["Drone", "Drone"]),
                10,
                0,
                "Co-proximity hyperedge: drone pairs within proximity threshold",
                now,
            ),
        )
        self._conn.commit()

    def _seed_node_columns(self, table_name: str, columns: list[ColumnDef]) -> None:
        """Insert column definitions using INSERT OR IGNORE (idempotent)."""
        for pos, col in enumerate(columns):
            self._conn.execute(
                "INSERT OR IGNORE INTO node_columns"
                " (table_name, col_name, col_type, is_pk, nullable, default_val, position)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (table_name, col.name, col.col_type,
                 int(col.is_pk), int(col.nullable), col.default_val, pos),
            )
        self._conn.commit()

    # ── Node column API ───────────────────────────────────────────────────

    def define_node_columns(
        self,
        table_name: str,
        columns:    list[ColumnDef],
    ) -> None:
        """
        Store the column definitions for *table_name*, replacing any
        previously defined columns.

        Raises ``ValueError`` if the table does not exist in ``node_tables``,
        or if no column is marked ``is_pk=True``, or if more than one is.
        """
        if not self.get_node_table(table_name):
            raise ValueError(
                f"Node table {table_name!r} does not exist. "
                f"Call create_node_table() first."
            )
        pks = [c for c in columns if c.is_pk]
        if len(pks) != 1:
            raise ValueError(
                f"define_node_columns: exactly one column must be the primary key "
                f"(is_pk=True), got {len(pks)} for table {table_name!r}."
            )
        # Delete existing definitions then re-insert
        self._conn.execute(
            "DELETE FROM node_columns WHERE table_name = ?", (table_name,)
        )
        for pos, col in enumerate(columns):
            self._conn.execute(
                "INSERT INTO node_columns"
                " (table_name, col_name, col_type, is_pk, nullable, default_val, position)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (table_name, col.name, col.col_type,
                 int(col.is_pk), int(col.nullable), col.default_val, pos),
            )
        self._conn.commit()

    def get_node_columns(self, table_name: str) -> list[ColumnDef]:
        """
        Return the ordered list of :class:`ColumnDef` for *table_name*.
        Returns an empty list if no columns have been defined yet.
        """
        rows = self._conn.execute(
            "SELECT col_name, col_type, is_pk, nullable, default_val"
            " FROM node_columns"
            " WHERE table_name = ?"
            " ORDER BY position",
            (table_name,),
        ).fetchall()
        return [
            ColumnDef(
                name        = r["col_name"],
                col_type    = r["col_type"],
                is_pk       = bool(r["is_pk"]),
                nullable    = bool(r["nullable"]),
                default_val = r["default_val"],
            )
            for r in rows
        ]

    # ── Node table API ────────────────────────────────────────────────────

    def create_node_table(self,
                           name: str,
                           description: str = "") -> Dict[str, Any]:
        """
        Create a new node table definition.
        Raises ValueError if a table with that name already exists.
        """
        now = int(time.time())
        try:
            self._conn.execute(
                "INSERT INTO node_tables (name, description, created_at)"
                " VALUES (?, ?, ?)",
                (name, description, now),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"Node table '{name}' already exists")
        return {"name": name, "description": description, "created_at": now}

    def drop_node_table(self, name: str) -> bool:
        """
        Remove a node table definition.
        Returns True if deleted, False if it did not exist.
        """
        cur = self._conn.execute(
            "DELETE FROM node_tables WHERE name = ?", (name,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_node_table(self, name: str) -> Optional[Dict[str, Any]]:
        """Return the node table dict, or None if it doesn't exist."""
        row = self._conn.execute(
            "SELECT name, description, created_at"
            " FROM node_tables WHERE name = ?",
            (name,),
        ).fetchone()
        return dict(row) if row else None

    def list_node_tables(self) -> List[Dict[str, Any]]:
        """Return all node table definitions, sorted by name."""
        rows = self._conn.execute(
            "SELECT name, description, created_at"
            " FROM node_tables ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Hyperedge table API ───────────────────────────────────────────────

    def create_hyperedge_table(self,
                                name: str,
                                member_tables: List[str],
                                bucket_seconds: int = 10,
                                compact_threshold: int = 0,
                                description: str = "") -> Dict[str, Any]:
        """
        Create a new hyperedge table definition.
        member_tables: list of node table names that this hyperedge connects.
        compact_threshold: WAL entry count that triggers auto-compact (0 = disabled).
        Raises ValueError if a table with that name already exists.
        """
        if bucket_seconds <= 0:
            raise ValueError("bucket_seconds must be a positive integer")
        if compact_threshold < 0:
            raise ValueError("compact_threshold must be >= 0")
        now = int(time.time())
        try:
            self._conn.execute(
                "INSERT INTO hyperedge_tables"
                " (name, member_tables, bucket_seconds, compact_threshold,"
                "  description, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (name, json.dumps(member_tables), bucket_seconds,
                 compact_threshold, description, now),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"Hyperedge table '{name}' already exists")
        return {
            "name":              name,
            "member_tables":     member_tables,
            "bucket_seconds":    bucket_seconds,
            "compact_threshold": compact_threshold,
            "description":       description,
            "created_at":        now,
        }

    def drop_hyperedge_table(self, name: str) -> bool:
        """
        Remove a hyperedge table definition.
        Returns True if deleted, False if it did not exist.
        """
        cur = self._conn.execute(
            "DELETE FROM hyperedge_tables WHERE name = ?", (name,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def rename_hyperedge_table(self, old_name: str, new_name: str) -> None:
        """
        Rename a hyperedge table and update all related catalog entries.

        Raises ``ValueError`` if ``old_name`` does not exist or
        ``new_name`` already exists.
        """
        if not self.get_hyperedge_table(old_name):
            raise ValueError(f"Hyperedge table '{old_name}' does not exist")
        if self.get_hyperedge_table(new_name):
            raise ValueError(
                f"Cannot rename: table '{new_name}' already exists"
            )
        self._conn.execute(
            "UPDATE hyperedge_tables SET name = ? WHERE name = ?",
            (new_name, old_name),
        )
        self._conn.execute(
            "UPDATE hyperedge_columns SET table_name = ? WHERE table_name = ?",
            (new_name, old_name),
        )
        # psi_indexes may not exist in older DBs — update only if present
        try:
            self._conn.execute(
                "UPDATE psi_indexes SET table_name = ? WHERE table_name = ?",
                (new_name, old_name),
            )
        except Exception:
            pass
        self._conn.commit()

    def set_compact_threshold(self, name: str, threshold: int) -> None:
        """
        Update the autocompact threshold for an existing hyperedge table.
        threshold=0 disables autocompact.
        """
        if threshold < 0:
            raise ValueError("compact_threshold must be >= 0")
        self._conn.execute(
            "UPDATE hyperedge_tables SET compact_threshold = ? WHERE name = ?",
            (threshold, name),
        )
        self._conn.commit()

    def get_hyperedge_table(self, name: str) -> Optional[Dict[str, Any]]:
        """Return the hyperedge table dict, or None if it doesn't exist."""
        row = self._conn.execute(
            "SELECT name, member_tables, bucket_seconds, compact_threshold,"
            "       description, created_at"
            " FROM hyperedge_tables WHERE name = ?",
            (name,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["member_tables"] = json.loads(d["member_tables"])
        return d

    def list_hyperedge_tables(self) -> List[Dict[str, Any]]:
        """Return all hyperedge table definitions, sorted by name."""
        rows = self._conn.execute(
            "SELECT name, member_tables, bucket_seconds, compact_threshold,"
            "       description, created_at"
            " FROM hyperedge_tables ORDER BY name"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["member_tables"] = json.loads(d["member_tables"])
            result.append(d)
        return result

    # ── Hyperedge column API (V2 property schema) ─────────────────────────

    @staticmethod
    def parse_props_csv(props_csv: str) -> list[ColumnDef]:
        """
        Parse a raw PROPERTIES body string from the C parser AST into a
        list of :class:`ColumnDef` objects.

        Input format (as captured by parser.c):
            ``"weight FLOAT, severity INT, label TEXT, confidence FLOAT"``

        Supported type aliases (case-insensitive):
            FLOAT, DOUBLE, REAL   → "REAL"
            INT, INTEGER, BIGINT  → "INTEGER"
            TEXT, STRING, VARCHAR → "TEXT"

        Returns an empty list for an empty / whitespace-only string.
        Raises ``ValueError`` for unknown types.
        """
        _type_map = {
            "FLOAT": "REAL", "DOUBLE": "REAL", "REAL": "REAL",
            "INT": "INTEGER", "INTEGER": "INTEGER", "BIGINT": "INTEGER",
            "TEXT": "TEXT", "STRING": "TEXT", "VARCHAR": "TEXT",
        }
        raw = (props_csv or "").strip()
        if not raw:
            return []
        columns: list[ColumnDef] = []
        for token in raw.split(","):
            parts = token.strip().split()
            if len(parts) < 2:
                continue
            col_name, raw_type = parts[0], parts[1].upper()
            if raw_type not in _type_map:
                raise ValueError(
                    f"Unknown property type {raw_type!r} for column {col_name!r}. "
                    f"Supported: {list(_type_map)}"
                )
            col_type = _type_map[raw_type]
            default_val = {"REAL": "0.0", "INTEGER": "0", "TEXT": "''"}[col_type]
            columns.append(ColumnDef(
                name        = col_name,
                col_type    = col_type,
                is_pk       = False,
                nullable    = True,
                default_val = default_val,
            ))
        return columns

    def define_hyperedge_columns(
        self,
        table_name: str,
        columns:    list[ColumnDef],
    ) -> None:
        """
        Store the property column definitions for a V2 hyperedge table.
        Replaces any previously defined columns.

        ``columns`` must not contain a primary-key column (hyperedge records
        do not have user-defined PKs — event_ts + members serves as identity).

        Raises ``ValueError`` if *table_name* does not exist.
        """
        if not self.get_hyperedge_table(table_name):
            raise ValueError(
                f"Hyperedge table {table_name!r} does not exist. "
                f"Call create_hyperedge_table() first."
            )
        pks = [c for c in columns if c.is_pk]
        if pks:
            raise ValueError(
                f"Hyperedge columns must not have is_pk=True "
                f"(event_ts + members is the implicit key)."
            )
        self._conn.execute(
            "DELETE FROM hyperedge_columns WHERE table_name = ?", (table_name,)
        )
        for pos, col in enumerate(columns):
            self._conn.execute(
                "INSERT INTO hyperedge_columns"
                " (table_name, col_name, col_type, nullable, default_val, position)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (table_name, col.name, col.col_type,
                 int(col.nullable), col.default_val, pos),
            )
        self._conn.commit()

    def get_hyperedge_columns(self, table_name: str) -> list[ColumnDef]:
        """
        Return the ordered property column definitions for *table_name*.
        Returns an empty list if no columns have been defined (V1-compatible).
        """
        rows = self._conn.execute(
            "SELECT col_name, col_type, nullable, default_val"
            " FROM hyperedge_columns"
            " WHERE table_name = ?"
            " ORDER BY position",
            (table_name,),
        ).fetchall()
        return [
            ColumnDef(
                name        = r["col_name"],
                col_type    = r["col_type"],
                is_pk       = False,
                nullable    = bool(r["nullable"]),
                default_val = r["default_val"],
            )
            for r in rows
        ]

    # ── Phase 4: PSI index catalog ────────────────────────────────────────

    def create_index(self, table_name: str, col_name: str) -> bool:
        """
        Register a PSI index for (table_name, col_name).
        Returns True if created, False if it already existed.
        Raises ValueError if the column does not exist in the table schema.
        """
        import time as _time
        tbl = table_name.upper()
        col = col_name.upper()

        # Validate the column exists
        prop_cols = self.get_hyperedge_columns(tbl)
        col_names_up = {c.name.upper() for c in prop_cols}
        if col not in col_names_up:
            raise ValueError(
                f"CREATE INDEX: column '{col}' is not defined on table '{tbl}'. "
                f"Available columns: {sorted(col_names_up)}"
            )

        try:
            self._conn.execute(
                "INSERT INTO hyperedge_indexes (table_name, col_name, created_at)"
                " VALUES (?, ?, ?)",
                (tbl, col, int(_time.time())),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            return False  # already exists

    def drop_index(self, table_name: str, col_name: str) -> bool:
        """
        Remove the PSI index registration for (table_name, col_name).
        Returns True if removed, False if it did not exist.
        """
        tbl = table_name.upper()
        col = col_name.upper()
        cur = self._conn.execute(
            "DELETE FROM hyperedge_indexes WHERE table_name = ? AND col_name = ?",
            (tbl, col),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_indexes(self, table_name: str = "") -> list[dict]:
        """
        Return all registered PSI indexes, optionally filtered by table.
        Each entry is a dict with keys: table_name, col_name, created_at.
        """
        if table_name:
            rows = self._conn.execute(
                "SELECT table_name, col_name, created_at"
                " FROM hyperedge_indexes"
                " WHERE table_name = ?"
                " ORDER BY table_name, col_name",
                (table_name.upper(),),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT table_name, col_name, created_at"
                " FROM hyperedge_indexes"
                " ORDER BY table_name, col_name"
            ).fetchall()
        return [dict(r) for r in rows]

    def index_exists(self, table_name: str, col_name: str) -> bool:
        """Return True if a PSI index is registered for (table_name, col_name)."""
        row = self._conn.execute(
            "SELECT 1 FROM hyperedge_indexes WHERE table_name = ? AND col_name = ?",
            (table_name.upper(), col_name.upper()),
        ).fetchone()
        return row is not None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
