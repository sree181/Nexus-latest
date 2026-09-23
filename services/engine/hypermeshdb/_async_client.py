"""
_async_client.py - asynchronous remote HyperMesh client.

Mirrors :class:`hypermeshdb.Client` for ``asyncio`` applications, backed by
``httpx.AsyncClient``. Same configuration (env vars, API key, TLS verify,
timeouts, retries) and the same exception taxonomy.

Usage
-----
>>> import asyncio
>>> from hypermeshdb import AsyncClient
>>>
>>> async def main():
...     async with AsyncClient("http://localhost:8000") as db:
...         res = await db.execute("MATCH HYPEREDGE (he:CoProximity) RETURN *")
...         print(res.num_tuples)
>>> asyncio.run(main())
"""

from __future__ import annotations

import asyncio
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

_IDEMPOTENT = frozenset({"GET", "HEAD", "OPTIONS"})


class AsyncClient:
    """
    Asynchronous remote HyperMesh client.

    Parameters are identical to :class:`hypermeshdb.Client`. Methods that hit
    the network are coroutines (``await db.execute(...)``). Parity caveats are
    the same: ``copy_from_*``, transactions, and ``to_hypergraph()`` are
    local-only.
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
                "httpx is required for AsyncClient.  "
                'Install it with:  pip install "hypermesh[client]"'
            ) from exc

        self._url = resolve_base_url(url)
        self._api_key = resolve_api_key(api_key)
        self._retry = RetryPolicy(retries=retries, backoff_factor=backoff_factor)
        self._http = (
            _http
            if _http is not None
            else httpx.AsyncClient(
                base_url=self._url,
                timeout=timeout,
                verify=verify,
                headers=build_headers(self._api_key, headers),
            )
        )
        self._closed = False

    # ── Context manager ───────────────────────────────────────────────────

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        if not self._closed:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._closed = True

    # ── Request plumbing (retry + backoff + error mapping) ─────────────────

    async def _request(
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
                r = await self._http.request(method, path, json=json, params=params or None)
            except Exception as exc:
                last_exc = map_transport_error(exc, where)
                if retry_transport and attempt < self._retry.retries:
                    await asyncio.sleep(self._retry.backoff(attempt + 1))
                    continue
                raise last_exc from exc

            if r.status_code >= 400:
                detail = extract_detail(r)
                if r.status_code in self._retry.retry_status and attempt < self._retry.retries:
                    await asyncio.sleep(self._retry.backoff(attempt + 1))
                    continue
                raise map_status_error(r.status_code, detail)

            data: dict[str, Any] = r.json()
            return data

        raise last_exc or HyperMeshError(f"{where} failed after retries")

    @staticmethod
    def _to_result(data: dict[str, Any]) -> QueryResult:
        cols = data["columns"]
        rows = [[row[c] for c in cols] for row in data["rows"]]
        return QueryResult(columns=cols, rows=rows)

    @staticmethod
    def _records_to_result(records: list[dict[str, Any]]) -> QueryResult:
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

    # ── API surface ───────────────────────────────────────────────────────

    async def execute(self, cypher: str, parameters: dict[str, Any] | None = None) -> QueryResult:
        """Execute any Cypher statement on the remote server."""
        payload: dict[str, Any] = {"query": cypher}
        if parameters:
            payload["parameters"] = parameters
        data = await self._request("POST", "/v1/query", json=payload)
        return self._to_result(data)

    async def insert(
        self,
        *,
        event_ts: int,
        members: list[int],
        weight: float = 0.0,
        mean_dist_m: float = 0.0,
        formation: str = "",
    ) -> None:
        """Insert a single hyperedge record."""
        await self._request(
            "POST",
            "/v1/hyperedges",
            json={
                "event_ts": event_ts,
                "members": members,
                "weight": weight,
                "mean_dist_m": mean_dist_m,
                "formation": formation,
            },
        )

    async def delete(self, *, event_ts: int, members: list[int]) -> None:
        """Write a tombstone (soft delete)."""
        await self._request(
            "DELETE", "/v1/hyperedges", json={"event_ts": event_ts, "members": members}
        )

    async def compact(self, *, ttl_seconds: int | None = None, table: str | None = None) -> None:
        """Trigger WAL compaction on the remote server."""
        body: dict[str, Any] = {}
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        if table is not None:
            body["table"] = table
        await self._request("POST", "/v1/hyperedges/compact", json=body)

    async def analytics(self, table: str, measure: str, **params: Any) -> Any:
        """Compute a hypergraph analytics measure on the remote server."""
        data = await self._request(
            "POST", f"/v1/analytics/{table}/{measure}", json={"params": params}
        )
        return data.get("result")

    async def batch(self, operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Execute an atomic batch of operations via ``POST /v1/batch``.

        Mirrors :meth:`hypermeshdb.Client.batch`; all operations commit or roll
        back together.
        """
        if not operations:
            raise ValueError("batch() requires at least one operation.")
        return await self._request("POST", "/v1/batch", json={"operations": operations})

    async def range(
        self,
        start_ts: int,
        end_ts: int,
        *,
        table: str = "CoProximity",
    ) -> QueryResult:
        """Fetch hyperedges in ``[start_ts, end_ts]`` via ``GET /v1/hyperedges``."""
        data = await self._request(
            "GET",
            "/v1/hyperedges",
            params={"start_ts": start_ts, "end_ts": end_ts, "table": table},
        )
        return self._records_to_result(data.get("hyperedges", []))

    async def tables(self) -> list[dict[str, Any]]:
        """List hyperedge tables via ``GET /v1/tables``."""
        return cast("list[dict[str, Any]]", await self._request("GET", "/v1/tables"))

    async def indexes(self, table: str | None = None) -> list[dict[str, Any]]:
        """List PSI property indexes via ``GET /v1/indexes``."""
        params = {"table": table} if table else None
        return cast(
            "list[dict[str, Any]]",
            await self._request("GET", "/v1/indexes", params=params),
        )

    async def wal_pending(self) -> int:
        """Number of WAL entries pending compaction on the server."""
        data = await self._request("GET", "/v1/wal")
        return int(data["pending"])

    async def health(self) -> dict[str, Any]:
        """Return the server health/statistics document."""
        return await self._request("GET", "/health")

    async def total_records(self) -> int:
        """Total records stored in TPI + WAL (awaitable parity with ``Client``)."""
        return int((await self.health())["total_records"])

    async def node_count(self) -> int:
        """Number of unique nodes across all hyperedges."""
        return int((await self.health())["node_count"])

    async def bucket_count(self) -> int:
        """Number of TPI time buckets."""
        return int((await self.health())["bucket_count"])

    async def bucket_seconds(self) -> int:
        """Width of each TPI bucket in seconds."""
        return int((await self.health())["bucket_seconds"])

    async def ping(self) -> bool:
        """Return ``True`` if the server is reachable and healthy, else ``False``."""
        try:
            await self.health()
            return True
        except HyperMeshError:
            return False

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return f"AsyncClient(url={self._url!r}, state={state!r})"
