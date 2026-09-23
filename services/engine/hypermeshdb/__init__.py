"""
HyperMesh DB — Temporal Hypergraph Database

A high-performance temporal hypergraph database with proprietary
Temporal Property Index (TPI) and Forward Member Index (FMI) for
N-ary relationship modelling at enterprise scale.

Quick start
-----------
>>> import hypermeshdb
>>>
>>> # Open (or create) a database
>>> db = hypermeshdb.connect(
...     "/tmp/my_db",
...     hyperedges_csv="path/to/hyperedges.csv",
...     nodes_csv="path/to/nodes.csv",
... )
>>>
>>> # Run a temporal range query  (TPI pushdown — reads only relevant buckets)
>>> result = db.execute(
...     "MATCH HYPEREDGE (he:CoProximity) "
...     "WHERE he.event_ts >= $start AND he.event_ts <= $end "
...     "RETURN *",
...     parameters={"start": 0, "end": 300},
... )
>>> print(result.num_tuples, "hyperedges found")
>>> print(result.query_plan)
>>>
>>> # Iterate rows
>>> for row in result:
...     print(row["event_ts"], row.members, row.weight)
>>>
>>> # Write path
>>> db.insert(event_ts=9999, members=[1, 2, 3], weight=0.95, formation="WEDGE")
>>> db.delete(event_ts=9999, members=[1, 2, 3])
>>> db.compact()
>>>
>>> # Schema DDL
>>> db.execute("CREATE HYPEREDGE TABLE Sensor (Drone, Drone) BUCKET_SECONDS 30")
>>> db.execute("CALL show_hyperedge_tables() RETURN *")
>>>
>>> db.close()
>>>
>>> # Bulk-load from a pandas / polars DataFrame (Phase 2)
>>> import pandas as pd
>>> df = pd.DataFrame({
...     "event_ts": [100, 200],
...     "members":  [[1, 2], [2, 3]],
...     "weight":   [0.9, 0.8],
... })
>>> result = db.copy_from_df(df, "CoProximity")
>>> print(result.fetchone()["rows_loaded"])  # → 2
>>>
>>> # Bulk-load from a NumPy array (Phase 3)
>>> import numpy as np
>>> arr = np.array([[100, "[1,2,3]", 0.9], [200, "[4,5,6]", 0.8]], dtype=object)
>>> result = db.copy_from_numpy(arr, "CoProximity",
...                             columns=["event_ts", "members", "weight"])
>>> print(result.fetchone()["rows_loaded"])  # → 2
>>>
>>> # Bulk-load from a Parquet file (Phase 4)
>>> # Via DDL — identical syntax to CSV, extension selects the loader
>>> db.execute("COPY CoProximity FROM 'edges.parquet'")
>>> # Or direct method call
>>> result = db.copy_from_parquet("CoProximity", "edges.parquet")
>>>
>>> # Bulk-load from JSON / NDJSON (Phase 5) — three formats auto-detected
>>> db.execute("COPY CoProximity FROM 'edges.json'")            # JSON array
>>> db.execute("COPY CoProximity FROM 'events.ndjson'")         # NDJSON
>>> db.execute("COPY CoProximity FROM 'data.json' (KEY='hyperedges')")  # nested
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from hypermesh_core.python.schema_store import ColumnDef

from ._async_client import AsyncClient
from ._client import Client
from ._connection import Connection, CopyOptions
from ._connection import _TransactionContext as TransactionContext  # noqa: F401
from ._loader import Loader
from ._result import HyperMeshEncoder, QueryResult, Row
from ._types import (
    AuthError,
    ConnectionError,
    EngineNotInstalledError,
    HyperMeshError,
    IngestError,
    QueryError,
    QueryPlan,
    SchemaError,
    TimeoutError,
)

# Library logging: attach a NullHandler so importing the package never emits
# log output unless the application configures logging explicitly.
logging.getLogger("hypermesh").addHandler(logging.NullHandler())

# ``Analytics`` / ``HypergraphPy`` pull in numpy (and scipy). They are imported
# lazily so that ``import hypermeshdb`` and the embedded engine work without the
# optional ``[analytics]`` dependencies installed.
if TYPE_CHECKING:
    from ._analytics import Analytics, HypergraphPy


def __getattr__(name: str) -> Any:  # PEP 562 lazy attribute access
    if name in ("Analytics", "HypergraphPy"):
        from . import _analytics

        return getattr(_analytics, name)
    if name in ("scan_windows", "scan_rows"):
        from . import _scan

        return getattr(_scan, name)
    raise AttributeError(f"module 'hypermeshdb' has no attribute {name!r}")

__all__ = [
    "connect",
    "Connection",
    "Client",
    "AsyncClient",
    "ColumnDef",
    "CopyOptions",
    "Loader",
    "QueryResult",
    "Row",
    "HyperMeshEncoder",
    "QueryPlan",
    "HyperMeshError",
    "ConnectionError",
    "QueryError",
    "SchemaError",
    "IngestError",
    "AuthError",
    "TimeoutError",
    "EngineNotInstalledError",
    "Analytics",
    "HypergraphPy",
    "scan_windows",
    "scan_rows",
    "__version__",
]

if TYPE_CHECKING:
    from ._scan import scan_rows, scan_windows

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("hypermesh")
except Exception:  # pragma: no cover - source tree before install
    __version__ = "0.1.0"


def connect(
    dir_path:       str,
    hyperedges_csv: str | None = None,
    nodes_csv:      str | None = None,
    bucket_seconds: int = 10,
) -> Connection:
    """
    Open (or create) a HyperMesh DB database at *dir_path*.

    Parameters
    ----------
    dir_path:
        Filesystem path to the database directory.  Created automatically
        if it does not exist.
    hyperedges_csv:
        Path to a hyperedges CSV file.  If the TPI + FMI index does not
        yet exist, it is built from this file automatically.  The CSV must
        have columns: ``event_ts, members, member_count, weight, mean_dist_m,
        formation``.
    nodes_csv:
        Optional path to a nodes CSV file.  On the first call, node
        properties are imported into ``nodes.db`` inside *dir_path*.
        Subsequent calls reopen the existing SQLite file — the CSV is not
        re-read.
    bucket_seconds:
        TPI time-bucket granularity in seconds.  Ignored if the index
        already exists.  Default: 10.

    Returns
    -------
    Connection
        An open connection.  Use as a context manager or call :meth:`~Connection.close`
        explicitly when finished.

    Raises
    ------
    HyperMeshError
        If the index does not exist and *hyperedges_csv* is not provided,
        or if any I/O error occurs during index build or open.

    Examples
    --------
    >>> with hypermeshdb.connect("/tmp/demo", hyperedges_csv="edges.csv") as db:
    ...     result = db.execute("CALL show_hyperedge_tables() RETURN *")
    ...     print(result.fetchone()["name"])
    CoProximity
    """
    return Connection(
        dir_path       = dir_path,
        hyperedges_csv = hyperedges_csv,
        nodes_csv      = nodes_csv,
        bucket_seconds = bucket_seconds,
    )
