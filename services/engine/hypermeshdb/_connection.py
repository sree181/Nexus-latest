"""
_connection.py — Connection class for the HyperMesh DB Python SDK.

A Connection wraps:
  - HmStore     (TPI + FMI C engine via ctypes)
  - SchemaStore (SQLite schema catalog)
  - NodeStore   (SQLite node property store, optional)

All public methods are typed and documented.
"""

from __future__ import annotations

import csv as _csv_mod
import io
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from hypermesh_core.python.hm_store     import (
    HmStore, HmQueryKind, HM_FIELD_SEP,
    HM_PSI_FLOAT, HM_PSI_INT,
    HM_PSI_EQ, HM_PSI_GT, HM_PSI_GEQ, HM_PSI_LT, HM_PSI_LEQ,
    _PRED_OP_TO_PSI,
)
from hypermesh_core.python.schema_store import SchemaStore, ColumnDef
from hypermesh_core.python.node_store   import NodeStore

from ._types  import HyperMeshError, QueryPlan
from ._result import QueryResult
from ._where  import WhereParseError, extract_bool_where

# ── COPY FROM statement parser ────────────────────────────────────────────────
#
# Accepted forms (case-insensitive):
#   COPY <Table> FROM '<path>'
#   COPY <Table> FROM '<path>' (HEADER=true, DELIM='|', SKIP=2, IGNORE_ERRORS)
#
_COPY_RE = re.compile(
    r"""^\s*COPY\s+(?P<table>\w+)\s+FROM\s+
        (?P<quote>['"])(?P<path>.+?)(?P=quote)
        (?:\s*\((?P<opts>[^)]*)\))?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# CREATE NODE TABLE Machine (machine_id INTEGER PRIMARY KEY, hostname TEXT, os TEXT DEFAULT '')
_CREATE_NODE_TABLE_RE = re.compile(
    r"""^\s*CREATE\s+NODE\s+TABLE\s+(?P<name>\w+)\s*\((?P<cols>[^)]+)\)\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# Individual column definition: col_name TYPE [PRIMARY KEY] [NOT NULL] [DEFAULT val]
_COL_DEF_RE = re.compile(
    r"""(?P<col_name>\w+)\s+(?P<col_type>INTEGER|REAL|TEXT)
        (?:\s+(?P<is_pk>PRIMARY\s+KEY))?
        (?:\s+(?P<not_null>NOT\s+NULL))?
        (?:\s+DEFAULT\s+(?P<default_val>\S+))?""",
    re.IGNORECASE | re.VERBOSE,
)

