"""
_result.py — QueryResult and Row for the HyperMesh DB Python SDK.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, overload

from ._types import QueryPlan

# ── Row ───────────────────────────────────────────────────────────────────────

class Row:
    """
    A single result row, accessible by column name or integer index.

    Examples
    --------
    >>> for row in result:
    ...     print(row["event_ts"], row.members, row[2])

    >>> row.to_dict()
    {'event_ts': 100, 'members': [1, 2, 3], 'weight': 0.9, ...}
    """

    __slots__ = ("_columns", "_values", "_index")

    def __init__(self, columns: list[str], values: list[Any]) -> None:
        self._columns: list[str] = columns
        self._values:  list[Any] = values
        self._index:   dict[str, int] = {c: i for i, c in enumerate(columns)}

    # ── Access ────────────────────────────────────────────────────────────

    @overload
    def __getitem__(self, key: int) -> Any: ...
    @overload
    def __getitem__(self, key: str) -> Any: ...

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        try:
            return self._values[self._index[key]]
        except KeyError:
            raise KeyError(f"Row has no column '{key}'. "
                           f"Available: {self._columns}")

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._values[self._index[name]]
        except KeyError:
            raise AttributeError(
                f"Row has no column '{name}'. Available: {self._columns}"
            )

    def __len__(self) -> int:
        return len(self._values)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __contains__(self, key: object) -> bool:
        """Return True if *key* is a column name in this row."""
        if isinstance(key, str):
            return key in self._index
        return False

    # ── Dict-like helpers ─────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value for *key*, or *default* if the column does not exist."""
        idx = self._index.get(key)
        if idx is None:
            return default
        return self._values[idx]

    def keys(self) -> list[str]:
        """Return column names."""
        return list(self._columns)

    def values(self) -> list[Any]:
        """Return row values."""
        return list(self._values)

    def items(self) -> list[tuple[str, Any]]:
        """Return (column, value) pairs."""
        return list(zip(self._columns, self._values))

    def to_dict(self) -> dict[str, Any]:
        """Return the row as a plain dictionary."""
        return dict(zip(self._columns, self._values))

    # ── Representation ────────────────────────────────────────────────────

    def __repr__(self) -> str:
        pairs = ", ".join(
            f"{k}={v!r}" for k, v in zip(self._columns, self._values)
        )
        return f"Row({pairs})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Row):
            return self._values == other._values
        if isinstance(other, (list, tuple)):
            return self._values == list(other)
        return NotImplemented


# ── QueryResult ───────────────────────────────────────────────────────────────

class QueryResult:
    """
    The result of a ``Connection.execute()`` call.

    Iterable — yields :class:`Row` objects::

        result = db.execute("MATCH HYPEREDGE (he:CoProximity) ...")
        for row in result:
            print(row["event_ts"], row.members)

    Or use :meth:`fetchall` / :meth:`fetchone` for cursor-style access::

        first = result.fetchone()
        rest  = result.fetchall()

    Attributes
    ----------
    columns:
        List of column names.
    num_tuples:
        Total number of rows returned.
    query_plan:
        Performance metadata from the C engine.  ``None`` for DDL statements.
    """

    def __init__(
        self,
        columns:    list[str],
        rows:       list[list[Any]],
        query_plan: QueryPlan | None = None,
    ) -> None:
        self._columns:   list[str] = columns
        self._raw_rows:  list[list[Any]] = rows
        self._rows:      list[Row] = [Row(columns, r) for r in rows]
        self._plan:      QueryPlan | None = query_plan
        self._cursor:    int = 0

    # ── Iteration / fetch ─────────────────────────────────────────────────

    def __iter__(self) -> Iterator[Row]:
        return iter(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def fetchall(self) -> list[Row]:
        """
        Return all rows as a list and advance the internal cursor to the end.
        Subsequent calls to :meth:`fetchone` will return ``None``.
        """
        remaining = self._rows[self._cursor:]
        self._cursor = len(self._rows)
        return remaining

    def fetchone(self) -> Row | None:
        """
        Return the next row and advance the cursor, or ``None`` if exhausted.
        """
        if self._cursor >= len(self._rows):
            return None
        row = self._rows[self._cursor]
        self._cursor += 1
        return row

    def fetchmany(self, size: int = 100) -> list[Row]:
        """
        Return up to *size* rows starting at the current cursor and advance it.

        DB-API-style batched fetch for iterating a large result in chunks::

            while batch := result.fetchmany(500):
                handle(batch)

        Returns an empty list once the result is exhausted. *size* must be
        positive.
        """
        if size <= 0:
            raise ValueError("fetchmany(size) requires size >= 1")
        end = min(self._cursor + size, len(self._rows))
        batch = self._rows[self._cursor:end]
        self._cursor = end
        return batch

    def reset(self) -> None:
        """Reset the cursor so :meth:`fetchone` starts from the beginning."""
        self._cursor = 0

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def columns(self) -> list[str]:
        """Column names, in the same order as the row values."""
        return list(self._columns)

    @property
    def num_tuples(self) -> int:
        """Total number of rows in this result."""
        return len(self._rows)

    @property
    def query_plan(self) -> QueryPlan | None:
        """
        Performance metadata from the C engine, or ``None`` for DDL results.
        """
        return self._plan

    # ── Convenience ───────────────────────────────────────────────────────

    @property
    def rows(self) -> list[Row]:
        """All rows as a list of :class:`Row` objects (non-consuming)."""
        return list(self._rows)

    def to_dicts(self) -> list[dict[str, Any]]:
        """Return all rows as a list of plain dictionaries."""
        return [r.to_dict() for r in self._rows]

    def to_df(self):
        """
        Convert to a pandas DataFrame.
        Requires pandas to be installed (``pip install pandas``).
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "pandas is required for to_df(). "
                "Install it with: pip install pandas"
            )
        return pd.DataFrame(self._raw_rows, columns=self._columns)

    # ── Representation ────────────────────────────────────────────────────

    def __repr__(self) -> str:
        plan_str = f", plan={self._plan}" if self._plan else ""
        return (
            f"QueryResult(columns={self._columns!r}, "
            f"num_tuples={len(self._rows)}{plan_str})"
        )

    def __bool__(self) -> bool:
        return len(self._rows) > 0


# ── JSON support ──────────────────────────────────────────────────────────────

class HyperMeshEncoder(json.JSONEncoder):
    """
    A :class:`json.JSONEncoder` subclass that serialises :class:`Row` and
    :class:`QueryResult` objects to plain JSON-compatible structures.

    Usage::

        import json
        from hypermeshdb import HyperMeshEncoder

        result = db.execute("MATCH HYPEREDGE (he:T) RETURN *")

        # Serialize a full result set
        json.dumps(list(result), cls=HyperMeshEncoder)

        # Serialize a single row
        row = result.fetchone()
        json.dumps(row, cls=HyperMeshEncoder)

        # Or use the built-in helpers (no custom encoder needed)
        json.dumps(result.to_dicts())
        json.dumps(row.to_dict())
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, Row):
            return o.to_dict()
        if isinstance(o, QueryResult):
            return o.to_dicts()
        return super().default(o)
