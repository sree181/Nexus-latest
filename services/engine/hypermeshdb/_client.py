"""
_client.py — HyperMesh DB remote Python client.

A drop-in replacement for :class:`~hypermeshdb.Connection` that talks to a
running ``hmdb serve`` server over HTTP instead of embedding the C library.

The key design goal is *interface parity*: any code that works with a local
``Connection`` can switch to a remote ``Client`` by changing one line:

    # Local
    db = hypermeshdb.connect("/path/to/db")

    # Remote (same calls below work unchanged)
    db = hypermeshdb.Client("http://localhost:8000")

Usage
-----
>>> from hypermeshdb import Client
>>> with Client("http://localhost:8000") as db:
...     result = db.execute("MATCH HYPEREDGE (he:CoProximity) RETURN *")
...     print(result.num_tuples)

Wire protocol
-------------
All communication goes through the HyperMesh DB REST API v1:

  POST /v1/query            → execute any Cypher statement
  POST /v1/hyperedges       → insert a single hyperedge
  DELETE /v1/hyperedges     → write a tombstone (soft delete)
  POST /v1/hyperedges/compact → trigger WAL compaction
  GET  /v1/wal              → WAL pending count
  GET  /health              → database statistics

Requests/responses are JSON.  ``httpx`` is used for the HTTP layer;
it ships with FastAPI and is already present in the workbench venv.
"""

from __future__ import annotations

import time
from typing import Any, cast

from ._http import (
    DEFAULT_TIMEOUT,
    RetryPolicy,
    build_headers,
    extract_detail,
    map_status_error,
    map_transport_error,
    resolve_api_key,
    resolve_base_url,
)
from ._result import QueryResult
from ._types import HyperMeshError

# Methods that are safe to retry after an ambiguous transport error (no
# response received). Non-idempotent writes are only retried on explicit
# retryable status codes (429/503/...), never on transport errors.
_IDEMPOTENT = frozenset({"GET", "HEAD", "OPTIONS"})


