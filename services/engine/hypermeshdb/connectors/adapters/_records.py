"""
Shared record -> IR converters for connector adapters.

Two shapes, mirroring the two paths the old ``generic.py`` connector code
took:

- ``records_to_ir`` — *tabular* rows: each row's entity-column values become
  member vertices and the row becomes one hyperedge.  Used by S3 and the
  webhook free-form path.
- ``prebuilt_to_ir`` — records that already carry a ``members`` list.  Used
  by the webhook pre-built path.  (Member ids are re-resolved to stable
  hashed uint32s by the builder; the original value is kept as the node
  label, so cross-table id references are not preserved — acceptable for the
  push/webhook case.)

Member vertices are typed ``"Entity"`` so the emitted hyperedge table is
``(Entity, Entity)`` — byte-for-byte the shape ``generic.py`` produced, so
the Explorer/queries are unaffected.  The logical entity type (machine / ip /
account / …) is carried as a vertex *property* and surfaces in the entity map.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Iterator, Optional

from hypermesh_ingest.adapters.base import IRRecord
from hypermesh_ingest.ir.provenance import Provenance, SourceLocator
from hypermesh_ingest.ir.records import HyperedgeMember, HyperedgeRecord, VertexRecord
from hypermesh_ingest.spec.defaults import coerce_event_ts
from hypermesh_ingest.spec.schema import MappingSpec

# Member vertex type — matches generic.py's ``CREATE HYPEREDGE TABLE … (Entity, Entity)``.
MEMBER_TYPE = "Entity"


def _prov(source_id: str, spec: Optional[MappingSpec], **locator: Any) -> Provenance:
    return Provenance(
        source=SourceLocator.make(source_id, **locator),
        spec=spec.spec_path if spec else None,
        captured_at=int(time.time()),
    )


def make_vertex(local_id: str, *, etype: str, display: str,
                source_id: str, spec: Optional[MappingSpec], **loc: Any) -> VertexRecord:
    return VertexRecord.make(
        local_id=local_id,
        table=MEMBER_TYPE,
        type=MEMBER_TYPE,
        identity_keys={"id": local_id},
        provenance=_prov(source_id, spec, **loc),
        properties={"type": etype, "raw": display, "display": display},
    )


def records_to_ir(
    records: Iterable[dict],
    *,
    entity_cols: list[str],
    ts_col: Optional[str],
    target_table: str,
    source_id: str,
    spec: Optional[MappingSpec] = None,
    entity_types: Optional[dict[str, str]] = None,
) -> Iterator[IRRecord]:
    """Tabular rows -> Vertex + Hyperedge IR."""
    entity_types = entity_types or {}
    for lineno, row in enumerate(records):
        members: list[HyperedgeMember] = []
        seen: set[str] = set()
        for col in entity_cols:
            raw = row.get(col)
            if raw in (None, ""):
                continue
            val = str(raw).strip()
            if not val:
                continue
            etype = entity_types.get(col, "generic")
            local_id = f"{etype}:{val}"
            if local_id in seen:
                continue
            seen.add(local_id)
            yield make_vertex(local_id, etype=etype, display=val,
                              source_id=source_id, spec=spec, row=lineno, col=col)
            members.append(HyperedgeMember.make(local_id))

        if len(members) < 2:
            continue
        ts = coerce_event_ts(row.get(ts_col)) if ts_col else 0
        yield HyperedgeRecord.make(
            local_id=f"{target_table}_{lineno}",
            table=target_table,
            type=target_table,
            members=members,
            provenance=_prov(source_id, spec, row=lineno),
            valid_time=(ts, None) if ts else None,
        )


def prebuilt_to_ir(
    records: Iterable[dict],
    *,
    target_table: str,
    source_id: str,
    spec: Optional[MappingSpec] = None,
) -> Iterator[IRRecord]:
    """Records with an explicit ``members`` list -> Hyperedge IR."""
    for lineno, rec in enumerate(records):
        raw_members = rec.get("members") or []
        members: list[HyperedgeMember] = []
        seen: set[str] = set()
        for m in raw_members:
            local_id = str(m)
            if local_id in seen:
                continue
            seen.add(local_id)
            yield make_vertex(local_id, etype="generic", display=local_id,
                              source_id=source_id, spec=spec, row=lineno)
            members.append(HyperedgeMember.make(local_id))

        if len(members) < 2:
            continue
        ts = coerce_event_ts(rec.get("ts", rec.get("event_ts", 0)))
        props: dict[str, Any] = {}
        if "weight" in rec:
            props["weight"] = rec["weight"]
        yield HyperedgeRecord.make(
            local_id=f"{target_table}_{lineno}",
            table=target_table,
            type=target_table,
            members=members,
            provenance=_prov(source_id, spec, row=lineno),
            properties=props or None,
            valid_time=(ts, None) if ts else None,
        )
