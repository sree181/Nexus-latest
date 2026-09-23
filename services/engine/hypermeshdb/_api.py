"""
_api.py — HyperMesh DB REST API v1

A production-ready FastAPI application that exposes the full hypermeshdb SDK
over HTTP.  Launched via ``hmdb serve <db_dir>`` or directly with uvicorn::

    HMDB_DIR=/path/to/db uvicorn hypermeshdb._api:app --host 0.0.0.0 --port 8000

Architecture
------------
All routes are defined on a module-level ``APIRouter``.  Each
:func:`create_app` call produces an **isolated** FastAPI instance with its own
lifespan closure and ``app.state.db`` connection.  This design allows the test
suite to create per-test app instances without cross-contaminating the session
connection via module-level globals.

The default ``app`` singleton (at the bottom of this module) is the entry point
used by uvicorn.  It reads ``HMDB_DIR`` from the environment at startup.

Authentication
--------------
Auth is **enabled by default** (``auth_disabled=False`` in create_app).
Disable it for local development with ``hmdb serve --no-auth`` or
``HMDB_AUTH_DISABLED=1`` (``auth_disabled=True``).
When enabled, every endpoint except ``/health/live`` and ``/health/ready``
requires an ``X-API-Key`` header (or ``Authorization: Bearer <key>``).

Role requirements per endpoint group:
  readonly   — GET queries, MATCH, analytics reads
  readwrite  — INSERT, DELETE, UPDATE, compact, autocompact
  admin      — DDL (CREATE/DROP TABLE/INDEX), backup, key management

Endpoints
---------
GET  /health/live                  Liveness probe (no auth)
GET  /health/ready                 Readiness probe (no auth)
GET  /health                       Full health stats
GET  /metrics                      Prometheus text format (readonly)
GET  /v1/info                      Database statistics
GET  /v1/tables
GET  /v1/tables/{name}
POST /v1/tables                    (admin)
DELETE /v1/tables/{name}           (admin)
POST /v1/query
GET  /v1/hyperedges               ?start_ts=&end_ts=&table=
GET  /v1/hyperedges/node/{node_id}
POST /v1/hyperedges               (readwrite)
DELETE /v1/hyperedges             (readwrite)
POST /v1/hyperedges/compact       (readwrite)
POST /v1/autocompact              (admin)
GET  /v1/wal
GET  /v1/indexes
POST /v1/indexes                  (admin)
DELETE /v1/indexes/{table}/{col}  (admin)
POST /v1/analytics/{table}/{measure}
POST /v1/backup                   (admin)
GET  /v1/keys                     (admin)
POST /v1/keys                     (admin)
DELETE /v1/keys/{key_id}          (admin)

Catastrophe / damage-assessment:
GET  /v1/assess/{table}                       per-building damage records (paginated)
GET  /v1/assess/{table}/summary               counts, severity quantiles, ROI estimate
GET  /v1/assess/{table}/building/{id}         single-building record
POST /v1/assess/{table}/annotate              adjuster confirm/override (writes hyperedge)
"""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import logging
import os
import re
import signal
import tarfile
import tempfile
import uuid
import textwrap
import threading
import time
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator

import hypermeshdb
from hypermeshdb._auth import ApiKeyRecord, AuthStore, require_role
from hypermeshdb._rate_limiter import RateLimiter, key_fingerprint
from hypermeshdb._types import HyperMeshError
from hypermeshdb._pagination import InvalidCursor, decode_cursor, encode_cursor, scope_fingerprint
from hypermesh_core.python.hm_store import QueryTimeout

log = logging.getLogger(__name__)

# P1.3: pagination bounds for REST read endpoints.
_PAGE_DEFAULT_LIMIT = 1000
_PAGE_MAX_LIMIT     = 10000

# ── Rate-limit middleware ─────────────────────────────────────────────────────

_NO_RATE_LIMIT_PATHS = frozenset({"/health/live", "/health/ready"})


