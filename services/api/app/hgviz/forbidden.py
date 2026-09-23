"""Forbidden sub-hypergraph detection: the local configurations that force
unavoidable polygon overlaps (Oliver, Zhang & Zhang, TVCG 2024, Fig. 5 / Thm 2).

We detect the two combinatorial bundle types exactly:
  a1 - 3-adjacent bundle of 2 hyperedges: two hyperedges sharing >= 3 vertices.
  a2 - 2-adjacent bundle of 3 hyperedges: three+ hyperedges sharing a common
       >= 2-vertex set.
These are the clean, common cases. The strangled-vertex / strangled-hyperedge
variants require the full cycle-adjacency (A(C4)) analysis; we surface those
through per-block entanglement (an entangled block with no bundle still flags as
a likely-overlap region) rather than enumerating them here.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from .hypergraph import Hypergraph


@dataclass
class ForbiddenBundle:
    kind: str                 # "3-adjacent-bundle-2edges" | "2-adjacent-bundle-3edges"
    edges: list[str]          # hyperedge ids involved
    shared: list[str]         # the shared vertices


def forbidden_bundles(h: Hypergraph) -> list[ForbiddenBundle]:
    out: list[ForbiddenBundle] = []
    edge_ids = list(h.edges)

    # a1: pairs of hyperedges sharing >= 3 vertices
    for ei, ej in combinations(edge_ids, 2):
        shared = h.edges[ei] & h.edges[ej]
        if len(shared) >= 3:
            out.append(ForbiddenBundle(
                kind="3-adjacent-bundle-2edges",
                edges=[ei, ej],
                shared=sorted(shared),
            ))

    # a2: three+ hyperedges sharing a common >= 2-vertex set. Index hyperedges by
    # every 2-subset of vertices they contain; a 2-subset shared by >= 3 edges is
    # a bundle. Dedup so a bundle is reported once by its edge set.
    pair_to_edges: dict[frozenset[str], list[str]] = {}
    for e, members in h.edges.items():
        for pair in combinations(sorted(members), 2):
            pair_to_edges.setdefault(frozenset(pair), []).append(e)

    seen: set[frozenset[str]] = set()
    for pair, edges in pair_to_edges.items():
        if len(edges) >= 3:
            key = frozenset(edges)
            if key in seen:
                continue
            seen.add(key)
            # the full common set across those edges (>= the 2-subset)
            common: set[str] = set(h.edges[edges[0]])
            for e in edges[1:]:
                common &= h.edges[e]
            out.append(ForbiddenBundle(
                kind="2-adjacent-bundle-3edges",
                edges=sorted(edges),
                shared=sorted(common),
            ))
    return out