# ── ALTER TABLE statement parser ──────────────────────────────────────────────
#
# Accepted forms (case-insensitive):
#   ALTER TABLE <name> ADD COLUMN <col_name> <TYPE> [DEFAULT <val>]
#   ALTER TABLE <name> DROP COLUMN <col_name>
#   ALTER TABLE <name> RENAME TO <new_name>
#
_ALTER_TABLE_ADD_RE = re.compile(
    r"""^\s*ALTER\s+TABLE\s+(?P<table>\w+)\s+ADD\s+COLUMN\s+
        (?P<col>\w+)\s+(?P<type>INTEGER|REAL|TEXT|FLOAT)
        (?:\s+DEFAULT\s+(?P<default>\S+))?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_ALTER_TABLE_DROP_RE = re.compile(
    r"""^\s*ALTER\s+TABLE\s+(?P<table>\w+)\s+DROP\s+COLUMN\s+(?P<col>\w+)\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_ALTER_TABLE_RENAME_RE = re.compile(
    r"""^\s*ALTER\s+TABLE\s+(?P<table>\w+)\s+RENAME\s+TO\s+(?P<new_name>\w+)\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# ── EXPLAIN / ANALYZE prefix ──────────────────────────────────────────────────
_EXPLAIN_RE = re.compile(r"^\s*EXPLAIN\s+", re.IGNORECASE)
_ANALYZE_RE = re.compile(r"^\s*ANALYZE\s+", re.IGNORECASE)

# ── SKIP / OFFSET pagination ──────────────────────────────────────────────────
# The C parser stops RETURN-column parsing at the SKIP keyword but does not store
# a skip value, and ``ORDER BY ... SKIP n LIMIT m`` would make it miss LIMIT
# entirely (it only looks for LIMIT immediately after ORDER BY). So SKIP/OFFSET
# is handled Python-side: extracted + stripped from the query before the C parser
# runs, then applied between ORDER BY and LIMIT (Cypher semantics). OFFSET is
# accepted as a synonym for SKIP.
#
# The clause is *tail-anchored*: SKIP/OFFSET must be the trailing pagination
# clause, followed only by an optional LIMIT clause (in either order) and an
# optional ';'. This both mirrors how LIMIT works without RETURN and guarantees
# we never strip "SKIP 5" that appears inside a WHERE string literal or as a COPY
# ``SKIP=2`` option (which uses '=' and is intercepted earlier anyway).
_SKIP_TAIL_RE = re.compile(
    r"\b(?:SKIP|OFFSET)\s+(\d+)\b(?P<rest>\s*(?:\bLIMIT\s+\d+\b\s*)?;?\s*)$",
    re.IGNORECASE,
)


def _extract_skip(query: str) -> tuple[str, int]:
    """
    Strip a trailing ``SKIP <int>`` / ``OFFSET <int>`` clause from *query* and
    return ``(cleaned_query, skip_n)``.

    Any trailing ``LIMIT`` clause is preserved (the C parser still handles it).
    Returns ``skip_n == 0`` and the query unchanged when no clause is present.
    """
    m = _SKIP_TAIL_RE.search(query)
    if not m:
        return query, 0
    skip_n = int(m.group(1))
    cleaned = (query[:m.start()] + m.group("rest")).strip()
    return cleaned, skip_n

# ── Transaction control statements ───────────────────────────────────────────
_BEGIN_RE    = re.compile(r"^\s*BEGIN(?:\s+TRANSACTION)?\s*;?\s*$",    re.IGNORECASE)
_COMMIT_RE   = re.compile(r"^\s*COMMIT(?:\s+TRANSACTION)?\s*;?\s*$",   re.IGNORECASE)
_ROLLBACK_RE = re.compile(r"^\s*ROLLBACK(?:\s+TRANSACTION)?\s*;?\s*$", re.IGNORECASE)

# ── UPDATE WHERE normalisation ────────────────────────────────────────────────
# The C parser only supports >=/>/<=/< for event_ts; rewrite equality to range.
_UPDATE_DETECT_RE  = re.compile(r"^\s*UPDATE\b",            re.IGNORECASE)
_UPDATE_EVTS_EQ_RE = re.compile(                           # event_ts = <int>
    r"(?<![.\w])(?:\w+\.)?event_ts\s*=\s*(\d+)", re.IGNORECASE
)
_UPDATE_MEMBERS_RE = re.compile(                           # members = [a, b, ...]
    r"(?<![.\w])(?:\w+\.)?members\s*=\s*\[([^\]]*)\]", re.IGNORECASE
)


def _normalise_update_query(query: str) -> "tuple[str, dict]":
    """
    Rewrite an UPDATE ... WHERE ... query so that the C parser can handle it.

    - ``event_ts = X`` → ``he.event_ts >= X AND he.event_ts <= X``
      (C parser requires range operators, not equality, for temporal index use.)
    - ``members = [a, b]`` is extracted as a Python-side predicate (the C
      predicate array never captures array-valued member equality).

    Returns (rewritten_query, {'members': [a, b]} | {}).
    """
    extracted: dict = {}

    m_mem = _UPDATE_MEMBERS_RE.search(query)
    if m_mem:
        raw = m_mem.group(1)
        extracted["members"] = [
            int(x.strip()) for x in raw.split(",") if x.strip()
        ]

    def _ts_eq_to_range(m: "re.Match") -> str:
        val = m.group(1)
        return f"he.event_ts >= {val} AND he.event_ts <= {val}"

    rewritten = _UPDATE_EVTS_EQ_RE.sub(_ts_eq_to_range, query)
    return rewritten, extracted


@dataclass
class CopyOptions:
    """Parsed options from the trailing `(...)` of a COPY FROM statement."""
    header:        bool | None = None   # None → auto-detect
    delimiter:     str         = ","
    quotechar:     str         = '"'
    skip:          int         = 0
    ignore_errors: bool        = False
    key:           str | None  = None   # JSON: extract records from this top-level key


@dataclass
class _TxEntry:
    """A single buffered write operation inside an open transaction."""
    op:          str              # "insert" | "delete"
    table:       str | None
    event_ts:    int
    members:     list[int]
    weight:      float            = 0.0
    mean_dist_m: float            = 0.0
    formation:   str              = ""


def _tokenize_opts(raw: str) -> list[str]:
    """
    Split an options string on commas while respecting quoted values.

    ``HEADER=true, DELIM=','`` → ``["HEADER=true", "DELIM=','"]``
    """
    tokens: list[str] = []
    current: list[str] = []
    in_quote: str | None = None
    for ch in raw:
        if in_quote is None and ch in ("'", '"'):
            in_quote = ch
            current.append(ch)
        elif ch == in_quote:
            in_quote = None
            current.append(ch)
        elif ch == "," and in_quote is None:
            tokens.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        tokens.append("".join(current).strip())
    return [t for t in tokens if t]


def _parse_copy_options(raw: str | None) -> CopyOptions:
    """
    Parse a raw options string like ``HEADER=true, DELIM='|', SKIP=2``
    into a :class:`CopyOptions` instance.

    Supported keys (case-insensitive):
      HEADER / HEADER=true|false|1|0
      DELIM / DELIMITER = '<char>'
      QUOTE = '<char>'
      SKIP  = <int>
      IGNORE_ERRORS / IGNORE_ERRORS=true|false

    Raises :class:`HyperMeshError` on unrecognised keys or malformed values.
    """
    opts = CopyOptions()
    if not raw or not raw.strip():
        return opts

    for token in _tokenize_opts(raw):
        if not token:
            continue

        # Split on the first '=' only; value is optional (bare flag → true)
        if "=" in token:
            key, _, val_raw = token.partition("=")
            key = key.strip().upper()
            val_raw = val_raw.strip()
            # Strip a matching pair of outer quotes (handles ',' and "," correctly)
            if (len(val_raw) >= 2
                    and val_raw[0] in ("'", '"')
                    and val_raw[-1] == val_raw[0]):
                val = val_raw[1:-1]
            else:
                val = val_raw
        else:
            key = token.strip().upper()
            val = "true"

        if key == "HEADER":
            opts.header = val.lower() not in ("false", "0", "no")
        elif key in ("DELIM", "DELIMITER"):
            if len(val) != 1:
                raise HyperMeshError(
                    f"COPY option {key} must be a single character, got: {val!r}"
                )
            opts.delimiter = val
        elif key == "QUOTE":
            if len(val) != 1:
                raise HyperMeshError(
                    f"COPY option QUOTE must be a single character, got: {val!r}"
                )
            opts.quotechar = val
        elif key == "SKIP":
            try:
                opts.skip = int(val)
            except ValueError:
                raise HyperMeshError(
                    f"COPY option SKIP must be an integer, got: {val!r}"
                )
        elif key == "IGNORE_ERRORS":
            opts.ignore_errors = val.lower() not in ("false", "0", "no")
        elif key == "KEY":
            if not val:
                raise HyperMeshError("COPY option KEY must be a non-empty string")
            opts.key = val
        else:
            raise HyperMeshError(
                f"Unknown COPY option: {key!r}. "
                f"Supported: HEADER, DELIM, DELIMITER, QUOTE, SKIP, IGNORE_ERRORS, KEY"
            )

    return opts


# Known hyperedge CSV columns (required + optional)
_HE_REQUIRED_COLS = {"event_ts", "members"}
_HE_OPTIONAL_COLS = {"member_count", "weight", "mean_dist_m", "formation"}
_HE_ALL_COLS      = _HE_REQUIRED_COLS | _HE_OPTIONAL_COLS

# Known node CSV columns — kept for backward compat heuristic in _is_node_table.
# _load_node_records now derives required/optional columns from the SchemaStore.
_NODE_REQUIRED_COLS = {"drone_id"}
_NODE_OPTIONAL_COLS = {
    "callsign", "role", "formation", "mission",
    "avg_battery", "avg_signal", "collision_events",
}
_NODE_ALL_COLS = _NODE_REQUIRED_COLS | _NODE_OPTIONAL_COLS


def _parse_members(raw: str) -> list[int]:
    """
    Parse a members value from a CSV cell.

    Accepted formats:
      "[1, 2, 3]"   — JSON-style array string (standard HyperMesh format)
      "1,2,3"       — bare comma-separated integers

    Raises ValueError on malformed input.
    """
    raw = raw.strip()
    if raw.startswith("["):
        parsed = json.loads(raw)
        return [int(x) for x in parsed]
    # bare comma-separated
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_members_native(v: Any) -> list[int]:
    """
    Coerce a DataFrame cell value into ``list[int]`` members.

    Handled input types:
    - ``list`` / ``tuple``          → each element cast to int
    - ``str``                       → delegated to :func:`_parse_members`
    - array-like (numpy ndarray)    → iterated and cast to int
    - ``None`` or NaN               → raises ``ValueError``

    Raises ``ValueError`` on null or unrecognised input.
    """
    if v is None:
        raise ValueError("members is null")
    if isinstance(v, float) and math.isnan(v):
        raise ValueError("members is NaN")
    if isinstance(v, str):
        return _parse_members(v)
    if isinstance(v, (list, tuple)):
        return [int(x) for x in v]
    # numpy ndarray and other iterables
    try:
        return [int(x) for x in v]
    except TypeError:
        raise ValueError(f"cannot parse members from {type(v).__name__!r}: {v!r}")


def _coerce_float(v: Any, default: float = 0.0) -> float:
    """Return ``float(v)`` or *default* when *v* is None / NaN."""
    if v is None:
        return default
    if isinstance(v, float) and math.isnan(v):
        return default
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _coerce_int(v: Any) -> int:
    """Return ``int(v)``, raising ``ValueError`` on None / NaN."""
    if v is None:
        raise ValueError("value is null")
    if isinstance(v, float) and math.isnan(v):
        raise ValueError("value is NaN")
    return int(v)


def _coerce_str(v: Any, default: str = "") -> str:
    """Return ``str(v)`` or *default* when *v* is None / NaN."""
    if v is None:
        return default
    if isinstance(v, float) and math.isnan(v):
        return default
    return str(v)


# Column schema for hyperedge result sets
_HE_COLUMNS = ["event_ts", "members", "weight", "mean_dist_m", "formation"]


class Connection:
    """
    An open connection to a HyperMesh DB database directory.

    Obtain one via :func:`hypermeshdb.connect` rather than constructing
    directly.

    Parameters
    ----------
    dir_path:
        Path to the database directory (created if absent).
    hyperedges_csv:
        If provided and the index does not yet exist, the TPI + FMI index
        is built from this CSV file automatically.
    nodes_csv:
        If provided, node properties are imported into ``nodes.db`` on the
        first run and served from SQLite on subsequent runs.
    bucket_seconds:
        TPI partition granularity in seconds.  Ignored if the index already
        exists.  Default: 10.

    Examples
    --------
    >>> import hypermeshdb
    >>> db = hypermeshdb.connect(
    ...     "/tmp/my_db",
    ...     hyperedges_csv="hyperedges.csv",
    ...     nodes_csv="nodes.csv",
    ... )
    >>> result = db.execute("CALL show_hyperedge_tables() RETURN *")
    >>> for row in result:
    ...     print(row["name"], row["row_count"])
    >>> db.close()
    """

    # ── Construction ──────────────────────────────────────────────────────

    # ── Layout constants ──────────────────────────────────────────────────
    #   "partitioned": Phase 6 layout — each table in its own subdirectory
    #   "legacy":      pre-Phase-6 flat layout — all files at db root
    #   "empty":       freshly created directory with no index yet
    _LAYOUT_PARTITIONED = "partitioned"
    _LAYOUT_LEGACY      = "legacy"
    _LAYOUT_EMPTY       = "empty"

    def __init__(
        self,
        dir_path:       str,
        hyperedges_csv: str | None = None,
        nodes_csv:      str | None = None,
        bucket_seconds: int = 10,
        primary_table:  str = "CoProximity",
    ) -> None:
        self._dir_path = os.path.abspath(dir_path)
        os.makedirs(self._dir_path, exist_ok=True)

        # Phase 6: one HmStore per logical hyperedge table
        self._stores: dict[str, HmStore] = {}

        layout = self._detect_layout()

        if layout == self._LAYOUT_LEGACY:
            raise HyperMeshError(
                f"Database at '{self._dir_path}' uses the legacy flat layout "
                f"(created before HyperMesh DB Phase 6). "
                f"Run: hmdb migrate \"{self._dir_path}\"  "
                f"to upgrade to the partitioned layout before reconnecting."
            )

        # ── Open schema catalog ───────────────────────────────────────────
        schema_db = os.path.join(self._dir_path, "schema.db")
        self._schema = SchemaStore(schema_db)
        self._schema.seed_defaults()

        if layout == self._LAYOUT_PARTITIONED:
            # Reconnect: open every table store found in schema + on disk
            for t in self._schema.list_hyperedge_tables():
                key = t["name"].upper()
                table_dir = self._table_dir(key)
                if os.path.exists(os.path.join(table_dir, "bucket_directory.bin")):
                    # Use _open_table_store so compact_threshold is restored
                    try:
                        self._stores[key] = self._open_table_store(
                            key, t.get("bucket_seconds", 10)
                        )
                    except HyperMeshError as exc:
                        raise HyperMeshError(
                            f"Cannot open table '{t['name']}': {exc}"
                        ) from exc
        else:
            # _LAYOUT_EMPTY: fresh database
            # If a CSV was provided, bootstrap the primary table from it
            if hyperedges_csv is not None:
                primary_key = primary_table.upper()
                table_dir   = self._table_dir(primary_key)
                os.makedirs(table_dir, exist_ok=True)
                try:
                    HmStore.build(table_dir, hyperedges_csv, bucket_seconds)
                except RuntimeError as exc:
                    raise HyperMeshError(f"Index build failed: {exc}") from exc
                # Ensure the primary table is registered (seed_defaults already
                # created "CoProximity"; handle any custom primary_table name)
                if primary_key != "COPROXIMITY":
                    try:
                        self._schema.create_hyperedge_table(
                            name           = primary_table,
                            member_tables  = [],
                            bucket_seconds = bucket_seconds,
                        )
                    except ValueError:
                        pass  # already exists
                try:
                    self._stores[primary_key] = HmStore(table_dir)
                except RuntimeError as exc:
                    raise HyperMeshError(f"Cannot open index: {exc}") from exc

        # Determine primary table: use the first table that actually has an open
        # store on disk.  seed_defaults() always registers "CoProximity" in the
        # schema catalog even on a brand-new database, so we cannot use the first
        # schema entry — it may be a phantom that was never physically created.
        # On a reconnect, the first entry in _stores (insertion-ordered) is the
        # correct primary.  On a fresh empty DB, fall back to the explicit arg
        # and update in _create_table() when the user creates their first table.
        if self._stores:
            first_open_key = next(iter(self._stores))
            he_tables = self._schema.list_hyperedge_tables()
            _name_by_key = {t["name"].upper(): t["name"] for t in he_tables}
            self._primary_table: str = _name_by_key.get(
                first_open_key, first_open_key
            )
        else:
            self._primary_table: str = primary_table

        # ── Open node stores (one per registered node table) ─────────────
        self._node_stores: dict[str, NodeStore] = {}
        self._nodes: NodeStore | None = None

        drone_cols = self._schema.get_node_columns("Drone")
        nodes_db   = os.path.join(self._dir_path, "nodes.db")
        if nodes_csv is not None:
            try:
                store = NodeStore.build_from_csv(nodes_db, "Drone", drone_cols, nodes_csv)
            except Exception as exc:
                raise HyperMeshError(f"Cannot open node store: {exc}") from exc
        else:
            try:
                store = NodeStore(nodes_db, "Drone", drone_cols)
            except Exception:
                store = None  # type: ignore[assignment]
        if store is not None:
            self._node_stores["Drone"] = store
            self._nodes = store

        self._closed = False

        # ── Transaction state (Phase B.1) ─────────────────────────────────
        self._in_transaction: bool          = False
        self._tx_buffer:      list[_TxEntry] = []

    # ── Layout helpers ────────────────────────────────────────────────────

    def _detect_layout(self) -> str:
        """Identify whether the database directory uses the Phase 6 partitioned
        layout, the legacy flat layout, or is completely empty."""
        # Legacy: flat files at the DB root (pre-Phase-6)
        if os.path.exists(os.path.join(self._dir_path, "bucket_directory.bin")):
            return self._LAYOUT_LEGACY
        # Partitioned: at least one table subdirectory contains a bucket dir
        try:
            for entry in os.scandir(self._dir_path):
                if entry.is_dir():
                    if os.path.exists(
                        os.path.join(entry.path, "bucket_directory.bin")
                    ):
                        return self._LAYOUT_PARTITIONED
        except OSError:
            pass
        return self._LAYOUT_EMPTY

    def _table_dir(self, table: str) -> str:
        """Absolute path of the per-table partition directory (table name is
        normalised to upper-case so 'Sensor' and 'SENSOR' share one path)."""
        return os.path.join(self._dir_path, table.upper())

    def _get_store(self, table: str) -> HmStore:
        """Return the HmStore for *table*, creating an empty one on demand if the
        table exists in the schema but its physical directory does not yet have
        index files (e.g. the table was just created via DDL)."""
        key = table.upper()
        store = self._stores.get(key)
        if store is not None:
            return store
        # Lazy initialisation: create empty partition for a schema-registered table
        schema_names = {t["name"].upper() for t in self._schema.list_hyperedge_tables()}
        if key not in schema_names:
            raise HyperMeshError(
                f"Table '{table}' does not exist. "
                f"Create it first: CREATE HYPEREDGE TABLE {table} (...)"
            )
        store = self._open_table_store(key)
        self._stores[key] = store
        return store

    def _open_table_store(self, table: str, bucket_seconds: int = 10) -> HmStore:
        """Open (or initialise) the per-table HmStore in its partition directory."""
        table_dir = self._table_dir(table)
        os.makedirs(table_dir, exist_ok=True)
        if not os.path.exists(os.path.join(table_dir, "bucket_directory.bin")):
            try:
                HmStore.init_empty(table_dir, bucket_seconds)
            except RuntimeError as exc:
                raise HyperMeshError(
                    f"Cannot initialise empty store for '{table}': {exc}"
                ) from exc
        try:
            store = HmStore(table_dir)
        except RuntimeError as exc:
            raise HyperMeshError(f"Cannot open table '{table}': {exc}") from exc

        # Phase 8: restore autocompact threshold from SchemaStore (if any)
        if self._schema:
            try:
                tbl_meta = self._schema.get_hyperedge_table(table)
                if tbl_meta:
                    ct = tbl_meta.get("compact_threshold", 0) or 0
                    if ct > 0:
                        store.set_autocompact(ct)
            except Exception:
                pass  # non-fatal; table still opens normally
        return store

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the connection and release all C and SQLite resources."""
        if self._closed:
            return
        self._closed = True
        for store in list(self._stores.values()):
            try:
                store.close()
            except Exception:
                pass
        self._stores.clear()
        if self._schema:
            self._schema.close()
        for ns in self._node_stores.values():
            try:
                ns.close()
            except Exception:
                pass

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise HyperMeshError("Connection is closed")

    # ── Metadata properties ───────────────────────────────────────────────

    @property
    def total_records(self) -> int:
        """Total hyperedge records across all table partitions (post-compaction)."""
        self._check_open()
        return sum(s.total_records for s in self._stores.values())

    @property
    def bucket_count(self) -> int:
        """Number of TPI time buckets in the primary table partition."""
        self._check_open()
        s = self._stores.get(self._primary_table.upper())
        return s.bucket_count if s else 0

    @property
    def bucket_seconds(self) -> int:
        """TPI partition granularity in seconds (from the primary table)."""
        self._check_open()
        s = self._stores.get(self._primary_table.upper())
        return s.bucket_seconds if s else 10

    @property
    def node_count(self) -> int:
        """Total unique nodes across all FMI indexes."""
        self._check_open()
        return sum(s.node_count for s in self._stores.values())

    @property
    def wal_pending(self) -> int:
        """Total uncompacted WAL entries across all table partitions."""
        self._check_open()
        return sum(s.wal_pending for s in self._stores.values())

    # ── Query execution ───────────────────────────────────────────────────

    def execute(
        self,
        query:      str,
        parameters: dict[str, Any] | None = None,
    ) -> QueryResult:
        """
        Execute a Cypher-like query and return a :class:`~hypermeshdb.QueryResult`.

        Supported statements
        --------------------
        ``CALL show_hyperedge_tables() RETURN *``
            List all hyperedge table definitions from the schema catalog.

        ``MATCH HYPEREDGE (he:<Table>) WHERE he.event_ts >= T1 AND he.event_ts <= T2``
            Temporal range query with TPI bucket pushdown.

        ``CALL GET_HYPEREDGES_BY_TIME_RANGE('<Table>', T1, T2) RETURN *``
            Alternative syntax for the same TPI range query.

        ``MATCH HYPEREDGE (he:<Table>) WHERE N IN he.members``
            FMI point lookup: all hyperedges containing node N.

        ``MATCH HYPEREDGE (he:<Table>)``
            Full hyperedge table scan (no WHERE clause).

        Pagination
        ----------
        ``RETURN`` queries support ``ORDER BY``, ``LIMIT n`` and
        ``SKIP n`` / ``OFFSET n`` (synonyms). Ordering is applied first, then
        ``SKIP``, then ``LIMIT`` — so ``... ORDER BY he.event_ts SKIP 100 LIMIT 50``
        returns rows 101–150. ``SKIP``/``OFFSET`` are evaluated client-side over
        the materialised result; for bounded-memory iteration of very large
        ranges use :func:`hypermesh.scan_windows` instead.

        ``CREATE HYPEREDGE TABLE <Name> (<Member>, ...) [BUCKET_SECONDS n]``
            Define a new hyperedge table in the schema catalog.

        ``DROP HYPEREDGE TABLE <Name>``
            Remove a hyperedge table definition from the schema catalog.

        Parameters
        ----------
        query:
            A Cypher-like query string.
        parameters:
            Optional ``{name: value}`` mapping.  Values are substituted for
            ``$name`` placeholders in the query before parsing.  Integers and
            strings are supported.

        Returns
        -------
        QueryResult
            Iterable result set with column metadata and an optional query plan.

        Raises
        ------
        HyperMeshError
            On parse errors, schema violations, or I/O failures.
        """
        self._check_open()

        # ── Transaction control: BEGIN / COMMIT / ROLLBACK ─────────────────
        if _BEGIN_RE.match(query):
            self.begin()
            return QueryResult(columns=["status"], rows=[["BEGIN"]])

        if _COMMIT_RE.match(query):
            self.commit()
            return QueryResult(columns=["status"], rows=[["COMMIT"]])

        if _ROLLBACK_RE.match(query):
            self.rollback()
            return QueryResult(columns=["status"], rows=[["ROLLBACK"]])

        # ── EXPLAIN / ANALYZE prefix stripping ────────────────────────────
        _explain_mode = bool(_EXPLAIN_RE.match(query))
        _analyze_mode = bool(_ANALYZE_RE.match(query))
        if _explain_mode:
            query = _EXPLAIN_RE.sub("", query, count=1).strip()
        elif _analyze_mode:
            query = _ANALYZE_RE.sub("", query, count=1).strip()

        # ── Parameter substitution ($name → value) ────────────────────────
        if parameters:
            for name, value in parameters.items():
                query = query.replace(f"${name}", str(value))

        # ── CREATE NODE TABLE intercept ───────────────────────────────────
        m_cnt = _CREATE_NODE_TABLE_RE.match(query)
        if m_cnt:
            return self._create_node_table(
                name     = m_cnt.group("name"),
                cols_str = m_cnt.group("cols"),
            )

        # ── ALTER TABLE intercept (Python-side, not in C parser) ──────────
        m_add = _ALTER_TABLE_ADD_RE.match(query)
        if m_add:
            return self._alter_table_add_column(
                table   = m_add.group("table"),
                col     = m_add.group("col"),
                col_type = m_add.group("type"),
                default = m_add.group("default"),
            )

        m_drop = _ALTER_TABLE_DROP_RE.match(query)
        if m_drop:
            return self._alter_table_drop_column(
                table = m_drop.group("table"),
                col   = m_drop.group("col"),
            )

        m_ren = _ALTER_TABLE_RENAME_RE.match(query)
        if m_ren:
            return self._alter_table_rename(
                table    = m_ren.group("table"),
                new_name = m_ren.group("new_name"),
            )

        # ── COPY FROM intercept (Python-side DDL, not in C parser) ────────
        m = _COPY_RE.match(query)
        if m:
            table = m.group("table")
            path  = m.group("path")
            opts  = _parse_copy_options(m.group("opts"))
            # Route by file extension: .parquet → Parquet; .json/.ndjson/.jsonl → JSON; else CSV
            _ext = path.lower()
            if _ext.endswith(".parquet"):
                return self.copy_from_parquet(
                    table         = table,
                    path          = path,
                    ignore_errors = opts.ignore_errors,
                )
            if _ext.endswith((".json", ".ndjson", ".jsonl")):
                return self.copy_from_json(
                    table         = table,
                    path          = path,
                    key           = opts.key,
                    ignore_errors = opts.ignore_errors,
                )
            return self.copy_from_csv(
                table         = table,
                path          = path,
                header        = opts.header,
                delimiter     = opts.delimiter,
                quotechar     = opts.quotechar,
                skip          = opts.skip,
                ignore_errors = opts.ignore_errors,
            )

        # ── UPDATE WHERE normalisation ─────────────────────────────────────
        # Rewrite  event_ts = X  →  he.event_ts >= X AND he.event_ts <= X
        # and extract members = [...] for Python-side filtering.
        _update_where_extra: dict = {}
        if _UPDATE_DETECT_RE.match(query):
            query, _update_where_extra = _normalise_update_query(query)

        # ── SKIP / OFFSET extraction (Python-side; not in the C grammar) ──
        query, _skip_n = _extract_skip(query)

        # ── OR / NOT / <> WHERE extraction (Python-side; not in C grammar) ──
        # For single-pattern MATCH HYPEREDGE queries whose WHERE uses boolean
        # operators the C grammar can't express, strip the WHERE (→ full scan)
        # and evaluate the predicate tree in _post_process.
        try:
            query, _bool_where = extract_bool_where(query)
        except WhereParseError as exc:
            raise HyperMeshError(f"Unsupported WHERE clause: {exc}") from exc

        # ── Parse ─────────────────────────────────────────────────────────
        ast = HmStore.parse_query(query)

        if ast.kind == HmQueryKind.PARSE_ERROR:
            raise HyperMeshError(f"Parse error: {ast.error}")

        ast.skip_n = _skip_n
        ast.bool_where = _bool_where

        # ── Transaction buffer: intercept writes ───────────────────────────
        if self._in_transaction and ast.kind == HmQueryKind.INSERT:
            return self._tx_buffer_insert(ast)

        if self._in_transaction and ast.kind == HmQueryKind.DELETE:
            return self._tx_buffer_delete(ast)

        # ── Dispatch ──────────────────────────────────────────────────────
        if ast.kind == HmQueryKind.SHOW_TABLES:
            result = self._show_tables()
        elif ast.kind in (HmQueryKind.TPI_RANGE, HmQueryKind.GET_BY_RANGE):
            raw    = self._tpi_range(ast.ts_start, ast.ts_end, table=ast.table)
            result = self._post_process_with_psi(ast.table, raw, ast)
        elif ast.kind == HmQueryKind.FMI_LOOKUP:
            raw    = self._fmi_lookup(ast.node_id, table=ast.table)
            result = self._post_process_with_psi(ast.table, raw, ast)
        elif ast.kind == HmQueryKind.FULL_SCAN:
            raw    = self._full_scan(table=ast.table)
            result = self._post_process_with_psi(ast.table, raw, ast)
        elif ast.kind == HmQueryKind.CREATE_TABLE:
            result = self._create_table(ast.table, ast.alias, ast.ts_start,
                                        ast.props_csv, ast.compact_threshold)
        elif ast.kind == HmQueryKind.DROP_TABLE:
            result = self._drop_table(ast.table)
        elif ast.kind == HmQueryKind.INSERT:
            result = self._dml_insert(ast)
        elif ast.kind == HmQueryKind.DELETE:
            result = self._dml_delete(ast)
        elif ast.kind == HmQueryKind.UPDATE:
            result = self._dml_update(ast, _update_where_extra)
        elif ast.kind == HmQueryKind.CREATE_INDEX:
            result = self._create_index(ast.table, ast.index_col)
        elif ast.kind == HmQueryKind.DROP_INDEX:
            result = self._drop_index(ast.table, ast.index_col)
        elif ast.kind == HmQueryKind.SHOW_INDEXES:
            result = self._show_indexes(ast.table)
        elif ast.kind == HmQueryKind.JOIN:
            result = self._execute_join(ast)
        else:
            raise HyperMeshError(f"Unhandled query kind: {ast.kind}")

        # ── EXPLAIN: return only query plan, no data rows ──────────────────
        if _explain_mode and result.query_plan:
            plan = result.query_plan
            return QueryResult(
                columns = ["strategy", "buckets_scanned", "total_buckets",
                           "speedup_factor", "elapsed_us",
                           "index_type", "estimated_rows"],
                rows    = [[
                    plan.strategy,
                    plan.buckets_scanned,
                    plan.total_buckets,
                    round(plan.speedup_factor, 2),
                    plan.elapsed_us,
                    _strategy_to_index(plan.strategy),
                    result.num_tuples,
                ]],
                query_plan = plan,
            )

        return result

    # ── Write path ────────────────────────────────────────────────────────

    def insert(
        self,
        event_ts:    int,
        members:     list[int],
        weight:      float = 0.0,
        mean_dist_m: float = 0.0,
        formation:   str   = "",
        *,
        table:       str | None = None,
    ) -> None:
        """
        Insert a new hyperedge into the WAL.

        The record is immediately visible to subsequent :meth:`execute` calls
        that perform range or full-scan queries — no compaction required.

        Parameters
        ----------
        event_ts:
            Mission timestamp in seconds.
        members:
            List of node IDs in this hyperedge.
        weight:
            Coalition strength  (0.0 – 1.0).
        mean_dist_m:
            Mean pairwise distance in metres.
        formation:
            Formation name string (truncated to 15 characters).
        table:
            Target hyperedge table name.  Defaults to the primary table
            (the first table created in this database).

        Raises
        ------
        HyperMeshError
            If members is empty, member_count exceeds 64, or an I/O error occurs.
        """
        self._check_open()
        if not members:
            raise HyperMeshError("members must be a non-empty list of node IDs")
        target = table or self._primary_table
        # If inside a transaction, buffer instead of writing
        if self._in_transaction:
            self._tx_buffer.append(_TxEntry(
                op          = "insert",
                table       = target,
                event_ts    = event_ts,
                members     = list(members),
                weight      = weight,
                mean_dist_m = mean_dist_m,
                formation   = formation,
            ))
            return
        try:
            self._get_store(target).insert(
                event_ts=event_ts,
                members=members,
                weight=weight,
                mean_dist_m=mean_dist_m,
                formation=formation,
            )
        except RuntimeError as exc:
            raise HyperMeshError(str(exc)) from exc

    def delete(self, event_ts: int, members: list[int], *, table: str | None = None) -> None:
        """
        Write a DELETE tombstone to the WAL.

        Any hyperedge matching ``(event_ts, same member set — order-independent)``
        is hidden from subsequent queries.  Physical removal happens at the
        next :meth:`compact` call.

        Parameters
        ----------
        event_ts:
            Timestamp of the hyperedge to delete.
        members:
            Member node IDs of the hyperedge to delete (order-independent).
        table:
            Target hyperedge table name.  Defaults to the primary table.

        Raises
        ------
        HyperMeshError
            On I/O failure.
        """
        self._check_open()
        target = table or self._primary_table
        # If inside a transaction, buffer instead of writing
        if self._in_transaction:
            self._tx_buffer.append(_TxEntry(
                op       = "delete",
                table    = target,
                event_ts = event_ts,
                members  = list(members),
            ))
            return
        try:
            self._get_store(target).delete(event_ts=event_ts, members=members)
        except RuntimeError as exc:
            raise HyperMeshError(str(exc)) from exc

    # ── Transaction API (Phase B.1) ───────────────────────────────────────────

    def begin(self) -> None:
        """
        Open a new transaction.  All subsequent ``INSERT`` and ``DELETE``
        operations are buffered until :meth:`commit` or :meth:`rollback`.

        Raises
        ------
        HyperMeshError
            If a transaction is already open.
        """
        self._check_open()
        if self._in_transaction:
            raise HyperMeshError(
                "A transaction is already open. COMMIT or ROLLBACK first."
            )
        self._in_transaction = True
        self._tx_buffer      = []

    def commit(self) -> None:
        """
        Flush the transaction buffer to the WAL atomically.

        If any write fails, already-written INSERTs are reversed with tombstone
        DELETE records and ``HyperMeshError`` is raised.

        Raises
        ------
        HyperMeshError
            If no transaction is open, or if a write fails (with rollback).
        """
        self._check_open()
        if not self._in_transaction:
            raise HyperMeshError("No transaction is open. Call BEGIN first.")

        # Group buffered ops by their target store, preserving insertion order.
        # Each store's batch is flushed atomically (one fdatasync, all-or-nothing
        # on a crash) via the engine's group-commit primitive, and the write lock
        # held for the whole batch keeps any partial transaction invisible to
        # concurrent readers.
        from collections import OrderedDict
        groups: OrderedDict[Any, list[_TxEntry]] = OrderedDict()
        try:
            for entry in self._tx_buffer:
                store = self._get_store(entry.table or self._primary_table)
                groups.setdefault(store, []).append(entry)
        except Exception as exc:
            self._in_transaction = False
            self._tx_buffer      = []
            raise HyperMeshError(f"Transaction aborted: {exc}") from exc

        committed: list[tuple[Any, list[_TxEntry]]] = []
        try:
            for store, entries in groups.items():
                ops: list[tuple] = []
                for e in entries:
                    if e.op == "insert":
                        ops.append(("insert", e.event_ts, e.members,
                                    e.weight, e.mean_dist_m, e.formation))
                    else:
                        ops.append(("delete", e.event_ts, e.members))
                store.commit_batch(ops)
                committed.append((store, entries))
        except (RuntimeError, Exception) as exc:
            # Each per-store batch is atomic, but a multi-table commit can still
            # fail after some tables committed — best-effort tombstone those.
            for store, entries in reversed(committed):
                for undone in reversed(entries):
                    if undone.op == "insert":
                        try:
                            store.delete(event_ts=undone.event_ts,
                                         members=undone.members)
                        except Exception:
                            pass
            self._in_transaction = False
            self._tx_buffer      = []
            raise HyperMeshError(f"Transaction rolled back: {exc}") from exc

        self._in_transaction = False
        self._tx_buffer      = []

    def rollback(self) -> None:
        """
        Discard the transaction buffer without writing to the WAL.

        Raises
        ------
        HyperMeshError
            If no transaction is open.
        """
        self._check_open()
        if not self._in_transaction:
            raise HyperMeshError("No transaction is open. Call BEGIN first.")
        self._in_transaction = False
        self._tx_buffer      = []

    def transaction(self):
        """
        Context manager that issues ``BEGIN`` on entry and ``COMMIT`` on
        clean exit, or ``ROLLBACK`` on exception.

        Usage::

            with db.transaction():
                db.insert(event_ts=1000, members=[1, 2])
                db.insert(event_ts=1001, members=[2, 3])
        """
        return _TransactionContext(self)

    # ── Transaction buffer helpers ────────────────────────────────────────────

    def _tx_buffer_insert(self, ast) -> "QueryResult":
        """Buffer an INSERT inside an open transaction (do not write WAL)."""
        from hypermesh_core.python.hm_store import HM_FIELD_SEP
        cols = [c.strip() for c in ast.insert_cols.split(",") if c.strip()]
        vals = ast.insert_vals.split(HM_FIELD_SEP)
        col_val: dict[str, Any] = {c.upper(): v for c, v in zip(cols, vals)}
        members_raw = col_val.get("MEMBERS", "")
        members = [int(x) for x in str(members_raw).strip("[]").split(",") if x.strip()]
        self._tx_buffer.append(_TxEntry(
            op          = "insert",
            table       = ast.table or None,
            event_ts    = int(col_val.get("EVENT_TS", 0)),
            members     = members,
            weight      = float(col_val.get("WEIGHT", 0.0)),
            mean_dist_m = float(col_val.get("MEAN_DIST_M", 0.0)),
            formation   = str(col_val.get("FORMATION", "")),
        ))
        return QueryResult(columns=["buffered"], rows=[[True]])

    def _tx_buffer_delete(self, ast) -> "QueryResult":
        """Buffer a DELETE inside an open transaction (do not write WAL)."""
        self._tx_buffer.append(_TxEntry(
            op       = "delete",
            table    = ast.table or None,
            event_ts = int(ast.ts_start),
            members  = list(ast.predicates[0].node_ids) if ast.predicates else [],
        ))
        return QueryResult(columns=["buffered"], rows=[[True]])

    # ── ALTER TABLE helpers (Phase B.4) ───────────────────────────────────────

    def _alter_table_add_column(
        self,
        table:    str,
        col:      str,
        col_type: str,
        default:  str | None,
    ) -> "QueryResult":
        if not self._schema:
            raise HyperMeshError("Schema catalog is not open")
        tbl = table.upper()
        existing = self._schema.get_hyperedge_columns(tbl)
        if any(c.name.upper() == col.upper() for c in existing):
            raise HyperMeshError(
                f"Column '{col}' already exists on table '{tbl}'"
            )
        # Normalise FLOAT → REAL for consistency
        normalized_type = "REAL" if col_type.upper() == "FLOAT" else col_type.upper()
        new_col = ColumnDef(
            name        = col.upper(),
            col_type    = normalized_type,
            nullable    = True,
            default_val = default,
        )
        self._schema.define_hyperedge_columns(tbl, existing + [new_col])
        return QueryResult(
            columns = ["table", "action", "column", "type", "default"],
            rows    = [[tbl, "ADD COLUMN", col.upper(), normalized_type, default]],
        )

    def _alter_table_drop_column(self, table: str, col: str) -> "QueryResult":
        if not self._schema:
            raise HyperMeshError("Schema catalog is not open")
        tbl = table.upper()
        existing = self._schema.get_hyperedge_columns(tbl)
        updated  = [c for c in existing if c.name.upper() != col.upper()]
        if len(updated) == len(existing):
            raise HyperMeshError(f"Column '{col}' not found on table '{tbl}'")
        self._schema.define_hyperedge_columns(tbl, updated)
        return QueryResult(
            columns = ["table", "action", "column"],
            rows    = [[tbl, "DROP COLUMN", col.upper()]],
        )

    def _alter_table_rename(self, table: str, new_name: str) -> "QueryResult":
        if not self._schema:
            raise HyperMeshError("Schema catalog is not open")
        tbl     = table.upper()
        new_tbl = new_name.upper()
        self._schema.rename_hyperedge_table(tbl, new_tbl)
        # Update in-memory store map
        key     = tbl
        new_key = new_tbl
        store   = self._stores.pop(key, None)
        if store is not None:
            self._stores[new_key] = store
        if self._primary_table.upper() == key:
            self._primary_table = new_name
        return QueryResult(
            columns = ["old_name", "new_name", "renamed"],
            rows    = [[tbl, new_tbl, True]],
        )

    # ── Compaction ────────────────────────────────────────────────────────────

    def compact(
        self,
        *,
        ttl_seconds: "int | None" = None,
        table: "str | None" = None,
    ) -> None:
        """
        Rebuild TPI + FMI from ``(existing index ∪ WAL inserts − WAL tombstones)``,
        write new binary files in the database directory, then truncate the WAL.

        Parameters
        ----------
        ttl_seconds:
            When provided, records with ``event_ts < (now − ttl_seconds)`` are
            permanently discarded during compaction.  Uses
            ``hm_compact_with_ttl()`` in the C engine.
        table:
            Compact only the named table.  When *None* (default) all open table
            stores are compacted.

        After :meth:`compact` returns:
          - :attr:`wal_pending` == 0 (for the targeted table(s))
          - All inserted records are permanently in the TPI index
          - All deleted records are permanently absent

        Raises
        ------
        HyperMeshError
            On I/O failure or OOM during rebuild.
        """
        import time as _time
        self._check_open()

        # Resolve which stores to compact
        if table is not None:
            key = table.upper()
            if key not in self._stores:
                raise HyperMeshError(f"compact(): unknown table '{table}'")
            targets = {key: self._stores[key]}
        else:
            targets = dict(self._stores)

        min_ts: "int | None" = None
        if ttl_seconds is not None:
            if ttl_seconds <= 0:
                raise HyperMeshError("compact(): ttl_seconds must be a positive integer")
            min_ts = max(0, int(_time.time()) - ttl_seconds)

        for table_key, store in targets.items():
            try:
                if min_ts is not None:
                    store.compact_with_ttl(min_ts)
                else:
                    store.compact()
            except RuntimeError as exc:
                raise HyperMeshError(
                    f"compact() failed for table '{table_key}': {exc}"
                ) from exc

        # Rebuild PSI files for compacted tables that have registered indexes
        if self._schema:
            try:
                all_indexes = self._schema.list_indexes()
                tables_with_indexes = {idx["table_name"] for idx in all_indexes}
                for tbl_key in targets:
                    if tbl_key in tables_with_indexes:
                        self._rebuild_psi(tbl_key)
            except Exception:
                pass  # PSI rebuild failure is non-fatal; queries degrade to full-scan

    def set_autocompact(
        self,
        threshold: int,
        *,
        table: "str | None" = None,
    ) -> None:
        """
        Configure automatic WAL compaction for one or all tables.

        After every successful insert or delete, if the WAL entry count for a
        table reaches or exceeds *threshold*, the C engine compacts that table
        automatically (while holding its write lock — no deadlock risk).

        Parameters
        ----------
        threshold:
            WAL entry count that triggers auto-compact.  ``0`` disables
            automatic compaction (the default for new tables).
        table:
            Apply only to the named table.  *None* applies to all open stores
            and persists the setting in the SchemaStore for each table.

        Raises
        ------
        HyperMeshError
            If the table is not open or the threshold is negative.
        """
        self._check_open()
        if threshold < 0:
            raise HyperMeshError("set_autocompact(): threshold must be >= 0")

        if table is not None:
            key = table.upper()
            if key not in self._stores:
                raise HyperMeshError(f"set_autocompact(): unknown table '{table}'")
            targets = {key: self._stores[key]}
        else:
            targets = dict(self._stores)

        for tbl_key, store in targets.items():
            store.set_autocompact(threshold)
            if self._schema:
                try:
                    self._schema.set_compact_threshold(tbl_key, threshold)
                except Exception:
                    pass  # schema update failure is non-fatal; setting is still active in-memory

    # ── Internal query handlers ───────────────────────────────────────────

    def _range_result_to_rows(
        self,
        result,
        table: str = "",
    ) -> tuple[list[str], list[list[Any]]]:
        """
        Convert HmStore RangeResult to (column_names, raw_row_lists).

        For V2 stores with user-defined properties, the property columns are
        decoded from the binary blob and appended after the standard columns.
        """
        prop_cols: list[ColumnDef] = []
        if table and self._schema:
            try:
                prop_cols = self._schema.get_hyperedge_columns(table)
            except Exception:
                pass

        base_cols = list(_HE_COLUMNS)
        extra_col_names = [c.name for c in prop_cols]
        columns = base_cols + extra_col_names

        rows: list[list[Any]] = []
        for he in result.hyperedges:
            row: list[Any] = [
                he["event_ts"],
                he["members"],
                he["weight"],
                he["mean_dist_m"],
                he["formation"],
            ]
            if prop_cols:
                blob = he.get("_props")
                if blob:
                    decoded = self._deserialize_props(blob, prop_cols)
                    for c in prop_cols:
                        row.append(decoded.get(c.name, c.coerce(None)))
                else:
                    for c in prop_cols:
                        row.append(c.coerce(None))
            rows.append(row)
        return columns, rows

    def _make_plan(self, hm_plan,
                    rows_scanned: int = 0,
                    rows_returned: int = 0,
                    predicates_applied: int = 0) -> QueryPlan:
        return QueryPlan(
            strategy           = hm_plan.strategy,
            buckets_scanned    = hm_plan.buckets_scanned,
            total_buckets      = hm_plan.total_buckets,
            speedup_factor     = hm_plan.speedup_factor,
            elapsed_us         = hm_plan.elapsed_us,
            rows_scanned       = rows_scanned,
            rows_returned      = rows_returned,
            predicates_applied = predicates_applied,
        )

    # ── Phase 2: Python-layer post-processing ─────────────────────────────

    @staticmethod
    def _row_as_dict(columns: list[str], row: list[Any]) -> dict[str, Any]:
        return {c: row[i] for i, c in enumerate(columns)}

    @staticmethod
    def _apply_predicates(columns: list[str],
                           rows: list[list[Any]],
                           predicates: list[dict]) -> list[list[Any]]:
        """
        Filter rows using Python-layer property predicates.
        Each predicate is {"col": str, "op": HmPredOp, "val": str, "is_str": bool}.
        Rows that fail any predicate are dropped.

        Column lookup priority:
          1. Exact case match (e.g. predicate col="WEIGHT" matches column "WEIGHT")
          2. Case-insensitive match (e.g. "WEIGHT" matches "weight")
          This ensures that a predicate on the user-defined column "WEIGHT" wins over
          the built-in lowercase "weight" column when both are present.
        """
        if not predicates:
            return rows

        from hypermesh_core.python.hm_store import HmPredOp
        # Build exact-case index first, then fall back to case-insensitive.
        # Use setdefault so the FIRST occurrence wins on case-insensitive conflicts
        # (built-in lowercase columns appear before user-defined uppercase ones).
        col_idx_exact = {c: i for i, c in enumerate(columns)}
        col_idx_ci: dict[str, int] = {}
        for i, c in enumerate(columns):
            col_idx_ci.setdefault(c.upper(), i)

        def _find_col(col_name: str) -> int | None:
            # 1. exact case
            if col_name in col_idx_exact:
                return col_idx_exact[col_name]
            # 2. case-insensitive (first-occurrence wins)
            return col_idx_ci.get(col_name.upper())

        filtered = []
        for row in rows:
            keep = True
            for pred in predicates:
                col    = pred["col"]
                op     = pred["op"]
                val_s  = pred["val"]
                is_str = pred["is_str"]

                idx = _find_col(col)
                if idx is None:
                    continue  # unknown column — skip predicate

                row_val = row[idx]
                if row_val is None:
                    keep = False
                    break

                try:
                    if is_str:
                        rv = str(row_val)
                        pv = str(val_s)
                    else:
                        rv = float(row_val)
                        pv = float(val_s)
                except (ValueError, TypeError):
                    keep = False
                    break

                if   op == HmPredOp.EQ:  keep = (rv == pv)
                elif op == HmPredOp.GT:  keep = (rv >  pv)
                elif op == HmPredOp.GEQ: keep = (rv >= pv)
                elif op == HmPredOp.LT:  keep = (rv <  pv)
                elif op == HmPredOp.LEQ: keep = (rv <= pv)
                else:                    keep = (rv == pv)

                if not keep:
                    break
            if keep:
                filtered.append(row)
        return filtered

    @staticmethod
    def _apply_bool_where(columns: list[str],
                          rows: list[list[Any]],
                          node) -> list[list[Any]]:
        """
        Filter rows with a boolean WHERE tree (``hypermeshdb._where.Node``)
        supporting AND / OR / NOT / parentheses and ``=`` ``<>`` ``>`` ``>=``
        ``<`` ``<=``.

        Column lookup mirrors :meth:`_apply_predicates`: exact case first, then
        case-insensitive (first occurrence wins). A comparison against an
        unknown column or a NULL value evaluates to ``False``.
        """
        col_idx_exact = {c: i for i, c in enumerate(columns)}
        col_idx_ci: dict[str, int] = {}
        for i, c in enumerate(columns):
            col_idx_ci.setdefault(c.upper(), i)

        def _make_get(row: list[Any]):
            def _get(col_name: str) -> Any:
                idx = col_idx_exact.get(col_name)
                if idx is None:
                    idx = col_idx_ci.get(col_name.upper())
                if idx is None:
                    return None
                return row[idx]
            return _get

        return [row for row in rows if node.eval(_make_get(row))]

    # Aggregate function names we recognise
    _AGG_FNS = frozenset({"COUNT", "AVG", "MAX", "MIN", "SUM"})
    _AGG_RE  = re.compile(r'^(?P<fn>\w+)\((?P<arg>[^)]*)\)$')

    @staticmethod
    def _parse_return_item(item: str) -> tuple[str, str | None]:
        """
        Parse a RETURN item string into (col_name, agg_fn_or_None).
        "COUNT(*)"     → ("COUNT(*)", "COUNT")
        "AVG(WEIGHT)"  → ("WEIGHT",   "AVG")
        "CONFIDENCE"   → ("CONFIDENCE", None)
        """
        item = item.strip()
        m = Connection._AGG_RE.match(item)
        if m:
            fn  = m.group("fn").upper()
            arg = m.group("arg").strip()
            if fn in Connection._AGG_FNS:
                return (item, fn)
        return (item, None)

    @classmethod
    def _build_col_ci(cls, columns: list[str]) -> dict[str, int]:
        """
        Build a case-insensitive column index mapping UPPER_NAME → row index.
        Handles both full column names ("ha.event_ts") and bare names ("event_ts"),
        with the full name taking priority over the bare name.
        """
        ci: dict[str, int] = {}
        # Pass 1: bare/stripped names (lower priority, added first)
        for i, c in enumerate(columns):
            bare = c.split(".", 1)[1] if "." in c else c
            ci.setdefault(bare.upper(), i)
        # Pass 2: full names (higher priority, may overwrite)
        for i, c in enumerate(columns):
            ci[c.upper()] = i
        return ci

    @classmethod
    def _apply_return(cls, columns: list[str], rows: list[list[Any]],
                       return_cols_str: str) -> tuple[list[str], list[list[Any]]]:
        """
        Apply RETURN projection / aggregation.
        Returns (new_columns, new_rows).

        Cypher implicit GROUP BY convention (same as Neo4j):
        - Pure aggregate RETURN (all items are functions) → single global summary row.
        - Mixed RETURN (some plain cols + some aggregates) → group by the plain
          columns, aggregate per group.  Plain columns become the implicit GROUP BY keys.
        """
        if not return_cols_str:
            return columns, rows

        items = [x.strip() for x in return_cols_str.split(",") if x.strip()]

        # Case-insensitive column lookup (C parser uppercases all names)
        col_ci = cls._build_col_ci(columns)

        def _resolve(arg: str) -> int | None:
            """Resolve a column arg (possibly alias.col) to its row index."""
            if "." in arg:
                arg = arg.split(".", 1)[1]
            return col_ci.get(arg.upper())

        def _compute_agg(fn: str, item: str, group_rows: list[list[Any]]) -> Any:
            """Apply an aggregate function over a slice of rows."""
            if fn == "COUNT":
                return len(group_rows)
            m = cls._AGG_RE.match(item)
            arg = m.group("arg").strip() if m else ""
            idx = _resolve(arg)
            if idx is None:
                return None
            vals = [r[idx] for r in group_rows if r[idx] is not None]
            try:
                fvals = [float(v) for v in vals]
            except (ValueError, TypeError):
                fvals = []
            if not fvals:
                return None
            if fn == "SUM":
                return sum(fvals)
            if fn == "AVG":
                return sum(fvals) / len(fvals)
            if fn == "MAX":
                return max(fvals)
            if fn == "MIN":
                return min(fvals)
            return None

        parsed = [cls._parse_return_item(it) for it in items]
        has_agg = any(fn is not None for _, fn in parsed)

        if has_agg:
            # Separate key (GROUP BY) columns from aggregate columns
            key_parsed = [(item, fn) for item, fn in parsed if fn is None]
            agg_parsed = [(item, fn) for item, fn in parsed if fn is not None]  # noqa: F841

            # Build output column names in the original RETURN order
            new_cols: list[str] = []
            for item, fn in parsed:
                if fn == "COUNT":
                    new_cols.append("COUNT(*)")
                elif fn in ("SUM", "AVG", "MAX", "MIN"):
                    m = cls._AGG_RE.match(item)
                    arg = m.group("arg").strip() if m else item
                    if "." in arg:
                        arg = arg.split(".", 1)[1]
                    new_cols.append(f"{fn}({arg})")
                else:
                    # Plain key column: use the source column's canonical name
                    idx = _resolve(item)
                    if idx is not None:
                        src = columns[idx]
                        new_cols.append(src.split(".", 1)[1] if "." in src else src)
                    else:
                        new_cols.append(item)

            if not key_parsed:
                # Pure global aggregate — single summary row
                new_row = [_compute_agg(fn, item, rows) if fn else None
                           for item, fn in parsed]
                return new_cols, [new_row]

            # Grouped aggregate: group by key column values, preserve insertion order
            from collections import defaultdict
            groups: "dict[tuple, list[list[Any]]]" = defaultdict(list)
            group_order: "list[tuple]" = []
            for row in rows:
                key = tuple(
                    row[_resolve(item)] if _resolve(item) is not None else None
                    for item, _ in key_parsed
                )
                if key not in groups:
                    group_order.append(key)
                groups[key].append(row)

            new_rows: list[list[Any]] = []
            for key in group_order:
                group_rows = groups[key]
                key_iter = iter(key)
                out_row: list[Any] = []
                for item, fn in parsed:
                    if fn is None:
                        out_row.append(next(key_iter))
                    else:
                        out_row.append(_compute_agg(fn, item, group_rows))
                new_rows.append(out_row)

            return new_cols, new_rows

        # Simple projection path — case-insensitive lookup (parser uppercases,
        # built-in column names are lowercase, user property names are uppercase).
        # For join results, columns are prefixed (e.g. "ha.event_ts").
        # Lookup priority:
        #   1. Exact match on full column name (e.g. "ha.event_ts" for item "ha.EVENT_TS")
        #   2. Exact match on stripped column name (e.g. "event_ts" for item "event_ts")
        #   3. Case-insensitive match on stripped name
        col_exact = {c: i for i, c in enumerate(columns)}
        col_idx_ci = {}
        for i, c in enumerate(columns):
            col_idx_ci.setdefault(c.upper(), i)
        # Also build index on the stripped (post-dot) part for alias.col lookups
        col_stripped_ci: dict[str, int] = {}
        for i, c in enumerate(columns):
            stripped = c.split(".", 1)[1] if "." in c else c
            col_stripped_ci.setdefault(stripped.upper(), i)

        out_cols: list[str] = []
        out_indices: list[int] = []
        used_indices: set[int] = set()  # avoid double-projecting the same column

        for item, _ in parsed:
            # Normalise: remove alias. prefix to get the bare column name
            bare = item.split(".", 1)[1] if "." in item else item

            # Priority 1: exact full-name match (handles prefixed join columns)
            # Try case-insensitive on full item first
            full_up = item.upper()
            idx: int | None = None
            for c, ci in col_exact.items():
                if c.upper() == full_up:
                    idx = ci
                    break

            # Priority 2: exact bare-name match
            if idx is None:
                for c, ci in col_exact.items():
                    stripped = c.split(".", 1)[1] if "." in c else c
                    if stripped == bare or stripped.upper() == bare.upper():
                        if ci not in used_indices:
                            idx = ci
                            break

            # Priority 3: case-insensitive stripped match
            if idx is None:
                idx = col_stripped_ci.get(bare.upper())

            if idx is None:
                continue  # unknown column — silently omit

            # Output column name: strip any alias prefix, use the original case
            # from the source column (e.g. "ha.event_ts" → "event_ts")
            src = columns[idx]
            out_col = src.split(".", 1)[1] if "." in src else src
            out_cols.append(out_col)
            out_indices.append(idx)
            used_indices.add(idx)

        if not out_cols:
            return columns, rows

        new_rows = [[row[i] for i in out_indices] for row in rows]
        return out_cols, new_rows

    @staticmethod
    def _apply_order(columns: list[str], rows: list[list[Any]],
                      order_col: str, order_desc: bool) -> list[list[Any]]:
        """Sort rows by the given column. Non-sortable values are pushed to end.

        order_col may be:
          - A bare column name: "EVENT_TS"
          - A prefixed name:    "HA.EVENT_TS" (join queries — stored by parser)
        Both forms are resolved against the columns list.
        """
        if not order_col or not rows:
            return rows

        # Build lookup maps
        col_exact = {c: i for i, c in enumerate(columns)}
        col_idx_ci: dict[str, int] = {}
        for i, c in enumerate(columns):
            col_idx_ci.setdefault(c.upper(), i)

        # Priority 1: exact full-name match (case-insensitive) — handles "ha.EVENT_TS"
        idx: int | None = None
        order_up = order_col.upper()
        for c, ci in col_exact.items():
            if c.upper() == order_up:
                idx = ci
                break

        # Priority 2: strip alias, case-insensitive bare-name match
        if idx is None:
            bare = order_col.split(".", 1)[1] if "." in order_col else order_col
            idx = col_idx_ci.get(bare.upper())

        if idx is None:
            return rows

        def sort_key(row: list[Any]):
            v = row[idx]
            if v is None:
                return (1, 0, "")
            try:
                return (0, float(v), "")
            except (ValueError, TypeError):
                return (0, 0, str(v))

        return sorted(rows, key=sort_key, reverse=order_desc)

    @staticmethod
    def _apply_skip(rows: list[list[Any]], skip_n: int) -> list[list[Any]]:
        """Drop the first *skip_n* rows (SKIP/OFFSET). Applied after ORDER BY and
        before LIMIT, matching Cypher/SQL semantics."""
        if skip_n > 0:
            return rows[skip_n:]
        return rows

    @staticmethod
    def _apply_limit(rows: list[list[Any]], limit_n: int) -> list[list[Any]]:
        if limit_n > 0:
            return rows[:limit_n]
        return rows

    def _post_process(self,
                       result: "QueryResult",
                       ast) -> "QueryResult":
        """
        Apply Phase 2 Python-layer transforms: predicate filtering,
        RETURN projection / aggregation, ORDER BY, and LIMIT.
        Updates QueryPlan with rows_scanned / rows_returned / predicates_applied.
        """
        cols = list(result.columns)
        rows = [list(r._values) for r in result]  # unpack Row objects → lists
        rows_scanned = len(rows)
        n_preds = len(ast.predicates) if ast.predicates else 0

        # 1. Predicate filtering
        if n_preds:
            rows = self._apply_predicates(cols, rows, ast.predicates)

        # 1b. Boolean WHERE (OR / NOT / <>) — evaluated Python-side. When set,
        # the WHERE was stripped before the C parser so ast.predicates is empty.
        bool_where = getattr(ast, "bool_where", None)
        if bool_where is not None:
            rows = self._apply_bool_where(cols, rows, bool_where)

        # 2. RETURN projection / aggregation
        if ast.return_cols:
            cols, rows = self._apply_return(cols, rows, ast.return_cols)

        # 3. ORDER BY
        if ast.order_col:
            rows = self._apply_order(cols, rows, ast.order_col, ast.order_desc)

        # 4. SKIP / OFFSET (before LIMIT)
        if getattr(ast, "skip_n", 0):
            rows = self._apply_skip(rows, ast.skip_n)

        # 5. LIMIT
        if ast.limit_n:
            rows = self._apply_limit(rows, ast.limit_n)

        rows_returned = len(rows)

        # Rebuild QueryPlan with updated stats
        old_plan = result.query_plan
        new_plan: "QueryPlan | None" = None
        if old_plan is not None:
            from dataclasses import replace
            new_plan = replace(
                old_plan,
                rows_scanned       = rows_scanned,
                rows_returned      = rows_returned,
                predicates_applied = n_preds,
            )

        return QueryResult(columns=cols, rows=rows, query_plan=new_plan)

    # ── Phase 7: Multi-table JOIN execution ──────────────────────────────

    def _execute_join(self, ast) -> "QueryResult":  # noqa: C901
        """
        Execute a two-pattern MATCH HYPEREDGE (...), HYPEREDGE (...) JOIN query.

        Execution strategy
        ------------------
        1.  Fetch candidates for each table independently, routing via TPI /
            FMI as appropriate.
        2.  For each candidate pair (r1, r2) evaluate join conditions:
               a. Cross-alias predicate conditions (e.g. ha.event_ts < hb.event_ts)
               b. Member-intersection: both rows share ≥ 1 member node
               c. Temporal-overlap: time ranges of the two hyperedges overlap
               d. General property conditions (cross-alias value comparisons)
        3.  Apply per-table property predicates (pred_alias aware).
        4.  Apply RETURN projection, ORDER BY, LIMIT (reusing single-table helpers).

        The merged result row has columns prefixed with the alias:
            alias1.col_name, alias2.col_name
        so the caller can distinguish them.  A RETURN * emits all prefixed
        columns.  RETURN alias.col strips to a bare col name in the output.

        Join semantics detected from the AST
        -------------------------------------
        • Member-intersection join:   node_id == node_id2 != 0
        • Independent FMI lookups:    node_id != 0 and node_id2 != 0 (different)
        • Temporal cross-join:        no node_id, both tables have ts bounds
        • Cross-join (no constraints): full cartesian — allowed but warned
        """
        from hypermesh_core.python.hm_store import HmPredOp

        alias1 = ast.alias.upper() if ast.alias else ast.table.upper()
        alias2 = ast.alias2.upper() if ast.alias2 else ast.table2.upper()
        UINT32_MAX = 0xFFFF_FFFF

        # ── 1. Fetch candidate rows for each table ────────────────────────
        def _fetch_table(table: str,
                         ts_start: int, ts_end: int,
                         node_id: int) -> "QueryResult":
            """Route table fetch: FMI if node_id set, TPI if range set, else scan."""
            if node_id:
                return self._fmi_lookup(node_id, table=table)
            if ts_end != UINT32_MAX or ts_start != 0:
                return self._tpi_range(ts_start, ts_end, table=table)
            return self._full_scan(table=table)

        res1 = _fetch_table(ast.table,  ast.ts_start,  ast.ts_end,  ast.node_id)
        res2 = _fetch_table(ast.table2, ast.ts_start2, ast.ts_end2, ast.node_id2)

        rows1 = [list(r._values) for r in res1]
        rows2 = [list(r._values) for r in res2]
        cols1 = list(res1.columns)
        cols2 = list(res2.columns)

        # ── 2. Apply per-table property predicates (pre-join pushdown) ────
        per_table_preds_1: list[dict] = []
        per_table_preds_2: list[dict] = []
        if ast.predicates:
            for pred in ast.predicates:
                pa = pred.get("alias", "").upper()
                if pa == alias2:
                    per_table_preds_2.append(pred)
                else:
                    # Assign to table1 if alias matches or is empty
                    per_table_preds_1.append(pred)

        if per_table_preds_1:
            rows1 = self._apply_predicates(cols1, rows1, per_table_preds_1)
        if per_table_preds_2:
            rows2 = self._apply_predicates(cols2, rows2, per_table_preds_2)

        # ── 3. Build case-insensitive column index helpers ─────────────────
        ci1 = {c.upper(): i for i, c in enumerate(cols1)}
        ci2 = {c.upper(): i for i, c in enumerate(cols2)}

        def _val1(row: list, col: str):
            return row[ci1[col.upper()]] if col.upper() in ci1 else None

        def _val2(row: list, col: str):
            return row[ci2[col.upper()]] if col.upper() in ci2 else None

        # ── 4. Evaluate a single join condition ───────────────────────────
        def _eval_join_cond(jc: dict, r1: list, r2: list) -> bool:
            """Evaluate one cross-alias predicate against a candidate pair."""
            la = jc["lhs_alias"].upper()
            lc = jc["lhs_col"].upper()
            ra = jc["rhs_alias"].upper()
            rc = jc["rhs_col"].upper()
            op = jc["op"]

            lv = (_val1(r1, lc) if la == alias1 else _val2(r2, lc))
            rv = (_val1(r1, rc) if ra == alias1 else _val2(r2, rc))

            if lv is None or rv is None:
                return False
            try:
                lv_f = float(lv)
                rv_f = float(rv)
            except (ValueError, TypeError):
                # String comparison fallback
                lv_f = str(lv)  # type: ignore[assignment]
                rv_f = str(rv)  # type: ignore[assignment]

            if   op == HmPredOp.EQ:  return lv_f == rv_f
            elif op == HmPredOp.GT:  return lv_f >  rv_f
            elif op == HmPredOp.GEQ: return lv_f >= rv_f
            elif op == HmPredOp.LT:  return lv_f <  rv_f
            elif op == HmPredOp.LEQ: return lv_f <= rv_f
            return lv_f == rv_f

        join_conds = ast.join_predicates or []

        # Determine if member-intersection auto-join should be applied:
        # Triggered when both tables were queried via FMI with the SAME node_id.
        member_intersect_join = (
            ast.node_id != 0
            and ast.node_id2 != 0
            and ast.node_id == ast.node_id2
        )

        # ── 5. Nested-loop join ───────────────────────────────────────────
        merged_cols = (
            [f"{alias1.lower()}.{c}" for c in cols1] +
            [f"{alias2.lower()}.{c}" for c in cols2]
        )
        merged_rows: list[list[Any]] = []

        for r1 in rows1:
            # Pre-compute members set for r1 if we need member-intersection
            members1_set: set[int] | None = None
            if member_intersect_join or not join_conds:
                raw_m1 = _val1(r1, "members")
                if isinstance(raw_m1, list):
                    members1_set = set(raw_m1)

            for r2 in rows2:
                # a. Explicit cross-alias join conditions
                if join_conds:
                    if not all(_eval_join_cond(jc, r1, r2) for jc in join_conds):
                        continue

                # b. Member-intersection auto-join
                #    Applied when same FMI node was used AND no explicit join conditions
                elif member_intersect_join and members1_set is not None:
                    raw_m2 = _val2(r2, "members")
                    members2_set = set(raw_m2) if isinstance(raw_m2, list) else set()
                    if not (members1_set & members2_set):
                        continue

                # c. No join conditions at all: emit every pair (cartesian)
                #    This is only reached when there are neither explicit join
                #    conditions nor member-intersection constraints.

                merged_rows.append(r1 + r2)

        rows_scanned = len(rows1) * len(rows2)

        # ── 6. Apply RETURN projection, ORDER BY, LIMIT ───────────────────
        out_cols = merged_cols
        out_rows = merged_rows

        if ast.return_cols:
            # For join queries, RETURN items may be prefixed (alias.col) or bare.
            # _apply_return handles both via case-insensitive lookup.
            out_cols, out_rows = self._apply_return(out_cols, out_rows, ast.return_cols)

        if ast.order_col:
            out_rows = self._apply_order(out_cols, out_rows, ast.order_col, ast.order_desc)

        if getattr(ast, "skip_n", 0):
            out_rows = self._apply_skip(out_rows, ast.skip_n)

        if ast.limit_n:
            out_rows = self._apply_limit(out_rows, ast.limit_n)

        from ._types import QueryPlan
        plan = QueryPlan(
            strategy           = f"JOIN({ast.table}×{ast.table2})",
            buckets_scanned    = 0,
            total_buckets      = 0,
            speedup_factor     = 1.0,
            elapsed_us         = 0,
            rows_scanned       = rows_scanned,
            rows_returned      = len(out_rows),
            predicates_applied = len(join_conds),
        )

        from ._result import QueryResult
        return QueryResult(columns=out_cols, rows=out_rows, query_plan=plan)

    # ── Phase 3: DML helpers ──────────────────────────────────────────────

    #: Built-in column names recognised in INSERT/UPDATE col lists (uppercase)
    _BUILTIN_COLS: frozenset[str] = frozenset({
        "EVENT_TS", "MEMBERS", "WEIGHT", "MEAN_DIST_M", "FORMATION",
    })

    @staticmethod
    def _coerce_value(raw: str, col_name: str,
                       prop_cols: "list[ColumnDef]") -> Any:
        """
        Coerce a raw string value from the parser into the correct Python type.
        For built-in cols uses heuristics; for user-defined cols uses the schema.
        Returns the coerced value, or raises HyperMeshError on type mismatch.
        """
        col_up = col_name.upper()

        if col_up == "EVENT_TS":
            try: return int(raw)
            except ValueError: raise HyperMeshError(f"event_ts must be integer, got {raw!r}")

        if col_up == "MEMBERS":
            # Accept "[1,2,3]" or "1,2,3"
            s = raw.strip().lstrip("[").rstrip("]")
            try: return [int(x.strip()) for x in s.split(",") if x.strip()]
            except ValueError: raise HyperMeshError(f"members must be int list, got {raw!r}")

        if col_up in ("WEIGHT", "MEAN_DIST_M"):
            try: return float(raw)
            except ValueError: raise HyperMeshError(f"{col_name} must be numeric, got {raw!r}")

        if col_up == "FORMATION":
            if raw.upper() == "NULL": return ""
            return str(raw)

        # User-defined property column
        schema_map = {c.name.upper(): c for c in prop_cols}
        if col_up in schema_map:
            return schema_map[col_up].coerce(None if raw.upper() == "NULL" else raw)

        # Unknown column — pass through as string
        return raw

    def _dml_result(self, rows_affected: int, operation: str) -> "QueryResult":
        """Return a standard single-row DML result."""
        return QueryResult(
            columns=["operation", "rows_affected"],
            rows=[[operation, rows_affected]],
            query_plan=None,
        )

    def _dml_insert(self, ast) -> "QueryResult":
        """
        Execute:  INSERT INTO <table> (<cols>) VALUES (<vals>)

        Algorithm:
        1. Split insert_cols (comma-sep) and insert_vals (0x1F-sep).
        2. Coerce each value using the SchemaStore property schema.
        3. Separate built-in fields from user-defined property fields.
        4. Serialize properties via _serialize_props.
        5. Call store.insert_v2 with the assembled record.
        """
        self._check_open()
        table = ast.table

        if not ast.insert_cols or not ast.insert_vals:
            raise HyperMeshError("INSERT: missing column list or VALUES clause")

        cols = [c.strip() for c in ast.insert_cols.split(",") if c.strip()]
        vals = ast.insert_vals.split(HM_FIELD_SEP)

        if len(cols) != len(vals):
            raise HyperMeshError(
                f"INSERT: {len(cols)} columns but {len(vals)} values"
            )

        # Get user-defined property schema
        prop_cols: list[ColumnDef] = []
        if self._schema:
            try: prop_cols = self._schema.get_hyperedge_columns(table)
            except Exception: pass

        col_val: dict[str, Any] = {}
        for col, raw in zip(cols, vals):
            col_val[col.upper()] = self._coerce_value(raw, col, prop_cols)

        # Extract built-in fields with defaults
        event_ts    = col_val.get("EVENT_TS", 0)
        members     = col_val.get("MEMBERS",  [])
        weight      = float(col_val.get("WEIGHT", 0.0))
        mean_dist_m = float(col_val.get("MEAN_DIST_M", 0.0))
        formation   = str(col_val.get("FORMATION", ""))

        if not members:
            raise HyperMeshError("INSERT: MEMBERS must be a non-empty list")

        # Build props dict from user-defined columns only
        props_dict: dict[str, Any] = {
            c.upper(): col_val[c.upper()]
            for c in col_val
            if c not in self._BUILTIN_COLS and c in {p.name.upper() for p in prop_cols}
        }

        props_bytes: bytes | None = None
        if prop_cols and props_dict:
            props_bytes = self._serialize_props(props_dict, prop_cols)

        self._get_store(ast.table).insert_v2(
            event_ts    = int(event_ts),
            members     = members,
            weight      = weight,
            mean_dist_m = mean_dist_m,
            formation   = formation,
            props       = props_bytes,
        )
        return self._dml_result(1, "INSERT")

    def _dml_delete(self, ast) -> "QueryResult":
        """
        Execute:  DELETE FROM <table> WHERE <where-expr>

        Algorithm:
        1. Run the equivalent MATCH query (TPI range / FMI / full-scan)
           using the WHERE clause already parsed into ast.ts_start, ts_end,
           predicates, etc.
        2. Apply property predicate filtering (Phase 2 _apply_predicates).
        3. For each surviving row call store.delete(event_ts, members).
        """
        self._check_open()
        table = ast.table

        # Run the equivalent MATCH to find all matching records
        raw_result = self._execute_match_for_dml(ast)
        if raw_result is None:
            return self._dml_result(0, "DELETE")

        cols  = list(raw_result.columns)
        rows  = [list(r._values) for r in raw_result]

        # Apply property predicates (Phase 2 reuse)
        if ast.predicates:
            rows = self._apply_predicates(cols, rows, ast.predicates)

        col_idx = {c: i for i, c in enumerate(cols)}
        store   = self._get_store(table)
        deleted = 0
        for row in rows:
            ts  = int(row[col_idx["event_ts"]])
            mem = list(row[col_idx["members"]])
            store.delete(event_ts=ts, members=mem)
            deleted += 1

        return self._dml_result(deleted, "DELETE")

    def _dml_update(self, ast, where_extra: "dict | None" = None) -> "QueryResult":
        """
        Execute:  UPDATE <table> SET col=val [, ...] WHERE <where-expr>

        Algorithm (emulated as DELETE + re-INSERT on the WAL):
        1. Run the equivalent MATCH and apply predicates.
        2. Parse update_set: alternating COL\\x1Fval pairs.
        3. For each matching row:
           a. Deserialize existing property blob.
           b. Apply the SET updates.
           c. Delete the old record.
           d. Insert the updated record.

        Note: Updated records are written to the WAL and are not visible to
        range queries until :meth:`compact` is called.  If the WHERE clause
        matches no records, ``rows_affected`` in the result will be 0 — callers
        should check this value rather than assuming success.
        """
        self._check_open()
        table = ast.table

        if not ast.update_set:
            raise HyperMeshError("UPDATE: empty SET clause")

        # Parse SET clause: COL\x1Fval\x1FCOL\x1Fval ...
        parts = ast.update_set.split(HM_FIELD_SEP)
        if len(parts) % 2 != 0:
            raise HyperMeshError("UPDATE: malformed SET clause (odd field count)")
        set_map = {parts[i].upper(): parts[i + 1] for i in range(0, len(parts), 2)}

        # Get user-defined property schema
        prop_cols: list[ColumnDef] = []
        if self._schema:
            try: prop_cols = self._schema.get_hyperedge_columns(table)
            except Exception: pass

        # Find matching records
        raw_result = self._execute_match_for_dml(ast)
        if raw_result is None:
            return self._dml_result(0, "UPDATE")

        cols = list(raw_result.columns)
        rows = [list(r._values) for r in raw_result]
        if ast.predicates:
            rows = self._apply_predicates(cols, rows, ast.predicates)

        # Python-side members equality filter (C parser never captures this predicate)
        if where_extra and "members" in where_extra:
            target = set(where_extra["members"])
            mem_idx = cols.index("members") if "members" in cols else -1
            if mem_idx >= 0:
                rows = [r for r in rows if set(r[mem_idx]) == target]

        col_idx = {c: i for i, c in enumerate(cols)}
        updated = 0

        for row in rows:
            ts      = int(row[col_idx["event_ts"]])
            members = list(row[col_idx["members"]])

            # Extract current built-in fields
            weight      = float(row[col_idx.get("weight",      col_idx.get("WEIGHT",      1))])
            mean_dist_m = float(row[col_idx.get("mean_dist_m", col_idx.get("MEAN_DIST_M", 1))])
            formation   = str  (row[col_idx.get("formation",   col_idx.get("FORMATION",   4))])

            # Read existing property values directly from the decoded row.
            # _range_result_to_rows already decodes the binary blob into named
            # columns, so we simply copy those values into existing_props.
            existing_props: dict[str, Any] = {}
            if prop_cols:
                col_idx_ci = {c.upper(): i for i, c in enumerate(cols)}
                for pc in prop_cols:
                    pc_up = pc.name.upper()
                    if pc_up in col_idx_ci:
                        existing_props[pc_up] = row[col_idx_ci[pc_up]]

            # Apply SET updates to built-in fields
            if "EVENT_TS" in set_map:
                ts = int(set_map["EVENT_TS"])
            if "MEMBERS" in set_map:
                s = set_map["MEMBERS"].strip().lstrip("[").rstrip("]")
                members = [int(x.strip()) for x in s.split(",") if x.strip()]
            if "WEIGHT" in set_map:
                weight = float(set_map["WEIGHT"])
            if "MEAN_DIST_M" in set_map:
                mean_dist_m = float(set_map["MEAN_DIST_M"])
            if "FORMATION" in set_map:
                formation = set_map["FORMATION"]

            # Apply SET updates to user-defined property columns
            prop_schema_names = {p.name.upper() for p in prop_cols}
            for col_up, raw_val in set_map.items():
                if col_up in prop_schema_names:
                    existing_props[col_up] = self._coerce_value(raw_val, col_up, prop_cols)

            # Serialize updated properties
            props_bytes: bytes | None = None
            if prop_cols and existing_props:
                props_bytes = self._serialize_props(existing_props, prop_cols)

            # Delete old record and re-insert with updated values
            dml_store = self._get_store(ast.table)
            dml_store.delete(event_ts=int(row[col_idx["event_ts"]]),
                             members=list(row[col_idx["members"]]))
            dml_store.insert_v2(
                event_ts    = ts,
                members     = members,
                weight      = weight,
                mean_dist_m = mean_dist_m,
                formation   = formation,
                props       = props_bytes,
            )
            updated += 1

        return self._dml_result(updated, "UPDATE")

    def _execute_match_for_dml(self, ast) -> "QueryResult | None":
        """
        Internal helper: run the MATCH equivalent of a DML WHERE clause.
        parse_where populates ts_start / ts_end / node_id even for DELETE/UPDATE
        (before the DML parser overrides ast.kind).  We use those stored values
        to choose the right scan strategy.
        """
        table = ast.table
        try:
            ts_start = ast.ts_start
            ts_end   = ast.ts_end
            node_id  = ast.node_id

            # FMI: node_id set and no meaningful time range
            if node_id and ts_start == 0 and ts_end == 0:
                return self._fmi_lookup(node_id, table=table)

            # TPI range or full scan (ts_end == 0 means parse_where saw no bound)
            ts1 = ts_end if ts_end else 0xFFFFFFFF
            return self._tpi_range(ts_start, ts1, table=table)
        except Exception:
            return self._full_scan(table=table)

    # ── Phase 4: PSI helpers ──────────────────────────────────────────────

    @staticmethod
    def _psi_col_type(col_def: "ColumnDef") -> int:
        """Map a ColumnDef to HM_PSI_FLOAT or HM_PSI_INT."""
        return HM_PSI_INT if col_def.col_type == "INTEGER" else HM_PSI_FLOAT

    def _rebuild_psi(self, table: str) -> None:
        """
        Rebuild all PSI files for *table* from the current TPI records.

        Algorithm:
        1. Full TPI scan to fetch all hyperedges for this table.
        2. Deserialize property blobs.
        3. For each registered index column, build a sorted (value, event_ts)
           array and write it to disk via hm_psi_write.

        Called after compact() and after DROP INDEX (to remove the file).
        """
        if not self._schema:
            return
        try:
            indexes = self._schema.list_indexes(table)
        except Exception:
            return

        if not indexes:
            return

        prop_cols = []
        try:
            prop_cols = self._schema.get_hyperedge_columns(table)
        except Exception:
            return

        if not prop_cols:
            return

        # Fetch all records via a full TPI scan
        try:
            raw = self._full_scan(table=table)
        except Exception:
            return

        cols  = list(raw.columns)
        rows  = [list(r._values) for r in raw]
        col_idx_ci = {c.upper(): i for i, c in enumerate(cols)}

        import ctypes
        from hypermesh_core.python import hm_store as _hms

        dir_bytes = self._table_dir(table).encode("utf-8")

        for idx_info in indexes:
            col_name = idx_info["col_name"]  # already uppercased in DB
            # Find the ColumnDef
            col_def = next(
                (c for c in prop_cols if c.name.upper() == col_name), None
            )
            if col_def is None:
                continue

            col_type = self._psi_col_type(col_def)

            # Build (value, event_ts) pairs from all rows
            Entry = ctypes.c_float * 2  # placeholder; we'll use struct instead

            # Collect valid (value, event_ts) pairs
            pairs: list[tuple[float, int]] = []
            # Column names in results may be lowercase (schema-defined) or uppercase
            col_i = col_idx_ci.get(col_name.upper(),
                    col_idx_ci.get(col_name.lower()))
            if col_i is None:
                continue

            ts_i = col_idx_ci.get("EVENT_TS", col_idx_ci.get("event_ts", 0))
            for row in rows:
                try:
                    val = float(row[col_i])
                    ts  = int(row[ts_i])
                    pairs.append((val, ts))
                except (TypeError, ValueError, KeyError):
                    continue

            # Build C array of HmPsiEntry {float value, uint32 event_ts}
            # HmPsiEntry is 8 bytes: 4-byte float + 4-byte uint32
            n = len(pairs)
            PsiEntryArray = (ctypes.c_uint8 * (8 * n)) if n > 0 else (ctypes.c_uint8 * 1)
            buf = PsiEntryArray()
            import struct
            for i, (val, ts) in enumerate(pairs):
                packed = struct.pack("<fI", val, ts)
                buf[i * 8: i * 8 + 8] = (ctypes.c_uint8 * 8)(*packed)

            ptr = ctypes.cast(buf, ctypes.c_void_p) if n > 0 else None

            rc = _hms._lib.hm_psi_write(
                dir_bytes,
                table.upper().encode("utf-8"),
                col_name.upper().encode("utf-8"),
                ctypes.c_uint8(col_type),
                ptr,
                ctypes.c_uint32(n),
            )
            if rc != 0:
                import sys
                print(f"[hypermesh] WARNING: hm_psi_write failed for {table}.{col_name}",
                      file=sys.stderr)

    def _psi_lookup(
        self,
        table: str,
        col:   str,
        op:    int,
        val:   float,
        max_results: int = 1_000_000,
    ) -> "list[int] | None":
        """
        Look up matching event_ts values via the PSI for (table, col).
        Returns a sorted list of event_ts ints, or None if the PSI is not
        available (caller falls back to full-scan + linear filter).

        op: HM_PSI_EQ/GT/GEQ/LT/LEQ (same numeric codes as HM_PRED_*)
        """
        import ctypes
        from hypermesh_core.python import hm_store as _hms

        dir_bytes = self._table_dir(table).encode("utf-8")

        exists = _hms._lib.hm_psi_exists(
            dir_bytes,
            table.upper().encode("utf-8"),
            col.upper().encode("utf-8"),
        )
        if not exists:
            return None

        out_ts_arr   = (ctypes.c_uint32 * max_results)()
        out_count    = ctypes.c_uint32(0)

        rc = _hms._lib.hm_psi_lookup(
            dir_bytes,
            table.upper().encode("utf-8"),
            col.upper().encode("utf-8"),
            ctypes.c_uint8(op),
            ctypes.c_float(val),
            out_ts_arr,
            ctypes.c_uint32(max_results),
            ctypes.byref(out_count),
        )

        if rc == -1:
            return None   # PSI not found or corrupt → fall back

        count = int(out_count.value)
        return [int(out_ts_arr[i]) for i in range(min(count, max_results))]

    def _post_process_with_psi(
        self,
        table:      str,
        result:     "QueryResult",
        ast,
    ) -> "QueryResult":
        """
        Phase 4 override of _post_process: if any predicate column has a PSI,
        use it to restrict the event_ts set before deserializing property blobs
        for non-matching rows.

        Algorithm:
        1. Split predicates into PSI-indexed and non-indexed groups.
        2. For each PSI-indexed predicate, call _psi_lookup → get allowed_ts set.
        3. Intersect allowed_ts sets (AND semantics across multiple PSI predicates).
        4. Filter raw rows by allowed_ts (O(1) per row via set lookup).
        5. Apply remaining non-indexed predicates linearly.
        6. Apply RETURN / ORDER BY / LIMIT as normal.
        """
        if not self._schema or not ast.predicates:
            return self._post_process(result, ast)

        # Check which predicate columns have PSI files
        try:
            indexes = {idx["col_name"] for idx in self._schema.list_indexes(table)}
        except Exception:
            indexes = set()

        if not indexes:
            return self._post_process(result, ast)

        cols = list(result.columns)
        rows = [list(r._values) for r in result]
        rows_scanned = len(rows)

        # Case-insensitive index lookup: column names in schema are uppercase,
        # but Cypher predicates use whatever case the user wrote.
        indexes_up = {c.upper() for c in indexes}

        # Split predicates — PSI predicates are used for timestamp pre-filtering;
        # non-PSI predicates are verified linearly for correctness.
        psi_preds  = [p for p in ast.predicates if p["col"].upper() in indexes_up]
        rest_preds = [p for p in ast.predicates if p["col"].upper() not in indexes_up]

        # Build allowed_ts set from PSI lookups (intersect)
        allowed_ts: "set[int] | None" = None

        col_idx_ci = {c.upper(): i for i, c in enumerate(cols)}
        ts_col = col_idx_ci.get("EVENT_TS", 0)

        for pred in psi_preds:
            psi_op  = _PRED_OP_TO_PSI.get(pred["op"], HM_PSI_EQ)
            psi_val = float(pred["val"])
            result_ts = self._psi_lookup(table, pred["col"], psi_op, psi_val)
            if result_ts is None:
                # PSI file absent for this column — fall back to linear check
                rest_preds.append(pred)
                continue
            ts_set = set(result_ts)
            if allowed_ts is None:
                allowed_ts = ts_set
            else:
                allowed_ts &= ts_set   # AND semantics

        # Timestamp pre-filter: eliminates rows that cannot possibly match (O(1)).
        if allowed_ts is not None:
            rows = [r for r in rows if int(r[ts_col]) in allowed_ts]

        # Apply non-PSI predicates linearly for correctness
        if rest_preds:
            rows = self._apply_predicates(cols, rows, rest_preds)

        # Apply Phase 2 RETURN / ORDER BY / LIMIT
        if ast.return_cols:
            cols, rows = self._apply_return(cols, rows, ast.return_cols)
        if ast.order_col:
            rows = self._apply_order(cols, rows, ast.order_col, ast.order_desc)
        if getattr(ast, "skip_n", 0):
            rows = self._apply_skip(rows, ast.skip_n)
        if ast.limit_n:
            rows = self._apply_limit(rows, ast.limit_n)

        rows_returned  = len(rows)
        n_preds_total  = len(ast.predicates)

        old_plan = result.query_plan
        if old_plan is not None:
            from dataclasses import replace
            new_plan = replace(
                old_plan,
                rows_scanned       = rows_scanned,
                rows_returned      = rows_returned,
                predicates_applied = n_preds_total,
            )
        else:
            new_plan = None

        from ._result import QueryResult as _QR
        _RowCls = type(next(iter(result), None)) if rows else None

        return QueryResult(columns=cols, rows=rows, query_plan=new_plan)

    def _tpi_range(self, start_ts: int, end_ts: int,
                    table: str = "") -> QueryResult:
        result = self._get_store(table or self._primary_table).range_query(start_ts, end_ts)
        cols, rows = self._range_result_to_rows(result, table)
        return QueryResult(
            columns    = cols,
            rows       = rows,
            query_plan = self._make_plan(result.plan),
        )

    def _full_scan(self, table: str = "") -> QueryResult:
        result = self._get_store(table or self._primary_table).range_query(0, 0xFFFF_FFFF)
        cols, rows = self._range_result_to_rows(result, table)
        return QueryResult(
            columns    = cols,
            rows       = rows,
            query_plan = self._make_plan(result.plan),
        )

    def _fmi_lookup(self, node_id: int, table: str = "") -> QueryResult:
        """
        FMI point lookup via C engine.

        Delegates entirely to hm_fmi_query() which implements:
          1. O(log N + degree): FMI binary search → sorted seq-IDs
          2. Sequential scan of hyperedges.bin collecting only those records,
             with early-exit once all seq-IDs are found
          3. WAL tombstones applied to TPI results
          4. WAL INSERT records containing node_id appended

        plan.strategy == "FMI_LOOKUP", plan.speedup_factor == N / degree.
        """
        result = self._get_store(table or self._primary_table).fmi_query(node_id)
        cols, rows = self._range_result_to_rows(result, table)
        return QueryResult(
            columns = cols,
            rows    = rows,
        )

    def _show_tables(self) -> QueryResult:
        tables = self._schema.list_hyperedge_tables()
        rows = [
            [
                t["name"],
                t["member_tables"],
                t["bucket_seconds"],
                t.get("compact_threshold", 0) or 0,
                (
                    self._stores[t["name"].upper()].total_records
                    if t["name"].upper() in self._stores else 0
                ),
            ]
            for t in tables
        ]
        return QueryResult(
            columns = ["name", "member_tables", "bucket_seconds",
                       "compact_threshold", "row_count"],
            rows    = rows,
        )

    # ── Node store helper ─────────────────────────────────────────────────

    def _get_node_store(self, table: str) -> NodeStore:
        """
        Return (or lazily create) the NodeStore for *table*.
        The Drone store is always available; other tables are created on
        first access if their schema is registered in SchemaStore.
        """
        if table in self._node_stores:
            return self._node_stores[table]
        columns = self._schema.get_node_columns(table)
        if not columns:
            raise HyperMeshError(
                f"No column definitions found for node table {table!r}. "
                f"Call CREATE NODE TABLE {table} (...) first."
            )
        db_name = "nodes.db" if table == "Drone" else f"nodes_{table}.db"
        db_path = os.path.join(self._dir_path, db_name)
        store   = NodeStore(db_path, table, columns)
        self._node_stores[table] = store
        if table == "Drone":
            self._nodes = store
        return store

    # ── CREATE NODE TABLE DDL ─────────────────────────────────────────────

    def _create_node_table(self, name: str, cols_str: str) -> QueryResult:
        """
        Parse column definitions and register a new node table in SchemaStore.

        Syntax::

            CREATE NODE TABLE Machine (
                machine_id INTEGER PRIMARY KEY,
                hostname   TEXT,
                os         TEXT DEFAULT '',
                role       TEXT DEFAULT ''
            )
        """
        columns: list[ColumnDef] = []
        for part in cols_str.split(","):
            part = part.strip()
            if not part:
                continue
            m = _COL_DEF_RE.match(part)
            if not m:
                raise HyperMeshError(
                    f"Cannot parse column definition: {part!r}. "
                    f"Expected: <name> INTEGER|REAL|TEXT [PRIMARY KEY] [DEFAULT val]"
                )
            columns.append(ColumnDef(
                name        = m.group("col_name"),
                col_type    = m.group("col_type").upper(),
                is_pk       = bool(m.group("is_pk")),
                nullable    = not bool(m.group("not_null")),
                default_val = m.group("default_val"),
            ))

        if not columns:
            raise HyperMeshError(f"CREATE NODE TABLE {name}: no column definitions found.")
        pks = [c for c in columns if c.is_pk]
        if len(pks) != 1:
            raise HyperMeshError(
                f"CREATE NODE TABLE {name}: exactly one column must be PRIMARY KEY, "
                f"got {len(pks)}."
            )

        try:
            self._schema.create_node_table(name, description=f"User-defined node table")
        except ValueError:
            raise HyperMeshError(f"Node table {name!r} already exists.")
        self._schema.define_node_columns(name, columns)

        return QueryResult(
            columns = ["name", "columns", "status"],
            rows    = [[name, [c.name for c in columns], "created"]],
        )

    def _create_table(
        self,
        name:              str,
        members_csv:       str,
        bucket_seconds:    int,
        props_csv:         str = "",
        compact_threshold: int = 0,
    ) -> QueryResult:
        member_list = (
            [m.strip() for m in members_csv.split(",") if m.strip()]
            if members_csv else []
        )
        # Fall back to primary table's bucket width when the DDL omits BUCKET_SECONDS
        primary_store = self._stores.get(self._primary_table.upper())
        default_bs    = primary_store.bucket_seconds if primary_store else 10
        bs = bucket_seconds if bucket_seconds > 0 else default_bs
        try:
            table = self._schema.create_hyperedge_table(
                name              = name,
                member_tables     = member_list,
                bucket_seconds    = bs,
                compact_threshold = compact_threshold,
            )
        except ValueError as exc:
            # Table already exists in the schema catalog.  This can happen
            # legitimately when the user creates a table that seed_defaults()
            # pre-registered (e.g. "CoProximity") but never built on disk.
            # If the table has no open store yet, proceed to open/create one.
            # If it already has a store open, raise as a genuine conflict.
            key = name.upper()
            if key in self._stores:
                raise HyperMeshError(str(exc)) from exc
            # Re-fetch the existing schema entry to use for the store
            existing = next(
                (t for t in self._schema.list_hyperedge_tables()
                 if t["name"].upper() == key),
                None,
            )
            if existing is None:
                raise HyperMeshError(str(exc)) from exc
            table = existing

        # V2: register property column definitions from PROPERTIES clause
        if props_csv and props_csv.strip():
            try:
                cols = SchemaStore.parse_props_csv(props_csv)
                if cols:
                    self._schema.define_hyperedge_columns(name, cols)
            except ValueError as exc:
                raise HyperMeshError(
                    f"CREATE HYPEREDGE TABLE {name}: invalid PROPERTIES — {exc}"
                ) from exc

        # Phase 6: create the per-table partition directory + empty store
        key = name.upper()
        if key not in self._stores:
            self._stores[key] = self._open_table_store(key, bs)

        # If _primary_table still points to a phantom table (seeded by
        # seed_defaults but never physically created), promote this new table
        # so that insert() / delete() work without requiring an explicit table arg.
        if self._primary_table.upper() not in self._stores:
            self._primary_table = name

        prop_cols = self._schema.get_hyperedge_columns(name)
        return QueryResult(
            columns = ["name", "member_tables", "bucket_seconds",
                       "compact_threshold", "created_at", "property_columns"],
            rows    = [[table["name"], table["member_tables"],
                        table["bucket_seconds"], table["compact_threshold"],
                        table["created_at"], [c.name for c in prop_cols]]],
        )

    def _drop_table(self, name: str) -> QueryResult:
        dropped = self._schema.drop_hyperedge_table(name)

        # Phase 6: close and remove the physical partition directory
        import shutil
        key = name.upper()
        store = self._stores.pop(key, None)
        if store:
            try:
                store.close()
            except Exception:
                pass
        table_dir = self._table_dir(name)
        if os.path.isdir(table_dir):
            shutil.rmtree(table_dir, ignore_errors=True)

        return QueryResult(
            columns = ["name", "dropped"],
            rows    = [[name, dropped]],
        )

    # ── Phase 4: PSI index DDL handlers ──────────────────────────────────

    def _create_index(self, table: str, col: str) -> QueryResult:
        """
        CREATE INDEX ON <table> (<col>)

        Registers the index in the schema catalog, then immediately builds the
        PSI file from the current TPI state (requires a prior compact() for
        pending WAL entries to be visible).
        """
        self._check_open()
        if not self._schema:
            raise HyperMeshError("CREATE INDEX: no schema store available")

        created = self._schema.create_index(table, col)
        action  = "created" if created else "already_exists"

        if created:
            # Build PSI immediately from the current TPI
            self._rebuild_psi(table)

        return QueryResult(
            columns = ["table", "column", "status"],
            rows    = [[table, col, action]],
        )

    def _drop_index(self, table: str, col: str) -> QueryResult:
        """
        DROP INDEX ON <table> (<col>)

        Removes the PSI file from disk and the registration from the catalog.
        """
        self._check_open()
        if not self._schema:
            raise HyperMeshError("DROP INDEX: no schema store available")

        dropped = self._schema.drop_index(table, col)

        if dropped:
            # Remove the PSI file from the table's partition directory
            import ctypes
            from hypermesh_core.python import hm_store as _hms
            dir_bytes = self._table_dir(table).encode("utf-8")
            _hms._lib.hm_psi_remove(
                dir_bytes,
                table.upper().encode("utf-8"),
                col.upper().encode("utf-8"),
            )

        return QueryResult(
            columns = ["table", "column", "dropped"],
            rows    = [[table, col, dropped]],
        )

    def _show_indexes(self, table: str = "") -> QueryResult:
        """
        CALL show_indexes() / SHOW INDEXES [ON <table>]
        """
        self._check_open()
        if not self._schema:
            return QueryResult(columns=["table", "column", "created_at"], rows=[])

        rows = self._schema.list_indexes(table)
        return QueryResult(
            columns = ["table", "column", "created_at"],
            rows    = [[r["table_name"], r["col_name"], r["created_at"]] for r in rows],
        )

    # ── COPY FROM CSV ─────────────────────────────────────────────────────

    def copy_from_csv(
        self,
        table:         str,
        path:          str,
        *,
        header:        bool | None = None,
        delimiter:     str         = ",",
        quotechar:     str         = '"',
        skip:          int         = 0,
        ignore_errors: bool        = False,
    ) -> QueryResult:
        """
        Bulk-load records from a CSV file into a HyperMesh DB table.

        This is the programmatic equivalent of executing::

            COPY <table> FROM '<path>' (HEADER=true, DELIM=',')

        The method auto-detects whether *table* is a hyperedge table or a node
        table from the first row's column names, then dispatches to the
        appropriate loader.

        Parameters
        ----------
        table:
            Name of the target hyperedge or node table.
        path:
            Absolute or relative path to the CSV file to read.
        header:
            ``True`` → first row is a header and is skipped as data.
            ``False`` → no header; columns are assumed in canonical order.
            ``None`` (default) → auto-detect: first row is treated as a
            header if its first cell cannot be parsed as a number.
        delimiter:
            Column separator character.  Default: ``,``.
        quotechar:
            Quoting character for string fields.  Default: ``"``.
        skip:
            Number of leading rows to discard **before** the header (or data
            if there is no header).  Default: ``0``.
        ignore_errors:
            When ``True``, malformed rows are silently skipped and counted in
            ``rows_skipped`` instead of raising :class:`HyperMeshError`.
            Default: ``False``.

        Returns
        -------
        QueryResult
            Single-row result with columns:
            ``table``, ``rows_loaded``, ``rows_skipped``, ``elapsed_ms``.

        Raises
        ------
        HyperMeshError
            If the file does not exist, is empty, has missing required columns,
            or contains malformed data (and ``ignore_errors=False``).

        Examples
        --------
        >>> # Via execute() DDL
        >>> db.execute("COPY CoProximity FROM 'edges.csv' (HEADER=true)")

        >>> # Or call directly:
        >>> result = db.copy_from_csv(
        ...     "CoProximity", "edges.csv", header=True, ignore_errors=True
        ... )
        >>> print(result.fetchone()["rows_loaded"], "rows loaded")
        """
        self._check_open()

        if skip < 0:
            raise HyperMeshError("COPY option SKIP must be >= 0")

        if not os.path.exists(path):
            raise HyperMeshError(
                f"COPY FROM: file not found: {path!r}"
            )

        t0 = time.perf_counter()

        try:
            raw_lines = open(path, newline="", encoding="utf-8").read()
        except OSError as exc:
            raise HyperMeshError(f"COPY FROM: cannot read {path!r}: {exc}") from exc

        reader = _csv_mod.reader(
            io.StringIO(raw_lines),
            delimiter = delimiter,
            quotechar = quotechar,
        )

        # Materialise all rows so we can skip and detect header without
        # rewinding (StringIO is cheap here even for large files)
        all_rows: list[list[str]] = list(reader)

        if skip >= len(all_rows):
            # Nothing left after skipping
            elapsed = int((time.perf_counter() - t0) * 1000)
            return QueryResult(
                columns = ["table", "rows_loaded", "rows_skipped", "elapsed_ms"],
                rows    = [[table, 0, 0, elapsed]],
            )

        all_rows = all_rows[skip:]
        if not all_rows:
            elapsed = int((time.perf_counter() - t0) * 1000)
            return QueryResult(
                columns = ["table", "rows_loaded", "rows_skipped", "elapsed_ms"],
                rows    = [[table, 0, 0, elapsed]],
            )

        # ── Header detection ──────────────────────────────────────────────
        first_row = all_rows[0]
        has_header = self._detect_header(first_row, header)

        if has_header:
            col_names = [c.strip().lower() for c in first_row]
            data_rows = all_rows[1:]
        else:
            data_rows = all_rows
            # No header: infer canonical column order based on table type
            col_names = None  # resolved inside the loader

        # ── Dispatch: hyperedge vs node ───────────────────────────────────
        is_node_table = self._is_node_table(table, col_names, data_rows)

        if is_node_table:
            rows_loaded, rows_skipped = self._copy_hyperedge_or_node(
                table         = table,
                col_names     = col_names,
                data_rows     = data_rows,
                ignore_errors = ignore_errors,
                node_mode     = True,
            )
        else:
            rows_loaded, rows_skipped = self._copy_hyperedge_or_node(
                table         = table,
                col_names     = col_names,
                data_rows     = data_rows,
                ignore_errors = ignore_errors,
                node_mode     = False,
            )

        elapsed = int((time.perf_counter() - t0) * 1000)
        return QueryResult(
            columns = ["table", "rows_loaded", "rows_skipped", "elapsed_ms"],
            rows    = [[table, rows_loaded, rows_skipped, elapsed]],
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    @staticmethod
    def _detect_header(first_row: list[str], hint: bool | None) -> bool:
        """
        Return True if *first_row* should be treated as a header row.

        If *hint* is not None, it is used directly.
        Otherwise, auto-detect: if the first cell looks like a column name
        (cannot be parsed as an integer) → header=True.
        """
        if hint is not None:
            return hint
        try:
            int(first_row[0].strip())
            return False   # first cell is a number → data row
        except (ValueError, IndexError):
            return True    # first cell is a string → likely a header

    def _is_node_table(
        self,
        table:     str,
        col_names: list[str] | None,
        data_rows: list[list[str]],
    ) -> bool:
        """
        Decide if the target table is a node table by:
        1. Checking schema catalog for the table name (node tables → True)
        2. Falling back to column names if present
        3. Defaulting to hyperedge table
        """
        # Check schema catalog first (most reliable)
        node_tables = [
            t["name"].upper()
            for t in (self._schema.list_node_tables()
                      if hasattr(self._schema, "list_node_tables") else [])
        ]
        if table.upper() in node_tables:
            return True

        # Fallback: column-name heuristic
        if col_names:
            cols = set(col_names)
            if "drone_id" in cols:
                return True
            if "event_ts" in cols or "members" in cols:
                return False

        return False   # default to hyperedge

    def _copy_hyperedge_or_node(
        self,
        table:         str,
        col_names:     list[str] | None,
        data_rows:     list[list[str]],
        ignore_errors: bool,
        node_mode:     bool,
    ) -> tuple[int, int]:
        """
        Load rows into the TPI (hyperedge) or NodeStore (node).
        Returns (rows_loaded, rows_skipped).
        """
        if node_mode:
            return self._load_node_rows(col_names, data_rows, ignore_errors, table=table)
        return self._load_hyperedge_rows(col_names, data_rows, ignore_errors, table=table)

    def _load_hyperedge_rows(
        self,
        col_names:     list[str] | None,
        data_rows:     list[list[str]],
        ignore_errors: bool,
        table:         str = "",
    ) -> tuple[int, int]:
        """
        Parse CSV rows into record dicts and bulk-insert via _load_hyperedge_records.

        Canonical column order (headerless):
            event_ts, members, member_count, weight, mean_dist_m, formation
        """
        CANONICAL = ["event_ts", "members", "member_count",
                     "weight", "mean_dist_m", "formation"]
        cols = col_names if col_names is not None else CANONICAL

        # Validate required columns when header is known
        if col_names is not None:
            missing = _HE_REQUIRED_COLS - set(cols)
            if missing:
                raise HyperMeshError(
                    f"COPY FROM: CSV is missing required hyperedge columns: "
                    f"{sorted(missing)!r}. "
                    f"Required: {sorted(_HE_REQUIRED_COLS)!r}"
                )

        # Build a column-name → index map
        col_idx: dict[str, int] = {c: i for i, c in enumerate(cols)}

        def _get(row: list[str], name: str, default: str = "") -> str:
            idx = col_idx.get(name)
            if idx is None or idx >= len(row):
                return default
            return row[idx].strip()

        records: list[dict[str, Any]] = []
        for row in data_rows:
            if not any(c.strip() for c in row):
                continue  # skip blank lines
            rec = {col: _get(row, col, "") for col in cols}
            records.append(rec)

        return self._load_hyperedge_records(records, cols, ignore_errors, table=table)

    def _load_node_rows(
        self,
        col_names:     list[str] | None,
        data_rows:     list[list[str]],
        ignore_errors: bool,
        table:         str = "Drone",
    ) -> tuple[int, int]:
        """
        Parse CSV string rows and upsert into the NodeStore for *table*.

        Column definitions (names, types, defaults) are read from the
        SchemaStore for *table* so this method is not hardcoded to the
        Drone schema.

        Canonical headerless column order defaults to the order defined in
        SchemaStore for the target table.
        """
        node_store = self._get_node_store(table)
        columns    = node_store._columns
        pk_col     = node_store._pk_col

        canonical  = [c.name for c in columns]
        cols       = col_names if col_names is not None else canonical

        if col_names is not None and pk_col.name not in set(cols):
            raise HyperMeshError(
                f"COPY FROM: CSV is missing required primary key column "
                f"{pk_col.name!r} for node table {table!r}."
            )

        col_idx: dict[str, int] = {c: i for i, c in enumerate(cols)}

        def _get_str(row: list[str], name: str) -> str:
            idx = col_idx.get(name)
            if idx is None or idx >= len(row):
                return ""
            return row[idx].strip()

        rows_loaded  = 0
        rows_skipped = 0

        for lineno, row in enumerate(data_rows, start=1):
            if not any(c.strip() for c in row):
                continue
            try:
                pk_str = _get_str(row, pk_col.name)
                if not pk_str:
                    raise ValueError(f"empty {pk_col.name}")
                node: dict[str, Any] = {}
                for col in columns:
                    raw = _get_str(row, col.name)
                    node[col.name] = col.coerce(raw if raw else None)
                node_store.upsert(node)
                rows_loaded += 1
            except Exception as exc:
                if ignore_errors:
                    rows_skipped += 1
                else:
                    raise HyperMeshError(
                        f"COPY FROM: error on data row {lineno} in table {table!r}: "
                        f"{exc!r}.  Use IGNORE_ERRORS=true to skip bad rows."
                    ) from exc

        return rows_loaded, rows_skipped

    # ── COPY FROM Parquet ─────────────────────────────────────────────────

    def copy_from_parquet(
        self,
        table:         str,
        path:          str,
        *,
        ignore_errors: bool = False,
    ) -> QueryResult:
        """
        Bulk-load records from a **Parquet file** into a HyperMesh DB table.

        This is the programmatic equivalent of::

            COPY <table> FROM 'data.parquet'
            COPY <table> FROM 'data.parquet' (IGNORE_ERRORS=true)

        The same DDL ``COPY … FROM`` syntax used for CSV files works
        automatically — the file extension (``.parquet``) triggers this
        loader instead of the CSV path.

        Requires **pyarrow** (``pip install pyarrow``).  If pyarrow is absent,
        pandas with a pyarrow backend is tried as a fallback
        (``pip install pandas pyarrow``).

        Parameters
        ----------
        table:
            Name of the target hyperedge or node table.
        path:
            Absolute or relative path to the ``.parquet`` file.
        ignore_errors:
            Skip malformed rows instead of raising.

        Returns
        -------
        QueryResult
            Single-row result:
            ``table``, ``rows_loaded``, ``rows_skipped``, ``elapsed_ms``.

        Raises
        ------
        HyperMeshError
            If pyarrow is not installed, the file does not exist, is missing
            required columns, or contains malformed data
            (and ``ignore_errors=False``).

        Notes
        -----
        - Parquet list columns (``list<int64>``) are converted to Python
          ``list`` and accepted natively by the ``members`` field.
        - Column names are normalised to lowercase before dispatch.
        - All numeric Parquet types (``int32``, ``int64``, ``float32``,
          ``float64``) are coerced correctly without a string round-trip.

        Examples
        --------
        >>> # Via execute() DDL — identical syntax to CSV
        >>> db.execute("COPY CoProximity FROM 'edges.parquet'")

        >>> # Direct method call
        >>> result = db.copy_from_parquet("CoProximity", "edges.parquet",
        ...                              ignore_errors=True)
        >>> print(result.fetchone()["rows_loaded"])
        """
        self._check_open()

        if not os.path.exists(path):
            raise HyperMeshError(
                f"COPY FROM: file not found: {path!r}"
            )

        t0 = time.perf_counter()

        # ── Read Parquet file (pyarrow primary, pandas fallback) ──────────
        try:
            import pyarrow.parquet as _pq
            _pa_table = _pq.read_table(path)
            # Convert to pandas for uniform downstream processing.
            # pyarrow list columns become Python lists; numerics stay typed.
            df = _pa_table.to_pandas()
        except ImportError:
            try:
                import pandas as _pd
                df = _pd.read_parquet(path)
            except ImportError:
                raise HyperMeshError(
                    "copy_from_parquet: pyarrow is not installed. "
                    "Run: pip install pyarrow"
                )
        except Exception as exc:
            raise HyperMeshError(
                f"copy_from_parquet: failed to read {path!r}: {exc}"
            ) from exc

        # ── Normalise columns & convert to list[dict] ─────────────────────
        col_names: list[str] = [str(c).strip().lower() for c in df.columns]

        try:
            records: list[dict[str, Any]] = df.to_dict("records")
        except Exception as exc:
            raise HyperMeshError(
                f"copy_from_parquet: failed to convert parquet table to "
                f"records: {exc}"
            ) from exc

        if not records:
            elapsed = int((time.perf_counter() - t0) * 1000)
            return QueryResult(
                columns = ["table", "rows_loaded", "rows_skipped", "elapsed_ms"],
                rows    = [[table, 0, 0, elapsed]],
            )

        # Normalise record keys to lowercase
        records = [{k.strip().lower(): v for k, v in rec.items()} for rec in records]

        # ── Dispatch ──────────────────────────────────────────────────────
        is_node = self._is_node_table(table, col_names, [])

        if is_node:
            rows_loaded, rows_skipped = self._load_node_records(
                records, col_names, ignore_errors, table=table
            )
        else:
            rows_loaded, rows_skipped = self._load_hyperedge_records(
                records, col_names, ignore_errors, table=table
            )

        elapsed = int((time.perf_counter() - t0) * 1000)
        return QueryResult(
            columns = ["table", "rows_loaded", "rows_skipped", "elapsed_ms"],
            rows    = [[table, rows_loaded, rows_skipped, elapsed]],
        )

    # ── COPY FROM JSON / NDJSON ───────────────────────────────────────────

    def copy_from_json(
        self,
        table:         str,
        path:          str,
        *,
        key:           str | None = None,
        ignore_errors: bool       = False,
    ) -> QueryResult:
        """
        Bulk-load records from a **JSON** or **NDJSON/JSONL** file into a
        HyperMesh DB table.

        Three file formats are auto-detected:

        **JSON array** ``[{...}, {...}, ...]``
            The file contains a top-level JSON array; each element must be a
            JSON object.  This is the most common interchange format.

            .. code-block:: json

                [
                  {"event_ts": 100, "members": [1, 2], "weight": 0.9},
                  {"event_ts": 200, "members": [3, 4], "weight": 0.8}
                ]

        **Nested JSON object + KEY option**
            The file is a JSON object whose records live under a named key.
            Use the ``key=`` parameter (or ``KEY=`` DDL option) to extract them.

            .. code-block:: json

                {"hyperedges": [{"event_ts": 100, "members": [1, 2]}, ...]}

            .. code-block:: python

                db.copy_from_json("CoProximity", "data.json", key="hyperedges")
                # or via DDL:
                db.execute("COPY CoProximity FROM 'data.json' (KEY='hyperedges')")

        **NDJSON / JSON Lines**
            Each line is an independent JSON object (``{...}``).  Blank lines
            are skipped.  This format is common in streaming, Kafka, and log
            pipelines.

            .. code-block:: text

                {"event_ts": 100, "members": [1, 2]}
                {"event_ts": 200, "members": [3, 4]}

        DDL auto-routing
        ----------------
        The ``COPY … FROM`` statement routes to this loader automatically for
        files ending in ``.json``, ``.ndjson``, or ``.jsonl``::

            COPY CoProximity FROM 'edges.json'
            COPY CoProximity FROM 'events.ndjson'
            COPY CoProximity FROM 'log.jsonl'   (IGNORE_ERRORS=true)

        Parameters
        ----------
        table:
            Name of the target hyperedge or node table.
        path:
            Absolute or relative path to the JSON / NDJSON / JSONL file.
        key:
            For nested-object files: the top-level key whose value is the
            list of records (e.g. ``"hyperedges"``).  Ignored for JSON array
            and NDJSON formats.
        ignore_errors:
            Skip malformed records instead of raising.

        Returns
        -------
        QueryResult
            Single-row result:
            ``table``, ``rows_loaded``, ``rows_skipped``, ``elapsed_ms``.

        Raises
        ------
        HyperMeshError
            If the file does not exist, cannot be parsed as JSON or NDJSON,
            is a JSON object without a ``key=`` hint, is missing required
            columns, or contains malformed data (and ``ignore_errors=False``).
        """
        self._check_open()

        if not os.path.exists(path):
            raise HyperMeshError(f"COPY FROM: file not found: {path!r}")

        t0 = time.perf_counter()

        try:
            text = open(path, encoding="utf-8").read()
        except OSError as exc:
            raise HyperMeshError(
                f"COPY FROM: cannot read {path!r}: {exc}"
            ) from exc

        stripped = text.strip()
        if not stripped:
            elapsed = int((time.perf_counter() - t0) * 1000)
            return QueryResult(
                columns = ["table", "rows_loaded", "rows_skipped", "elapsed_ms"],
                rows    = [[table, 0, 0, elapsed]],
            )

        # ── Format detection ──────────────────────────────────────────────
        # .ndjson / .jsonl extensions always force line-by-line parsing so
        # that single-record NDJSON files are not mistaken for JSON objects.
        raw_records: list[dict[str, Any]] = []
        ndjson_parse_errors: list[str] = []

        _force_lines = path.lower().endswith((".ndjson", ".jsonl"))

        if not _force_lines:
            try:
                data = json.loads(stripped)

                if isinstance(data, list):
                    # ── Format 1: JSON array ──────────────────────────────
                    raw_records = data

                elif isinstance(data, dict):
                    # ── Format 2: nested JSON object ──────────────────────
                    if key is None:
                        top_keys = sorted(data.keys())[:10]
                        raise HyperMeshError(
                            f"copy_from_json: file {path!r} is a JSON object, "
                            f"not an array.  Use key=<name> (or DDL option "
                            f"KEY='<name>') to specify which field holds the "
                            f"records.  Top-level keys: {top_keys!r}"
                        )
                    val = data.get(key)
                    if val is None:
                        raise HyperMeshError(
                            f"copy_from_json: key {key!r} not found in JSON "
                            f"object.  Available keys: {sorted(data.keys())!r}"
                        )
                    if not isinstance(val, list):
                        raise HyperMeshError(
                            f"copy_from_json: key {key!r} maps to "
                            f"{type(val).__name__!r}, expected a list of objects."
                        )
                    raw_records = val
                else:
                    raise HyperMeshError(
                        f"copy_from_json: expected a JSON array or object, "
                        f"got {type(data).__name__!r}"
                    )

            except json.JSONDecodeError:
                # Falls through to NDJSON parsing below
                _force_lines = True

        if _force_lines:
            # ── Format 3: NDJSON / JSON Lines ─────────────────────────────
            for lineno, line in enumerate(stripped.splitlines(), start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        msg = (
                            f"NDJSON line {lineno} is not a JSON object: "
                            f"{line[:60]!r}"
                        )
                        if ignore_errors:
                            ndjson_parse_errors.append(msg)
                            continue
                        raise HyperMeshError(f"copy_from_json: {msg}")
                    raw_records.append(obj)
                except json.JSONDecodeError as exc:
                    msg = f"NDJSON line {lineno}: {exc}"
                    if ignore_errors:
                        ndjson_parse_errors.append(msg)
                        continue
                    raise HyperMeshError(
                        f"copy_from_json: {msg}.  "
                        f"Use ignore_errors=True to skip bad lines."
                    ) from exc

        if not raw_records:
            elapsed = int((time.perf_counter() - t0) * 1000)
            # Pre-parse errors counted as skipped even with 0 loaded
            return QueryResult(
                columns = ["table", "rows_loaded", "rows_skipped", "elapsed_ms"],
                rows    = [[table, 0, len(ndjson_parse_errors), elapsed]],
            )

        # ── Normalise keys to lowercase ───────────────────────────────────
        col_names: list[str] = [
            k.strip().lower() for k in raw_records[0].keys()
        ]
        records: list[dict[str, Any]] = [
            {k.strip().lower(): v for k, v in rec.items()}
            for rec in raw_records
            if isinstance(rec, dict)
        ]

        # ── Dispatch ──────────────────────────────────────────────────────
        is_node = self._is_node_table(table, col_names, [])

        if is_node:
            rows_loaded, rows_skipped = self._load_node_records(
                records, col_names, ignore_errors, table=table
            )
        else:
            rows_loaded, rows_skipped = self._load_hyperedge_records(
                records, col_names, ignore_errors, table=table
            )

        # Count pre-parse errors (NDJSON bad lines) in rows_skipped
        rows_skipped += len(ndjson_parse_errors)

        elapsed = int((time.perf_counter() - t0) * 1000)
        return QueryResult(
            columns = ["table", "rows_loaded", "rows_skipped", "elapsed_ms"],
            rows    = [[table, rows_loaded, rows_skipped, elapsed]],
        )

    # ── COPY FROM NumPy ───────────────────────────────────────────────────

    def copy_from_numpy(
        self,
        arr:           Any,
        table:         str,
        columns:       list[str] | None = None,
        *,
        ignore_errors: bool = False,
    ) -> QueryResult:
        """
        Bulk-load records from a **NumPy array** into a HyperMesh DB table.

        Two array shapes are accepted:

        **Regular 2-D array** ``shape (N, C)``
            Each row is one record; ``columns`` maps each column index to a
            field name and is **required** (arrays carry no column labels).

            .. code-block:: python

                arr = np.array([
                    [100, "[1,2,3]", 0.9],
                    [200, "[4,5,6]", 0.8],
                ], dtype=object)
                db.copy_from_numpy(arr, "CoProximity",
                                   columns=["event_ts", "members", "weight"])

        **Structured (record) array** ``dtype.names != None``
            Column names are read from the dtype field names; ``columns``
            is optional (it overrides the dtype names when provided).

            .. code-block:: python

                dt = np.dtype([("event_ts", np.int64), ("members", object),
                               ("weight", np.float64)])
                arr = np.array([(100, [1, 2], 0.9)], dtype=dt)
                db.copy_from_numpy(arr, "CoProximity")

        Parameters
        ----------
        arr:
            A ``numpy.ndarray``.  Must be 2-D for regular arrays; 1-D is
            accepted only for structured arrays (each element is one record).
        table:
            Target hyperedge or node table name.
        columns:
            List of field names, one per column (required for regular 2-D
            arrays; optional override for structured arrays).
        ignore_errors:
            Skip malformed rows instead of raising.

        Returns
        -------
        QueryResult
            Single-row result:
            ``table``, ``rows_loaded``, ``rows_skipped``, ``elapsed_ms``.

        Raises
        ------
        HyperMeshError
            If *arr* is not a numpy ndarray, has wrong dimensionality,
            ``columns`` is missing for a regular array, or required columns
            are absent.
        """
        self._check_open()

        # ── Import numpy lazily so the SDK doesn't hard-depend on it ─────
        try:
            import numpy as _np
        except ImportError:
            raise HyperMeshError(
                "copy_from_numpy: numpy is not installed. "
                "Run: pip install numpy"
            )

        if not isinstance(arr, _np.ndarray):
            raise HyperMeshError(
                f"copy_from_numpy: expected numpy.ndarray, "
                f"got {type(arr).__name__!r}"
            )

        t0 = time.perf_counter()

        # ── Resolve column names & convert to list[dict] ──────────────────
        is_structured = arr.dtype.names is not None

        if is_structured:
            # Structured / record array — dtype carries field names
            raw_names: tuple[str, ...] = arr.dtype.names  # type: ignore[assignment]
            col_names = (
                [c.strip().lower() for c in columns]
                if columns is not None
                else [c.strip().lower() for c in raw_names]
            )
            if arr.ndim not in (0, 1):
                raise HyperMeshError(
                    f"copy_from_numpy: structured array must be 0-D or 1-D "
                    f"(one element per record), got ndim={arr.ndim}"
                )
            flat = arr.ravel() if arr.ndim == 0 else arr
            records: list[dict[str, Any]] = [
                {col: flat[i][fn] for col, fn in zip(col_names, raw_names)}
                for i in range(len(flat))
            ]
        else:
            # Regular N-D array — columns parameter is required
            if columns is None:
                raise HyperMeshError(
                    "copy_from_numpy: 'columns' parameter is required for "
                    "non-structured arrays (arrays have no column labels)."
                )
            if arr.ndim != 2:
                raise HyperMeshError(
                    f"copy_from_numpy: regular array must be 2-D "
                    f"(shape N×C), got ndim={arr.ndim}"
                )
            col_names = [c.strip().lower() for c in columns]
            n_rows, n_cols = arr.shape
            if len(col_names) != n_cols:
                raise HyperMeshError(
                    f"copy_from_numpy: columns list has {len(col_names)} names "
                    f"but array has {n_cols} columns"
                )
            records = [
                {col_names[j]: arr[i, j] for j in range(n_cols)}
                for i in range(n_rows)
            ]

        if not records:
            elapsed = int((time.perf_counter() - t0) * 1000)
            return QueryResult(
                columns = ["table", "rows_loaded", "rows_skipped", "elapsed_ms"],
                rows    = [[table, 0, 0, elapsed]],
            )

        # Dispatch to the same type-aware loaders used by copy_from_df
        is_node = self._is_node_table(table, col_names, [])

        if is_node:
            rows_loaded, rows_skipped = self._load_node_records(
                records, col_names, ignore_errors, table=table
            )
        else:
            rows_loaded, rows_skipped = self._load_hyperedge_records(
                records, col_names, ignore_errors, table=table
            )

        elapsed = int((time.perf_counter() - t0) * 1000)
        return QueryResult(
            columns = ["table", "rows_loaded", "rows_skipped", "elapsed_ms"],
            rows    = [[table, rows_loaded, rows_skipped, elapsed]],
        )

    # ── COPY FROM DataFrame ───────────────────────────────────────────────

    def copy_from_df(
        self,
        df:            Any,
        table:         str,
        *,
        ignore_errors: bool = False,
    ) -> QueryResult:
        """
        Bulk-load records from a **pandas** or **polars** DataFrame into a
        HyperMesh DB table.

        This is the programmatic equivalent of :meth:`copy_from_csv`, but
        accepts a native in-memory DataFrame rather than a file path.  Column
        values are coerced directly from their native Python / NumPy types
        without a string round-trip, so ``int64`` node IDs and ``float64``
        weights are handled correctly.

        Parameters
        ----------
        df:
            A ``pandas.DataFrame`` or ``polars.DataFrame`` (or any object that
            exposes ``.columns`` and either ``.to_dict("records")`` or
            ``.to_dicts()``).
        table:
            Name of the target hyperedge or node table (used for result
            metadata and dispatch).
        ignore_errors:
            When ``True``, rows that fail to parse are silently skipped and
            counted in ``rows_skipped``.  Default: ``False``.

        Returns
        -------
        QueryResult
            Single-row result with columns:
            ``table``, ``rows_loaded``, ``rows_skipped``, ``elapsed_ms``.

        Raises
        ------
        HyperMeshError
            If *df* is not a recognised DataFrame type, is missing required
            columns, or contains malformed data (and ``ignore_errors=False``).

        Notes
        -----
        - Column names are normalised to lowercase and stripped of surrounding
          whitespace before dispatch.
        - The ``members`` column may contain Python lists, tuples, NumPy
          arrays, or string representations (``"[1,2,3]"`` / ``"1,2,3"``).
        - Polars ``null`` and pandas ``NaN`` / ``NA`` are both treated as
          "missing": optional fields fall back to their default values;
          required fields (``event_ts``, ``members``, ``drone_id``) raise an
          error unless ``ignore_errors=True``.

        Examples
        --------
        >>> import pandas as pd
        >>> df = pd.DataFrame({
        ...     "event_ts": [100, 200, 300],
        ...     "members":  [[1, 2], [2, 3], [1, 3]],
        ...     "weight":   [0.9, 0.8, 0.7],
        ... })
        >>> result = db.copy_from_df(df, "CoProximity")
        >>> print(result.fetchone()["rows_loaded"])  # → 3
        """
        self._check_open()

        if not hasattr(df, "columns"):
            raise HyperMeshError(
                f"copy_from_df: expected a pandas.DataFrame or polars.DataFrame, "
                f"got {type(df).__name__!r}"
            )

        t0 = time.perf_counter()

        # Normalise column names to lowercase strings
        col_names: list[str] = [str(c).strip().lower() for c in df.columns]

        # Convert to list[dict] using the library's native method.
        # Check polars (.to_dicts) BEFORE pandas (.to_dict) because polars
        # also exposes .to_dict() (with a different signature) and we don't
        # want to accidentally call it with the wrong argument.
        try:
            if hasattr(df, "to_dicts"):
                # polars: .to_dicts() → list[dict]
                records: list[dict[str, Any]] = df.to_dicts()
            elif hasattr(df, "to_dict"):
                # pandas: .to_dict("records") → list[dict]
                records = df.to_dict("records")
            else:
                raise HyperMeshError(
                    f"copy_from_df: unsupported DataFrame type {type(df).__name__!r} — "
                    f"no 'to_dict' or 'to_dicts' method found."
                )
        except HyperMeshError:
            raise
        except Exception as exc:
            raise HyperMeshError(
                f"copy_from_df: failed to convert DataFrame to records: {exc}"
            ) from exc

        if not records:
            elapsed = int((time.perf_counter() - t0) * 1000)
            return QueryResult(
                columns = ["table", "rows_loaded", "rows_skipped", "elapsed_ms"],
                rows    = [[table, 0, 0, elapsed]],
            )

        # Normalise record keys to lowercase (polars preserves original case)
        records = [{k.strip().lower(): v for k, v in rec.items()} for rec in records]

        # Dispatch to the appropriate loader
        is_node = self._is_node_table(table, col_names, [])

        if is_node:
            rows_loaded, rows_skipped = self._load_node_records(
                records, col_names, ignore_errors, table=table
            )
        else:
            rows_loaded, rows_skipped = self._load_hyperedge_records(
                records, col_names, ignore_errors, table=table
            )

        elapsed = int((time.perf_counter() - t0) * 1000)
        return QueryResult(
            columns = ["table", "rows_loaded", "rows_skipped", "elapsed_ms"],
            rows    = [[table, rows_loaded, rows_skipped, elapsed]],
        )

    # ── Property blob serialisation ───────────────────────────────────────

    @staticmethod
    def _serialize_props(
        rec:     dict[str, Any],
        columns: list[ColumnDef],
    ) -> bytes | None:
        """
        Serialise user-defined property columns into a compact binary blob.

        Encoding per column (little-endian):
          REAL    → 4 bytes  (float32)
          INTEGER → 4 bytes  (int32, signed)
          TEXT    → 4-byte uint32 length prefix + UTF-8 bytes

        Returns None if columns is empty.
        """
        import struct
        if not columns:
            return None
        # Build a case-insensitive lookup map so CSV rows with lowercase keys
        # (e.g. "confidence") match schema column names that are uppercase
        # (e.g. "CONFIDENCE") due to the C lexer uppercasing all identifiers.
        rec_ci: dict[str, Any] = {k.upper(): v for k, v in rec.items()}
        parts: list[bytes] = []
        for col in columns:
            val = col.coerce(rec_ci.get(col.name.upper()))
            if col.col_type == "REAL":
                parts.append(struct.pack("<f", float(val)))
            elif col.col_type == "INTEGER":
                parts.append(struct.pack("<i", int(val)))
            else:  # TEXT
                encoded = str(val).encode("utf-8", errors="replace")
                parts.append(struct.pack("<I", len(encoded)) + encoded)
        return b"".join(parts)

    @staticmethod
    def _deserialize_props(
        blob:    bytes,
        columns: list[ColumnDef],
    ) -> dict[str, Any]:
        """
        Deserialise a property blob back into a dict of column → value.
        Mirrors the encoding used by :meth:`_serialize_props`.
        """
        import struct
        result: dict[str, Any] = {}
        offset = 0
        for col in columns:
            try:
                if col.col_type == "REAL":
                    (val,) = struct.unpack_from("<f", blob, offset)
                    offset += 4
                elif col.col_type == "INTEGER":
                    (val,) = struct.unpack_from("<i", blob, offset)
                    offset += 4
                else:  # TEXT
                    (slen,) = struct.unpack_from("<I", blob, offset)
                    offset += 4
                    val = blob[offset: offset + slen].decode("utf-8", errors="replace")
                    offset += slen
                result[col.name] = val
            except struct.error:
                result[col.name] = col.coerce(None)
        return result

    def _load_hyperedge_records(
        self,
        records:       list[dict[str, Any]],
        col_names:     list[str],
        ignore_errors: bool,
        table:         str = "",
    ) -> tuple[int, int]:
        """
        Insert hyperedge records (native Python types) via WAL.

        Coerces each field from its native type:
        - ``event_ts``   → int   (required)
        - ``members``    → list[int]  (required; accepts list/tuple/ndarray/str)
        - ``weight``     → float (optional, default 0.0)
        - ``mean_dist_m``→ float (optional, default 0.0)
        - ``formation``  → str   (optional, default "")

        V2: if *table* has hyperedge column definitions registered in SchemaStore,
        user-defined property values are serialised into a binary blob and stored
        via hm_insert_record_v2.
        """
        missing = _HE_REQUIRED_COLS - set(col_names)
        if missing:
            raise HyperMeshError(
                f"copy_from_df: DataFrame is missing required hyperedge columns: "
                f"{sorted(missing)!r}. Required: {sorted(_HE_REQUIRED_COLS)!r}"
            )

        # Load V2 property columns from schema (empty list → V1-compatible insert)
        prop_cols: list[ColumnDef] = []
        if table and self._schema:
            try:
                prop_cols = self._schema.get_hyperedge_columns(table)
            except Exception:
                pass

        rows_loaded  = 0
        rows_skipped = 0
        # Resolve the target store once for the whole batch
        tgt_store = self._get_store(table) if table else self._get_store(self._primary_table)

        for lineno, rec in enumerate(records, start=1):
            try:
                event_ts_raw = rec.get("event_ts")
                members_raw  = rec.get("members")

                if event_ts_raw is None:
                    raise ValueError("event_ts is null")
                if isinstance(event_ts_raw, float) and math.isnan(event_ts_raw):
                    raise ValueError("event_ts is NaN")

                event_ts = int(event_ts_raw)
                members  = _parse_members_native(members_raw)

                if not members:
                    raise ValueError("members list is empty")

                weight      = _coerce_float(rec.get("weight"),      0.0)
                mean_dist_m = _coerce_float(rec.get("mean_dist_m"), 0.0)
                formation   = _coerce_str(rec.get("formation"),     "")

                props_blob = self._serialize_props(rec, prop_cols)

                if props_blob:
                    tgt_store.insert_v2(
                        event_ts    = event_ts,
                        members     = members,
                        weight      = weight,
                        mean_dist_m = mean_dist_m,
                        formation   = formation,
                        props       = props_blob,
                    )
                else:
                    tgt_store.insert(
                        event_ts    = event_ts,
                        members     = members,
                        weight      = weight,
                        mean_dist_m = mean_dist_m,
                        formation   = formation,
                    )
                rows_loaded += 1

            except Exception as exc:
                if ignore_errors:
                    rows_skipped += 1
                else:
                    raise HyperMeshError(
                        f"copy_from_df: error on record {lineno}: {exc!r}. "
                        f"Pass ignore_errors=True to skip bad records."
                    ) from exc

        return rows_loaded, rows_skipped

    def _load_node_records(
        self,
        records:       list[dict[str, Any]],
        col_names:     list[str],
        ignore_errors: bool,
        table:         str = "Drone",
    ) -> tuple[int, int]:
        """
        Upsert node records (native Python types) into the NodeStore.

        Column definitions and type coercions are derived from the SchemaStore
        so this method works for any node table, not just ``Drone``.

        Parameters
        ----------
        records:
            List of dicts, one per row.  Keys must match column names
            (case-insensitive).
        col_names:
            Column names present in the source data (used for validation).
        ignore_errors:
            If True, skip malformed rows instead of raising.
        table:
            Target node table name.  Defaults to ``"Drone"`` for backward
            compatibility.
        """
        node_store = self._get_node_store(table)
        columns    = node_store._columns
        pk_col     = node_store._pk_col

        # Validate that the primary key column is present in the source data
        if col_names and pk_col.name not in set(col_names):
            raise HyperMeshError(
                f"copy_from_df: DataFrame is missing required primary key column "
                f"{pk_col.name!r} for node table {table!r}. "
                f"Available columns: {col_names!r}"
            )

        rows_loaded  = 0
        rows_skipped = 0

        for lineno, rec in enumerate(records, start=1):
            try:
                pk_raw = rec.get(pk_col.name)
                if pk_raw is None:
                    raise ValueError(f"{pk_col.name} is null")
                if isinstance(pk_raw, float) and math.isnan(pk_raw):
                    raise ValueError(f"{pk_col.name} is NaN")
                # Build the node dict using schema-driven coercions
                node: dict[str, Any] = {
                    col.name: col.coerce(rec.get(col.name))
                    for col in columns
                }
                node_store.upsert(node)
                rows_loaded += 1

            except Exception as exc:
                if ignore_errors:
                    rows_skipped += 1
                else:
                    raise HyperMeshError(
                        f"copy_from_df: error on record {lineno} in table {table!r}: "
                        f"{exc!r}.  Pass ignore_errors=True to skip bad records."
                    ) from exc

        return rows_loaded, rows_skipped

    # ── Representation ────────────────────────────────────────────────────

    def __repr__(self) -> str:
        status = "closed" if self._closed else "open"
        return (
            f"Connection({self._dir_path!r}, "
            f"records={self.total_records if not self._closed else '?'}, "
            f"status={status!r})"
        )

    # ── Phase 10: Hypergraph Analytics ────────────────────────────────────

    def to_hypergraph(self, table: str | None = None) -> "HypergraphPy":
        """
        Build a sparse in-memory :class:`~hypermeshdb.HypergraphPy` from
        *table* (defaults to the primary table).

        Performs a full table scan and constructs a binary incidence matrix
        ``B`` of shape ``(n_nodes, n_edges)`` using SciPy CSR format, together
        with per-edge weight, timestamp, and size arrays.

        The returned object is a snapshot — it is not live-updated when new
        records are inserted.

        Parameters
        ----------
        table :
            Name of the hyperedge table to materialise.  Defaults to the
            primary table (``CoProximity`` for fresh databases).

        Returns
        -------
        HypergraphPy
            Sparse incidence matrix representation.

        Raises
        ------
        HyperMeshError
            If the table does not exist.
        ImportError
            If ``scipy`` is not installed.

        Examples
        --------
        >>> hg = db.to_hypergraph("Events")
        >>> print(hg.n_nodes, hg.n_edges)
        >>> print(hg.B.toarray())
        """
        try:
            from scipy import sparse as _sp  # noqa: F401
        except ImportError:
            raise ImportError(
                "scipy is required for to_hypergraph(). "
                "Install it with: pip install scipy"
            ) from None

        from ._analytics import build_hypergraph, HypergraphPy  # noqa: F401

        self._check_open()
        tbl = table or self._primary_table
        result = self._full_scan(table=tbl)
        rows = result.fetchall()
        return build_hypergraph(rows)

    def analytics(self, table: str | None = None) -> "Analytics":
        """
        Return an :class:`~hypermeshdb.Analytics` engine for *table*.

        Equivalent to ``Analytics(db.to_hypergraph(table))``.

        Parameters
        ----------
        table :
            Name of the hyperedge table.  Defaults to the primary table.

        Returns
        -------
        Analytics
            Object exposing all 25 hypergraph measures as methods.

        Raises
        ------
        HyperMeshError
            If the table does not exist.
        ImportError
            If ``scipy`` is not installed.

        Examples
        --------
        >>> an = db.analytics("Events")
        >>> print(an.density())
        >>> print(an.spectral_gap())
        >>> print(an.pagerank())
        >>> print(an.summary())
        """
        from ._analytics import Analytics  # noqa: F401
        hg = self.to_hypergraph(table)
        return Analytics(hg)


# ── Transaction context manager ────────────────────────────────────────────────

class _TransactionContext:
    """Returned by ``Connection.transaction()``."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def __enter__(self) -> "_TransactionContext":
        self._conn.begin()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        return False   # re-raise exceptions


# ── EXPLAIN helper ─────────────────────────────────────────────────────────────

def _strategy_to_index(strategy: str) -> str:
    """Map a plan strategy string to a human-readable index type."""
    s = strategy.upper()
    if "TPI" in s:
        return "TPI"
    if "FMI" in s:
        return "FMI"
    if "PSI" in s:
        return "PSI"
    if "FULL" in s or "SCAN" in s:
        return "FULL_SCAN"
    return strategy
