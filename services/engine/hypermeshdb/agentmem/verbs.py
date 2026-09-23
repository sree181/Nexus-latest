"""
Project:     HyperMesh DB
File:        hypermeshdb/agentmem/verbs.py
Description: The four verbs, surfaced as presentation-ready operations
             over the memory substrate: why (the evidence chain behind a
             belief, flattened with sources), revert (forget one belief
             and its derivation closure, returning a deletion certificate),
             rewind (what the mind held as of a past instant), and relearn
             (record a corrected belief and re-derive from it). These are
             the operations no flat-text or pairwise-graph memory can
             offer, because each is a hypergraph traversal.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ._envelope import Kind, Memory, Origin, Status
from .store import MemoryStore


@dataclass
class Evidence:
    """One node in a why-chain, with the fields an auditor reads."""

    ulid: str
    kind: str
    status: str
    source: str
    statement: str | None
    rel_to_child: str | None
    redacted: bool
    parents: list["Evidence"] = field(default_factory=list)

    def flatten(self) -> list[dict[str, Any]]:
        """Depth-first list of (ulid, source, statement) for a report."""
        out = [{
            "ulid": self.ulid,
            "kind": self.kind,
            "status": self.status,
            "source": self.source,
            "statement": self.statement,
            "via": self.rel_to_child,
        }]
        for p in self.parents:
            out.extend(p.flatten())
        return out


@dataclass
class DeletionCertificate:
    """The artifact a revert issues: proof of what was purged and that the
    audit chain survives it (each hash retained)."""

    root: str
    reason: str
    actor: str
    issued_at: int
    purged: list[dict[str, Any]]
    closure: list[str]

    @property
    def count(self) -> int:
        return len(self.purged)

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "reason": self.reason,
            "actor": self.actor,
            "issued_at": self.issued_at,
            "purged_count": self.count,
            "closure": self.closure,
            "purged": self.purged,
        }


class Verbs:
    """The debuggable-mind operations over one memory store."""

    def __init__(self, memory: MemoryStore) -> None:
        self._m = memory

    # ── why ────────────────────────────────────────────────────────────────

    def why(self, ulid: str, *, max_depth: int = 16) -> Evidence:
        """The evidence chain behind a belief, each node carrying its
        source and statement. This answers 'why did you believe that?'
        with a traversal, not a log grep."""
        raw = self._m.why(ulid, max_depth=max_depth)
        return self._decorate(raw, rel=None)

    def _decorate(self, node: dict[str, Any], *, rel: str | None) -> Evidence:
        ulid = node["ulid"]
        m = self._m.get(ulid)
        statement = None
        if m is not None and m.content is not None:
            statement = (
                m.content.get("statement")
                or m.content.get("doc")
                or m.content.get("claim")
                or m.content.get("task")
            )
        return Evidence(
            ulid=ulid,
            kind=m.envelope.kind.name if m else "UNKNOWN",
            status=m.envelope.status.name if m else "UNKNOWN",
            source=m.envelope.source if m else "",
            statement=statement,
            rel_to_child=rel,
            redacted=(m.redacted if m else False),
            parents=[
                self._decorate(p, rel=p.get("rel"))
                for p in node.get("parents", [])
                if "cycle" not in p
            ],
        )

    def trace_to_source(self, ulid: str) -> list[dict[str, Any]]:
        """Flattened root-cause list: every belief the target rests on,
        with its source. The first external source in the list is the
        origin an incident points at."""
        return self.why(ulid).flatten()

    # ── revert / forget ─────────────────────────────────────────────────────

    def revert(
        self, ulid: str, *, reason: str, actor: str
    ) -> DeletionCertificate:
        """Forget a belief and everything derived from it, and issue a
        deletion certificate. Alias-with-a-certificate over the store's
        forget primitive: the closure is computed, each edge tombstoned
        (payload redacted, hash kept), and the result is presentation
        ready for a CISO or an auditor."""
        cert = self._m.forget(ulid, reason=reason, actor=actor)
        return DeletionCertificate(
            root=cert["root"],
            reason=cert["reason"],
            actor=cert["actor"],
            issued_at=cert["issued_at"],
            purged=cert["tombstoned"],
            closure=cert["closure"],
        )

    # ── rewind ───────────────────────────────────────────────────────────────

    def rewind(self, ts: int) -> list[Memory]:
        """The beliefs current as of epoch-second *ts*: what the mind held
        then, reconstructed from validity intervals and the supersession
        and tombstone history, not from any snapshot."""
        return [
            m for u in self._m.as_of(ts)
            if (m := self._m.get(u)) is not None
        ]

    def rewind_statement(self, subject: str, ts: int) -> str | None:
        """The statement the mind held about *subject* as of *ts*, if any.
        Answers 'what would you have told me on that date?'"""
        current = {m.ulid for m in self.rewind(ts)}
        for u in self._m.find_by_subject(subject):
            if u in current:
                m = self._m.get(u)
                if m and m.content:
                    return m.content.get("statement")
        return None

    # ── relearn ──────────────────────────────────────────────────────────────

    def relearn(
        self,
        subject: str,
        corrected_statement: str,
        *,
        source: str,
        origin: Origin = Origin.USER,
        status: Status = Status.USER_STATED,
        supersede_ulid: str | None = None,
    ) -> str:
        """Record a corrected belief. When *supersede_ulid* is a live
        (non-tombstoned) belief, this supersedes it; when it is tombstoned
        (the usual case after a revert), a fresh belief is written with the
        clean provenance, since a tombstoned edge cannot be superseded.
        Returns the new belief's ULID."""
        if supersede_ulid is not None:
            existing = self._m.get(supersede_ulid)
            if existing is not None and not existing.tombstoned:
                return self._m.supersede(
                    supersede_ulid,
                    payload={"statement": corrected_statement},
                    source=source,
                    status=status,
                )
        return self._m.write(
            Kind.FACT,
            [subject],
            origin=origin,
            status=status,
            source=source,
            payload={"statement": corrected_statement},
        )
