"""
Bridge a ``ConnectorConfig`` to a ``hypermesh_ingest`` ``MappingSpec``.

Connector data lands in a single hyperedge table (``target_table``) whose
members are generic ``Entity`` nodes — matching the ``(Entity, Entity)``
shape ``generic.py`` produced.  The namespace is keyed to the target table
so repeated syncs into the same table resolve identical entities to the same
node id (stable, dedup-friendly).
"""

from __future__ import annotations

from hypermesh_ingest.spec.schema import MappingSpec, MemberRole, TableSpec

from .base import ConnectorConfig

_MEMBER_TYPE = "Entity"


def connector_spec(config: ConnectorConfig) -> MappingSpec:
    target = (config.target_table or f"{config.type.upper()}_DATA").upper()
    bucket = int(config.settings.get("bucket_seconds", 10))

    he = TableSpec(
        name=target,
        kind="hyperedge",
        target_table=target,
        identity_keys=[],
        member_roles=[
            MemberRole(role="", type=_MEMBER_TYPE),
            MemberRole(role="", type=_MEMBER_TYPE),
        ],
        bucket_seconds=bucket,
    )
    return MappingSpec(
        namespace=target,
        source_type=config.type,
        source_url=config.settings.get("bucket", "") or config.target_table or "",
        tables={target: he},
    )
