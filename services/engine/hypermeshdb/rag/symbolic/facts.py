"""
rag/symbolic/facts.py — Fact base and the edge→fact bridge.

The reasoner operates over a finite fact base. Base (EDB) facts are produced
from retrieved hyperedges so that reasoning is in continuity with HyperMesh's
canonical formation logic: each hyperedge ``E`` (a co-occurrence of members
under a typed ``formation`` at ``event_ts``) yields::

    hyperedge(E)
    formation(E, <formation>)
    weight(E, <float>)
    at(E, <event_ts>)
    member(E, <node_id>)        for each member
    member_type(<node_id>, <t>) for each member's type

Every base fact remembers the edge it came from (``edge_ref``) so proof leaves
can be bound back to verifiable provenance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator


@dataclass(frozen=True)
class Fact:
    """A ground atom: ``pred(arg, ...)``. Hashable identity = (pred, args)."""
    pred: str
    args: tuple[Any, ...]

    def __str__(self) -> str:
        inner = ", ".join(str(a) for a in self.args)
        return f"{self.pred}({inner})"


@dataclass
class FactMeta:
    source: str                                  # "base" | "derived"
    confidence: float = 1.0
    support: tuple[Fact, ...] = ()               # premises (for derived facts)
    rule_id: str | None = None
    edge_ref: dict[str, Any] | None = None       # for base facts: origin edge


class FactBase:
    """Indexed set of facts with per-fact metadata."""

    def __init__(self) -> None:
        self._facts: dict[Fact, FactMeta] = {}
        self._by_pred: dict[str, list[Fact]] = {}

    # ── mutation ──────────────────────────────────────────────────────────────
    def add_base(self, fact: Fact, *, confidence: float = 1.0,
                 edge_ref: dict[str, Any] | None = None) -> bool:
        if fact in self._facts:
            return False
        self._facts[fact] = FactMeta(source="base", confidence=confidence,
                                     edge_ref=edge_ref)
        self._by_pred.setdefault(fact.pred, []).append(fact)
        return True

    def add_derived(self, fact: Fact, support: Iterable[Fact], rule_id: str,
                    confidence: float) -> bool:
        """Add a derived fact. Returns True only if newly added (keeps the
        derivation monotone → guaranteed termination)."""
        if fact in self._facts:
            return False
        self._facts[fact] = FactMeta(
            source="derived", confidence=confidence,
            support=tuple(support), rule_id=rule_id,
        )
        self._by_pred.setdefault(fact.pred, []).append(fact)
        return True

    # ── query ──────────────────────────────────────────────────────────────────
    def by_pred(self, pred: str) -> list[Fact]:
        return self._by_pred.get(pred, [])

    def meta(self, fact: Fact) -> FactMeta:
        return self._facts[fact]

    def __contains__(self, fact: object) -> bool:
        return fact in self._facts

    def __len__(self) -> int:
        return len(self._facts)

    def __iter__(self) -> Iterator[Fact]:
        return iter(self._facts)

    def derived(self) -> list[Fact]:
        return [f for f, m in self._facts.items() if m.source == "derived"]


def _norm_type(t: Any) -> str:
    return str(t or "entity").strip().lower()


def facts_from_edges(edges: Iterable[Any], table: str = "") -> FactBase:
    """Build a base FactBase from retrieved hyperedges (``RetrievedEdge``)."""
    fb = FactBase()
    for e in edges:
        eidx = int(getattr(e, "edge_idx"))
        ts = int(getattr(e, "timestamp", 0))
        members = [int(m) for m in getattr(e, "member_ids", []) or []]
        formation = str(getattr(e, "formation", "") or "")
        weight = round(float(getattr(e, "weight", 0.0) or 0.0), 6)
        types = list(getattr(e, "member_types", []) or [])
        edge_ref = {
            "table": table,
            "edge_idx": eidx,
            "event_ts": ts,
            "member_ids": members,
            "hedge_tag": f"HEDGE-{eidx}",
        }
        fb.add_base(Fact("hyperedge", (eidx,)), edge_ref=edge_ref)
        if formation:
            fb.add_base(Fact("formation", (eidx, formation)), edge_ref=edge_ref)
        fb.add_base(Fact("weight", (eidx, weight)), edge_ref=edge_ref)
        if ts:
            fb.add_base(Fact("at", (eidx, ts)), edge_ref=edge_ref)
        for i, nid in enumerate(members):
            fb.add_base(Fact("member", (eidx, nid)), edge_ref=edge_ref)
            etype = _norm_type(types[i]) if i < len(types) else "entity"
            fb.add_base(Fact("member_type", (nid, etype)), edge_ref=edge_ref)
    return fb
