"""
_scan.py — bounded-memory temporal scanning for HyperMesh DB.

A plain ``MATCH … RETURN *`` materialises every matching hyperedge into memory
at once. For large time ranges that can be billions of rows. ``scan_windows``
and ``scan_rows`` walk a ``[start_ts, end_ts]`` range one fixed-width time
window at a time, so peak memory is bounded by a single window rather than the
whole range. Each window is a server-side TPI bucket-pushdown query, so this is
efficient against both an embedded :class:`~hypermeshdb.Connection` and a remote
:class:`~hypermeshdb.Client`.

Usage
-----
>>> import hypermesh as hm
>>> db = hm.connect("/var/lib/hypermesh/data")
>>> for window in hm.scan_windows(db, "CoProximity", 0, 86_400, window_seconds=600):
...     process(window)              # one QueryResult per 10-minute window
>>>
>>> for row in hm.scan_rows(db, "CoProximity", 0, 86_400, window_seconds=600):
...     handle(row)                  # flat row-by-row stream
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ._result import QueryResult, Row

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class _Executable(Protocol):
    """Anything with a synchronous ``execute(cypher, parameters)`` method."""

    def execute(
        self, cypher: str, parameters: dict[str, Any] | None = ...
    ) -> QueryResult: ...


def _validate(table: str, start_ts: int, end_ts: int, window_seconds: int) -> None:
    if not _IDENT_RE.fullmatch(table):
        raise ValueError(
            f"Invalid table name {table!r}; expected a bare identifier "
            "([A-Za-z_][A-Za-z0-9_]*)."
        )
    if window_seconds <= 0:
        raise ValueError("window_seconds must be >= 1")
    if start_ts > end_ts:
        raise ValueError(f"start_ts ({start_ts}) must be <= end_ts ({end_ts})")


def scan_windows(
    db: _Executable,
    table: str,
    start_ts: int,
    end_ts: int,
    *,
    window_seconds: int,
    skip_empty: bool = True,
) -> Iterator[QueryResult]:
    """
    Yield one :class:`~hypermeshdb.QueryResult` per fixed-width time window
    covering the inclusive range ``[start_ts, end_ts]``.

    Parameters
    ----------
    db :
        An embedded ``Connection`` or remote ``Client`` (anything with
        ``execute(cypher, parameters)``).
    table :
        Hyperedge table name (validated as a bare identifier).
    start_ts, end_ts :
        Inclusive range bounds, in the table's timestamp units.
    window_seconds :
        Width of each window. Peak memory is bounded by the largest single
        window's result, not the whole range.
    skip_empty :
        When ``True`` (default), windows with no matching rows are not yielded.

    Notes
    -----
    Windows are half-open internally (``lo`` inclusive, ``lo + window`` exclusive)
    so adjacent windows never double-count an edge, while the final window is
    clamped to include ``end_ts``.
    """
    _validate(table, start_ts, end_ts, window_seconds)
    cypher = (
        f"MATCH HYPEREDGE (he:{table}) "
        "WHERE he.event_ts >= $lo AND he.event_ts <= $hi RETURN *"
    )

    lo = start_ts
    while lo <= end_ts:
        hi = min(lo + window_seconds - 1, end_ts)
        result = db.execute(cypher, {"lo": lo, "hi": hi})
        if result.num_tuples or not skip_empty:
            yield result
        lo = hi + 1


def scan_rows(
    db: _Executable,
    table: str,
    start_ts: int,
    end_ts: int,
    *,
    window_seconds: int,
) -> Iterator[Row]:
    """
    Flatten :func:`scan_windows` into a single bounded-memory stream of
    :class:`~hypermeshdb.Row` objects.

    Equivalent to iterating every window and yielding its rows, but only one
    window is held in memory at a time.
    """
    for window in scan_windows(
        db, table, start_ts, end_ts,
        window_seconds=window_seconds, skip_empty=True,
    ):
        yield from window
