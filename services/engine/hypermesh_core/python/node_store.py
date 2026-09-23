"""
node_store.py — Schema-driven SQLite node-property store for HyperMesh DB.

Every node table has its own ``NodeStore`` instance.  The SQLite table DDL
is generated at runtime from a list of :class:`~schema_store.ColumnDef`
objects, so any node table schema can be represented — not just the
original drone-swarm columns.

The ``nodes.db`` file is still used for the built-in ``Drone`` table so
that existing databases are opened without migration.  Each additional
node table gets its own ``nodes_{TableName}.db`` file.

Design principles
─────────────────
1. Schema-driven DDL: no hardcoded column names or types.
2. Generic ``_row_to_frontend``: the frontend dict is built from column
   definitions; ``id`` is the PK value; ``label`` is the first non-PK TEXT
   column (configurable via the ``label_col`` kwarg on :meth:`build_from_csv`).
3. Idempotent init: ``build_from_csv`` skips import when the table is already
   populated.
4. Zero extra dependencies: sqlite3 is in the standard library.
5. Thread-safe reads: WAL journal mode + ``check_same_thread=False``.
6. Backward-compatible: the public API (``get_all``, ``get_by_id``, ``upsert``,
   ``count``, ``close``) is unchanged; callers do not need to know the
   underlying column names.
"""

from __future__ import annotations

import csv as _csv_mod
import sqlite3
from typing import Any, Optional

from .schema_store import ColumnDef


class NodeStore:
    """
    SQLite-backed durable store for a single node table.

    Parameters
    ----------
    db_path:
        Path to the SQLite file (created if it does not exist).
    table_name:
        Name of the SQLite table to create/use within the file.
        Also used in diagnostic messages.
    columns:
        Ordered list of :class:`~schema_store.ColumnDef` objects that
        define the table schema.  Exactly one column must have
        ``is_pk=True``.

    Raises
    ------
    ValueError
        If ``columns`` is empty, or if exactly one PK column is not present.
    """

    def __init__(
        self,
        db_path:    str,
        table_name: str       = "nodes",
        columns:    list[ColumnDef] | None = None,
    ) -> None:
        if columns is None:
            # Backward-compat shim: import the Drone defaults so that
            # NodeStore(db_path) still works without explicit column defs.
            from .schema_store import DRONE_COLUMNS
            columns = DRONE_COLUMNS
        if not columns:
            raise ValueError("NodeStore: columns list must not be empty")
        pks = [c for c in columns if c.is_pk]
        if len(pks) != 1:
            raise ValueError(
                f"NodeStore: exactly one column must be the primary key, "
                f"got {len(pks)} for table {table_name!r}"
            )

        self._db_path    = db_path
        self._table_name = table_name
        self._columns    = columns
        self._pk_col     = pks[0]
        # First non-PK TEXT column is used as the human-readable label
        self._label_col  = next(
            (c for c in columns if not c.is_pk and c.col_type == "TEXT"),
            self._pk_col,
        )
        self._col_names  = [c.name for c in columns]

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_table()

    # ── DDL ───────────────────────────────────────────────────────────────

    def _create_table(self) -> None:
        col_defs = ",\n    ".join(c.ddl_fragment() for c in self._columns)
        ddl = (
            f"CREATE TABLE IF NOT EXISTS {self._table_name} (\n"
            f"    {col_defs}\n"
            f");\n"
            f"CREATE INDEX IF NOT EXISTS idx_{self._table_name}_{self._pk_col.name} "
            f"ON {self._table_name} ({self._pk_col.name});"
        )
        self._conn.executescript(ddl)
        self._conn.commit()

    # ── Factory ───────────────────────────────────────────────────────────

    @classmethod
    def build_from_csv(
        cls,
        db_path:    str,
        table_name: str,
        columns:    list[ColumnDef],
        csv_path:   str,
    ) -> "NodeStore":
        """
        Open (or create) the node store at *db_path*.
        Import rows from *csv_path* if and only if the table is empty.
        Idempotent: returns the same store on every subsequent call.
        """
        store = cls(db_path, table_name, columns)
        if store.count() == 0 and csv_path:
            store._import_csv(csv_path)
            print(f"[NodeStore:{table_name}] Imported {store.count()} rows  →  {db_path}")
        else:
            print(f"[NodeStore:{table_name}] Opened {store.count()} rows from {db_path}")
        return store

    # ── CSV import (private) ─────────────────────────────────────────────

    def _import_csv(self, csv_path: str) -> None:
        with open(csv_path, newline="") as f:
            reader = _csv_mod.DictReader(f)
            rows = list(reader)

        params = []
        for r in rows:
            row_lower = {k.strip().lower(): v for k, v in r.items()}
            params.append(
                tuple(col.coerce(row_lower.get(col.name)) for col in self._columns)
            )

        placeholders = ", ".join("?" * len(self._columns))
        col_list     = ", ".join(self._col_names)
        with self._conn:
            self._conn.executemany(
                f"INSERT OR REPLACE INTO {self._table_name} ({col_list}) VALUES ({placeholders})",
                params,
            )

    # ── Row → frontend dict ───────────────────────────────────────────────

    def _row_to_frontend(self, row: sqlite3.Row) -> dict:
        """
        Convert a SQLite Row to a generic frontend-ready node dict.

        The output shape is domain-agnostic:
        - ``id``         — string representation of the primary-key value
        - ``label``      — value of the first non-PK TEXT column
        - ``tableType``  — empty string (set by callers if needed)
        - ``properties`` — dict of all column values keyed by column name
        """
        pk_val  = row[self._pk_col.name]
        lbl_val = row[self._label_col.name] if self._label_col != self._pk_col else str(pk_val)
        props   = {c.name: row[c.name] for c in self._columns}
        return {
            "id":        str(pk_val),
            "label":     str(lbl_val) if lbl_val is not None else "",
            "tableType": "",
            "properties": props,
        }

    # ── Reads ─────────────────────────────────────────────────────────────

    def count(self) -> int:
        return int(
            self._conn.execute(
                f"SELECT COUNT(*) FROM {self._table_name}"
            ).fetchone()[0]
        )

    def get_all(self) -> list[dict]:
        cur = self._conn.execute(
            f"SELECT * FROM {self._table_name} ORDER BY {self._pk_col.name}"
        )
        return [self._row_to_frontend(r) for r in cur.fetchall()]

    def get_by_id(self, node_id: int) -> Optional[dict]:
        cur = self._conn.execute(
            f"SELECT * FROM {self._table_name} WHERE {self._pk_col.name} = ?",
            (int(node_id),),
        )
        row = cur.fetchone()
        return self._row_to_frontend(row) if row else None

    # ── Writes ────────────────────────────────────────────────────────────

    def upsert(self, node: dict[str, Any]) -> None:
        """
        Insert or replace a node record.
        *node* must contain at least the primary key column.
        Missing optional columns are coerced to their default values.
        """
        values = tuple(col.coerce(node.get(col.name)) for col in self._columns)
        placeholders = ", ".join("?" * len(self._columns))
        col_list     = ", ".join(self._col_names)
        with self._conn:
            self._conn.execute(
                f"INSERT OR REPLACE INTO {self._table_name} ({col_list}) VALUES ({placeholders})",
                values,
            )

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        self._conn.close()

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"NodeStore(table={self._table_name!r}, "
            f"db={self._db_path!r}, rows={self.count()})"
        )
