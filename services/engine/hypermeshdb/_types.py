"""
_types.py — Shared types for the HyperMesh DB Python SDK.
"""

from __future__ import annotations

from dataclasses import dataclass


# ── Public exception taxonomy ───────────────────────────────────────────────

class HyperMeshError(Exception):
    """
    Base class for all HyperMesh errors: parse failures, I/O errors, schema
    violations, invalid arguments, and remote-server failures.

    Catch this to handle any HyperMesh error; catch a subclass for fine-grained
    control.
    """


class ConnectionError(HyperMeshError):
    """A local database cannot be opened or a remote server is unreachable."""


class QueryError(HyperMeshError):
    """A query is malformed or failed during execution."""


class SchemaError(HyperMeshError):
    """A DDL / schema-catalog violation (unknown table, bad column type, ...)."""


class IngestError(HyperMeshError):
    """An ingestion run failed (bad source, mapping spec, or bundle emit)."""


class AuthError(HyperMeshError):
    """Authentication against a remote server failed (missing/invalid API key)."""


class TimeoutError(HyperMeshError):
    """A remote request exceeded its configured timeout."""


class EngineNotInstalledError(HyperMeshError):
    """
    The embedded engine was requested but the compiled core library is absent.

    Install the engine extra::

        pip install "hypermesh[engine]"

    Remote usage via ``connect("http://...")`` does not require the engine.
    """


# ── Query plan (real measured values from the C engine) ───────────────────────

@dataclass(frozen=True)
class QueryPlan:
    """
    Performance metadata attached to every data query result.

    Attributes
    ----------
    strategy:
        Either "TPI_BUCKET_PUSHDOWN" (range query used the index) or
        "FULL_SCAN" (all records scanned sequentially).
    buckets_scanned:
        Number of TPI time buckets actually read from disk.
    total_buckets:
        Total number of TPI buckets in the index.
    speedup_factor:
        Ratio total_buckets / buckets_scanned.  A factor of 25× means
        only 1/25th of the data was touched.
    elapsed_us:
        Wall-clock microseconds measured by the C engine for the query.
    rows_scanned:
        Number of raw rows returned by the C engine before Python-layer
        predicate filtering, projection, and LIMIT.
    rows_returned:
        Number of rows in the final result after all Python-layer processing.
    predicates_applied:
        Number of property predicates that were evaluated in Python.
    """
    strategy:            str
    buckets_scanned:     int
    total_buckets:       int
    speedup_factor:      float
    elapsed_us:          int
    rows_scanned:        int   = 0
    rows_returned:       int   = 0
    predicates_applied:  int   = 0

    def __str__(self) -> str:
        pred_note = (f", {self.predicates_applied} predicate(s) evaluated"
                     if self.predicates_applied else "")
        filter_note = (f", {self.rows_scanned}→{self.rows_returned} rows"
                       if self.rows_scanned else "")
        return (
            f"{self.strategy}: read {self.buckets_scanned}/{self.total_buckets} "
            f"buckets ({self.speedup_factor:.1f}x speedup, {self.elapsed_us}µs"
            f"{filter_note}{pred_note})"
        )
