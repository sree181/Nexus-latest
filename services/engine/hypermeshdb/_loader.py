"""
_loader.py — Stage-2 pipeline: raw tabular data → HyperMesh hyperedges.

This module fills the critical gap identified in the architecture audit:
HyperMesh DB requires data to be pre-shaped as (event_ts, members, ...)
hyperedge rows.  Most real-world data arrives as raw tables (logs, attendance
records, transactions, sensor readings) where hyperedges must be *constructed*
by grouping rows that share a common attribute.

The :class:`Loader` class encapsulates those grouping rules and automates the
transformation.

Design principles
-----------------
- **Zero domain assumptions** — no drone-swarm specifics; works for any
  tabular domain.
- **Pandas-native** — the primary API; NumPy arrays and polars DataFrames
  can be pre-converted with `.to_pandas()` before passing in.
- **Deterministic encoding** — member string values are mapped to integers
  in sorted order so the same raw value always gets the same ID across
  different Loader instances on the same dataset.
- **Composable** — :meth:`construct` returns a plain pandas DataFrame so
  users can inspect, filter, or further transform the result before loading.
- **Integrated** — :meth:`load_into` calls ``Connection.copy_from_df``
  directly, reusing all error-handling and WAL logic built in Phase 2.

Example
-------
>>> import pandas as pd
>>> import hypermeshdb
>>>
>>> # Raw network log: rows are (src_machine, shared_ip, timestamp, action)
>>> raw = pd.DataFrame({
...     "timestamp":  ["2026-01-01 12:00", "2026-01-01 12:01",
...                    "2026-01-01 12:00", "2026-01-02 09:00"],
...     "machine":    ["PC1", "PC2", "PC1", "PC3"],
...     "ip_address": ["1.2.3.4", "1.2.3.4", "5.6.7.8", "5.6.7.8"],
... })
>>>
>>> loader = hypermeshdb.Loader(
...     raw,
...     group_by      = "ip_address",   # rows sharing an IP → one hyperedge
...     member_col    = "machine",       # unique machines become member IDs
...     timestamp_col = "timestamp",     # aggregated to event_ts (Unix seconds)
... )
>>>
>>> he_df = loader.construct()           # inspect before loading
>>> print(he_df)
>>> print(loader.node_map)              # {"PC1": 0, "PC2": 1, "PC3": 2}
>>>
>>> db = hypermeshdb.connect("/tmp/mydb", hyperedges_csv="seed.csv")
>>> result = loader.load_into(db, "CoProximity")
>>> print(result.fetchone()["rows_loaded"])  # 2
"""

from __future__ import annotations

import math
from typing import Any

from ._types import HyperMeshError

# Supported aggregation names
_TS_AGGS    = {"min", "max", "first", "last"}
_NUM_AGGS   = {"mean", "min", "max", "sum", "median"}
_STR_AGGS   = {"first", "last", "mode"}


def _require_pandas() -> Any:
    try:
        import pandas as pd
        return pd
    except ImportError:
        raise HyperMeshError(
            "Loader requires pandas. Run: pip install pandas"
        )