class _RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter applied after FastAPI routing.

    The limiter bucket is the SHA-256 fingerprint of the raw API key so
    we never persist the plaintext.  When auth is disabled we fall back
    to the remote IP address as the bucket key.
    """

    async def dispatch(self, request: Request, call_next):
        limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)

        if limiter is None or limiter.disabled:
            return await call_next(request)

        if request.url.path in _NO_RATE_LIMIT_PATHS:
            return await call_next(request)

        # Resolve bucket key — API key or IP fallback
        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header[7:].strip()
        bucket = key_fingerprint(api_key) if api_key else (
            getattr(request.client, "host", "anon")
        )

        allowed, remaining, reset_epoch = limiter.check(bucket)

        headers = {
            "X-RateLimit-Limit":     str(limiter.limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset":     str(int(reset_epoch)),
        }

        if not allowed:
            retry = max(0, int(reset_epoch - time.time()))
            return JSONResponse(
                status_code = 429,
                content     = {"detail": "Rate limit exceeded. See Retry-After header."},
                headers     = {**headers, "Retry-After": str(retry)},
            )

        response = await call_next(request)
        for k, v in headers.items():
            response.headers[k] = v
        return response


# ── Dependency: get the active Connection from app.state ─────────────────────
# Reads request.app.state.db — works correctly for ANY FastAPI app instance
# created by create_app(), including test instances.

def _get_db(request: Request) -> hypermeshdb.Connection:
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
            detail      = "Database connection is not initialised.",
        )
    return db


DbDep = Annotated[hypermeshdb.Connection, Depends(_get_db)]


def _hypermesh_exc(exc: HyperMeshError, code: int = 400) -> HTTPException:
    return HTTPException(status_code=code, detail=str(exc))


# ── Auth shorthand dependencies ───────────────────────────────────────────────

_RO  = Depends(require_role("readonly"))
_RW  = Depends(require_role("readwrite"))
_ADM = Depends(require_role("admin"))


# ── Pydantic models ───────────────────────────────────────────────────────────

class HealthOut(BaseModel):
    status:         str   = Field("ok")
    version:        str
    db_dir:         str
    total_records:  int
    bucket_count:   int
    bucket_seconds: int
    node_count:     int
    wal_pending:    int
    uptime_seconds: float


class QueryPlanOut(BaseModel):
    strategy:        str
    buckets_scanned: int
    total_buckets:   int
    speedup_factor:  float
    elapsed_us:      int


class HyperedgeOut(BaseModel):
    # Allow extra V2 user-defined properties (family, attribution, color, …)
    # to flow through without listing each column.  The router populates them
    # via Pydantic's `model_extra` so the front-end's GraphExplorer can drive
    # disease-aware colouring + the GNN-explanation panel.
    model_config = ConfigDict(extra="allow")
    event_ts:    int
    members:     list[int]
    weight:      float
    mean_dist_m: float = 0.0
    formation:   str   = ""


class RangeQueryOut(BaseModel):
    hyperedges:  list[HyperedgeOut]
    num_tuples:  int
    query_plan:  QueryPlanOut | None = None
    next_cursor: str | None = None  # opaque token for the next page; null = last


class QueryOut(BaseModel):
    columns:    list[str]
    rows:       list[dict[str, Any]]
    num_tuples: int
    query_plan: QueryPlanOut | None = None


class TableOut(BaseModel):
    name:              str
    member_tables:     list[str]
    bucket_seconds:    int
    compact_threshold: int = 0
    row_count:         int = 0


class WalStatusOut(BaseModel):
    pending:   int
    threshold: int = 100


class IndexOut(BaseModel):
    table:      str
    column:     str
    created_at: int


# ── Identifier safety (injection guard) ───────────────────────────────────────
#
# Table and column names are interpolated into Cypher/DDL statement strings
# (e.g. ``DROP HYPEREDGE TABLE {name}``).  Identifiers cannot be parameterised
# the way values can, so the only safe defense against statement injection is to
# reject anything that is not a bare identifier.  ``_check_ident`` is the single
# choke point reused by both the request models (edge validation, 422 with a
# field path) and the path-parameter routes (``safe_ident``).

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _check_ident(value: str, what: str = "identifier") -> str:
    """Return ``value`` if it is a legal identifier, else raise ``ValueError``.

    Used by Pydantic ``field_validator`` hooks (Pydantic converts the raised
    ``ValueError`` into a 422 with the offending field path).
    """
    if not isinstance(value, str) or not _IDENT_RE.match(value):
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def safe_ident(value: str, what: str = "identifier") -> str:
    """Route-level identifier guard for path parameters — raises HTTP 422.

    Path parameters do not pass through a request model, so routes that
    interpolate them into a statement must call this explicitly.
    """
    try:
        return _check_ident(value, what)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _backup_root(db_dir: str) -> str:
    """The allow-root that every backup target must stay under.

    Configurable via ``HMDB_BACKUP_ROOT``; defaults to ``<db_dir>/backups``.
    """
    return os.path.realpath(
        os.environ.get("HMDB_BACKUP_ROOT") or os.path.join(db_dir, "backups")
    )


def safe_backup_dir(db_dir: str, backup_dir: str) -> str:
    """Resolve a client-supplied ``backup_dir`` under the backup allow-root.

    The request value is always interpreted *relative to* the root, so absolute
    paths and ``..`` traversal cannot escape it. Returns the realpath'd target
    directory (created if needed); raises HTTP 422 on any escape attempt.
    """
    root   = _backup_root(db_dir)
    target = os.path.realpath(os.path.join(root, backup_dir))
    try:
        contained = os.path.commonpath([root, target]) == root
    except ValueError:  # different drives / mixed path kinds
        contained = False
    if not contained:
        raise HTTPException(
            status_code=422,
            detail=f"backup_dir escapes the allowed backup root ({root})",
        )
    os.makedirs(target, exist_ok=True)
    return target


class CreateIndexRequest(BaseModel):
    table:  str
    column: str

    @field_validator("table", "column")
    @classmethod
    def _validate_ident(cls, v: str, info) -> str:
        return _check_ident(v, info.field_name)


class CompactOut(BaseModel):
    wal_pending_before: int
    wal_pending_after:  int
    compacted:          bool
    ttl_seconds:        int | None = None
    table:              str | None = None


class CompactRequest(BaseModel):
    ttl_seconds: int | None = Field(None, gt=0,
                                    description="Discard records older than this many seconds")
    table: str | None = Field(None, description="Compact only this table; None = all tables")


class AutocompactRequest(BaseModel):
    threshold: int = Field(..., ge=0,
                           description="WAL entries before auto-compact fires; 0 = disable")
    table: str | None = Field(None, description="Apply to this table only; None = all tables")


class AutocompactOut(BaseModel):
    threshold: int
    table:     str | None = None
    applied:   bool


class InsertRequest(BaseModel):
    event_ts:    int
    members:     list[int]   = Field(..., min_length=1)
    weight:      float       = Field(0.0,  ge=0.0, le=1.0)
    mean_dist_m: float       = Field(0.0,  ge=0.0)
    formation:   str         = ""


class DeleteRequest(BaseModel):
    event_ts: int
    members:  list[int]


class CreateTableRequest(BaseModel):
    name:              str
    member_tables:     list[str]
    bucket_seconds:    int = Field(10, gt=0)
    compact_threshold: int = Field(0, ge=0,
                                   description="Auto-compact WAL threshold; 0 = disabled")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return _check_ident(v, "name")

    @field_validator("member_tables")
    @classmethod
    def _validate_member_tables(cls, v: list[str]) -> list[str]:
        return [_check_ident(t, "member_table") for t in v]


class CypherRequest(BaseModel):
    query:      str
    parameters: dict[str, Any] | None = None


class BackupRequest(BaseModel):
    backup_dir: str = Field(..., description="Server-side directory to write the backup archive")
    compact_first: bool = Field(True, description="Run compaction before backup for a clean snapshot")


class BackupOut(BaseModel):
    path:         str
    size_bytes:   int
    tables:       list[str]
    compact_first: bool


class ApiKeyCreateRequest(BaseModel):
    description: str  = ""
    role:        str  = Field("readonly", pattern="^(readonly|readwrite|admin)$")
    expires_days: int | None = Field(None, ge=1, le=3650)


class ApiKeyOut(BaseModel):
    key_id:       str
    description:  str
    role:         str
    created_at:   int
    last_used_at: int | None = None
    expires_at:   int | None = None


class ApiKeyCreatedOut(ApiKeyOut):
    """Returned only once at key creation — includes plaintext key."""
    plaintext_key: str


class BatchOperation(BaseModel):
    """A single operation within a batch transaction."""
    type:        str            = Field(..., description="'insert' | 'delete' | 'query'")
    table:       str | None     = Field(None)
    # insert / delete fields
    event_ts:    int | None     = None
    members:     list[int] | None = None
    weight:      float          = 0.0
    mean_dist_m: float          = 0.0
    formation:   str            = ""
    # query field
    cypher:      str | None     = None


class BatchRequest(BaseModel):
    """Atomic batch of operations — all commit or all roll back."""
    operations: list[BatchOperation] = Field(..., min_length=1)


class BatchOpResult(BaseModel):
    op:         int             # 0-based index
    type:       str
    success:    bool
    rows:       list[dict[str, Any]] | None = None
    error:      str | None                  = None


class BatchOut(BaseModel):
    committed:   bool
    ops_total:   int
    ops_success: int
    error:       str | None          = None
    results:     list[BatchOpResult] = Field(default_factory=list)


# ── Module-level router (shared by all app instances) ─────────────────────────

router = APIRouter()


# ── Liveness / Readiness (no auth — used by Kubernetes probes) ───────────────

@router.get("/health/live", include_in_schema=False)
async def health_live():
    """Kubernetes liveness probe — always 200 if the process is running."""
    return {"status": "live"}


@router.get("/health/ready", include_in_schema=False)
async def health_ready(request: Request):
    """
    Kubernetes readiness probe — 200 only once startup has fully completed and
    the DB connection is open, 503 otherwise.

    Returns 503 during the startup window (before the lifespan finishes opening
    the DB), while draining on shutdown, or if the DB failed to open — so a load
    balancer never routes traffic to a process that cannot serve it.
    """
    state = request.app.state
    ready = getattr(state, "ready", False)
    db    = getattr(state, "db", None)
    if not ready or db is None:
        detail = getattr(state, "not_ready_reason", None) or "Database not ready"
        raise HTTPException(status_code=503, detail=detail)
    return {"status": "ready"}


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthOut, tags=["System"],
            summary="Health check")
async def health(request: Request, db: DbDep, _: Any = _RO):
    state = request.app.state
    return HealthOut(
        status         = "ok",
        version        = "1.0.0",
        db_dir         = getattr(state, "db_dir", ""),
        total_records  = db.total_records,
        bucket_count   = db.bucket_count,
        bucket_seconds = db.bucket_seconds,
        node_count     = db.node_count,
        wal_pending    = db.wal_pending,
        uptime_seconds = round(time.monotonic() - getattr(state, "start_time", 0), 3),
    )


# ── Prometheus metrics ────────────────────────────────────────────────────────

@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    include_in_schema=False,
    summary="Prometheus metrics",
)
async def metrics(request: Request, db: DbDep, _: Any = _RO):
    """
    Prometheus text format metrics endpoint.
    Scrape with: ``prometheus.yml`` job ``static_configs: [{targets: ['host:8000']}]``
    """
    state    = request.app.state
    uptime   = round(time.monotonic() - getattr(state, "start_time", 0), 3)

    # Gather per-table stats
    try:
        tables_result = db.execute("CALL show_hyperedge_tables() RETURN *")
        table_rows    = list(tables_result)
    except Exception:
        table_rows = []

    lines: list[str] = []

    def _gauge(name: str, help_: str, value: float | int,
               labels: dict[str, str] | None = None) -> None:
        label_str = ""
        if labels:
            parts = ",".join(f'{k}="{v}"' for k, v in labels.items())
            label_str = "{" + parts + "}"
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name}{label_str} {value}")

    _gauge("hypermeshdb_uptime_seconds",  "Server uptime in seconds",  uptime)
    _gauge("hypermeshdb_records_total",   "Total hyperedge records",    db.total_records)
    _gauge("hypermeshdb_wal_pending",     "Uncompacted WAL entries",    db.wal_pending)
    _gauge("hypermeshdb_bucket_count",    "Total TPI buckets",          db.bucket_count)
    _gauge("hypermeshdb_node_count",      "Unique node IDs",            db.node_count)

    for row in table_rows:
        tbl = row["name"]
        _gauge("hypermeshdb_table_records",
               "Records per table", row["row_count"],
               {"table": tbl})

    lines.append("")
    return "\n".join(lines)


@router.get("/v1/info", response_model=HealthOut, tags=["Database"],
            summary="Database statistics")
async def info(request: Request, db: DbDep, _: Any = _RO):
    return await health(request=request, db=db, _=_)


# ── Tables ────────────────────────────────────────────────────────────────────

@router.get("/v1/tables", response_model=list[TableOut], tags=["Schema"],
            summary="List all hyperedge tables")
async def list_tables(db: DbDep, _: Any = _RO):
    try:
        result = db.execute("CALL show_hyperedge_tables() RETURN *")
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc, 500)
    return [
        TableOut(
            name              = row["name"],
            member_tables     = row["member_tables"],
            bucket_seconds    = row["bucket_seconds"],
            compact_threshold = row["compact_threshold"],
            row_count         = row["row_count"],
        )
        for row in result
    ]


@router.get("/v1/tables/{name}", response_model=TableOut, tags=["Schema"],
            summary="Get one table definition",
            responses={404: {"description": "Table not found"}})
async def get_table(name: str, db: DbDep, _: Any = _RO):
    try:
        result = db.execute("CALL show_hyperedge_tables() RETURN *")
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc, 500)
    for row in result:
        if row["name"].upper() == name.upper():
            return TableOut(
                name              = row["name"],
                member_tables     = row["member_tables"],
                bucket_seconds    = row["bucket_seconds"],
                compact_threshold = row["compact_threshold"],
                row_count         = row["row_count"],
            )
    raise HTTPException(status_code=404, detail=f"Table '{name}' not found")


@router.post("/v1/tables", response_model=TableOut,
             status_code=status.HTTP_201_CREATED, tags=["Schema"],
             summary="Create a hyperedge table")
async def create_table(req: CreateTableRequest, db: DbDep, _: Any = _ADM):
    members_csv = ", ".join(req.member_tables)
    cypher = (
        f"CREATE HYPEREDGE TABLE {req.name} ({members_csv}) "
        f"BUCKET_SECONDS {req.bucket_seconds} "
        f"COMPACT_THRESHOLD {req.compact_threshold}"
    )
    try:
        result = db.execute(cypher)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)
    row = result.fetchone()
    return TableOut(
        name              = row["name"],
        member_tables     = row["member_tables"],
        bucket_seconds    = row["bucket_seconds"],
        compact_threshold = row["compact_threshold"],
        row_count         = 0,
    )


@router.delete("/v1/tables/{name}", response_model=dict, tags=["Schema"],
               summary="Drop a hyperedge table")
async def drop_table(name: str, db: DbDep, _: Any = _ADM):
    try:
        result = db.execute(f"DROP HYPEREDGE TABLE {safe_ident(name, 'name')}")
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)
    row = result.fetchone()
    return {"name": row["name"], "dropped": row["dropped"]}


# ── Cypher query ──────────────────────────────────────────────────────────────

@router.post("/v1/query", response_model=QueryOut, tags=["Query"],
             summary="Execute a Cypher query")
async def execute_query(req: CypherRequest, db: DbDep, _: Any = _RO):
    try:
        result = db.execute(req.query, parameters=req.parameters)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)

    plan = None
    if result.query_plan:
        p = result.query_plan
        plan = QueryPlanOut(
            strategy        = p.strategy,
            buckets_scanned = p.buckets_scanned,
            total_buckets   = p.total_buckets,
            speedup_factor  = p.speedup_factor,
            elapsed_us      = p.elapsed_us,
        )
    return QueryOut(
        columns    = result.columns,
        rows       = result.to_dicts(),
        num_tuples = result.num_tuples,
        query_plan = plan,
    )


# ── Range query ───────────────────────────────────────────────────────────────

@router.get("/v1/hyperedges", response_model=RangeQueryOut, tags=["Hyperedges"],
            summary="Temporal range query (TPI bucket pushdown)")
async def range_query(
    db:       DbDep,
    _:        Any = _RO,
    start_ts: int = Query(..., ge=0, description="Start timestamp (inclusive)"),
    end_ts:   int = Query(..., ge=0, description="End timestamp (inclusive)"),
    table:    str = Query("CoProximity", description="Hyperedge table name"),
    limit:    int = Query(_PAGE_DEFAULT_LIMIT, ge=1, le=_PAGE_MAX_LIMIT,
                          description="Max hyperedges to return in this page"),
    cursor:   str | None = Query(None, description="Opaque pagination cursor from a prior page"),
):
    cypher = (
        f"MATCH HYPEREDGE (he:{table}) "
        f"WHERE he.event_ts >= {start_ts} AND he.event_ts <= {end_ts} RETURN *"
    )
    try:
        result = db.execute(cypher)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)

    # Pagination: slice the deterministically-ordered result. The cursor is
    # bound to (table, start_ts, end_ts) so it cannot be replayed against a
    # different query.
    scope = scope_fingerprint(table, start_ts, end_ts)
    offset = 0
    if cursor is not None:
        try:
            offset = decode_cursor(cursor, scope)
        except InvalidCursor as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    rows = list(result)
    page = rows[offset:offset + limit]
    next_cursor = (
        encode_cursor(offset + limit, scope)
        if offset + limit < len(rows) else None
    )

    plan = None
    if result.query_plan:
        p = result.query_plan
        plan = QueryPlanOut(
            strategy        = p.strategy,
            buckets_scanned = p.buckets_scanned,
            total_buckets   = p.total_buckets,
            speedup_factor  = p.speedup_factor,
            elapsed_us      = p.elapsed_us,
        )
    return RangeQueryOut(
        hyperedges = [
            HyperedgeOut(
                event_ts    = row["event_ts"],
                members     = row["members"],
                weight      = row["weight"],
                mean_dist_m = row["mean_dist_m"],
                formation   = row["formation"],
            )
            for row in page
        ],
        num_tuples  = len(page),
        query_plan  = plan,
        next_cursor = next_cursor,
    )


@router.get("/v1/hyperedges/node/{node_id}", response_model=RangeQueryOut,
            tags=["Hyperedges"], summary="FMI point lookup")
async def node_lookup(
    node_id: int,
    db:      DbDep,
    _:       Any = _RO,
    table:   str = Query("CoProximity"),
):
    # Keep this in sync with the _PROP_ALLOWLIST used by /v1/hyperedges/s_walk
    # so the front-end gets the same set of V2 user-defined properties no
    # matter which endpoint it queried (the GraphExplorer relies on `family`,
    # `top_class`, `attribution`, `readable_features`, …).
    _PROP_ALLOWLIST = (
        "edge_id", "disease_class", "top_class", "family", "color",
        "features", "readable_features",
        "support_train", "support_all", "combo_size",
        "cluster", "attribution", "max_attribution",
        "chunk_index", "total_chunks",
    )

    cypher = (
        f"MATCH HYPEREDGE (he:{table}) "
        f"WHERE {node_id} IN he.members RETURN *"
    )
    try:
        result = db.execute(cypher)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)

    edges_out: list[HyperedgeOut] = []
    for row in result:
        extras: dict[str, Any] = {}
        for key in _PROP_ALLOWLIST:
            try:
                val = row[key.upper()]
            except (KeyError, IndexError, TypeError):
                continue
            if val is None:
                continue
            if isinstance(val, (bytes, bytearray)):
                val = val.decode("utf-8", errors="replace")
            extras[key] = val

        # `mean_dist_m` only exists on non-Mayo tables (CoProximity / Helene
        # damage models).  Default to 0.0 when absent so Pydantic is happy.
        try:
            mean_dist = float(row["mean_dist_m"] or 0.0)
        except (KeyError, IndexError, TypeError):
            mean_dist = 0.0

        edges_out.append(HyperedgeOut(
            event_ts    = int(row["event_ts"] or 0),
            members     = [int(m) for m in row["members"]],
            weight      = float(row["weight"] or 0.0),
            mean_dist_m = mean_dist,
            formation   = str(row["formation"] or ""),
            **extras,
        ))

    return RangeQueryOut(
        hyperedges = edges_out,
        num_tuples = result.num_tuples,
    )


# ── s-Walk: hyperedge-dual BFS ────────────────────────────────────────────────
#
#  GET /v1/hyperedges/s_walk?node_id=X&table=T&s=2&depth=2&max_hedges=60
#
#  Returns the s-line graph rooted at all hyperedges that contain node_id.
#  Two hyperedges H₁, H₂ are s-adjacent iff |H₁ ∩ H₂| ≥ s.
#
#  Performance model (critical):
#  ─────────────────────────────
#  Naïve approach: one FMI lookup per member of every frontier hedge.
#    Cost = frontier_size × avg_members = e.g. 50 × 32 = 1 600 DB round-trips.
#    On PATENT_CO_CITATION (116 K hyperedges) this takes 60–120 s. Unusable.
#
#  Fast approach used here (member-pivot deduplication):
#    1. Collect ALL unique members across the ENTIRE frontier in one pass.
#    2. Do exactly ONE FMI lookup per unique member.
#    3. Build per-member results into a shared cache keyed by member ID.
#    4. Reuse the cache when evaluating each frontier hedge's overlap.
#    Cost = |unique members across frontier| DB calls, typically 10-50× fewer.
#
#  The entire BFS is run in a thread-pool executor so synchronous db.execute()
#  calls do not block the FastAPI event loop.

@router.get("/v1/hyperedges/s_walk", tags=["Hyperedges"],
            summary="s-adjacent hyperedge walk (Act II — hyperedge dual graph)")
async def s_walk(
    request:     Request,
    db:          DbDep,
    _:           Any = _RO,
    node_id:     int = Query(...,  description="Focal node ID"),
    table:       str = Query("CoProximity", description="Hyperedge table"),
    s:           int = Query(2,    ge=1, le=10, description="Min shared-member overlap"),
    depth:       int = Query(1,    ge=1, le=3,  description="BFS depth (1–3)"),
    max_hedges:  int = Query(60,   ge=5, le=300, description="Hard cap on returned hyperedges"),
    max_seed:    int = Query(30,   ge=3, le=100, description="Max seed hedges from focal node"),
    max_pivot_members: int = Query(20, ge=2, le=64,
                                   description="Members sampled per hedge for pivot lookups"),
):
    import asyncio as _asyncio
    import time    as _t

    def _run_bfs() -> dict:
        t0 = _t.perf_counter()

        # ── helpers ───────────────────────────────────────────────────────
        # Allow-list of extra (V2) property column names that we forward to
        # the client.  Keys are the lowercase output names; values are the
        # uppercase column names emitted by the C parser (lexer uppercases
        # all identifiers).  Unknown columns are ignored.
        _PROP_ALLOWLIST = (
            "edge_id", "disease_class", "top_class", "family", "color",
            "features", "readable_features",
            "support_train", "support_all", "combo_size",
            "cluster", "attribution", "max_attribution",
            "chunk_index", "total_chunks",
        )

        def _fetch(member_id: int) -> list[dict]:
            try:
                res = db.execute(
                    f"MATCH HYPEREDGE (h:{table}) WHERE {member_id} IN h.members RETURN *"
                )
                out: list[dict] = []
                for r in res:
                    rec: dict = {
                        "event_ts":  int(r["event_ts"]),
                        "members":   [int(m) for m in r["members"]],
                        "weight":    float(r["weight"] or 0.0),
                        "formation": str(r["formation"] or ""),
                    }
                    # Pass through V2 user-defined properties when present.
                    # Values may be int / float / str — we keep types as-is so
                    # the frontend can use them directly (e.g. for blob colour
                    # via he.color, family bucket via he.family, etc.).
                    for key in _PROP_ALLOWLIST:
                        upper = key.upper()
                        try:
                            val = r[upper]
                        except (KeyError, IndexError, TypeError):
                            continue
                        if val is None:
                            continue
                        if isinstance(val, (bytes, bytearray)):
                            val = val.decode("utf-8", errors="replace")
                        rec[key] = val
                    out.append(rec)
                return out
            except HyperMeshError:
                return []

        def _key(members: list[int]) -> frozenset:
            return frozenset(members)

        # ── Step 1: seed ─────────────────────────────────────────────────
        # Sort by V2 attribution score DESC (when present) so the most
        # clinically informative edges always make it into the seed window.
        # Edges without attribution (non-V2 tables) preserve insertion order.
        seed_all = _fetch(node_id)
        seed_all.sort(
            key=lambda h: (
                float(h.get("attribution") or 0.0),
                float(h.get("max_attribution") or 0.0),
            ),
            reverse=True,
        )
        seed_raw = seed_all[:max_seed]
        if not seed_raw:
            elapsed = round((_t.perf_counter() - t0) * 1000, 1)
            return {
                "focal_node": node_id, "table": table, "s": s, "depth": depth,
                "hyperedges": [], "s_edges": [],
                "stats": {"total_hyperedges": 0, "total_s_edges": 0,
                          "max_depth_reached": 0, "capped": False, "elapsed_ms": elapsed},
            }

        all_hedges: list[dict] = []
        seen_keys:  set[frozenset] = set()
        frontier:   list[dict] = []
        capped = False

        for h in seed_raw:
            k = _key(h["members"])
            if k not in seen_keys:
                seen_keys.add(k)
                rec = {**h, "depth": 0}
                all_hedges.append(rec)
                frontier.append(rec)

        # ── Step 2: BFS — one FMI call per UNIQUE member across frontier ──
        for d in range(1, depth + 1):
            if not frontier or capped:
                break

            # Collect unique pivot members across the ENTIRE frontier first.
            # This is the key optimisation: avoids N_hedges × M_members DB calls.
            unique_pivots: set[int] = set()
            for h in frontier:
                for m in h["members"][:max_pivot_members]:
                    unique_pivots.add(m)

            # One FMI lookup per unique pivot member → shared result cache
            pivot_cache: dict[int, list[dict]] = {}
            for m in unique_pivots:
                pivot_cache[m] = _fetch(m)

            # Now evaluate s-adjacency per frontier hedge using the cache
            next_frontier: list[dict] = []
            for h in frontier:
                if capped:
                    break
                h_key      = _key(h["members"])
                # member → (shared_members_list, candidate_raw) for new candidates
                overlap: dict[frozenset, tuple[list[int], dict]] = {}
                for m in h["members"][:max_pivot_members]:
                    for cand in pivot_cache.get(m, []):
                        ck = _key(cand["members"])
                        if ck == h_key:    continue   # skip self
                        if ck in seen_keys: continue  # already accepted
                        if ck not in overlap:
                            overlap[ck] = ([], cand)
                        overlap[ck][0].append(m)

                for ck, (shared_list, cand) in overlap.items():
                    if len(shared_list) >= s:
                        rec = {**cand, "depth": d}
                        seen_keys.add(ck)
                        all_hedges.append(rec)
                        next_frontier.append(rec)
                        if len(all_hedges) >= max_hedges:
                            capped = True
                            break

            frontier = next_frontier

        # ── Step 3: pairwise s-edges O(n²) ───────────────────────────────
        n = len(all_hedges)
        s_edges: list[dict] = []
        for i in range(n):
            si = set(all_hedges[i]["members"])
            for j in range(i + 1, n):
                shared = list(si & set(all_hedges[j]["members"]))
                if len(shared) >= s:
                    s_edges.append({
                        "h1_idx": i, "h2_idx": j,
                        "shared": shared, "s_count": len(shared),
                    })

        elapsed = round((_t.perf_counter() - t0) * 1000, 1)
        return {
            "focal_node":  node_id,
            "table":       table,
            "s":           s,
            "depth":       depth,
            "hyperedges": [
                {**h, "idx": i, "member_count": len(h["members"])}
                for i, h in enumerate(all_hedges)
            ],
            "s_edges": s_edges,
            "stats": {
                "total_hyperedges":  n,
                "total_s_edges":     len(s_edges),
                "max_depth_reached": max((h["depth"] for h in all_hedges), default=0),
                "capped":            capped,
                "elapsed_ms":        elapsed,
            },
        }

    # Run the synchronous BFS in a thread pool so we don't block the event loop.
    loop = _asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run_bfs)


# ── Write path ────────────────────────────────────────────────────────────────

@router.post("/v1/hyperedges", response_model=HyperedgeOut,
             status_code=status.HTTP_201_CREATED, tags=["Hyperedges"],
             summary="Insert a hyperedge record")
async def insert_hyperedge(req: InsertRequest, db: DbDep, _: Any = _RW):
    try:
        db.insert(
            event_ts    = req.event_ts,
            members     = req.members,
            weight      = req.weight,
            mean_dist_m = req.mean_dist_m,
            formation   = req.formation,
        )
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)
    return HyperedgeOut(
        event_ts    = req.event_ts,
        members     = req.members,
        weight      = req.weight,
        mean_dist_m = req.mean_dist_m,
        formation   = req.formation,
    )


@router.delete("/v1/hyperedges", response_model=dict, tags=["Hyperedges"],
               summary="Delete a hyperedge (write tombstone)")
async def delete_hyperedge(req: DeleteRequest, db: DbDep, _: Any = _RW):
    try:
        db.delete(event_ts=req.event_ts, members=req.members)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)
    return {"deleted": True, "event_ts": req.event_ts, "members": req.members}


@router.post("/v1/hyperedges/compact", response_model=CompactOut,
             tags=["Hyperedges"], summary="Compact WAL into TPI+FMI")
async def compact(
    request: Request,
    db: DbDep,
    _: Any = _RW,
    req: CompactRequest | None = None,
):
    # Refuse new compactions during graceful shutdown
    if getattr(request.app.state, "shutting_down", False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server is shutting down — no new compactions accepted",
        )
    compact_lock = getattr(request.app.state, "compact_lock", None)
    before = db.wal_pending
    ttl = req.ttl_seconds if req else None
    tbl = req.table if req else None
    try:
        if compact_lock:
            async with compact_lock:
                if getattr(request.app.state, "shutting_down", False):
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Server is shutting down",
                    )
                db.compact(ttl_seconds=ttl, table=tbl)
        else:
            db.compact(ttl_seconds=ttl, table=tbl)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc, 500)
    return CompactOut(
        wal_pending_before = before,
        wal_pending_after  = db.wal_pending,
        compacted          = True,
        ttl_seconds        = ttl,
        table              = tbl,
    )


@router.post("/v1/autocompact", response_model=AutocompactOut,
             tags=["Database"],
             summary="Configure automatic WAL compaction")
async def set_autocompact(db: DbDep, req: AutocompactRequest, _: Any = _ADM):
    try:
        db.set_autocompact(req.threshold, table=req.table)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc, 400)
    return AutocompactOut(
        threshold = req.threshold,
        table     = req.table,
        applied   = True,
    )


@router.get("/v1/wal", response_model=WalStatusOut, tags=["Database"],
            summary="WAL status")
async def wal_status(db: DbDep, _: Any = _RO):
    return WalStatusOut(pending=db.wal_pending, threshold=100)


# ── Batch transaction ─────────────────────────────────────────────────────────

@router.post(
    "/v1/batch",
    response_model=BatchOut,
    tags=["Transactions"],
    summary="Atomic batch of operations (BEGIN / execute / COMMIT or ROLLBACK)",
    description=(
        "Execute a list of INSERT, DELETE, and MATCH operations atomically.\n\n"
        "All operations are buffered and committed together.  If any operation\n"
        "fails the entire batch is rolled back — inserts already written to the\n"
        "WAL are reversed with tombstone records.\n\n"
        "**Requires readwrite role.**\n\n"
        "Supported operation types:\n"
        "- `insert`  — requires `table`, `event_ts`, `members`\n"
        "- `delete`  — requires `table`, `event_ts`, `members`\n"
        "- `query`   — requires `cypher` (MATCH only; results included in response)\n"
    ),
)
async def batch_transaction(req: BatchRequest, db: DbDep, _: Any = _RW):
    from hypermeshdb._types import HyperMeshError as HME

    try:
        db.begin()
    except HME as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    results: list[BatchOpResult] = []
    commit_error: str | None = None

    for idx, op in enumerate(req.operations):
        try:
            if op.type == "insert":
                if op.event_ts is None or not op.members:
                    raise ValueError("insert requires event_ts and members")
                db.insert(
                    event_ts    = op.event_ts,
                    members     = op.members,
                    weight      = op.weight,
                    mean_dist_m = op.mean_dist_m,
                    formation   = op.formation,
                )
                results.append(BatchOpResult(op=idx, type="insert", success=True))

            elif op.type == "delete":
                if op.event_ts is None or not op.members:
                    raise ValueError("delete requires event_ts and members")
                db.delete(event_ts=op.event_ts, members=op.members)
                results.append(BatchOpResult(op=idx, type="delete", success=True))

            elif op.type == "query":
                if not op.cypher:
                    raise ValueError("query requires cypher")
                result = db.execute(op.cypher)
                rows   = result.to_dicts()
                results.append(BatchOpResult(op=idx, type="query",
                                             success=True, rows=rows))

            else:
                raise ValueError(f"Unknown operation type: {op.type!r}")

        except (HME, ValueError, Exception) as exc:
            results.append(BatchOpResult(op=idx, type=op.type,
                                         success=False, error=str(exc)))
            commit_error = str(exc)
            break

    if commit_error:
        try:
            db.rollback()
        except Exception:
            pass
        return BatchOut(
            committed   = False,
            ops_total   = len(req.operations),
            ops_success = sum(1 for r in results if r.success),
            error       = commit_error,
            results     = results,
        )

    try:
        db.commit()
    except HME as exc:
        return BatchOut(
            committed   = False,
            ops_total   = len(req.operations),
            ops_success = 0,
            error       = f"Commit failed: {exc}",
            results     = results,
        )

    return BatchOut(
        committed   = True,
        ops_total   = len(req.operations),
        ops_success = len(results),
        results     = results,
    )


# ── PSI index management ──────────────────────────────────────────────────────

@router.get("/v1/indexes", response_model=list[IndexOut], tags=["Schema"],
            summary="List PSI property indexes")
async def list_indexes(
    db:    DbDep,
    _:     Any = _RO,
    table: str = Query("", description="Filter by table name (case-insensitive)"),
):
    cypher = f"SHOW INDEXES ON {table}" if table else "SHOW INDEXES"
    try:
        result = db.execute(cypher)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc, 500)
    return [
        IndexOut(
            table      = row["table"],
            column     = row["column"],
            created_at = row["created_at"],
        )
        for row in result
    ]


@router.post("/v1/indexes", response_model=IndexOut,
             status_code=status.HTTP_201_CREATED, tags=["Schema"],
             summary="Create a PSI property index")
async def create_index(req: CreateIndexRequest, db: DbDep, _: Any = _ADM):
    try:
        db.execute(f"CREATE INDEX ON {req.table} ({req.column})")
        idx_result = db.execute(f"SHOW INDEXES ON {req.table}")
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)

    created_at = 0
    for row in idx_result:
        if row["column"].upper() == req.column.upper():
            created_at = row["created_at"]
            break

    return IndexOut(table=req.table.upper(), column=req.column.upper(),
                    created_at=created_at)


@router.delete("/v1/indexes/{table}/{col}", response_model=dict, tags=["Schema"],
               summary="Drop a PSI property index")
async def drop_index(table: str, col: str, db: DbDep, _: Any = _ADM):
    try:
        result = db.execute(f"DROP INDEX ON {safe_ident(table, 'table')} ({safe_ident(col, 'column')})")
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)
    row = result.fetchone()
    return {"table": row["table"], "column": row["column"], "dropped": row["dropped"]}


# ── Backup ────────────────────────────────────────────────────────────────────

@router.post("/v1/backup", response_model=BackupOut, tags=["Operations"],
             summary="Create a database backup archive",
             description=(
                 "Writes a gzip-compressed tar archive of the entire database "
                 "directory to ``backup_dir`` on the server.  If "
                 "``compact_first=true`` (default), all tables are compacted "
                 "before archiving for a clean snapshot.\n\n"
                 "**Requires admin role.**"
             ))
async def backup(req: BackupRequest, db: DbDep, request: Request,
                 _: Any = _ADM):
    db_dir: str = request.app.state.db_dir

    # Resolve/validate the destination *before* doing any work, so a traversal
    # attempt is rejected with 422 and never triggers compaction or I/O.
    backup_dir = safe_backup_dir(db_dir, req.backup_dir)

    if req.compact_first:
        try:
            db.compact()
        except HyperMeshError as exc:
            raise _hypermesh_exc(exc, 500)

    ts   = int(time.time())
    dest = os.path.join(backup_dir, f"hypermeshdb_backup_{ts}.tar.gz")

    # Collect table names
    try:
        tables_result = db.execute("CALL show_hyperedge_tables() RETURN *")
        table_names   = [row["name"] for row in tables_result]
    except Exception:
        table_names = []

    try:
        with tarfile.open(dest, "w:gz") as tf:
            tf.add(db_dir, arcname=os.path.basename(db_dir))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}")

    size = os.path.getsize(dest)
    log.info("backup created path=%s size=%d tables=%s", dest, size, table_names)
    return BackupOut(path=dest, size_bytes=size, tables=table_names,
                     compact_first=req.compact_first)


# ── API key management ────────────────────────────────────────────────────────

def _get_auth_store(request: Request) -> AuthStore:
    store = getattr(request.app.state, "auth_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Authentication is disabled on this server. "
                   "Restart with --auth to enable key management.",
        )
    return store


@router.get("/v1/keys", response_model=list[ApiKeyOut], tags=["Auth"],
            summary="List API keys (key hashes never returned)")
async def list_keys(request: Request, _: Any = _ADM):
    store = _get_auth_store(request)
    return [
        ApiKeyOut(
            key_id       = r.key_id,
            description  = r.description,
            role         = r.role,
            created_at   = r.created_at,
            last_used_at = r.last_used_at,
            expires_at   = r.expires_at,
        )
        for r in store.list_keys()
    ]


@router.post("/v1/keys", response_model=ApiKeyCreatedOut,
             status_code=status.HTTP_201_CREATED, tags=["Auth"],
             summary="Create an API key (plaintext returned once only)")
async def create_key(req: ApiKeyCreateRequest, request: Request, _: Any = _ADM):
    store = _get_auth_store(request)
    plaintext, rec = store.create_key(
        description=req.description,
        role=req.role,
        expires_in_days=req.expires_days,
    )
    return ApiKeyCreatedOut(
        key_id        = rec.key_id,
        description   = rec.description,
        role          = rec.role,
        created_at    = rec.created_at,
        last_used_at  = None,
        expires_at    = rec.expires_at,
        plaintext_key = plaintext,
    )


@router.delete("/v1/keys/{key_id}", response_model=dict, tags=["Auth"],
               summary="Revoke an API key")
async def revoke_key(key_id: str, request: Request, _: Any = _ADM):
    store = _get_auth_store(request)
    deleted = store.revoke(key_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Key '{key_id}' not found")
    return {"key_id": key_id, "revoked": True}


# ── Analytics endpoints (Phase 10) ────────────────────────────────────────────

class AnalyticsRequest(BaseModel):
    """Optional parameters forwarded to a specific analytics measure."""
    params: dict[str, Any] = Field(default_factory=dict)


class AnalyticsOut(BaseModel):
    """Response envelope for every analytics measure."""
    measure: str
    table:   str
    result:  Any


_ANALYTICS_MEASURES: set[str] = {
    "node_degree", "hyperedge_size", "density", "redundancy",
    "intersection_profile",
    "weighted_degree", "eigenvector_centrality", "pagerank",
    "katz_centrality", "hedc",
    "zhou_laplacian_eigenvalues", "spectral_gap", "cheeger_constant",
    "zhou_clustering", "pairwise_clustering", "global_transitivity",
    "hypermodularity",
    "s_adjacency", "s_distance", "s_closeness", "s_betweenness",
    "s_diameter", "s_efficiency",
    "hyperedge_persistence", "burstiness", "temporal_degree_entropy",
    "activity_windows", "temporal_changepoints",
    "summary",
}


@router.post(
    "/v1/analytics/{table}/{measure}",
    response_model=AnalyticsOut,
    tags=["Analytics"],
    summary="Compute a hypergraph measure",
)
async def run_analytics(
    table:   str,
    measure: str,
    db:      DbDep,
    _:       Any = _RO,
    req:     AnalyticsRequest = AnalyticsRequest(),
):
    if measure not in _ANALYTICS_MEASURES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown measure '{measure}'. Supported: {sorted(_ANALYTICS_MEASURES)}",
        )
    try:
        an = db.analytics(table)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    fn = getattr(an, measure)
    try:
        raw = fn(**req.params)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{measure}() failed: {exc}")

    return AnalyticsOut(
        measure=measure,
        table=table,
        result=_serialise_analytics(raw),
    )


# ── Tables ────────────────────────────────────────────────────────────────────

@router.get("/v1/tables", response_model=list[TableOut], tags=["Schema"],
            summary="List all hyperedge tables")
async def list_tables(db: DbDep):
    try:
        result = db.execute("CALL show_hyperedge_tables() RETURN *")
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc, 500)
    return [
        TableOut(
            name              = row["name"],
            member_tables     = row["member_tables"],
            bucket_seconds    = row["bucket_seconds"],
            compact_threshold = row["compact_threshold"],
            row_count         = row["row_count"],
        )
        for row in result
    ]


@router.get("/v1/tables/{name}", response_model=TableOut, tags=["Schema"],
            summary="Get one table definition",
            responses={404: {"description": "Table not found"}})
async def get_table(name: str, db: DbDep):
    try:
        result = db.execute("CALL show_hyperedge_tables() RETURN *")
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc, 500)
    for row in result:
        if row["name"].upper() == name.upper():
            return TableOut(
                name              = row["name"],
                member_tables     = row["member_tables"],
                bucket_seconds    = row["bucket_seconds"],
                compact_threshold = row["compact_threshold"],
                row_count         = row["row_count"],
            )
    raise HTTPException(status_code=404, detail=f"Table '{name}' not found")


@router.post("/v1/tables", response_model=TableOut,
             status_code=status.HTTP_201_CREATED, tags=["Schema"],
             summary="Create a hyperedge table")
async def create_table(req: CreateTableRequest, db: DbDep):
    members_csv = ", ".join(req.member_tables)
    cypher = (
        f"CREATE HYPEREDGE TABLE {req.name} ({members_csv}) "
        f"BUCKET_SECONDS {req.bucket_seconds} "
        f"COMPACT_THRESHOLD {req.compact_threshold}"
    )
    try:
        result = db.execute(cypher)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)
    row = result.fetchone()
    return TableOut(
        name              = row["name"],
        member_tables     = row["member_tables"],
        bucket_seconds    = row["bucket_seconds"],
        compact_threshold = row["compact_threshold"],
        row_count         = 0,
    )


@router.delete("/v1/tables/{name}", response_model=dict, tags=["Schema"],
               summary="Drop a hyperedge table")
async def drop_table(name: str, db: DbDep):
    try:
        result = db.execute(f"DROP HYPEREDGE TABLE {safe_ident(name, 'name')}")
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)
    row = result.fetchone()
    return {"name": row["name"], "dropped": row["dropped"]}


# ── Cypher query ──────────────────────────────────────────────────────────────

@router.post("/v1/query", response_model=QueryOut, tags=["Query"],
             summary="Execute a Cypher query")
async def execute_query(req: CypherRequest, db: DbDep):
    try:
        result = db.execute(req.query, parameters=req.parameters)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)

    plan = None
    if result.query_plan:
        p = result.query_plan
        plan = QueryPlanOut(
            strategy        = p.strategy,
            buckets_scanned = p.buckets_scanned,
            total_buckets   = p.total_buckets,
            speedup_factor  = p.speedup_factor,
            elapsed_us      = p.elapsed_us,
        )
    return QueryOut(
        columns    = result.columns,
        rows       = result.to_dicts(),
        num_tuples = result.num_tuples,
        query_plan = plan,
    )


# ── Range query ───────────────────────────────────────────────────────────────

@router.get("/v1/hyperedges", response_model=RangeQueryOut, tags=["Hyperedges"],
            summary="Temporal range query (TPI bucket pushdown)")
async def range_query(
    db:       DbDep,
    start_ts: int = Query(..., ge=0, description="Start timestamp (inclusive)"),
    end_ts:   int = Query(..., ge=0, description="End timestamp (inclusive)"),
    table:    str = Query("CoProximity", description="Hyperedge table name"),
):
    cypher = (
        f"MATCH HYPEREDGE (he:{table}) "
        f"WHERE he.event_ts >= {start_ts} AND he.event_ts <= {end_ts} RETURN *"
    )
    try:
        result = db.execute(cypher)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)

    plan = None
    if result.query_plan:
        p = result.query_plan
        plan = QueryPlanOut(
            strategy        = p.strategy,
            buckets_scanned = p.buckets_scanned,
            total_buckets   = p.total_buckets,
            speedup_factor  = p.speedup_factor,
            elapsed_us      = p.elapsed_us,
        )
    return RangeQueryOut(
        hyperedges = [
            HyperedgeOut(
                event_ts    = row["event_ts"],
                members     = row["members"],
                weight      = row["weight"],
                mean_dist_m = row["mean_dist_m"],
                formation   = row["formation"],
            )
            for row in result
        ],
        num_tuples = result.num_tuples,
        query_plan = plan,
    )


# NOTE: an earlier /v1/hyperedges/node/{node_id} handler is registered above
# (~line 615) with V2 property forwarding.  We keep this duplicate in place as
# a deliberate no-op so FastAPI's last-write-wins routing keeps the V2-aware
# version, but if you find yourself editing this block be aware of the dupe.


# ── Write path ────────────────────────────────────────────────────────────────

@router.post("/v1/hyperedges", response_model=HyperedgeOut,
             status_code=status.HTTP_201_CREATED, tags=["Hyperedges"],
             summary="Insert a hyperedge record")
async def insert_hyperedge(req: InsertRequest, db: DbDep):
    try:
        db.insert(
            event_ts    = req.event_ts,
            members     = req.members,
            weight      = req.weight,
            mean_dist_m = req.mean_dist_m,
            formation   = req.formation,
        )
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)
    return HyperedgeOut(
        event_ts    = req.event_ts,
        members     = req.members,
        weight      = req.weight,
        mean_dist_m = req.mean_dist_m,
        formation   = req.formation,
    )


@router.delete("/v1/hyperedges", response_model=dict, tags=["Hyperedges"],
               summary="Delete a hyperedge (write tombstone)")
async def delete_hyperedge(req: DeleteRequest, db: DbDep):
    try:
        db.delete(event_ts=req.event_ts, members=req.members)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)
    return {"deleted": True, "event_ts": req.event_ts, "members": req.members}


@router.post("/v1/hyperedges/compact", response_model=CompactOut,
             tags=["Hyperedges"], summary="Compact WAL into TPI+FMI")
async def compact(db: DbDep, req: CompactRequest | None = None):
    before = db.wal_pending
    ttl = req.ttl_seconds if req else None
    tbl = req.table if req else None
    try:
        db.compact(ttl_seconds=ttl, table=tbl)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc, 500)
    return CompactOut(
        wal_pending_before = before,
        wal_pending_after  = db.wal_pending,
        compacted          = True,
        ttl_seconds        = ttl,
        table              = tbl,
    )


@router.post("/v1/autocompact", response_model=AutocompactOut,
             tags=["Database"],
             summary="Configure automatic WAL compaction",
             description=(
                 "Set the WAL entry threshold that triggers automatic compaction "
                 "after each write.  Set threshold=0 to disable.  "
                 "The setting is persisted in the schema catalog and restored on "
                 "next open."
             ))
async def set_autocompact(db: DbDep, req: AutocompactRequest):
    try:
        db.set_autocompact(req.threshold, table=req.table)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc, 400)
    return AutocompactOut(
        threshold = req.threshold,
        table     = req.table,
        applied   = True,
    )


@router.get("/v1/wal", response_model=WalStatusOut, tags=["Database"],
            summary="WAL status")
async def wal_status(db: DbDep):
    return WalStatusOut(pending=db.wal_pending, threshold=100)


# ── PSI index management ──────────────────────────────────────────────────────

@router.get("/v1/indexes", response_model=list[IndexOut], tags=["Schema"],
            summary="List PSI property indexes",
            description=(
                "Returns all Property Secondary Indexes (PSI) registered in the "
                "schema catalog.  Use the optional `table` parameter to filter "
                "by table name."
            ))
async def list_indexes(
    db:    DbDep,
    table: str = Query("", description="Filter by table name (case-insensitive)"),
):
    cypher = f"SHOW INDEXES ON {table}" if table else "SHOW INDEXES"
    try:
        result = db.execute(cypher)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc, 500)
    return [
        IndexOut(
            table      = row["table"],
            column     = row["column"],
            created_at = row["created_at"],
        )
        for row in result
    ]


@router.post("/v1/indexes", response_model=IndexOut,
             status_code=status.HTTP_201_CREATED, tags=["Schema"],
             summary="Create a PSI property index",
             description=(
                 "Builds a Property Secondary Index (PSI) on a numeric column of "
                 "a hyperedge table.  The index is built immediately from the "
                 "current TPI state and persisted to disk as a sorted flat file."
             ))
async def create_index(req: CreateIndexRequest, db: DbDep):
    try:
        db.execute(f"CREATE INDEX ON {req.table} ({req.column})")
        # Fetch the created_at timestamp from the catalog
        idx_result = db.execute(f"SHOW INDEXES ON {req.table}")
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)

    created_at = 0
    for row in idx_result:
        if row["column"].upper() == req.column.upper():
            created_at = row["created_at"]
            break

    return IndexOut(table=req.table.upper(), column=req.column.upper(),
                    created_at=created_at)


@router.delete("/v1/indexes/{table}/{col}", response_model=dict, tags=["Schema"],
               summary="Drop a PSI property index",
               description=(
                   "Removes the Property Secondary Index (PSI) for `col` on `table`. "
                   "The on-disk PSI file and catalog entry are both deleted."
               ))
async def drop_index(table: str, col: str, db: DbDep):
    try:
        result = db.execute(f"DROP INDEX ON {safe_ident(table, 'table')} ({safe_ident(col, 'column')})")
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)
    row = result.fetchone()
    return {"table": row["table"], "column": row["column"], "dropped": row["dropped"]}


# ── Analytics endpoints (Phase 10) ────────────────────────────────────────────

class AnalyticsRequest(BaseModel):
    """Optional parameters forwarded to a specific analytics measure."""
    params: dict[str, Any] = Field(default_factory=dict)


class AnalyticsOut(BaseModel):
    """Response envelope for every analytics measure."""
    measure: str
    table:   str
    result:  Any


# Supported measures and their expected optional kwargs
_ANALYTICS_MEASURES: set[str] = {
    # Structural
    "node_degree", "hyperedge_size", "density", "redundancy",
    "intersection_profile",
    # Centrality
    "weighted_degree", "eigenvector_centrality", "pagerank",
    "katz_centrality", "hedc",
    # Spectral
    "zhou_laplacian_eigenvalues", "spectral_gap", "cheeger_constant",
    # Clustering
    "zhou_clustering", "pairwise_clustering", "global_transitivity",
    # Modularity
    "hypermodularity",
    # s-Walk
    "s_adjacency", "s_distance", "s_closeness", "s_betweenness",
    "s_diameter", "s_efficiency",
    # Temporal
    "hyperedge_persistence", "burstiness", "temporal_degree_entropy",
    "activity_windows", "temporal_changepoints",
    # Summary
    "summary",
}


def _serialise_analytics(raw: Any) -> Any:
    """
    Convert analytics results into JSON-serialisable form.

    - ``dict`` with tuple keys  → list of ``[[k1, k2, v], ...]``
    - ``dict`` with scalar keys → dict with string keys
    - scalars/lists             → returned as-is
    """
    if isinstance(raw, dict):
        first_key = next(iter(raw), None)
        if isinstance(first_key, tuple):
            return [[*k, v] for k, v in raw.items()]
        return {str(k): v for k, v in raw.items()}
    return raw


@router.post(
    "/v1/analytics/{table}/{measure}",
    response_model=AnalyticsOut,
    tags=["Analytics"],
    summary="Compute a hypergraph measure",
    description=(
        "Materialises the named table into a sparse incidence matrix and "
        "computes the requested measure.  Pass optional keyword arguments "
        "in the JSON body under `params` (e.g. `{\"s\": 2, \"alpha\": 0.1}`).\n\n"
        "**Available measures**: " + ", ".join(sorted(_ANALYTICS_MEASURES))
    ),
)
async def run_analytics(
    table:   str,
    measure: str,
    db:      DbDep,
    req:     AnalyticsRequest = AnalyticsRequest(),
):
    if measure not in _ANALYTICS_MEASURES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown measure '{measure}'. "
                f"Supported: {sorted(_ANALYTICS_MEASURES)}"
            ),
        )
    try:
        an = db.analytics(table)
    except HyperMeshError as exc:
        raise _hypermesh_exc(exc)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    fn = getattr(an, measure)
    try:
        raw = fn(**req.params)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{measure}() failed: {exc}")

    return AnalyticsOut(
        measure=measure,
        table=table,
        result=_serialise_analytics(raw),
    )


# ── Generic ingestion endpoints ───────────────────────────────────────────────

class IngestColumnDef(BaseModel):
    name:          str
    role:          str            # "timestamp" | "entity" | "property" | "skip"
    entity_type:   str  = "generic"
    sample_values: list[str] = Field(default_factory=list)
    null_pct:      float     = 0.0
    dtype_hint:    str       = "string"


class NativeStatsOut(BaseModel):
    total_simplices:           int
    multi_member_simplices:    int
    size_1_skipped:            int
    time_range_epoch:          list[int]
    time_range_label:          list[str]
    unique_nodes:              int
    member_size_distribution:  dict[str, int]
    has_node_labels:           bool
    node_label_sample:         list[dict]


class IngestSchemaOut(BaseModel):
    columns:               list[IngestColumnDef]
    row_count:             int
    suggested_ts_col:      str | None
    suggested_entity_cols: list[str]
    sample_hyperedges:     list[dict]
    ingest_mode:           str             = "tabular"
    native_stats:          NativeStatsOut | None = None


class IngestConfigIn(BaseModel):
    table_name:       str
    ts_column:        str          = ""
    entity_columns:   list[str]    = Field(default_factory=list)
    property_columns: list[str]    = Field(default_factory=list)
    entity_types:     dict[str, str] = Field(default_factory=dict)
    formation_column: str | None   = None
    drop_existing:    bool         = True
    compact_after:    bool         = True
    rows_cap:         int | None   = None
    ingest_mode:      str          = "tabular"


class IngestRunOut(BaseModel):
    table:         str
    inserted:      int
    errors:        int
    elapsed_s:     float
    entity_count:  int
    entity_map_path: str | None = None


@router.post(
    "/v1/ingest/preview",
    response_model=IngestSchemaOut,
    tags=["Ingestion"],
    summary="Upload a file and infer its hyperedge schema",
    description=(
        "Upload a CSV, XLSX, JSON, JSON-Lines, or Parquet file.  "
        "Returns auto-detected column roles (timestamp/entity/property/skip) "
        "and 3 sample hyperedges so the user can verify the mapping before committing."
    ),
)
async def ingest_preview(
    file: UploadFile = File(..., description="Data file (CSV/XLSX/JSON/JSONL/Parquet)"),
    sample_n: int = Query(200, ge=10, le=5000, description="Rows to sample for inference"),
):
    from hypermeshdb.ingest.generic import load_records, infer_schema, ColumnDef as _CD

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    filename = file.filename or "upload.csv"
    try:
        records, total, native_meta = load_records(content, filename)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot parse file: {exc}")

    if not records:
        raise HTTPException(status_code=422, detail="File contains no rows")

    try:
        schema = infer_schema(records, metadata=native_meta, sample_n=sample_n)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Schema inference failed: {exc}")

    native_stats_out = None
    if schema.native_stats:
        ns = schema.native_stats
        native_stats_out = NativeStatsOut(
            total_simplices=ns.total_simplices,
            multi_member_simplices=ns.multi_member_simplices,
            size_1_skipped=ns.size_1_skipped,
            time_range_epoch=ns.time_range_epoch,
            time_range_label=ns.time_range_label,
            unique_nodes=ns.unique_nodes,
            member_size_distribution=ns.member_size_distribution,
            has_node_labels=ns.has_node_labels,
            node_label_sample=ns.node_label_sample,
        )

    return IngestSchemaOut(
        columns=[
            IngestColumnDef(
                name=c.name,
                role=c.role,
                entity_type=c.entity_type,
                sample_values=c.sample_values,
                null_pct=c.null_pct,
                dtype_hint=c.dtype_hint,
            )
            for c in schema.columns
        ],
        row_count=total,
        suggested_ts_col=schema.suggested_ts_col,
        suggested_entity_cols=schema.suggested_entity_cols,
        sample_hyperedges=schema.sample_hyperedges,
        ingest_mode=schema.ingest_mode,
        native_stats=native_stats_out,
    )


@router.post(
    "/v1/ingest/run",
    response_model=IngestRunOut,
    tags=["Ingestion"],
    summary="Ingest an uploaded file into a hyperedge table",
    description=(
        "Upload a data file together with a JSON ingestion config.  "
        "The engine resolves entity columns to integer node IDs, builds "
        "hyperedges, batch-inserts them, compacts the WAL, and saves an "
        "entity map for label decoding.\n\n"
        "**config** must be a JSON string matching IngestConfigIn."
    ),
)
async def ingest_run(
    request: Request,
    db:      DbDep,
    file:   UploadFile = File(...),
    config: str        = Form(..., description="JSON-encoded IngestConfigIn"),
):
    from hypermeshdb.ingest.generic import (
        load_records, IngestionConfig, run_ingestion,
    )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    filename = file.filename or "upload.csv"

    try:
        cfg_dict = json.loads(config)
        cfg = IngestionConfig(**cfg_dict)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid config: {exc}")

    try:
        records, _, native_meta = load_records(content, filename)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot parse file: {exc}")

    if not records:
        raise HTTPException(status_code=422, detail="File contains no rows")

    # Resolve entity map save path — saved inside the DB directory
    db_dir  = getattr(request.app.state, "db_dir", "")
    em_path = None
    if db_dir:
        safe_tbl = cfg.table_name.upper().replace(" ", "_")
        em_path  = os.path.join(db_dir, f"{safe_tbl}_entity_map.json")

    try:
        result = run_ingestion(records, cfg, db, entity_map_path=em_path,
                               native_metadata=native_meta)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")

    return IngestRunOut(
        table=result.table,
        inserted=result.inserted,
        errors=result.errors,
        elapsed_s=result.elapsed_s,
        entity_count=result.entity_count,
        entity_map_path=result.entity_map_path,
    )


@router.get(
    "/v1/ingest/entity-map/{table}",
    tags=["Ingestion"],
    summary="Return the entity map for a previously ingested table",
    description=(
        "Returns the string-entity-to-integer-ID map written by the last "
        "ingestion run for `table`.  Used by the Graph Explorer to resolve "
        "node IDs back to human-readable labels."
    ),
)
async def ingest_entity_map(
    table:   str,
    request: Request,
    _:       Any = _RO,
):
    db_dir = getattr(request.app.state, "db_dir", "")
    if not db_dir:
        raise HTTPException(status_code=503, detail="DB directory not known")

    safe_tbl = table.upper().replace(" ", "_")
    # Check DB dir first (written by ingest_run), then project data dir (legacy)
    candidates = [
        os.path.join(db_dir, f"{safe_tbl}_entity_map.json"),
        os.path.join(os.path.dirname(db_dir), "data", f"{safe_tbl}_entity_map.json"),
        os.path.join(os.path.dirname(db_dir), "data", "threat_entity_map.json"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            try:
                return json.loads(Path(p).read_text("utf-8"))
            except Exception:
                pass

    return {}


# ── Pattern detection endpoint ────────────────────────────────────────────────

class PatternFindingOut(BaseModel):
    id:             str
    type:           str
    severity:       str
    title:          str
    description:    str
    evidence:       dict[str, Any]
    affected_nodes: list[int]
    query:          str
    remediation:    str


class PatternDetectionOut(BaseModel):
    table_name:     str
    elapsed_s:      float
    findings:       list[PatternFindingOut]
    summary:        dict[str, Any]


@router.post(
    "/v1/patterns/detect/{table}",
    response_model=PatternDetectionOut,
    tags=["Patterns"],
    summary="Automatically detect security patterns in a hyperedge table",
)
async def detect_patterns(
    table:      str,
    full_scan:  bool = Query(False, description="Run heavy detectors (lateral movement, beaconing entropy)"),
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    """
    Run the automated pattern detection engine against the specified table.

    **Fast detectors** (always run, < 5 s on 90 k-row tables):
    - Hub nodes (degree > 2σ above mean)
    - Temporal burst (burstiness coefficient)
    - Dense cluster (density + redundancy)
    - Fan-out entities (> 30% of events)
    - Spectral anomaly (connectivity)

    **Heavy detectors** (opt-in via `full_scan=true`, adds 10–30 s on large tables):
    - Lateral movement via s=2 adjacency chains
    - Beaconing via per-node temporal entropy
    """
    from hypermeshdb.patterns.detector import PatternDetector
    import json as _json

    try:
        an = db.analytics(table.upper())
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Table '{table}' not found: {exc}") from exc

    # Load entity map (best-effort)
    em: dict = {}
    db_dir = getattr(db, "_db_dir", None) or os.environ.get("HMDB_DIR", "")
    safe_tbl = table.upper().replace(" ", "_")
    candidates = []
    if db_dir:
        candidates.append(os.path.join(db_dir, f"{safe_tbl}_entity_map.json"))
    candidates += [
        os.path.join("data", f"{safe_tbl}_entity_map.json"),
        os.path.join("data", "threat_entity_map.json"),
    ]
    for p in candidates:
        try:
            em = _json.loads(Path(p).read_text("utf-8"))
            break
        except Exception:
            pass

    detector = PatternDetector(an, entity_map=em, table_name=table.upper())

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: detector.detect(full_scan=full_scan)
        )
    except Exception as exc:
        logging.exception("Pattern detection failed for table %s", table)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result.as_dict()


# ── Report generation endpoints ───────────────────────────────────────────────

class ReportMeta(BaseModel):
    report_id:      str
    table_name:     str
    generated_at:   str
    elapsed_s:      float
    risk_score:     int
    total_findings: int
    severity_counts: dict[str, int]
    full_scan:      bool


class GenerateReportOut(BaseModel):
    report_id:   str
    table_name:  str
    generated_at: str
    elapsed_s:   float
    risk_score:  int
    total_findings: int
    severity_counts: dict[str, int]
    findings:    list[dict]
    summary:     dict[str, Any]


def _reports_dir(request: "Request") -> Path:
    """Return (and create) the reports storage directory."""
    db_dir = getattr(request.app.state, "db_dir", "") or os.environ.get("HMDB_DIR", "/tmp/hypermesh_db")
    d = Path(db_dir) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_entity_map_for_table(table: str, db_dir: str) -> dict:
    import json as _json
    safe_tbl = table.upper().replace(" ", "_")
    candidates = []
    if db_dir:
        candidates.append(os.path.join(db_dir, f"{safe_tbl}_entity_map.json"))
    candidates += [
        os.path.join("data", f"{safe_tbl}_entity_map.json"),
        os.path.join("data", "threat_entity_map.json"),
    ]
    for p in candidates:
        try:
            return _json.loads(Path(p).read_text("utf-8"))
        except Exception:
            pass
    return {}


@router.post(
    "/v1/reports/generate/{table}",
    response_model=GenerateReportOut,
    tags=["Reports"],
    summary="Run pattern detection and generate a persisted report",
)
async def generate_report(
    table:      str,
    full_scan:  bool = Query(False, description="Run heavy detectors"),
    request:    Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    """
    Runs the pattern detection engine, generates HTML + STIX 2.1 reports,
    persists them to ``{db_dir}/reports/{report_id}/``, and returns the
    full detection payload.
    """
    import uuid as _uuid
    import json as _json
    from hypermeshdb.patterns.detector import PatternDetector
    from hypermeshdb.reports.html_renderer import render_html_report
    from hypermeshdb.reports.stix_exporter import build_stix_bundle

    try:
        an = db.analytics(table.upper())
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Table '{table}' not found: {exc}") from exc

    db_dir = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "/tmp/hypermesh_db")
    em = _load_entity_map_for_table(table, db_dir)

    detector = PatternDetector(an, entity_map=em, table_name=table.upper())
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: detector.detect(full_scan=full_scan)
        )
    except Exception as exc:
        logging.exception("Pattern detection failed for table %s", table)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    result_dict  = result.as_dict()
    report_id    = str(_uuid.uuid4())
    generated_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Render HTML and STIX
    html_content  = render_html_report(result_dict, em, report_id)
    stix_bundle   = build_stix_bundle(result_dict, em, report_id)

    # Persist to disk
    rdir = _reports_dir(request) if request else Path(db_dir) / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    report_dir = rdir / report_id
    report_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "report_id":      report_id,
        "table_name":     table.upper(),
        "generated_at":   generated_at,
        "elapsed_s":      round(result.elapsed_s, 3),
        "risk_score":     result.summary.get("risk_score", 0),
        "total_findings": len(result.findings),
        "severity_counts": result.summary.get("severity_counts", {}),
        "full_scan":      full_scan,
    }
    (report_dir / "meta.json").write_text(_json.dumps(meta, indent=2), encoding="utf-8")
    (report_dir / "report.html").write_text(html_content, encoding="utf-8")
    (report_dir / "report.stix.json").write_text(_json.dumps(stix_bundle, indent=2), encoding="utf-8")
    (report_dir / "findings.json").write_text(_json.dumps(result_dict, indent=2), encoding="utf-8")

    return {
        **result_dict,
        "report_id":      report_id,
        "generated_at":   generated_at,
        "risk_score":     result.summary.get("risk_score", 0),
        "total_findings": len(result.findings),
        "severity_counts": result.summary.get("severity_counts", {}),
    }


@router.get(
    "/v1/reports",
    response_model=list[ReportMeta],
    tags=["Reports"],
    summary="List all generated reports",
)
async def list_reports(
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    import json as _json
    rdir = _reports_dir(request) if request else Path(
        getattr(db, "_db_dir", "/tmp/hypermesh_db")
    ) / "reports"

    metas = []
    if rdir.exists():
        for d in sorted(rdir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            meta_file = d / "meta.json"
            if meta_file.exists():
                try:
                    metas.append(_json.loads(meta_file.read_text("utf-8")))
                except Exception:
                    pass
    return metas


@router.get(
    "/v1/reports/{report_id}/html",
    tags=["Reports"],
    summary="Download the HTML report",
    response_class=PlainTextResponse,
)
async def download_report_html(
    report_id: str,
    request:   Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from fastapi.responses import HTMLResponse
    rdir = _reports_dir(request) if request else Path(
        os.environ.get("HMDB_DIR", "/tmp/hypermesh_db")
    ) / "reports"
    html_path = rdir / report_id / "report.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    content = html_path.read_text("utf-8")
    return HTMLResponse(
        content=content,
        headers={"Content-Disposition": f'attachment; filename="hypermesh_report_{report_id[:8]}.html"'},
    )


@router.get(
    "/v1/reports/{report_id}/stix",
    tags=["Reports"],
    summary="Download the STIX 2.1 bundle",
)
async def download_report_stix(
    report_id: str,
    request:   Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    import json as _json
    rdir = _reports_dir(request) if request else Path(
        os.environ.get("HMDB_DIR", "/tmp/hypermesh_db")
    ) / "reports"
    stix_path = rdir / report_id / "report.stix.json"
    if not stix_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    content = _json.loads(stix_path.read_text("utf-8"))
    from fastapi.responses import JSONResponse
    return JSONResponse(
        content=content,
        headers={"Content-Disposition": f'attachment; filename="hypermesh_stix_{report_id[:8]}.json"'},
    )


@router.get(
    "/v1/reports/{report_id}/json",
    tags=["Reports"],
    summary="Download the raw findings JSON",
)
async def download_report_json(
    report_id: str,
    request:   Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    import json as _json
    rdir = _reports_dir(request) if request else Path(
        os.environ.get("HMDB_DIR", "/tmp/hypermesh_db")
    ) / "reports"
    json_path = rdir / report_id / "findings.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    content = _json.loads(json_path.read_text("utf-8"))
    from fastapi.responses import JSONResponse
    return JSONResponse(
        content=content,
        headers={"Content-Disposition": f'attachment; filename="hypermesh_findings_{report_id[:8]}.json"'},
    )


@router.delete(
    "/v1/reports/{report_id}",
    tags=["Reports"],
    summary="Delete a saved report",
)
async def delete_report(
    report_id: str,
    request:   Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RW,
):
    import shutil
    rdir = _reports_dir(request) if request else Path(
        os.environ.get("HMDB_DIR", "/tmp/hypermesh_db")
    ) / "reports"
    report_dir = rdir / report_id
    if not report_dir.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    shutil.rmtree(report_dir)
    return {"deleted": report_id}


# ── Connector hub endpoints ───────────────────────────────────────────────────

def _get_connector_store(request: "Request") -> "ConnectorStore":
    from hypermeshdb.connectors.config_store import ConnectorStore
    db_dir = getattr(request.app.state if request else None, "db_dir", "") \
             or os.environ.get("HMDB_DIR", "/tmp/hypermesh_db")
    return ConnectorStore(db_dir)


class ConnectorCreateRequest(BaseModel):
    name:         str
    type:         str
    enabled:      bool                    = True
    target_table: str                     = ""
    credentials:  dict[str, Any]          = Field(default_factory=dict)
    settings:     dict[str, Any]          = Field(default_factory=dict)


class ConnectorUpdateRequest(BaseModel):
    name:         str | None              = None
    enabled:      bool | None             = None
    target_table: str | None              = None
    credentials:  dict[str, Any] | None   = None
    settings:     dict[str, Any] | None   = None


@router.get(
    "/v1/connectors",
    tags=["Connectors"],
    summary="List all configured connectors",
)
async def list_connectors(
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    store = _get_connector_store(request)
    return [c.as_public_dict() for c in store.list_all()]


@router.post(
    "/v1/connectors",
    tags=["Connectors"],
    summary="Create a new connector",
)
async def create_connector(
    req:     ConnectorCreateRequest,
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RW,
):
    from hypermeshdb.connectors.base import ConnectorConfig, CONNECTOR_TYPES
    if req.type not in CONNECTOR_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown connector type: {req.type!r}. Valid: {list(CONNECTOR_TYPES)}")
    store  = _get_connector_store(request)
    config = ConnectorConfig(
        name=req.name, type=req.type, enabled=req.enabled,
        target_table=req.target_table.upper() if req.target_table else "",
        credentials=req.credentials, settings=req.settings,
    )
    store.save(config)
    return config.as_public_dict()


@router.get(
    "/v1/connectors/{connector_id}",
    tags=["Connectors"],
    summary="Get a connector by ID",
)
async def get_connector(
    connector_id: str,
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    store  = _get_connector_store(request)
    config = store.get(connector_id)
    if not config:
        raise HTTPException(status_code=404, detail="Connector not found")
    return config.as_public_dict()


@router.put(
    "/v1/connectors/{connector_id}",
    tags=["Connectors"],
    summary="Update a connector",
)
async def update_connector(
    connector_id: str,
    req:     ConnectorUpdateRequest,
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RW,
):
    store  = _get_connector_store(request)
    config = store.get(connector_id)
    if not config:
        raise HTTPException(status_code=404, detail="Connector not found")
    if req.name         is not None: config.name         = req.name
    if req.enabled      is not None: config.enabled      = req.enabled
    if req.target_table is not None: config.target_table = req.target_table.upper()
    if req.credentials  is not None: config.credentials  = req.credentials
    if req.settings     is not None: config.settings     = req.settings
    store.save(config)
    return config.as_public_dict()


@router.delete(
    "/v1/connectors/{connector_id}",
    tags=["Connectors"],
    summary="Delete a connector",
)
async def delete_connector(
    connector_id: str,
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RW,
):
    store = _get_connector_store(request)
    if not store.delete(connector_id):
        raise HTTPException(status_code=404, detail="Connector not found")
    return {"deleted": connector_id}


@router.post(
    "/v1/connectors/{connector_id}/test",
    tags=["Connectors"],
    summary="Test a connector's connection",
)
async def test_connector(
    connector_id: str,
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.connectors.base import build_connector
    store  = _get_connector_store(request)
    config = store.get(connector_id)
    if not config:
        raise HTTPException(status_code=404, detail="Connector not found")
    try:
        connector = build_connector(config, db)
        result    = await asyncio.get_event_loop().run_in_executor(
            None, connector.test_connection
        )
        return result
    except Exception as exc:
        return {"ok": False, "message": str(exc), "latency_ms": 0}


@router.post(
    "/v1/connectors/{connector_id}/sync",
    tags=["Connectors"],
    summary="Trigger a sync run for a connector",
)
async def sync_connector(
    connector_id: str,
    limit:   int = Query(10_000, ge=1, le=500_000, description="Max records per sync run"),
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RW,
):
    from hypermeshdb.connectors.base import build_connector, STATUS_OK, STATUS_ERROR
    store  = _get_connector_store(request)
    config = store.get(connector_id)
    if not config:
        raise HTTPException(status_code=404, detail="Connector not found")
    if not config.enabled:
        raise HTTPException(status_code=400, detail="Connector is disabled")

    try:
        connector = build_connector(config, db)
        result    = await asyncio.get_event_loop().run_in_executor(
            None, lambda: connector.sync(limit=limit)
        )
        store.update_sync_status(
            connector_id,
            status    = STATUS_OK,
            count     = result.inserted,
            elapsed_s = result.elapsed_s,
        )
        return result.as_dict()
    except Exception as exc:
        store.update_sync_status(connector_id, status=STATUS_ERROR, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/v1/connectors/{connector_id}/preview",
    tags=["Connectors"],
    summary="Preview schema for a connector",
)
async def preview_connector_schema(
    connector_id: str,
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.connectors.base import build_connector
    store  = _get_connector_store(request)
    config = store.get(connector_id)
    if not config:
        raise HTTPException(status_code=404, detail="Connector not found")
    try:
        connector = build_connector(config, db)
        result    = await asyncio.get_event_loop().run_in_executor(
            None, connector.preview_schema
        )
        return result
    except Exception as exc:
        return {"columns": [], "sample_rows": [], "error": str(exc)}


@router.post(
    "/v1/connectors/webhook/{table}",
    tags=["Connectors"],
    summary="Push events into a webhook connector queue",
    status_code=202,
)
async def webhook_receive(
    table:   str,
    payload: Any = None,
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
):
    """
    Inbound webhook receiver endpoint.

    POST JSON to this URL to enqueue events for a webhook connector whose
    ``target_table`` matches ``table``.  The payload can be:
      - A single event object: ``{ "members": [...], "ts": ..., "props": {...} }``
      - A list of event objects
      - Free-form JSON (entity columns auto-detected)
      - Raw JSON body

    The events are queued immediately and flushed to HyperMesh on the next
    sync run (or when ``POST /v1/connectors/{id}/sync`` is called).
    """
    from hypermeshdb.connectors.base import build_connector
    from hypermeshdb.connectors.webhook import WebhookConnector

    store   = _get_connector_store(request)
    configs = store.list_all()

    # Find a webhook connector matching this table
    matching = [
        c for c in configs
        if c.type == "webhook" and c.target_table.upper() == table.upper()
    ]

    if not matching:
        # Auto-create a connector for this table
        from hypermeshdb.connectors.base import ConnectorConfig
        config = ConnectorConfig(
            name         = f"Webhook — {table.upper()}",
            type         = "webhook",
            target_table = table.upper(),
        )
        store.save(config)
    else:
        config = matching[0]

    connector = WebhookConnector(config, db)

    if payload is None:
        # Try reading raw body
        body = await request.body() if request else b""
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            payload = {}

    n = connector.enqueue(payload)
    return {"queued": n, "table": table.upper(), "connector_id": config.id}


# ── Workspace endpoints ──────────────────────────────────────────────────────
#
#  GET  /v1/workspaces         — list all workspace definitions
#  GET  /v1/workspaces/{id}    — single workspace config
#  GET  /v1/snn/results/{table}— per-window SNN anomaly scores
#

def _workspaces_file(db_state) -> Path:
    """Resolve workspaces.json — next to the DB directory."""
    db_dir = Path(db_state.db_dir)
    candidates = [
        db_dir / "workspaces.json",
        db_dir.parent / "workspaces.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]  # preferred location (db_dir itself)


@router.get("/v1/workspaces", tags=["Workspaces"])
async def list_workspaces(request: Request):
    """Return all workspace definitions from workspaces.json."""
    ws_file = _workspaces_file(request.app.state)
    if not ws_file.exists():
        return {"version": 1, "workspaces": []}
    data = json.loads(ws_file.read_text())
    return data


@router.get("/v1/workspaces/{workspace_id}", tags=["Workspaces"])
async def get_workspace(workspace_id: str, request: Request):
    """Return a single workspace config by id."""
    ws_file = _workspaces_file(request.app.state)
    if not ws_file.exists():
        raise HTTPException(404, "workspaces.json not found")
    data = json.loads(ws_file.read_text())
    ws = next((w for w in data.get("workspaces", []) if w["id"] == workspace_id), None)
    if not ws:
        raise HTTPException(404, f"Workspace '{workspace_id}' not found")
    return ws


@router.get("/v1/snn/results/{table}", tags=["SNN"])
async def get_snn_results(
    table:     str,
    request:   Request,
    date_from: str | None = Query(None, description="ISO date e.g. 2026-02-09"),
    date_to:   str | None = Query(None, description="ISO date e.g. 2026-02-20"),
    limit:     int        = Query(500, le=5000),
    offset:    int        = Query(0),
):
    """
    Return per-window SNN anomaly scores for a given table.

    Reads from data/snn_results_{table_lower}.json.
    The workspace's snn_results map is used to resolve the filename.
    """
    db_dir = Path(request.app.state.db_dir)

    # Resolve file: check workspace manifest first, then fallback patterns
    ws_file = _workspaces_file(request.app.state)
    result_file: Path | None = None

    if ws_file.exists():
        ws_data = json.loads(ws_file.read_text())
        for ws in ws_data.get("workspaces", []):
            fname = ws.get("snn_results", {}).get(table.upper())
            if fname:
                candidate = db_dir.parent / fname
                if candidate.exists():
                    result_file = candidate
                    break

    if not result_file:
        # Fallback patterns
        base = db_dir.parent
        for pattern in [
            base / f"snn_results_{table.lower()}.json",
            base / f"snn_results_{table}.json",
        ]:
            if pattern.exists():
                result_file = pattern
                break

    if not result_file:
        raise HTTPException(404, f"No SNN results found for table '{table}'")

    data = json.loads(result_file.read_text())
    windows = data.get("windows", [])

    # Optional date filter
    if date_from or date_to:
        from datetime import datetime, timezone as _tz
        def _ts(s: str) -> float:
            return datetime.fromisoformat(s).replace(tzinfo=_tz.utc).timestamp()
        lo = _ts(date_from) if date_from else 0.0
        hi = _ts(date_to)   if date_to   else float("inf")
        windows = [w for w in windows if lo <= w["ts"] <= hi]

    total = len(windows)
    page  = windows[offset: offset + limit]

    return {
        "table":   table,
        "total":   total,
        "offset":  offset,
        "limit":   limit,
        "meta":    data.get("meta", {}),
        "summary": data.get("anomaly_mode", {}),
        "reservoir_distances": data.get("reservoir_distances", {}),
        "windows": page,
    }


# ── Catastrophe / damage-assessment endpoints ────────────────────────────────
#
#   GET  /v1/assess/{table}                    — paginated, filterable building list
#   GET  /v1/assess/{table}/summary            — counts by xbd_label + ROI
#   GET  /v1/assess/{table}/building/{id}      — full record for one building
#   POST /v1/assess/{table}/annotate           — adjuster confirm/override
#
#   The "event" is implicit in the table (one table per event/AOI pair).
#   Backed by ``<db_dir>/<TABLE>_attributes.parquet`` written by GeoDisasterStrategy.
#

def _attr_path(db_dir: Path, table: str) -> Path:
    return db_dir / f"{table.upper()}_attributes.parquet"


def _load_attrs(db_dir: Path, table: str):
    p = _attr_path(db_dir, table)
    if not p.exists():
        raise HTTPException(404, f"No catastrophe attributes for table '{table}' (looked at {p.name})")
    import pandas as pd
    return pd.read_parquet(p)


@router.get("/v1/assess/{table}", tags=["Catastrophe"])
async def assess_buildings(
    table:        str,
    request:      Request,
    severity_min: float | None = Query(None, ge=0.0, le=1.0),
    severity_max: float | None = Query(None, ge=0.0, le=1.0),
    xbd_label:    str   | None = Query(None, description="no_damage | minor | major | destroyed"),
    bbox:         str   | None = Query(None, description="minlon,minlat,maxlon,maxlat (EPSG:4326)"),
    sort_by:      str          = Query("severity", description="severity | area_m2 | dist_to_water_m"),
    descending:   bool         = Query(True),
    limit:        int          = Query(500, le=5000),
    offset:       int          = Query(0),
):
    """Return a paginated, filterable list of per-building damage records."""
    db_dir = Path(request.app.state.db_dir)
    df = _load_attrs(db_dir, table)

    if severity_min is not None: df = df[df["severity"] >= severity_min]
    if severity_max is not None: df = df[df["severity"] <= severity_max]
    if xbd_label:                df = df[df["xbd_label"] == xbd_label]
    if bbox:
        parts = [float(x) for x in bbox.split(",")]
        if len(parts) != 4:
            raise HTTPException(400, "bbox must be 'minlon,minlat,maxlon,maxlat'")
        minlon, minlat, maxlon, maxlat = parts
        df = df[(df["lon"] >= minlon) & (df["lon"] <= maxlon)
              & (df["lat"] >= minlat) & (df["lat"] <= maxlat)]

    if sort_by in df.columns:
        df = df.sort_values(sort_by, ascending=not descending)

    total = int(len(df))
    page  = df.iloc[offset: offset + limit]

    cols = ["entity_id","building_id","lon","lat","area_m2","source",
            "dino_cos_dist","visual_diff","severity","xbd_label","dist_to_water_m"]
    cols = [c for c in cols if c in page.columns]
    records = page[cols].to_dict(orient="records")

    return {
        "table":   table.upper(),
        "total":   total,
        "offset":  offset,
        "limit":   limit,
        "filters": {
            "severity_min": severity_min, "severity_max": severity_max,
            "xbd_label":    xbd_label,    "bbox":         bbox,
        },
        "buildings": records,
    }


@router.get("/v1/assess/{table}/summary", tags=["Catastrophe"])
async def assess_summary(
    table:   str,
    request: Request,
    repair_per_m2: float = Query(250.0, description="USD per m² placeholder repair cost"),
):
    """Return counts by xbd_label + a rough total addressable claim estimate."""
    db_dir = Path(request.app.state.db_dir)
    df = _load_attrs(db_dir, table)

    label_counts = df["xbd_label"].value_counts().to_dict()
    label_means  = df.groupby("xbd_label")["severity"].mean().to_dict()

    LOSS_FRAC = {"no_damage": 0.0, "minor": 0.10, "major": 0.45, "destroyed": 1.0}
    df = df.assign(loss_frac=df["xbd_label"].map(LOSS_FRAC).fillna(0.0))
    total_exposure_m2 = float((df["area_m2"]).sum())
    total_loss_m2     = float((df["area_m2"] * df["loss_frac"]).sum())
    total_loss_usd    = float(total_loss_m2 * repair_per_m2)

    by_distance: list[dict] = []
    if "dist_to_water_m" in df.columns:
        bins = [(0,500),(500,1000),(1000,2000),(2000,3000),(3000,1e9)]
        for lo, hi in bins:
            m = (df["dist_to_water_m"] >= lo) & (df["dist_to_water_m"] < hi)
            n = int(m.sum())
            if n == 0:
                continue
            by_distance.append({
                "band":         f"{int(lo)}-{int(hi) if hi < 1e8 else '+'} m",
                "n":            n,
                "mean_sev":     float(df.loc[m, "severity"].mean()),
                "pct_destroyed":float(100 * (df.loc[m, "xbd_label"] == "destroyed").mean()),
            })

    event_id = df["event_id"].iloc[0] if "event_id" in df.columns and len(df) else None
    aoi_id   = df["aoi_id"].iloc[0]   if "aoi_id"   in df.columns and len(df) else None

    return {
        "table":         table.upper(),
        "event_id":      event_id,
        "aoi_id":        aoi_id,
        "total_buildings": int(len(df)),
        "label_counts":  {k: int(v) for k, v in label_counts.items()},
        "label_mean_severity": {k: float(v) for k, v in label_means.items()},
        "severity_quantiles": {
            "p25": float(df["severity"].quantile(0.25)),
            "p50": float(df["severity"].quantile(0.50)),
            "p75": float(df["severity"].quantile(0.75)),
            "p95": float(df["severity"].quantile(0.95)),
        },
        "exposure_m2":       round(total_exposure_m2, 1),
        "estimated_loss_m2": round(total_loss_m2, 1),
        "estimated_loss_usd_at_per_m2": {
            "per_m2_usd":  repair_per_m2,
            "total_usd":   round(total_loss_usd, 0),
        },
        "by_distance_band":  by_distance,
    }


@router.get("/v1/assess/{table}/export.csv", tags=["Catastrophe"])
async def assess_export_csv(
    table:         str,
    request:       Request,
    repair_per_m2: float = Query(250.0, description="USD/m² placeholder repair cost"),
):
    """Return a claims-ready CSV (text/csv) for the entire AOI.

    Columns are tuned for downstream insurance workflows: building_id, lat/lon,
    area, severity score, xBD label, distance to water, and a placeholder
    estimated loss in USD using the standard FEMA-style loss-fraction table.
    """
    db_dir = Path(request.app.state.db_dir)
    df = _load_attrs(db_dir, table).copy()

    LOSS_FRAC = {"no_damage": 0.0, "minor": 0.10, "major": 0.45, "destroyed": 1.0}
    df["loss_frac"] = df["xbd_label"].map(LOSS_FRAC).fillna(0.0)
    df["loss_m2"]   = (df["area_m2"] * df["loss_frac"]).round(1)
    df["loss_usd"]  = (df["loss_m2"] * repair_per_m2).round(0).astype(int)

    cols = [
        "building_id", "lat", "lon", "area_m2", "source",
        "dino_cos_dist", "visual_diff", "severity", "xbd_label",
        "dist_to_water_m", "loss_frac", "loss_m2", "loss_usd",
    ]
    cols = [c for c in cols if c in df.columns]
    csv_text = df[cols].to_csv(index=False)

    fname = f"{table.upper()}_claims.csv"
    return PlainTextResponse(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/v1/assess/{table}/building/{building_id}", tags=["Catastrophe"])
async def assess_building(table: str, building_id: str, request: Request):
    """Return the full attribute record for a single building."""
    db_dir = Path(request.app.state.db_dir)
    df = _load_attrs(db_dir, table)
    row = df[df["building_id"].astype(str) == str(building_id)]
    if row.empty:
        raise HTTPException(404, f"Building '{building_id}' not found in {table}")
    return row.iloc[0].to_dict()


class AssessAnnotateIn(BaseModel):
    building_id: str
    decision:    str = Field(..., description="confirm | override")
    new_label:   str | None = Field(None, description="Required when decision=override")
    note:        str | None = None
    user:        str = Field("anonymous")


@router.post("/v1/assess/{table}/annotate", tags=["Catastrophe"])
async def assess_annotate(table: str, body: AssessAnnotateIn, request: Request):
    """Adjuster confirm/override on a building. Writes an ADJUSTER_REVIEWED hyperedge."""
    if body.decision not in ("confirm", "override"):
        raise HTTPException(400, "decision must be 'confirm' or 'override'")
    if body.decision == "override":
        if not body.new_label or body.new_label not in ("no_damage","minor","major","destroyed"):
            raise HTTPException(400, "override requires new_label in {no_damage, minor, major, destroyed}")

    db_dir = Path(request.app.state.db_dir)
    df = _load_attrs(db_dir, table)
    row = df[df["building_id"].astype(str) == str(body.building_id)]
    if row.empty:
        raise HTTPException(404, f"Building '{body.building_id}' not found in {table}")
    eid = int(row["entity_id"].iloc[0])

    import time as _time
    review_ts = int(_time.time())
    weight    = 1.0 if body.decision == "confirm" else 0.5

    db = request.app.state.db
    sql = (
        f"INSERT INTO {table.upper()} (event_ts, members, weight, formation) "
        f"VALUES ({review_ts}, [{eid},1], {weight}, 'ADJUSTER_REVIEWED')"
    )
    db.execute(sql)

    # Persist override label to sidecar so subsequent /assess calls reflect it
    if body.decision == "override":
        df.loc[df["building_id"].astype(str) == str(body.building_id), "xbd_label"] = body.new_label
        df.to_parquet(_attr_path(db_dir, table), index=False)

    return {
        "table":      table.upper(),
        "building_id":body.building_id,
        "entity_id":  eid,
        "decision":   body.decision,
        "new_label":  body.new_label,
        "note":       body.note,
        "user":       body.user,
        "review_ts":  review_ts,
        "ok":         True,
    }


# ── HyperGraphRAG endpoints ───────────────────────────────────────────────────
#
#  POST /v1/rag/query          — main NL query → grounded answer
#  POST /v1/rag/stream         — streaming version (SSE)
#  GET  /v1/rag/parse          — parse a query without executing (debug)
#

class RAGQueryIn(BaseModel):
    query:          str
    table:          str
    openai_api_key: str   = Field("", description="OpenAI key (or OPENAI_API_KEY env var)")
    model:          str   = Field("gpt-4o-mini", description="LLM model name")
    base_url:       str   = Field("https://api.openai.com/v1", description="API base URL")
    top_k:          int   = Field(40,  ge=1,  le=200)
    token_budget:   int   = Field(3000, ge=500, le=12000)
    time_start:     int | None = None
    time_end:       int | None = None
    # ── Neuro-symbolic controls ──
    mode:           str   = Field("hybrid", description="neuro | symbolic | hybrid")
    require_proof:  bool  = Field(False, description="Abstain unless a proof is derived")
    mock:           bool  = Field(False, description="Use the deterministic mock LLM (no network)")
    rules:          list[dict] | None = Field(
        None, description="Optional ad-hoc rules (else the durable rule store is used)")


@router.post(
    "/v1/rag/query",
    tags=["HyperGraphRAG"],
    summary="Natural-language query over temporal hyperedges — grounded answer with provenance",
)
async def rag_query(
    body:    RAGQueryIn,
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.rag.pipeline import RAGPipeline
    from hypermeshdb.rag.generator import LLMConfig

    db_dir = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    em     = _load_entity_map_for_table(body.table, db_dir)
    api_key = body.openai_api_key or os.environ.get("OPENAI_API_KEY", "")

    if body.mode not in ("neuro", "symbolic", "hybrid"):
        raise HTTPException(status_code=422, detail="mode must be neuro|symbolic|hybrid")

    cfg = LLMConfig.mock() if body.mock else LLMConfig(
        model=body.model, base_url=body.base_url, api_key=api_key)
    try:
        # ValueError covers bad mode; RuleValidationError covers bad ad-hoc rules.
        pipe = RAGPipeline(db, table=body.table, entity_map=em,
                           llm_config=cfg, top_k=body.top_k,
                           token_budget=body.token_budget, db_dir=db_dir,
                           mode=body.mode, require_proof=body.require_proof,
                           rules=body.rules)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # If explicit time window provided, override parsed one
    from hypermeshdb.rag.query_parser import QueryParser
    parsed = QueryParser(em).parse(body.query)
    if body.time_start is not None:
        parsed.time_start = body.time_start
    if body.time_end is not None:
        parsed.time_end = body.time_end

    try:
        result = await pipe.query(body.query)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("RAG query failed for table %s", body.table)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result.as_dict()


# ── Neuro-symbolic rule store + reasoning endpoints ───────────────────────────
#
#  GET    /v1/rules            — list rules
#  POST   /v1/rules            — create/update a rule (re-validates whole set)
#  POST   /v1/rules/validate   — dry-run validate a rule (no write)
#  GET    /v1/rules/{rule_id}  — fetch one rule
#  DELETE /v1/rules/{rule_id}  — delete a rule
#  POST   /v1/reason           — retrieve + reason (LLM-free) → derived facts + proofs
#

def _rule_store(request):
    from hypermeshdb.rag.symbolic import RuleStore
    db_dir = getattr(request.app.state if request else None, "db_dir", "") \
        or os.environ.get("HMDB_DIR", "data")
    return RuleStore(db_dir)


@router.get("/v1/rules", tags=["HyperGraphRAG"], summary="List symbolic rules")
async def list_rules(request: Request = None, db: hypermeshdb.Connection = Depends(_get_db), _: Any = _RO):
    return {"rules": _rule_store(request).list()}


@router.post("/v1/rules", tags=["HyperGraphRAG"], summary="Create/update a symbolic rule")
async def upsert_rule(body: dict, request: Request = None,
                      db: hypermeshdb.Connection = Depends(_get_db), _: Any = _RW):
    from hypermeshdb.rag.symbolic import RuleValidationError
    try:
        return _rule_store(request).upsert(body)
    except RuleValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/v1/rules/validate", tags=["HyperGraphRAG"], summary="Validate a rule (no write)")
async def validate_rule(body: dict, request: Request = None,
                        db: hypermeshdb.Connection = Depends(_get_db), _: Any = _RO):
    return _rule_store(request).validate(body)


@router.get("/v1/rules/{rule_id}", tags=["HyperGraphRAG"], summary="Fetch one rule")
async def get_rule(rule_id: str, request: Request = None,
                   db: hypermeshdb.Connection = Depends(_get_db), _: Any = _RO):
    rule = _rule_store(request).get(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"rule {rule_id!r} not found")
    return rule


@router.delete("/v1/rules/{rule_id}", tags=["HyperGraphRAG"], summary="Delete a rule")
async def delete_rule(rule_id: str, request: Request = None,
                      db: hypermeshdb.Connection = Depends(_get_db), _: Any = _RW):
    if not _rule_store(request).delete(rule_id):
        raise HTTPException(status_code=404, detail=f"rule {rule_id!r} not found")
    return {"id": rule_id, "deleted": True}


class ReasonIn(BaseModel):
    query:         str
    table:         str
    top_k:         int = Field(40, ge=1, le=200)
    require_proof: bool = False
    rules:         list[dict] | None = Field(
        None, description="Optional ad-hoc rules (else durable rule store)")


@router.post(
    "/v1/reason",
    tags=["HyperGraphRAG"],
    summary="Symbolic reasoning over retrieved hyperedges — derived facts + proofs (no LLM)",
)
async def reason(body: ReasonIn, request: Request = None,
                 db: hypermeshdb.Connection = Depends(_get_db), _: Any = _RO):
    from hypermeshdb.rag.pipeline import RAGPipeline
    from hypermeshdb.rag.symbolic import RuleValidationError
    db_dir = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    em = _load_entity_map_for_table(body.table, db_dir)
    try:
        pipe = RAGPipeline(db, table=body.table, entity_map=em, top_k=body.top_k,
                           db_dir=db_dir, mode="symbolic",
                           require_proof=body.require_proof, rules=body.rules)
        result = await pipe.query(body.query)
    except (ValueError, RuleValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("Reasoning failed for table %s", body.table)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    d = result.as_dict()
    return {
        "query":         d["query"],
        "table":         d["table"],
        "answer":        d["answer"],
        "abstained":     d["abstained"],
        "derived_facts": d["derived_facts"],
        "proofs":        d["proofs"],
        "rules_fired":   d["rules_fired"],
        "reasoning_ms":  d["reasoning_ms"],
    }


class RAGFeedbackIn(BaseModel):
    rating: str   = Field(..., description="'good' or 'bad'")


@router.post(
    "/v1/rag/feedback/{interaction_id}",
    tags=["HyperGraphRAG"],
    summary="Rate a RAG answer 👍 / 👎 (saves to fine-tune training data)",
)
async def rag_feedback(
    interaction_id: str,
    body:    RAGFeedbackIn,
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.rag.harvester import InteractionHarvester
    db_dir = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    harvester = InteractionHarvester(db_dir)
    try:
        record = harvester.rate(interaction_id, body.rating)
        return {"interaction_id": interaction_id, "rating": body.rating, "ok": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/v1/rag/training-stats",
    tags=["HyperGraphRAG"],
    summary="Training data stats — interactions, approved, rejected",
)
async def rag_training_stats(
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.rag.harvester import InteractionHarvester
    db_dir = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    h = InteractionHarvester(db_dir)
    return h.stats()


@router.get(
    "/v1/rag/training-history",
    tags=["HyperGraphRAG"],
    summary="Recent RAG interactions (for review and rating)",
)
async def rag_training_history(
    n:       int     = Query(20, ge=1, le=200),
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.rag.harvester import InteractionHarvester
    db_dir = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    h = InteractionHarvester(db_dir)
    return {"interactions": h.recent(n)}


class SynthesizeIn(BaseModel):
    table:          str
    n_batches:      int   = Field(50, ge=1, le=500)
    n_questions:    int   = Field(3, ge=1, le=10)
    openai_api_key: str   = Field("", description="OpenAI key or OPENAI_API_KEY env")
    model:          str   = Field("gpt-4o-mini")


@router.post(
    "/v1/rag/synthesize",
    tags=["HyperGraphRAG"],
    summary="Generate synthetic training data via reverse hyperedge synthesis",
)
async def rag_synthesize(
    body:    SynthesizeIn,
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.rag.synthesizer import ReverseHyperedgeSynthesizer, SynthesizerConfig
    db_dir  = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    em      = _load_entity_map_for_table(body.table, db_dir)
    api_key = body.openai_api_key or os.environ.get("OPENAI_API_KEY", "")

    cfg = SynthesizerConfig(
        openai_api_key = api_key,
        model          = body.model,
        n_questions    = body.n_questions,
        output_dir     = os.path.join(db_dir, "rag_training"),
    )
    synth = ReverseHyperedgeSynthesizer(db, body.table, entity_map=em, config=cfg, db_dir=db_dir)

    try:
        result = await synth.synthesize(n_batches=body.n_batches)
    except Exception as exc:
        logging.exception("Synthesis failed for table %s", body.table)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result


@router.get(
    "/v1/rag/synthesize/stats",
    tags=["HyperGraphRAG"],
    summary="Synthetic data file stats for a table",
)
async def rag_synthesize_stats(
    table:   str     = Query(...),
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.rag.synthesizer import ReverseHyperedgeSynthesizer, SynthesizerConfig
    db_dir = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    cfg    = SynthesizerConfig(output_dir=os.path.join(db_dir, "rag_training"))
    synth  = ReverseHyperedgeSynthesizer(db, table, config=cfg, db_dir=db_dir)
    return synth.stats()


# ── Training data export + validation ─────────────────────────────────────────

@router.get(
    "/v1/rag/training-export",
    tags=["HyperGraphRAG"],
    summary="Download merged fine-tune JSONL (approved + synthetic)",
)
async def rag_training_export(
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from fastapi.responses import FileResponse
    from hypermeshdb.rag.harvester import InteractionHarvester
    db_dir    = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    harvester = InteractionHarvester(db_dir)
    out_path  = harvester.export_training_jsonl()
    return FileResponse(
        path=out_path,
        media_type="application/jsonlines",
        filename="hypermesh_rag_training.jsonl",
    )


@router.post(
    "/v1/rag/training-validate",
    tags=["HyperGraphRAG"],
    summary="Validate training JSONL — returns stats + quality report",
)
async def rag_training_validate(
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    import json as _json
    from hypermeshdb.rag.harvester import InteractionHarvester
    db_dir    = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    harvester = InteractionHarvester(db_dir)
    export_path = harvester.export_training_jsonl()

    records: list[dict] = []
    errors:  list[str]  = []
    type_counts: dict[str, int] = {}

    try:
        with open(export_path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                try:
                    rec  = _json.loads(line)
                    msgs = rec.get("messages", [])
                    if len(msgs) != 3:
                        errors.append(f"Line {i}: expected 3 messages, got {len(msgs)}")
                        continue
                    records.append(rec)
                    # Infer question type from user message
                    user_text = msgs[1].get("content", "").lower()
                    q_type = "summary"
                    for kw, t in [("lateral", "threat"), ("cluster", "pattern"),
                                  ("frequen", "entity"), ("when", "temporal"),
                                  ("machine", "entity"), ("process", "entity")]:
                        if kw in user_text:
                            q_type = t
                            break
                    type_counts[q_type] = type_counts.get(q_type, 0) + 1
                except _json.JSONDecodeError as e:
                    errors.append(f"Line {i}: {e}")
    except FileNotFoundError:
        return {"total": 0, "errors": ["No training data found. Generate via synthesis or approve interactions."], "ready": False}

    token_estimates = [
        sum(len(m["content"]) // 4 for m in r["messages"])
        for r in records
    ]
    avg_tokens = sum(token_estimates) // max(1, len(token_estimates))
    max_tokens = max(token_estimates) if token_estimates else 0

    return {
        "total":           len(records),
        "error_count":     len(errors),
        "errors_sample":   errors[:5],
        "avg_tokens":      avg_tokens,
        "max_tokens":      max_tokens,
        "question_types":  type_counts,
        "ready":           len(records) >= 100 and len(errors) == 0,
        "recommendation":  (
            "Ready for fine-tuning." if len(records) >= 500
            else f"Collect more data — {500 - len(records)} more examples recommended."
        ),
        "output_path":     export_path,
    }


# ── Colab notebook generator ──────────────────────────────────────────────────

@router.get(
    "/v1/rag/training-notebook",
    tags=["HyperGraphRAG"],
    summary="Generate a ready-to-run Colab fine-tuning notebook (.ipynb download)",
)
async def rag_training_notebook(
    base_model: str   = Query("microsoft/Phi-3-mini-4k-instruct"),
    epochs:     int   = Query(3, ge=1, le=10),
    lora_r:     int   = Query(16),
    request:    Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    import json as _json
    from fastapi.responses import Response

    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.10.0"},
            "accelerator": "GPU",
        },
        "cells": [
            _nb_markdown("# HyperMesh HyperGraphRAG — Fine-Tune Notebook\n\nGenerated by HyperMesh Workbench. Uses QLoRA to fine-tune a small language model on your hypergraph training data.\n\n**Runtime → Change runtime type → T4 GPU** before running."),
            _nb_code("# Install dependencies\n!pip install -q transformers peft datasets bitsandbytes accelerate trl huggingface_hub"),
            _nb_markdown("## 1. Upload training data\n\nUpload your `hypermesh_rag_training.jsonl` file (downloaded from the HyperMesh workbench)."),
            _nb_code(
                "from google.colab import files\nimport json\n\n"
                "uploaded = files.upload()\n"
                "data_path = list(uploaded.keys())[0]\n"
                "print(f'Loaded: {data_path}')\n\n"
                "records = []\nwith open(data_path) as f:\n"
                "    for line in f:\n        records.append(json.loads(line))\nprint(f'Total examples: {len(records)}')"
            ),
            _nb_markdown("## 2. Training configuration"),
            _nb_code(
                f"BASE_MODEL   = '{base_model}'\n"
                f"EPOCHS       = {epochs}\n"
                f"LORA_R       = {lora_r}\n"
                f"LORA_ALPHA   = {lora_r * 2}\n"
                "BATCH_SIZE   = 2\n"
                "GRAD_ACC     = 8\n"
                "LR           = 2e-4\n"
                "MAX_LEN      = 2048\n"
                "OUTPUT_DIR   = './hypermesh-rag-model'"
            ),
            _nb_markdown("## 3. Load model + LoRA"),
            _nb_code(
                "import torch\n"
                "from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainingArguments\n"
                "from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training\n"
                "from datasets import Dataset\n"
                "from trl import SFTTrainer\n\n"
                "bnb_cfg = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_use_double_quant=True,\n"
                "                             bnb_4bit_quant_type='nf4', bnb_4bit_compute_dtype=torch.bfloat16)\n\n"
                "tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)\n"
                "tokenizer.pad_token = tokenizer.eos_token\n\n"
                "model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, quantization_config=bnb_cfg,\n"
                "    device_map='auto', trust_remote_code=True)\n"
                "model = prepare_model_for_kbit_training(model)\n\n"
                "lora_cfg = LoraConfig(r=LORA_R, lora_alpha=LORA_ALPHA, lora_dropout=0.05, bias='none',\n"
                "    task_type='CAUSAL_LM',\n"
                "    target_modules=['q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'])\n"
                "model = get_peft_model(model, lora_cfg)\n"
                "model.print_trainable_parameters()"
            ),
            _nb_markdown("## 4. Prepare dataset"),
            _nb_code(
                "def fmt(rec):\n"
                "    m = rec['messages']\n"
                "    return f'<|system|>\\n{m[0][\"content\"]}<|end|>\\n<|user|>\\n{m[1][\"content\"]}<|end|>\\n<|assistant|>\\n{m[2][\"content\"]}<|end|>\\n'\n\n"
                "dataset = Dataset.from_list([{'text': fmt(r)} for r in records])\n"
                "print(dataset)"
            ),
            _nb_markdown("## 5. Train"),
            _nb_code(
                "training_args = TrainingArguments(\n"
                "    output_dir=OUTPUT_DIR + '/checkpoints',\n"
                "    num_train_epochs=EPOCHS,\n"
                "    per_device_train_batch_size=BATCH_SIZE,\n"
                "    gradient_accumulation_steps=GRAD_ACC,\n"
                "    learning_rate=LR,\n"
                "    lr_scheduler_type='cosine',\n"
                "    warmup_ratio=0.05,\n"
                "    fp16=True,\n"
                "    logging_steps=10,\n"
                "    save_steps=100,\n"
                "    save_total_limit=2,\n"
                "    optim='paged_adamw_8bit',\n"
                "    report_to='none',\n"
                ")\n\n"
                "trainer = SFTTrainer(\n"
                "    model=model, train_dataset=dataset, tokenizer=tokenizer,\n"
                "    args=training_args, dataset_text_field='text',\n"
                "    max_seq_length=MAX_LEN, packing=True,\n"
                ")\ntrainer.train()"
            ),
            _nb_markdown("## 6. Save merged model"),
            _nb_code(
                "import os\n"
                "merged_dir = OUTPUT_DIR + '/merged'\n"
                "os.makedirs(merged_dir, exist_ok=True)\n"
                "trainer.model.save_pretrained(merged_dir)\n"
                "tokenizer.save_pretrained(merged_dir)\n"
                "print(f'Saved to {merged_dir}')"
            ),
            _nb_markdown("## 7. (Optional) Convert to GGUF for edge deployment"),
            _nb_code(
                "# Clone llama.cpp and convert to GGUF\n"
                "!git clone -q https://github.com/ggerganov/llama.cpp /content/llama.cpp\n"
                "!pip install -q gguf protobuf\n"
                "!python /content/llama.cpp/convert_hf_to_gguf.py {merged_dir} --outfile /content/hypermesh-rag.gguf\n"
                "# Optional: quantize to 4-bit (~2GB)\n"
                "# !make -C /content/llama.cpp -j4 llama-quantize\n"
                "# !/content/llama.cpp/llama-quantize /content/hypermesh-rag.gguf /content/hypermesh-rag-q4.gguf q4_k_m"
            ),
            _nb_markdown("## 8. Download the model"),
            _nb_code(
                "# Download the GGUF (or the full merged model)\n"
                "from google.colab import files\n"
                "files.download('/content/hypermesh-rag.gguf')\n\n"
                "# After downloading, register it in HyperMesh Workbench:\n"
                "# HyperGraphRAG → Model Pipeline → Register Model → type: llama_cpp"
            ),
        ],
    }

    return Response(
        content=_json.dumps(nb, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=hypermesh_rag_finetune.ipynb"},
    )


def _nb_code(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {"id": str(uuid.uuid4())[:8]},
        "outputs": [],
        "source": source,
    }


def _nb_markdown(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {"id": str(uuid.uuid4())[:8]},
        "source": source,
    }


# ── Ollama integration ────────────────────────────────────────────────────────

@router.get(
    "/v1/rag/ollama/models",
    tags=["HyperGraphRAG"],
    summary="List locally available Ollama models",
)
async def rag_ollama_models(
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    import subprocess
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {"available": False, "models": [], "error": result.stderr.strip()}
        lines   = result.stdout.strip().split("\n")[1:]   # skip header
        models  = []
        for line in lines:
            parts = line.split()
            if parts:
                models.append({
                    "name":  parts[0],
                    "size":  parts[3] if len(parts) > 3 else "",
                    "modified": " ".join(parts[4:]) if len(parts) > 4 else "",
                })
        return {"available": True, "models": models}
    except FileNotFoundError:
        return {"available": False, "models": [], "error": "ollama not found. Install from https://ollama.ai"}
    except subprocess.TimeoutExpired:
        return {"available": False, "models": [], "error": "ollama timed out"}


class OllamaCreateIn(BaseModel):
    name:      str = Field(..., description="Model name, e.g. 'hypermesh-rag'")
    gguf_path: str = Field(..., description="Absolute path to .gguf file")
    system_prompt: str = Field("", description="Optional system prompt for the Modelfile")


@router.post(
    "/v1/rag/ollama/create",
    tags=["HyperGraphRAG"],
    summary="Register a GGUF with Ollama (creates a Modelfile and runs ollama create)",
)
async def rag_ollama_create(
    body: OllamaCreateIn,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    import subprocess, tempfile

    system_line = f"\nSYSTEM \"{body.system_prompt}\"" if body.system_prompt else ""
    modelfile   = f"FROM {body.gguf_path}{system_line}\n"

    with tempfile.NamedTemporaryFile("w", suffix=".Modelfile", delete=False) as f:
        f.write(modelfile)
        mf_path = f.name

    try:
        result = subprocess.run(
            ["ollama", "create", body.name, "-f", mf_path],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=result.stderr.strip())
        return {
            "created": True,
            "model":   body.name,
            "base_url": "http://localhost:11434/v1",
            "message": result.stdout.strip(),
        }
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="ollama not found. Install from https://ollama.ai")
    finally:
        import os as _os
        _os.unlink(mf_path)


# ── Model registry endpoints ──────────────────────────────────────────────────

@router.get(
    "/v1/rag/models",
    tags=["HyperGraphRAG"],
    summary="List all registered model backends",
)
async def rag_models_list(
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.rag.registry import ModelRegistry
    db_dir = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    reg    = ModelRegistry(db_dir)
    return {
        "active_id": reg._data.get("active_id"),
        "models":    reg.list_models(),
    }


class RegisterModelIn(BaseModel):
    name:         str
    model_type:   str  = Field(..., description="'openai' | 'ollama' | 'llama_cpp'")
    model:        str  = Field("", description="Model name (for openai/ollama)")
    base_url:     str  = Field("", description="API base URL (for openai/ollama)")
    api_key:      str  = Field("")
    gguf_path:    str  = Field("", description="Path to .gguf file (for llama_cpp)")
    n_gpu_layers: int  = Field(-1)


@router.post(
    "/v1/rag/models/register",
    tags=["HyperGraphRAG"],
    summary="Register a new model backend",
)
async def rag_models_register(
    body:    RegisterModelIn,
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.rag.registry import ModelRegistry
    db_dir = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    reg    = ModelRegistry(db_dir)
    entry  = reg.register(
        name         = body.name,
        model_type   = body.model_type,
        model        = body.model,
        base_url     = body.base_url,
        api_key      = body.api_key,
        gguf_path    = body.gguf_path,
        n_gpu_layers = body.n_gpu_layers,
    )
    return entry


@router.post(
    "/v1/rag/models/{model_id}/activate",
    tags=["HyperGraphRAG"],
    summary="Set a registered model as the active backend",
)
async def rag_models_activate(
    model_id: str,
    request:  Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.rag.registry import ModelRegistry
    db_dir = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    reg    = ModelRegistry(db_dir)
    try:
        entry = reg.activate(model_id)
        return {"activated": True, "model": entry}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete(
    "/v1/rag/models/{model_id}",
    tags=["HyperGraphRAG"],
    summary="Remove a model from the registry",
)
async def rag_models_delete(
    model_id: str,
    request:  Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.rag.registry import ModelRegistry
    db_dir = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    reg    = ModelRegistry(db_dir)
    removed = reg.remove(model_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Model {model_id!r} not found")
    return {"deleted": model_id}


@router.get(
    "/v1/rag/parse",
    tags=["HyperGraphRAG"],
    summary="Parse a query into structured parameters (debug / preview)",
)
async def rag_parse(
    query: str = Query(..., description="Natural language query"),
    table: str = Query("", description="Table name for entity map loading"),
    request: Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.rag.query_parser import QueryParser
    db_dir = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "data")
    em     = _load_entity_map_for_table(table, db_dir) if table else {}
    parsed = QueryParser(em).parse(query)
    return parsed.as_dict()


# ── Ingest Strategy Library endpoints ────────────────────────────────────────
#
#  GET  /v1/ingest/strategies            — list all strategies + param schemas
#  POST /v1/ingest/strategies/{name}/run — run a strategy (background job)
#  GET  /v1/ingest/strategies/jobs/{id}  — poll job status + progress

import asyncio as _asyncio

_strategy_jobs: dict[str, dict] = {}   # job_id → {status, progress, message, result, error}


@router.get(
    "/v1/ingest/strategies",
    tags=["Ingest Strategies"],
    summary="List all available ingestion strategies and their parameter schemas",
)
async def list_ingest_strategies(_: Any = _RO):
    from hypermeshdb.ingest.strategies import registry as _strategy_registry
    return {"strategies": _strategy_registry()}


class StrategyRunRequest(BaseModel):
    config: dict = {}   # strategy-specific parameter dict


@router.post(
    "/v1/ingest/strategies/{name}/run",
    tags=["Ingest Strategies"],
    summary="Run a named ingest strategy in the background",
)
async def run_ingest_strategy(
    name:    str,
    body:    StrategyRunRequest,
    request: Request = None,
    db:      hypermeshdb.Connection = Depends(_get_db),
    _:       Any = _RW,
):
    from hypermeshdb.ingest.strategies import get_strategy
    try:
        cls = get_strategy(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    job_id = str(uuid.uuid4())
    db_dir = (
        getattr(request.app.state if request else None, "db_dir", "")
        or os.environ.get("HMDB_DIR", "data")
    )

    _strategy_jobs[job_id] = {
        "status":   "running",
        "progress": 0.0,
        "message":  "Starting …",
        "result":   None,
        "error":    None,
    }

    def _progress(fraction: float, message: str):
        _strategy_jobs[job_id]["progress"] = round(fraction, 3)
        _strategy_jobs[job_id]["message"]  = message

    async def _run():
        loop = _asyncio.get_running_loop()
        strategy = cls()
        config   = body.config

        # Open a *separate* connection so the background thread doesn't
        # share the request-scoped connection.
        import hypermeshdb as _hm
        bg_conn = _hm.connect(db_dir)
        try:
            result = await loop.run_in_executor(
                None,
                lambda: strategy.run(config, bg_conn, db_dir, _progress),
            )
            _strategy_jobs[job_id]["status"] = "done"
            _strategy_jobs[job_id]["result"] = {
                "table":           result.table,
                "entities":        result.entities,
                "hyperedges":      result.hyperedges,
                "errors":          result.errors,
                "formations":      result.formations,
                "entity_map_path": result.entity_map_path,
                "elapsed_s":       round(result.elapsed_s, 2),
                "notes":           result.notes,
            }
        except Exception as exc:
            _strategy_jobs[job_id]["status"] = "error"
            _strategy_jobs[job_id]["error"]  = str(exc)
        finally:
            bg_conn.close()

    _asyncio.create_task(_run())
    return {"job_id": job_id, "status": "running"}


@router.get(
    "/v1/ingest/strategies/jobs/{job_id}",
    tags=["Ingest Strategies"],
    summary="Poll the status and progress of a running ingest strategy job",
)
async def get_strategy_job(job_id: str, _: Any = _RO):
    job = _strategy_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return {"job_id": job_id, **job}


# ── Lens explainability endpoints ────────────────────────────────────────────
#
#  All routes are read-only (readonly role).  They add structural provenance
#  on top of existing analytics — no writes, no schema changes.
#
#  GET /v1/lens/entity/{table}/{node_id}
#  GET /v1/lens/pattern/{table}
#  GET /v1/lens/temporal/{table}
#  GET /v1/lens/counterfactual/{table}/{node_id}
#  GET /v1/lens/bridges/{table}
#  GET /v1/lens/explain/{table}

def _get_lens_builder(table: str, request: "Request", db: hypermeshdb.Connection):
    """Build a ProvenanceBuilder for the given table, loading entity map."""
    from hypermeshdb.lens.provenance import ProvenanceBuilder, LensConfig
    try:
        an = db.analytics(table.upper())
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Table '{table}' not found: {exc}") from exc
    db_dir = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "/tmp/hypermesh_db")
    em = _load_entity_map_for_table(table, db_dir)
    return ProvenanceBuilder(an.hypergraph, entity_map=em, config=LensConfig())


@router.get(
    "/v1/lens/entity/{table}/{node_id}",
    tags=["Lens"],
    summary="Entity provenance — why is this node important?",
)
async def lens_entity(
    table:       str,
    node_id:     int,
    start_ts:    int | None = Query(None),
    end_ts:      int | None = Query(None),
    request:     Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    builder = _get_lens_builder(table, request, db)
    result  = await asyncio.get_event_loop().run_in_executor(
        None, lambda: builder.build_entity_provenance(node_id, start_ts, end_ts)
    )
    return result.as_dict()


@router.get(
    "/v1/lens/pattern/{table}",
    tags=["Lens"],
    summary="Pattern provenance — why was this pattern detected?",
)
async def lens_pattern(
    table:    str,
    start_ts: int | None = Query(None),
    end_ts:   int | None = Query(None),
    request:  Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.patterns.detector import PatternDetector
    builder = _get_lens_builder(table, request, db)
    db_dir  = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "/tmp/hypermesh_db")
    em      = _load_entity_map_for_table(table, db_dir)

    try:
        an       = db.analytics(table.upper())
        detector = PatternDetector(an, entity_map=em, table_name=table.upper())
        det_res  = await asyncio.get_event_loop().run_in_executor(None, lambda: detector.detect())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    findings = [f.as_dict() for f in det_res.findings]
    if not findings:
        return {"provenance_type": "pattern", "narrative": "No patterns detected.", "findings": [], "subgraphs": []}

    subgraphs = []
    for fd in findings[:5]:  # top-5 only to keep response size bounded
        pg = await asyncio.get_event_loop().run_in_executor(
            None, lambda f=fd: builder.build_pattern_provenance(f, start_ts, end_ts)
        )
        subgraphs.append({"finding": fd, "provenance": pg.as_dict()})

    return {"provenance_type": "pattern", "findings": findings, "subgraphs": subgraphs}


@router.get(
    "/v1/lens/temporal/{table}",
    tags=["Lens"],
    summary="Temporal provenance — how did the pattern evolve?",
)
async def lens_temporal(
    table:      str,
    start_ts:   int | None = Query(None),
    end_ts:     int | None = Query(None),
    n_windows:  int        = Query(10, ge=2, le=50),
    request:    Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    builder = _get_lens_builder(table, request, db)
    result  = await asyncio.get_event_loop().run_in_executor(
        None, lambda: builder.build_temporal_provenance(start_ts, end_ts, n_windows)
    )
    return result.as_dict()


@router.get(
    "/v1/lens/counterfactual/{table}/{node_id}",
    tags=["Lens"],
    summary="Counterfactual — what if this node were removed?",
)
async def lens_counterfactual(
    table:    str,
    node_id:  int,
    start_ts: int | None = Query(None),
    end_ts:   int | None = Query(None),
    request:  Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    builder = _get_lens_builder(table, request, db)
    result  = await asyncio.get_event_loop().run_in_executor(
        None, lambda: builder.build_counterfactual_provenance(node_id, start_ts, end_ts)
    )
    return result.as_dict()


@router.get(
    "/v1/lens/bridges/{table}",
    tags=["Lens"],
    summary="Bridge provenance — who connects the sub-communities?",
)
async def lens_bridges(
    table:    str,
    start_ts: int | None = Query(None),
    end_ts:   int | None = Query(None),
    request:  Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    builder = _get_lens_builder(table, request, db)
    result  = await asyncio.get_event_loop().run_in_executor(
        None, lambda: builder.build_bridge_provenance(start_ts, end_ts)
    )
    return result.as_dict()


@router.get(
    "/v1/lens/explain/{table}",
    tags=["Lens"],
    summary="Full explanation — one-call comprehensive structural provenance",
)
async def lens_explain(
    table:    str,
    start_ts: int | None = Query(None),
    end_ts:   int | None = Query(None),
    request:  Request = None,
    db: hypermeshdb.Connection = Depends(_get_db),
    _: Any = _RO,
):
    from hypermeshdb.patterns.detector import PatternDetector
    builder = _get_lens_builder(table, request, db)
    db_dir  = getattr(request.app.state if request else None, "db_dir", "") or os.environ.get("HMDB_DIR", "/tmp/hypermesh_db")
    em      = _load_entity_map_for_table(table, db_dir)

    try:
        an       = db.analytics(table.upper())
        detector = PatternDetector(an, entity_map=em, table_name=table.upper())
        det_res  = await asyncio.get_event_loop().run_in_executor(None, lambda: detector.detect())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    findings = [f.as_dict() for f in det_res.findings]
    result   = await asyncio.get_event_loop().run_in_executor(
        None, lambda: builder.build_full_explanation(findings, start_ts, end_ts)
    )
    return result


# ── HyperDx (Mayo) — hyperedge-centric explorer endpoints ─────────────────────
# The HyperDx UI is purpose-built for the Mayo dataset's 406 disease-attributed
# hyperedges. It loads the slim graph once, then drills into individual edges
# or patients on demand. See hypermeshdb/_hyperdx.py for the loader + cache.

from hypermeshdb import _hyperdx as _hyperdx_mod  # noqa: E402

@router.get(
    "/v1/hyperdx/graph",
    tags=["HyperDx"],
    summary="Slim hyperedge-centric graph for the Mayo HyperDx explorer.",
)
async def hyperdx_graph(table: str = Query(..., description="Workspace table name (e.g. MAYOCLINICAL)")):
    """Slim hypergraph for the bubble explorer.

    Returns the full 406-edge metadata (family, attribution, support, readable
    features) plus all node summaries.  Member arrays are NOT included to
    keep the payload small — fetch them on demand via ``/edge/{id}/members``.
    """
    payload = _hyperdx_mod.get_graph_payload(table)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"HyperDx graph for table={table!r} is unavailable.")
    return payload


@router.get(
    "/v1/hyperdx/graph-full",
    tags=["HyperDx"],
    summary="Full slim graph WITH per-edge member arrays (for Lens polygon view).",
)
async def hyperdx_graph_full(table: str = Query(...)):
    """Slim graph plus every edge's `member_indices`.

    Used by the Lens v4 polygon/hull/metro view which needs all member
    lists client-side to render polygons. Adds ≈ 80 KB over `/v1/hyperdx/graph`.
    """
    payload = _hyperdx_mod.get_graph_full(table)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"HyperDx graph for table={table!r} is unavailable.")
    return payload


@router.get(
    "/v1/hyperdx/edge/{edge_id}/members",
    tags=["HyperDx"],
    summary="Member patients of a specific hyperedge.",
)
async def hyperdx_edge_members(edge_id: int, table: str = Query(...)):
    payload = _hyperdx_mod.get_edge_members(table, edge_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Edge {edge_id} (table={table!r}) not found.")
    return payload


@router.get(
    "/v1/hyperdx/patient/{patient_index}/edges",
    tags=["HyperDx"],
    summary="All hyperedges containing a given patient (ranked by attribution).",
)
async def hyperdx_patient_edges(patient_index: int, table: str = Query(...)):
    payload = _hyperdx_mod.get_patient_edges(table, patient_index)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Patient {patient_index} (table={table!r}) not found.")
    return payload


@router.get(
    "/v1/hyperdx/overlap",
    tags=["HyperDx"],
    summary="Inter-hyperedge overlap graph (edges with ≥ N shared patients).",
)
async def hyperdx_overlap(
    table:       str = Query(...),
    min_overlap: int = Query(5, ge=1, le=10_000),
):
    g = _hyperdx_mod.get_graph(table)
    if g is None:
        raise HTTPException(status_code=404, detail=f"HyperDx graph for table={table!r} is unavailable.")
    return {"min_overlap": min_overlap, "edges": _hyperdx_mod.get_overlap_edges(table, min_overlap)}


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(
    db_dir:       str | None = None,
    auth_disabled: bool      = False,
    ui_dir:       str | None = None,
) -> FastAPI:
    """
    Create and return an isolated FastAPI application instance.

    Parameters
    ----------
    db_dir:
        Path to the database directory.  If ``None``, the ``HMDB_DIR``
        environment variable is read at startup time.
    auth_disabled:
        If ``False`` (default), API-key authentication is enforced — every
        endpoint except the liveness/readiness probes requires a valid key.
        Set to ``True`` (or ``hmdb serve --no-auth`` / ``HMDB_AUTH_DISABLED=1``)
        to allow unauthenticated access — intended for local development only.
    ui_dir:
        Optional path to a built single-page-app directory (containing
        ``index.html``).  If given (or ``HMDB_UI_DIR`` is set), the app serves
        the SPA and its assets alongside the API from the *same* process, with
        a client-side-routing fallback to ``index.html``.  This is what lets
        ``hmdb serve <db> --ui-dir <build>`` run the whole product as a single
        process — no separate Node front door.

    Returns
    -------
    FastAPI
        A fully configured application instance.
    """
    _auth_disabled = auth_disabled or (
        os.environ.get("HMDB_AUTH_DISABLED", "").strip() == "1"
    )
    _ui_dir = ui_dir or os.environ.get("HMDB_UI_DIR", "").strip() or None
    _ui_path = Path(_ui_dir).resolve() if _ui_dir else None
    if _ui_path is not None and not (_ui_path / "index.html").is_file():
        logging.getLogger(__name__).warning(
            "HMDB_UI_DIR / --ui-dir set to %s but no index.html found — "
            "serving API only.", _ui_path,
        )
        _ui_path = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Not ready until the DB is open and startup completes. /health/ready
        # reports 503 until we flip this, so nothing routes traffic to a
        # half-started process.
        app.state.ready            = False
        app.state.not_ready_reason = "starting up"

        resolved = db_dir or os.environ.get("HMDB_DIR", "")
        if not resolved:
            raise RuntimeError(
                "Database directory not set.  Pass db_dir to create_app() "
                "or set the HMDB_DIR environment variable."
            )
        app.state.db           = hypermeshdb.connect(resolved)
        app.state.db_dir       = os.path.abspath(resolved)
        app.state.start_time   = time.monotonic()
        app.state.auth_disabled = _auth_disabled
        app.state.rate_limiter  = RateLimiter.from_env()
        # Graceful-shutdown state: prevents new compactions from starting while
        # the server is draining.  The lock serialises concurrent compaction
        # requests so we never compact in parallel (which would be unsafe).
        app.state.compact_lock   = asyncio.Lock()
        app.state.shutting_down  = False

        if not _auth_disabled:
            store = AuthStore(resolved)
            app.state.auth_store = store
            if store.count() == 0:
                log.warning(
                    "auth enabled but no API keys exist — "
                    "create one with: hmdb add-key %s --role admin",
                    resolved,
                )
        else:
            app.state.auth_store = None
            log.warning(
                "HyperMesh DB started with authentication DISABLED "
                "(--no-auth / HMDB_AUTH_DISABLED=1). This is intended for local "
                "development only — do not expose this server on a network."
            )

        # Startup complete: begin accepting traffic.
        app.state.ready            = True
        app.state.not_ready_reason = None

        yield

        # ── Graceful-shutdown teardown ───────────────────────────────────────
        # Stop advertising readiness immediately so the load balancer drains us
        # before we tear down the DB.
        app.state.ready            = False
        app.state.not_ready_reason = "shutting down"
        # Signal that no new compactions should start, then wait for any
        # in-flight compaction to release the lock before closing the DB.
        app.state.shutting_down = True
        async with app.state.compact_lock:
            pass  # waits until any running compaction finishes

        if getattr(app.state, "db", None):
            app.state.db.close()
            app.state.db = None
        if getattr(app.state, "auth_store", None):
            app.state.auth_store.close()
            app.state.auth_store = None

    _app = FastAPI(
        title        = "HyperMesh DB REST API",
        version      = "1.0.0",
        description  = (
            "REST API for **HyperMesh DB** — temporal hypergraph database with "
            "proprietary TPI+FMI indexing.\n\n"
            "Start with: `hmdb serve /path/to/db [--auth]`\n\n"
            + ("⚠️  **Authentication is currently DISABLED.**"
               if _auth_disabled else
               "🔒  **Authentication is enabled.** All endpoints require `X-API-Key`.")
        ),
        contact      = {
            "name":  "Sreehas Gopinathan",
            "url":   "https://github.com/sree181/hypermesh-workbench",
            "email": "sreehasgopinathan@auburn.edu",
        },
        license_info = {"name": "Proprietary"},
        lifespan     = lifespan,
    )

    _app.add_middleware(
        CORSMiddleware,
        allow_origins     = ["*"],
        allow_methods     = ["*"],
        allow_headers     = ["*", "X-API-Key"],
        allow_credentials = False,
    )
    _app.add_middleware(_RateLimitMiddleware)

    @_app.get("/", include_in_schema=False)
    async def _root():
        # With a bundled UI, "/" serves the app; otherwise send people to docs.
        if _ui_path is not None:
            return FileResponse(_ui_path / "index.html")
        return RedirectResponse(url="/docs")

    @_app.exception_handler(QueryTimeout)
    async def _on_query_timeout(_request: Request, exc: QueryTimeout):
        # 504: the query exceeded HMDB_QUERY_TIMEOUT_MS and was cooperatively
        # aborted by the engine so it could not pin the read lock indefinitely.
        return JSONResponse(status_code=504, content={"detail": str(exc)})

    _app.include_router(router)

    # Agent memory — governed-mind verbs under /v1/agentmem/*. Part of the
    # base install (numpy-only core), so it mounts unconditionally.
    try:
        from hypermeshdb.agentmem.api import router as _agentmem_router
        _app.include_router(_agentmem_router)
    except Exception as exc:  # pragma: no cover - defensive import guard
        logging.getLogger(__name__).warning(
            "agentmem routes not mounted: %s", exc
        )

    # HyperMesh Virtual — zero-copy hypergraph routes under /v1/virtual/*.
    # Optional: gated behind the `virtual` extra (duckdb + lark). We mount only
    # when those runtime deps are importable, so the surface is never advertised
    # in an install that can't serve it, and a missing/broken install never
    # takes down the core API.
    try:
        import importlib.util

        if all(importlib.util.find_spec(m) for m in ("duckdb", "lark")):
            from hypermesh_virtual.api import router as _virtual_router
            _app.include_router(_virtual_router)
        else:
            logging.getLogger(__name__).info(
                "virtual routes not mounted: install the 'virtual' extra "
                "(pip install 'hypermesh[virtual]') to enable /v1/virtual/*"
            )
    except Exception as exc:  # pragma: no cover - defensive import guard
        logging.getLogger(__name__).warning("virtual routes not mounted: %s", exc)

    # ── Single-process UI (SPA) ──────────────────────────────────────────────
    # Serve the built front-end and its assets from the same process as the API
    # so the whole product runs as one process. Registered LAST so every API
    # route takes precedence; only unmatched paths fall through here. Real files
    # are served directly; everything else returns index.html for client-side
    # routing. API namespaces never fall through to the SPA.
    if _ui_path is not None:
        _api_prefixes = ("v1/", "v1", "health", "metrics", "docs", "redoc",
                         "openapi.json")
        _index_file = _ui_path / "index.html"

        @_app.get("/{full_path:path}", include_in_schema=False)
        async def _spa(full_path: str):
            if full_path.startswith(_api_prefixes):
                raise HTTPException(status_code=404, detail="Not found")
            if full_path:
                candidate = (_ui_path / full_path).resolve()
                # Path-traversal guard: candidate must stay inside the UI dir.
                try:
                    candidate.relative_to(_ui_path)
                except ValueError:
                    raise HTTPException(status_code=404, detail="Not found")
                if candidate.is_file():
                    return FileResponse(candidate)
            return FileResponse(_index_file)

    return _app


# ── Default app (used by uvicorn: hypermeshdb._api:app) ───────────────────────

app = create_app()