class Client:
    """
    Remote HyperMesh DB client.

    Connects to a running ``hmdb serve`` instance and exposes the same
    high-level API as :class:`~hypermeshdb.Connection`.

    Parameters
    ----------
    url:
        Base URL of the server, e.g. ``"http://localhost:8000"``. If omitted,
        the ``HYPERMESH_URL`` environment variable is used.
    timeout:
        HTTP request timeout in seconds (default 30).
    api_key:
        Bearer token sent as ``Authorization: Bearer <key>``. If omitted, the
        ``HYPERMESH_API_KEY`` environment variable is used.
    verify:
        TLS certificate verification toggle (default ``True``). Set to a CA
        bundle path or ``False`` to customise.
    retries:
        Number of automatic retries on transient failures (default 2).
    backoff_factor:
        Base for exponential backoff with full jitter (default 0.25s).
    headers:
        Extra HTTP headers to send on every request.

    Notes
    -----
    Pass ``_http`` to inject a custom transport (e.g. FastAPI's
    ``TestClient`` during testing) without opening a real socket.

    Parity with the embedded :class:`~hypermeshdb.Connection`: ``execute``,
    ``insert``, ``delete``, ``compact``, ``analytics`` and the stats
    properties are supported remotely. The bulk ``copy_from_*`` helpers,
    multi-statement transactions, and ``to_hypergraph()`` are **local-only**;
    use them against an embedded connection or send equivalent ``COPY``/DDL
    statements via :meth:`execute`.
    """

    def __init__(
        self,
        url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        *,
        api_key: str | None = None,
        verify: bool | str = True,
        retries: int = 2,
        backoff_factor: float = 0.25,
        headers: dict[str, str] | None = None,
        _http: Any = None,
    ) -> None:
        try:
            import httpx
        except ImportError as exc:
            raise ImportError(
                'httpx is required for Client.  Install it with:  pip install "hypermesh[client]"'
            ) from exc

        self._url = resolve_base_url(url)
        self._api_key = resolve_api_key(api_key)
        self._retry = RetryPolicy(retries=retries, backoff_factor=backoff_factor)
        self._http = (
            _http
            if _http is not None
            else httpx.Client(
                base_url=self._url,
                timeout=timeout,
                verify=verify,
                headers=build_headers(self._api_key, headers),
            )
        )
        self._closed = False

    # ── Context manager ───────────────────────────────────────────────────

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        if not self._closed:
            try:
                self._http.close()
            except Exception:
                pass
            self._closed = True

    # ── Internal request helpers (retry + backoff + error mapping) ─────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        where = f"HTTP {method} {path}"
        retry_transport = method.upper() in _IDEMPOTENT
        last_exc: HyperMeshError | None = None

        for attempt in range(self._retry.retries + 1):
            try:
                r = self._http.request(method, path, json=json, params=params or None)
            except Exception as exc:  # transport-level failure
                last_exc = map_transport_error(exc, where)
                if retry_transport and attempt < self._retry.retries:
                    time.sleep(self._retry.backoff(attempt + 1))
                    continue
                raise last_exc from exc

            if r.status_code >= 400:
                detail = extract_detail(r)
                if r.status_code in self._retry.retry_status and attempt < self._retry.retries:
                    time.sleep(self._retry.backoff(attempt + 1))
                    continue
                raise map_status_error(r.status_code, detail)

            data: dict[str, Any] = r.json()
            return data

        # Exhausted retries on a retryable status without ever succeeding.
        raise last_exc or HyperMeshError(f"{where} failed after retries")

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, json=payload)

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def _delete(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("DELETE", path, json=json)

    @staticmethod
    def _to_result(data: dict[str, Any]) -> QueryResult:
        """Convert a /v1/query JSON response to a QueryResult."""
        cols = data["columns"]
        rows = [[row[c] for c in cols] for row in data["rows"]]
        return QueryResult(columns=cols, rows=rows)

    @staticmethod
    def _records_to_result(records: list[dict[str, Any]]) -> QueryResult:
        """Convert a list of record dicts (e.g. hyperedges) to a QueryResult.

        Column order follows first appearance, with the canonical hyperedge
        columns (``event_ts``, ``members``, ``weight``) leading when present.
        """
        cols: list[str] = []
        for lead in ("event_ts", "members", "weight"):
            if any(lead in rec for rec in records):
                cols.append(lead)
        for rec in records:
            for k in rec:
                if k not in cols:
                    cols.append(k)
        rows = [[rec.get(c) for c in cols] for rec in records]
        return QueryResult(columns=cols, rows=rows)

    # ── Core execute ──────────────────────────────────────────────────────

    def execute(
        self,
        cypher: str,
        parameters: dict[str, Any] | None = None,
    ) -> QueryResult:
        """
        Execute any Cypher statement on the remote server.

        Identical interface to :meth:`Connection.execute`.  Supports MATCH,
        CREATE/DROP HYPEREDGE TABLE, INSERT, UPDATE, DELETE, CREATE/DROP INDEX,
        SHOW INDEXES, COPY FROM, and CALL … RETURN *.

        Parameters
        ----------
        cypher:
            Cypher query string.
        parameters:
            Optional named parameters (``$name`` placeholders).

        Returns
        -------
        QueryResult
            Result rows and column names.

        Raises
        ------
        HyperMeshError
            On parse errors, missing tables, or server-side failures.
        """
        payload: dict[str, Any] = {"query": cypher}
        if parameters:
            payload["parameters"] = parameters
        data = self._post("/v1/query", payload)
        return self._to_result(data)

    # ── Convenience write path ─────────────────────────────────────────────

    def insert(
        self,
        *,
        event_ts: int,
        members: list[int],
        weight: float = 0.0,
        mean_dist_m: float = 0.0,
        formation: str = "",
    ) -> None:
        """
        Insert a single hyperedge record via ``POST /v1/hyperedges``.

        Identical interface to :meth:`Connection.insert`.
        """
        self._post(
            "/v1/hyperedges",
            {
                "event_ts": event_ts,
                "members": members,
                "weight": weight,
                "mean_dist_m": mean_dist_m,
                "formation": formation,
            },
        )

    def delete(self, *, event_ts: int, members: list[int]) -> None:
        """
        Write a tombstone (soft delete) via ``DELETE /v1/hyperedges``.

        Identical interface to :meth:`Connection.delete`.
        """
        self._delete(
            "/v1/hyperedges",
            json={
                "event_ts": event_ts,
                "members": members,
            },
        )

    # ── WAL + compact ─────────────────────────────────────────────────────

    def compact(
        self,
        *,
        ttl_seconds: int | None = None,
        table: str | None = None,
    ) -> None:
        """
        Trigger WAL compaction on the remote server.

        Parameters
        ----------
        ttl_seconds:
            When set, records older than ``now - ttl_seconds`` are discarded.
        table:
            Compact only the named table.  *None* compacts all tables.
        """
        body: dict[str, Any] = {}
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        if table is not None:
            body["table"] = table
        self._post("/v1/hyperedges/compact", body)

    def set_autocompact(
        self,
        threshold: int,
        *,
        table: str | None = None,
    ) -> None:
        """
        Configure automatic WAL compaction on the remote server.

        Parameters
        ----------
        threshold:
            WAL entry count that triggers auto-compact.  0 = disable.
        table:
            Apply to the named table only.  *None* applies to all tables.
        """
        body: dict[str, Any] = {"threshold": threshold}
        if table is not None:
            body["table"] = table
        self._post("/v1/autocompact", body)

    @property
    def wal_pending(self) -> int:
        """Number of WAL entries pending compaction on the server."""
        return int(self._get("/v1/wal")["pending"])

    # ── Database statistics ────────────────────────────────────────────────

    def _health(self) -> dict[str, Any]:
        return self._get("/health")

    def health(self) -> dict[str, Any]:
        """
        Return the server health/statistics document (``GET /health``).

        Includes ``total_records``, ``node_count``, ``bucket_count`` and
        ``bucket_seconds``. Raises on connectivity/auth failure.
        """
        return self._health()

    def ping(self) -> bool:
        """
        Return ``True`` if the server is reachable and healthy, else ``False``.

        Never raises; use :meth:`health` if you want the underlying error.
        """
        try:
            self._health()
            return True
        except HyperMeshError:
            return False

    @property
    def total_records(self) -> int:
        """Total records stored in TPI + WAL."""
        return int(self._health()["total_records"])

    @property
    def node_count(self) -> int:
        """Number of unique nodes across all hyperedges."""
        return int(self._health()["node_count"])

    @property
    def bucket_count(self) -> int:
        """Number of TPI time buckets."""
        return int(self._health()["bucket_count"])

    @property
    def bucket_seconds(self) -> int:
        """Width of each TPI bucket in seconds."""
        return int(self._health()["bucket_seconds"])

    # ── Representation ────────────────────────────────────────────────────

    def analytics(
        self,
        table: str,
        measure: str,
        **params: Any,
    ) -> Any:
        """
        Compute a hypergraph analytics measure on the remote server.

        Parameters
        ----------
        table :
            Name of the hyperedge table.
        measure :
            One of the 25 supported analytics measure names (e.g. ``"density"``,
            ``"pagerank"``, ``"spectral_gap"``).
        **params :
            Optional keyword arguments forwarded to the measure
            (e.g. ``s=2``, ``alpha=0.1``, ``node_id=5``).

        Returns
        -------
        Any
            The raw result value as returned by the server.  Scalar measures
            return a float/int; per-node measures return a dict with string
            keys (node IDs).

        Examples
        --------
        >>> client.analytics("Events", "density")
        0.04166666666666667
        >>> client.analytics("Events", "pagerank")
        {"1": 0.25, "2": 0.35, ...}
        >>> client.analytics("Events", "s_adjacency", s=2)
        [...]
        """
        body = {"params": params}
        resp = self._post(f"/v1/analytics/{table}/{measure}", body)
        return resp.get("result")

    # ── Atomic batch ──────────────────────────────────────────────────────

    def batch(self, operations: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Execute an atomic batch of operations via ``POST /v1/batch``.

        All operations commit together or roll back together. Each operation is
        a dict with a ``type`` of ``"insert"``, ``"delete"`` or ``"query"``:

        >>> client.batch([
        ...     {"type": "insert", "event_ts": 100, "members": [1, 2],
        ...      "weight": 0.9, "table": "CoProximity"},
        ...     {"type": "query", "cypher": "MATCH HYPEREDGE (he:CoProximity) RETURN *"},
        ... ])

        Returns the server ``BatchOut`` document: ``committed``, ``ops_total``,
        ``ops_success``, ``error`` and a per-operation ``results`` list.
        """
        if not operations:
            raise ValueError("batch() requires at least one operation.")
        return self._post("/v1/batch", {"operations": operations})

    # ── Temporal range query ──────────────────────────────────────────────

    def range(
        self,
        start_ts: int,
        end_ts: int,
        *,
        table: str = "CoProximity",
    ) -> QueryResult:
        """
        Fetch hyperedges in the inclusive ``[start_ts, end_ts]`` window via
        ``GET /v1/hyperedges`` (TPI bucket pushdown on the server).

        Returns a :class:`QueryResult` so it reads like any other query.
        """
        data = self._get("/v1/hyperedges", start_ts=start_ts, end_ts=end_ts, table=table)
        return self._records_to_result(data.get("hyperedges", []))

    # ── Schema inspection ──────────────────────────────────────────────────

    def tables(self) -> list[dict[str, Any]]:
        """List hyperedge tables via ``GET /v1/tables``.

        Each entry has ``name``, ``member_tables``, ``bucket_seconds``,
        ``compact_threshold`` and ``row_count``.
        """
        return cast("list[dict[str, Any]]", self._request("GET", "/v1/tables"))

    def indexes(self, table: str | None = None) -> list[dict[str, Any]]:
        """List PSI property indexes via ``GET /v1/indexes``.

        Pass *table* to filter to a single table. Each entry has ``table``,
        ``column`` and ``created_at``.
        """
        params = {"table": table} if table else None
        return cast(
            "list[dict[str, Any]]",
            self._request("GET", "/v1/indexes", params=params),
        )

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return f"Client(url={self._url!r}, state={state!r})"