def _datetime_to_unix_seconds(dt_series: Any) -> Any:
    """
    Convert a pandas datetime Series (any resolution) to integer Unix seconds.

    pandas 3.x changed the default datetime64 resolution from ``[ns]`` to
    ``[us]``.  This function detects the resolution from the dtype string and
    divides accordingly, so it works on pandas 1.x – 3.x.
    """
    import re
    dtype_str = str(dt_series.dtype)
    # Extract unit: datetime64[ns], datetime64[us, UTC], datetime64[ms], etc.
    m = re.search(r"\[(\w+)", dtype_str)
    unit = m.group(1) if m else "us"
    divisors = {"ns": 10 ** 9, "us": 10 ** 6, "ms": 10 ** 3, "s": 1}
    divisor = divisors.get(unit, 10 ** 6)
    return (dt_series.astype("int64") // divisor).fillna(0).astype("int64")


def _to_unix_seconds(series: Any) -> Any:
    """
    Coerce a pandas Series to integer Unix timestamps (seconds since epoch).

    Accepts:
    - Integer / float series already in Unix seconds
    - String / datetime Series (parsed with pd.to_datetime)
    - pandas Timestamp values
    - numpy datetime64 values

    NaT / NaN values are coerced to 0 (callers should replace with default_ts).
    """
    pd = _require_pandas()
    if pd.api.types.is_integer_dtype(series):
        return series.fillna(0).astype("int64")
    if pd.api.types.is_float_dtype(series):
        return series.fillna(0).astype("int64")
    # datetime-like or string → parse to datetime64, then convert to seconds
    try:
        dt = pd.to_datetime(series, utc=True, errors="coerce")
    except Exception:
        dt = pd.to_datetime(series, errors="coerce")
    return _datetime_to_unix_seconds(dt)


def _mode_or_first(series: Any) -> Any:
    """Return the most frequent value in a Series, or the first if all equal."""
    m = series.mode()
    if len(m) == 0:
        return ""
    return m.iloc[0]


class Loader:
    """
    Transform raw tabular data into HyperMesh hyperedge rows.

    Parameters
    ----------
    df:
        Source data as a ``pandas.DataFrame``.
    group_by:
        Column name (or list of names) whose unique value combination
        defines one hyperedge.  All rows sharing the same group-by value
        are collapsed into a single hyperedge whose ``members`` are the
        distinct values found in ``member_col``.

        Examples:

        - ``group_by="ip_address"`` — rows that share an IP form a
          co-presence hyperedge.
        - ``group_by=["date", "room"]`` — rows that share the same date
          *and* room form an attendance hyperedge.

    member_col:
        Column whose per-row values become integer member IDs in the
        hyperedge.  String/object values are encoded to integers via a
        sorted deterministic mapping (see :attr:`node_map`).  Duplicate
        values within the same group are deduplicated (each member appears
        at most once per hyperedge).

    timestamp_col:
        Optional column to derive ``event_ts`` (Unix seconds).  The values
        in each group are aggregated according to ``timestamp_agg``.
        When ``None``, ``default_ts`` is used for all hyperedges.

    timestamp_agg:
        How to aggregate timestamps within a group.
        One of ``"min"`` (earliest event), ``"max"`` (latest),
        ``"first"`` (row order), ``"last"`` (row order).
        Default: ``"min"``.

    weight_col:
        Optional numeric column to aggregate into ``weight``.
        Default aggregation: ``"mean"``.

    weight_agg:
        Aggregation for ``weight_col``.
        One of ``"mean"``, ``"min"``, ``"max"``, ``"sum"``, ``"median"``.
        Default: ``"mean"``.

    formation_col:
        Optional string column to aggregate into ``formation``.
        Default aggregation: most frequent value (``"mode"``).

    formation_agg:
        Aggregation for ``formation_col``.
        One of ``"first"``, ``"last"``, ``"mode"``.
        Default: ``"mode"``.

    default_ts:
        ``event_ts`` used for all hyperedges when ``timestamp_col`` is
        ``None`` or all values are null.  Default: ``0``.

    min_members:
        Minimum number of distinct members required to form a hyperedge.
        Groups with fewer members are dropped.  Default: ``2``.

    Attributes
    ----------
    node_map:
        ``dict`` mapping each original member value to its assigned
        integer ID.  Available after the first call to :meth:`construct`
        or :meth:`load_into`.

    Examples
    --------
    >>> loader = Loader(
    ...     raw_logs,
    ...     group_by="shared_ip",
    ...     member_col="machine_name",
    ...     timestamp_col="event_time",
    ...     weight_col="severity",
    ...     weight_agg="max",
    ... )
    >>> he_df = loader.construct()
    >>> print(loader.node_map)
    >>> loader.load_into(db, "CoProximity")
    """

    def __init__(
        self,
        df:              Any,
        *,
        group_by:        str | list[str],
        member_col:      str,
        timestamp_col:   str | None = None,
        timestamp_agg:   str        = "min",
        weight_col:      str | None = None,
        weight_agg:      str        = "mean",
        formation_col:   str | None = None,
        formation_agg:   str        = "mode",
        default_ts:      int        = 0,
        min_members:     int        = 2,
    ) -> None:
        pd = _require_pandas()

        # ── Validate df ───────────────────────────────────────────────────
        if not hasattr(df, "columns"):
            raise HyperMeshError(
                f"Loader: expected a pandas.DataFrame, got {type(df).__name__!r}"
            )
        self._df = df.copy()

        # ── Validate group_by ─────────────────────────────────────────────
        self._group_by: list[str] = (
            [group_by] if isinstance(group_by, str) else list(group_by)
        )
        for col in self._group_by:
            if col not in self._df.columns:
                raise HyperMeshError(
                    f"Loader: group_by column {col!r} not found in DataFrame. "
                    f"Available columns: {list(self._df.columns)!r}"
                )

        # ── Validate member_col ───────────────────────────────────────────
        if member_col not in self._df.columns:
            raise HyperMeshError(
                f"Loader: member_col {member_col!r} not found in DataFrame. "
                f"Available columns: {list(self._df.columns)!r}"
            )
        self._member_col = member_col

        # ── Validate optional columns ─────────────────────────────────────
        def _check_col(name: str | None, label: str) -> None:
            if name is not None and name not in self._df.columns:
                raise HyperMeshError(
                    f"Loader: {label} column {name!r} not found. "
                    f"Available columns: {list(self._df.columns)!r}"
                )

        _check_col(timestamp_col, "timestamp_col")
        _check_col(weight_col, "weight_col")
        _check_col(formation_col, "formation_col")

        self._timestamp_col  = timestamp_col
        self._timestamp_agg  = timestamp_agg.lower()
        self._weight_col     = weight_col
        self._weight_agg     = weight_agg.lower()
        self._formation_col  = formation_col
        self._formation_agg  = formation_agg.lower()
        self._default_ts     = int(default_ts)
        self._min_members    = max(1, int(min_members))

        # ── Validate aggregation names ────────────────────────────────────
        if self._timestamp_agg not in _TS_AGGS:
            raise HyperMeshError(
                f"Loader: invalid timestamp_agg {timestamp_agg!r}. "
                f"Supported: {sorted(_TS_AGGS)!r}"
            )
        if self._weight_agg not in _NUM_AGGS:
            raise HyperMeshError(
                f"Loader: invalid weight_agg {weight_agg!r}. "
                f"Supported: {sorted(_NUM_AGGS)!r}"
            )
        if self._formation_agg not in _STR_AGGS:
            raise HyperMeshError(
                f"Loader: invalid formation_agg {formation_agg!r}. "
                f"Supported: {sorted(_STR_AGGS)!r}"
            )

        # ── Internal state ────────────────────────────────────────────────
        self._node_map: dict[Any, int] = {}  # built during construct()

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def node_map(self) -> dict[Any, int]:
        """
        Mapping from original member values → assigned integer IDs.

        Available after the first call to :meth:`construct` or
        :meth:`load_into`.  The mapping is stable across calls (adding more
        data does NOT re-number existing nodes).
        """
        return dict(self._node_map)

    # ── Core transformation ───────────────────────────────────────────────

    def construct(self) -> Any:
        """
        Apply grouping rules and return a hyperedge-shaped ``pandas.DataFrame``.

        Columns in the returned DataFrame:

        - ``event_ts``    — integer Unix seconds
        - ``members``     — Python ``list[int]`` (sorted member IDs)
        - ``member_count``— number of distinct members
        - ``weight``      — float (``0.0`` if no ``weight_col``)
        - ``mean_dist_m`` — ``0.0`` (not derived from tabular data)
        - ``formation``   — string (``""`` if no ``formation_col``)

        Groups with fewer than ``min_members`` distinct members are dropped.

        Returns
        -------
        pandas.DataFrame
            Ready for ``Connection.copy_from_df()`` or further inspection.
        """
        pd = _require_pandas()

        if self._df.empty:
            return pd.DataFrame(columns=[
                "event_ts", "members", "member_count",
                "weight", "mean_dist_m", "formation",
            ])

        # ── Step 1: build / extend node_map from member column ────────────
        unique_vals = sorted(
            set(str(v) for v in self._df[self._member_col].dropna().unique())
        )
        for val in unique_vals:
            if val not in self._node_map:
                self._node_map[val] = len(self._node_map)

        # ── Step 2: encode member column → integer IDs ────────────────────
        work = self._df.copy()
        work["__member_id__"] = (
            work[self._member_col]
            .astype(str)
            .map(self._node_map)
        )

        # ── Step 3: convert timestamp column ─────────────────────────────
        if self._timestamp_col is not None:
            raw_ts  = _to_unix_seconds(work[self._timestamp_col])
            was_null = work[self._timestamp_col].isna()
            # Replace zeros that originated from NaT/NaN with default_ts
            work["__ts__"] = raw_ts.where(~was_null, self._default_ts)
        else:
            work["__ts__"] = self._default_ts

        # ── Step 4: group and aggregate ───────────────────────────────────
        grp = work.groupby(self._group_by, sort=False)

        # members — sorted list of unique integer IDs per group
        members_s = grp["__member_id__"].apply(
            lambda ids: sorted(set(int(x) for x in ids.dropna()))
        )

        # timestamp
        if self._timestamp_agg in ("first", "last"):
            ts_s = grp["__ts__"].agg(self._timestamp_agg)
        else:
            ts_s = grp["__ts__"].agg(self._timestamp_agg)
        ts_s = ts_s.fillna(self._default_ts).astype("int64")

        # weight
        if self._weight_col is not None:
            w_s = grp[self._weight_col].agg(self._weight_agg).fillna(0.0)
        else:
            w_s = ts_s * 0.0  # same index, all zeros

        # formation
        if self._formation_col is not None:
            if self._formation_agg == "mode":
                fm_s = grp[self._formation_col].agg(_mode_or_first)
            else:
                fm_s = grp[self._formation_col].agg(self._formation_agg)
            fm_s = fm_s.fillna("").astype(str)
        else:
            fm_s = ts_s.apply(lambda _: "")

        # ── Step 5: assemble output DataFrame ─────────────────────────────
        result = pd.DataFrame({
            "event_ts":    ts_s,
            "members":     members_s,
            "member_count": members_s.apply(len),
            "weight":      w_s.astype(float),
            "mean_dist_m": 0.0,
            "formation":   fm_s,
        }).reset_index(drop=True)

        # ── Step 6: filter by min_members ─────────────────────────────────
        result = result[result["member_count"] >= self._min_members].copy()
        result = result.reset_index(drop=True)

        return result

    # ── Integration ───────────────────────────────────────────────────────

    def load_into(
        self,
        conn:          Any,
        table:         str | None = None,
        *,
        ignore_errors: bool = False,
    ) -> Any:
        """
        :meth:`construct` the hyperedge DataFrame and bulk-load it into
        *conn* via :meth:`~hypermeshdb.Connection.copy_from_df`.

        Parameters
        ----------
        conn:
            An open :class:`~hypermeshdb.Connection`.
        table:
            Target hyperedge table name.  Defaults to the primary table
            (the first table created in the database, same as
            :meth:`~hypermeshdb.Connection.insert`).
        ignore_errors:
            Passed through to ``copy_from_df``; skips malformed rows.

        Returns
        -------
        QueryResult
            Same result object returned by ``copy_from_df``.
        """
        he_df = self.construct()
        target = table or getattr(conn, "_primary_table", "CoProximity")
        return conn.copy_from_df(he_df, target, ignore_errors=ignore_errors)

    # ── Convenience ───────────────────────────────────────────────────────

    def node_frame(
        self,
        id_col:    str = "drone_id",
        label_col: str = "callsign",
    ) -> Any:
        """
        Return a ``pandas.DataFrame`` of the encoded nodes, suitable for
        loading into a node table via :meth:`~hypermeshdb.Connection.copy_from_df`.

        Parameters
        ----------
        id_col:
            Name to give the integer-ID column.
            Default: ``"drone_id"`` (matches the built-in node schema).
            Override for other domains, e.g. ``"machine_id"``, ``"person_id"``.
        label_col:
            Name to give the original-value column.
            Default: ``"callsign"``.
            Override for other domains, e.g. ``"hostname"``, ``"username"``.

        Examples
        --------
        For drone-swarm data (default)::

            loader.load_into(db, "CoProximity")
            db.copy_from_df(loader.node_frame(), "Drone")

        For CISO machine data::

            loader.load_into(db, "CoProximity")
            db.copy_from_df(
                loader.node_frame(id_col="drone_id", label_col="callsign"),
                "Drone",
            )
        """
        if not self._node_map:
            self.construct()

        pd = _require_pandas()
        rows = [
            {id_col: int_id, label_col: str(orig_val)}
            for orig_val, int_id in sorted(
                self._node_map.items(), key=lambda kv: kv[1]
            )
        ]
        return pd.DataFrame(rows)

    # ── Representation ────────────────────────────────────────────────────

    def __repr__(self) -> str:
        gb = (
            self._group_by[0]
            if len(self._group_by) == 1
            else self._group_by
        )
        return (
            f"Loader(group_by={gb!r}, member_col={self._member_col!r}, "
            f"rows={len(self._df)}, nodes={len(self._node_map)})"
        )
