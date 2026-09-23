"""
Project:     HyperMesh DB
File:        hypermeshdb/agentmem/api.py
Description: REST surface for governed agent memory, mounted under
             /v1/agentmem/*. It exposes the debuggable-mind verbs over
             HTTP so a service, not only an in-process agent, can record
             gated beliefs and then explain, audit, forget, or rewind
             them. A MemoryStore is built per request over the app's
             shared engine connection and its db_dir, so the id space and
             sidecars match the MCP and in-process paths.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ._envelope import Kind, Origin, Status
from .store import MemoryStore
from .verbs import Verbs

# Reuse the host API's role guards so this surface enforces the same auth
# as the rest of /v1. Imported here rather than at package top so the base
# agentmem package never hard-depends on FastAPI or the server module;
# _api imports this router only inside create_app, by which point the host
# API module is fully loaded, so this import does not cycle.
try:
    from .._api import require_role

    _RO = Depends(require_role("readonly"))
    _RW = Depends(require_role("readwrite"))
except Exception:  # pragma: no cover - server extra absent; routes never mount
    def _noop() -> None:
        return None

    _RO = Depends(_noop)
    _RW = Depends(_noop)

router = APIRouter(prefix="/v1/agentmem", tags=["agentmem"])

_ORIGIN = {
    "user": (Origin.USER, Status.USER_STATED),
    "agent": (Origin.AGENT, Status.UNVERIFIED),
    "external": (Origin.EXTERNAL, Status.UNVERIFIED),
}


def _store(request: Request) -> MemoryStore:
    db = getattr(request.app.state, "db", None)
    db_dir = getattr(request.app.state, "db_dir", None)
    if db is None or db_dir is None:
        raise HTTPException(status_code=503, detail="database not ready")
    return MemoryStore(db, db_dir)


class LearnIn(BaseModel):
    subject: str
    statement: str
    origin: str = Field(default="agent")
    source: str = Field(default="agent")
    derived_from: list[str] | None = None


class ForgetIn(BaseModel):
    reason: str
    actor: str


@router.post("/beliefs")
async def learn(body: LearnIn, request: Request, _: Any = _RW) -> dict[str, Any]:
    """Record a governed belief through the write gate. External content
    is capped at unverified and instruction-shaped text is quarantined."""
    if body.origin.strip().lower() not in _ORIGIN:
        raise HTTPException(
            status_code=422, detail=f"origin must be one of {sorted(_ORIGIN)}"
        )
    store = _store(request)
    org, st = _ORIGIN[body.origin.strip().lower()]
    try:
        ulid = store.write(
            Kind.FACT,
            [body.subject],
            origin=org,
            status=st,
            source=body.source,
            payload={"statement": body.statement},
            derived_from=body.derived_from,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    m = store.get(ulid)
    return {
        "ulid": ulid,
        "citation": f"EDGE-{ulid}",
        "status": m.envelope.status.name.lower(),
        "quarantined": m.envelope.status == Status.QUARANTINED,
    }


@router.get("/beliefs/{ulid}")
async def get_belief(ulid: str, request: Request, _: Any = _RO) -> dict[str, Any]:
    store = _store(request)
    try:
        m = store.get(ulid)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if m is None:
        raise HTTPException(status_code=404, detail=f"no belief {ulid}")
    return {
        "ulid": ulid,
        "kind": m.envelope.kind.name,
        "status": m.envelope.status.name,
        "origin": m.envelope.origin.name,
        "source": m.envelope.source,
        "statement": (m.content or {}).get("statement"),
        "tombstoned": m.tombstoned,
        "superseded_by": m.superseded_by,
        "content_sha": m.envelope.content_sha,
    }


@router.get("/beliefs/{ulid}/why")
async def why(ulid: str, request: Request, max_depth: int = 16, _: Any = _RO) -> dict[str, Any]:
    """The evidence chain behind a belief, each node with its source."""
    store = _store(request)
    if store.get(ulid) is None:
        raise HTTPException(status_code=404, detail=f"no belief {ulid}")
    return {"ulid": ulid, "chain": Verbs(store).why(ulid, max_depth=max_depth).flatten()}


@router.get("/beliefs/{ulid}/audit")
async def audit(ulid: str, request: Request, _: Any = _RO) -> dict[str, Any]:
    """Incident record: provenance, currency, evidence chain, blast radius."""
    store = _store(request)
    m = store.get(ulid)
    if m is None:
        raise HTTPException(status_code=404, detail=f"no belief {ulid}")
    return {
        "ulid": ulid,
        "kind": m.envelope.kind.name,
        "status": m.envelope.status.name,
        "origin": m.envelope.origin.name,
        "source": m.envelope.source,
        "content_sha": m.envelope.content_sha,
        "tombstoned": m.tombstoned,
        "redacted": m.redacted,
        "superseded_by": m.superseded_by,
        "evidence_chain": Verbs(store).why(ulid).flatten(),
        "blast_radius": store.closure(ulid),
    }


@router.post("/beliefs/{ulid}/forget")
async def forget(ulid: str, body: ForgetIn, request: Request, _: Any = _RW) -> dict[str, Any]:
    """Forget a belief and its derivation closure; return the deletion
    certificate (purged edges, closure, retained hashes)."""
    store = _store(request)
    if store.get(ulid) is None:
        raise HTTPException(status_code=404, detail=f"no belief {ulid}")
    cert = Verbs(store).revert(ulid, reason=body.reason, actor=body.actor)
    return cert.as_dict()


@router.get("/rewind")
async def rewind(request: Request, as_of_ts: int, _: Any = _RO) -> dict[str, Any]:
    """The governed beliefs current as of epoch-second ``as_of_ts``."""
    store = _store(request)
    mems = Verbs(store).rewind(int(as_of_ts))
    return {
        "as_of": int(as_of_ts),
        "count": len(mems),
        "beliefs": [
            {
                "ulid": m.ulid,
                "status": m.envelope.status.name.lower(),
                "source": m.envelope.source,
                "statement": (m.content or {}).get("statement"),
            }
            for m in mems
        ],
    }
