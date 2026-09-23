"""
Project:     HyperMesh DB
File:        hypermeshdb/agentmem/store.py
Description: MemoryStore, the agent-memory substrate over the embedded
             engine. Identity-as-member: every memory hyperedge carries a
             minted ``edge:<ulid>`` entity among its members, so the FMI
             index resolves lookup-by-id and derivation links are ordinary
             hyperedges over id entities. Append-only by construction:
             supersession and tombstoning are new edges, never deletes,
             and memory tables are exempt from compaction.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

import re
import time
from typing import Any

from .._types import HyperMeshError
from ._content import ContentStore, sha256_of
from .gate import GateRejection, WriteGate
from ._envelope import (
    DERIVATION_PROPS_DDL,
    MEMORY_PROPS_DDL,
    Envelope,
    Kind,
    Memory,
    Origin,
    Rel,
    Status,
    TTLClass,
)
from ._ids import is_ulid, new_ulid
from ._registry import SqliteEntityRegistry

MEMORY_TABLE = "AGENT_MEMORY"
DERIVATION_TABLE = "AGENT_DERIVATION"

# Engine caps members at 64 per hyperedge; one slot is the edge-id entity.
MAX_SUBJECTS = 63

_SRC_SAFE = re.compile(r"[^A-Za-z0-9:_/.@#?&=+\- ]")


def _sanitize_text(value: str) -> str:
    """Restrict TEXT property values to a parser-safe charset."""
    return _SRC_SAFE.sub("_", value)


class MemoryStore:
    """Provenance-versioned agent memory over one HyperMesh database.

    All writes are appends. ``supersede`` and ``tombstone`` record change
    as new derivation edges rather than mutating or deleting records, so
    the full history remains queryable and ``compact`` is never invoked.
    """

    def __init__(self, db, db_dir: str, *, gate: WriteGate | None = None) -> None:
        """*db* is an open ``hypermeshdb.Connection``; *db_dir* its
        directory (sidecars live next to the engine files). Every write
        passes through *gate* (a default :class:`WriteGate` unless one is
        injected); pass a subclass to tighten policy, never to loosen the
        substrate invariants, which hold regardless."""
        self._db = db
        self._registry = SqliteEntityRegistry(db_dir)
        self._content = ContentStore(db_dir)
        self._gate = gate or WriteGate()
        self._ensure_tables()

    # ── setup ────────────────────────────────────────────────────────────

    def _ensure_tables(self) -> None:
        for name, props in (
            (MEMORY_TABLE, MEMORY_PROPS_DDL),
            (DERIVATION_TABLE, DERIVATION_PROPS_DDL),
        ):
            try:
                self._db.execute(
                    f"CREATE HYPEREDGE TABLE {name} () {props}"
                )
            except HyperMeshError as exc:
                if "exist" not in str(exc).lower():
                    raise

    # ── entity helpers ───────────────────────────────────────────────────

    def _edge_entity(self, ulid: str) -> int:
        return self._registry.get_or_create(f"edge:{ulid}")

    def _edge_node(self, ulid: str) -> int | None:
        return self._registry.get(f"edge:{ulid}")

    # ── writes ───────────────────────────────────────────────────────────

    def write(
        self,
        kind: Kind,
        subjects: list[str],
        *,
        origin: Origin,
        status: Status,
        source: str,
        payload: dict | None = None,
        confidence: float = 1.0,
        valid_from: int = 0,
        valid_to: int = 0,
        ttl_class: TTLClass | None = None,
        version: int = 1,
        event_ts: int | None = None,
        derived_from: list[str] | None = None,
    ) -> str:
        """Append one memory hyperedge; returns its ULID.

        Substrate-level invariants (the write gate proper arrives in
        Phase 3, but the type rules hold here):
        - EXTERNAL content enters only as UNVERIFIED or QUARANTINED.
        - Episodes are PERMANENT and never carry EXTERNAL origin.
        """
        if not subjects:
            raise ValueError("a memory needs at least one subject entity")
        if len(subjects) > MAX_SUBJECTS:
            raise ValueError(f"at most {MAX_SUBJECTS} subjects per memory")

        decision = self._gate.check(
            kind=kind,
            origin=origin,
            status=status,
            payload=payload,
            subjects=subjects,
            source=source,
        )
        if not decision.allowed:
            raise GateRejection(
                "write refused by gate: " + ", ".join(decision.reasons)
            )
        status = decision.status

        if origin == Origin.EXTERNAL and status not in (
            Status.UNVERIFIED,
            Status.QUARANTINED,
        ):
            raise ValueError(
                "external-origin content can only enter as UNVERIFIED or "
                "QUARANTINED; verification is a separate recorded step"
            )
        if kind == Kind.EPISODE:
            ttl_class = TTLClass.PERMANENT
            if origin == Origin.EXTERNAL:
                raise ValueError("episodes are agent records, never external")
        if ttl_class is None:
            ttl_class = TTLClass.STANDARD

        ulid = new_ulid()
        ts = int(event_ts if event_ts is not None else time.time())
        content_sha = self._content.put(ulid, payload) if payload else ""

        env = Envelope(
            kind=kind,
            origin=origin,
            status=status,
            confidence=confidence,
            valid_from=valid_from,
            valid_to=valid_to,
            version=version,
            ttl_class=ttl_class,
            source=_sanitize_text(source)[:120],
            content_sha=content_sha,
        )

        members = [self._edge_entity(ulid)]
        members += [self._registry.get_or_create(s) for s in subjects]
        self._insert(MEMORY_TABLE, ts, members, env.to_columns())

        if derived_from:
            self.derive(ulid, derived_from, Rel.DERIVED_FROM, event_ts=ts)
        return ulid

    def derive(
        self,
        child_ulid: str,
        parent_ulids: list[str],
        rel: Rel,
        *,
        event_ts: int | None = None,
    ) -> None:
        """Record a typed derivation link: child <rel> parents."""
        if not parent_ulids:
            raise ValueError("derivation needs at least one parent")
        child_node = self._edge_node(child_ulid)
        if child_node is None:
            raise ValueError(f"unknown child memory {child_ulid}")
        members = [child_node]
        for p in parent_ulids:
            node = self._edge_node(p)
            if node is None:
                raise ValueError(f"unknown parent memory {p}")
            members.append(node)
        ts = int(event_ts if event_ts is not None else time.time())
        self._insert(
            DERIVATION_TABLE,
            ts,
            members,
            {"REL": int(rel), "CHILD": child_ulid},
        )

    def supersede(
        self,
        old_ulid: str,
        *,
        subjects: list[str] | None = None,
        payload: dict | None = None,
        origin: Origin | None = None,
        status: Status | None = None,
        source: str | None = None,
        confidence: float | None = None,
        event_ts: int | None = None,
    ) -> str:
        """Write the next version of a memory and link SUPERSEDES old.

        Unspecified fields carry over from the old version. The old edge
        is untouched; currency is derived from the supersession chain."""
        old = self.get(old_ulid)
        if old is None:
            raise ValueError(f"unknown memory {old_ulid}")
        if old.tombstoned:
            raise ValueError(f"memory {old_ulid} is tombstoned")
        if old.superseded_by is not None:
            raise ValueError(
                f"memory {old_ulid} already superseded by {old.superseded_by}"
            )
        env = old.envelope
        new_ulid_ = self.write(
            env.kind,
            subjects
            if subjects is not None
            else [n for n in old.member_names if not n.startswith("edge:")],
            origin=origin if origin is not None else env.origin,
            status=status if status is not None else env.status,
            source=source if source is not None else env.source,
            payload=payload,
            confidence=confidence if confidence is not None else env.confidence,
            ttl_class=env.ttl_class,
            version=env.version + 1,
            event_ts=event_ts,
        )
        self.derive(new_ulid_, [old_ulid], Rel.SUPERSEDES, event_ts=event_ts)
        return new_ulid_

    def tombstone(
        self, ulid: str, *, reason: str, actor: str, event_ts: int | None = None
    ) -> dict[str, Any]:
        """Redact one memory: payload dropped, hash and graph retained.

        Returns the per-edge tombstone record used in certificates."""
        node = self._edge_node(ulid)
        if node is None:
            raise ValueError(f"unknown memory {ulid}")
        ts = int(event_ts if event_ts is not None else time.time())
        already = self._tombstone_ts(ulid) is not None
        if not already:
            self._insert(
                DERIVATION_TABLE,
                ts,
                [node, self._registry.get_or_create(f"actor:{actor}")],
                {"REL": int(Rel.TOMBSTONES), "CHILD": ulid},
            )
        _, sha, _ = self._content.get(ulid)
        redacted_now = self._content.redact(ulid)
        return {
            "ulid": ulid,
            "tombstoned_at": ts,
            "already_tombstoned": already,
            "payload_redacted": redacted_now,
            "content_sha_retained": sha or "",
            "reason": reason,
            "actor": actor,
        }

    def forget(
        self, ulid: str, *, reason: str, actor: str
    ) -> dict[str, Any]:
        """Tombstone a memory and its full forward derivation closure.

        Returns the deletion certificate: what was purged, what each
        purge retained (the hash), and the closure the decision covered."""
        closure = self.closure(ulid)
        records = [
            self.tombstone(u, reason=reason, actor=actor)
            for u in [ulid, *closure]
        ]
        return {
            "root": ulid,
            "closure": closure,
            "tombstoned": records,
            "reason": reason,
            "actor": actor,
            "issued_at": int(time.time()),
        }

    # ── reads ────────────────────────────────────────────────────────────

    def get(self, ulid: str) -> Memory | None:
        """Fetch one memory by ULID, with content and lineage flags."""
        if not is_ulid(ulid):
            raise ValueError(f"not a ULID: {ulid!r}")
        node = self._edge_node(ulid)
        if node is None:
            return None
        rows = self._fmi(MEMORY_TABLE, node)
        if not rows:
            return None
        row = rows[0].to_dict()
        envelope = Envelope.from_row(row)
        payload, sidecar_sha, redacted = self._content.get(ulid)
        graph_sha = envelope.content_sha
        if graph_sha != (sidecar_sha or ""):
            raise HyperMeshError(
                f"content integrity failure for {ulid}: graph and sidecar hashes differ"
            )
        if payload is not None and sha256_of(payload) != sidecar_sha:
            raise HyperMeshError(
                f"content integrity failure for {ulid}: payload hash differs"
            )
        tombstoned = self._tombstone_ts(ulid, node) is not None
        if tombstoned and not redacted:
            # The graph tombstone is written before the sidecar is redacted.
            # A process can die between those writes; completing the second
            # step here makes the operation restart-safe and never re-exposes
            # content that the authoritative graph says was deleted.
            self._content.redact(ulid)
            payload, redacted = None, True
        if redacted and not tombstoned:
            raise HyperMeshError(
                f"content integrity failure for {ulid}: payload is missing without a tombstone"
            )
        names = self._registry.names_of([int(m) for m in row["members"]])
        return Memory(
            ulid=ulid,
            envelope=envelope,
            event_ts=int(row["event_ts"]),
            member_names=[names.get(int(m), str(m)) for m in row["members"]],
            content=payload,
            redacted=redacted,
            superseded_by=self._superseded_by(ulid, node),
            tombstoned=tombstoned,
        )

    def find_by_subject(self, subject: str) -> list[str]:
        """ULIDs of memories whose members include *subject*, oldest first."""
        node = self._registry.get(subject)
        if node is None:
            return []
        out = []
        for r in self._fmi(MEMORY_TABLE, node):
            u = self._row_ulid(r)
            if u:
                out.append((int(r["event_ts"]), u))
        return [u for _, u in sorted(out)]

    def why(self, ulid: str, *, max_depth: int = 16) -> dict[str, Any]:
        """The evidence chain behind a memory: recursive parents through
        DERIVED_FROM, SUPPORTS and SUPERSEDES links."""
        seen: set[str] = set()

        def walk(u: str, depth: int) -> dict[str, Any]:
            seen.add(u)
            node = {"ulid": u, "parents": []}
            if depth >= max_depth:
                return node
            for rel, parent in self._parents_of(u):
                entry: dict[str, Any] = {"rel": rel.name, **walk(parent, depth + 1)} \
                    if parent not in seen else {"rel": rel.name, "ulid": parent, "cycle": True}
                node["parents"].append(entry)
            return node

        return walk(ulid, 0)

    def closure(self, ulid: str, *, max_depth: int = 64) -> list[str]:
        """Forward derivation closure: everything derived from, supported
        by, or superseding *ulid*. This is a forget's blast radius."""
        out: list[str] = []
        seen = {ulid}
        frontier = [ulid]
        depth = 0
        while frontier and depth < max_depth:
            nxt: list[str] = []
            for u in frontier:
                for child in self._children_of(u):
                    if child not in seen:
                        seen.add(child)
                        out.append(child)
                        nxt.append(child)
            frontier = nxt
            depth += 1
        return out

    def as_of(self, ts: int) -> list[str]:
        """ULIDs current at epoch-second *ts*: written by then, not yet
        superseded or tombstoned by then, and inside their validity
        window. This is the rewind primitive."""
        rows = self._db.execute(
            f"MATCH HYPEREDGE (he:{MEMORY_TABLE}) "
            f"WHERE he.event_ts >= 0 AND he.event_ts <= {int(ts)} RETURN *"
        ).rows
        current: list[str] = []
        for r in rows:
            u = self._row_ulid(r)
            if u is None:
                continue
            env = Envelope.from_row(r.to_dict())
            if env.valid_from and ts < env.valid_from:
                continue
            if env.valid_to and ts > env.valid_to:
                continue
            sup = self._supersession_ts(u)
            if sup is not None and sup <= ts:
                continue
            tomb = self._tombstone_ts(u)
            if tomb is not None and tomb <= ts:
                continue
            current.append(u)
        return current

    # ── internals ────────────────────────────────────────────────────────

    def _insert(
        self, table: str, ts: int, members: list[int], cols: dict[str, Any]
    ) -> None:
        col_names = ", ".join(cols)
        vals = []
        for v in cols.values():
            if isinstance(v, str):
                vals.append(f"'{_sanitize_text(v)}'")
            elif isinstance(v, float):
                vals.append(repr(v))
            else:
                vals.append(str(int(v)))
        members_lit = "[" + ", ".join(str(int(m)) for m in members) + "]"
        self._db.execute(
            f"INSERT INTO {table} (event_ts, members, {col_names}) "
            f"VALUES ({int(ts)}, {members_lit}, {', '.join(vals)})"
        )

    def _fmi(self, table: str, node: int):
        return self._db.execute(
            f"MATCH HYPEREDGE (he:{table}) WHERE {int(node)} IN he.members "
            "RETURN *"
        ).rows

    def _row_ulid(self, row) -> str | None:
        names = self._registry.names_of([int(m) for m in row["members"]])
        for name in names.values():
            if name.startswith("edge:"):
                return name[5:]
        return None

    def _derivations_touching(self, ulid: str, node: int | None = None):
        node = node if node is not None else self._edge_node(ulid)
        if node is None:
            return []
        return self._fmi(DERIVATION_TABLE, node)

    def _parents_of(self, ulid: str) -> list[tuple[Rel, str]]:
        out: list[tuple[Rel, str]] = []
        for r in self._derivations_touching(ulid):
            if str(r["CHILD"]) != ulid:
                continue
            rel = Rel(int(r["REL"]))
            if rel == Rel.TOMBSTONES:
                continue
            names = self._registry.names_of([int(m) for m in r["members"]])
            for name in names.values():
                if name.startswith("edge:") and name[5:] != ulid:
                    out.append((rel, name[5:]))
        return out

    def _children_of(self, ulid: str) -> list[str]:
        out: list[str] = []
        for r in self._derivations_touching(ulid):
            child = str(r["CHILD"])
            if child != ulid and Rel(int(r["REL"])) != Rel.TOMBSTONES:
                out.append(child)
        return out

    def _superseded_by(self, ulid: str, node: int | None = None) -> str | None:
        for r in self._derivations_touching(ulid, node):
            if (
                Rel(int(r["REL"])) == Rel.SUPERSEDES
                and str(r["CHILD"]) != ulid
            ):
                return str(r["CHILD"])
        return None

    def _supersession_ts(self, ulid: str) -> int | None:
        for r in self._derivations_touching(ulid):
            if (
                Rel(int(r["REL"])) == Rel.SUPERSEDES
                and str(r["CHILD"]) != ulid
            ):
                return int(r["event_ts"])
        return None

    def _tombstone_ts(self, ulid: str, node: int | None = None) -> int | None:
        for r in self._derivations_touching(ulid, node):
            if (
                Rel(int(r["REL"])) == Rel.TOMBSTONES
                and str(r["CHILD"]) == ulid
            ):
                return int(r["event_ts"])
        return None

    def close(self) -> None:
        self._registry.close()
        self._content.close()
