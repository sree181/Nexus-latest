"""Structure-aware simplification for multi-scale hypergraph views.

Two atomic operations from the paper, applied where the decomposition says they
help (branches first, then the most entangled cycles):

  - leaf pruning: remove a degree-1 vertex from its hyperedge (and empty
    hyperedges). Topology-preserving: does not change B1. Clears branches.
  - minimal cycle collapse: merge two vertices lying on a shortest cycle,
    reducing B1 by exactly one. Topology-altering: unknots an entangled block.

`multiscale` returns a sequence of scales, coarsest structure preserved last,
so a viewer can page from the full graph to a clutter-free overview.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from .decompose import betti
from .hypergraph import Hypergraph, bipartite, is_dual, undual


@dataclass
class SimplifyOp:
    kind: str            # "leaf-prune" | "cycle-collapse"
    detail: str


def leaf_prune(h: Hypergraph) -> tuple[Hypergraph, list[SimplifyOp]]:
    """Remove all degree-1 vertices and resulting empty/singleton hyperedges.
    Iterates to a fixpoint. Preserves every cycle (B1 unchanged)."""
    h = h.copy()
    ops: list[SimplifyOp] = []
    changed = True
    while changed:
        changed = False
        # degree of each vertex across hyperedges
        deg: dict[str, int] = {}
        for members in h.edges.values():
            for v in members:
                deg[v] = deg.get(v, 0) + 1
        for e in list(h.edges):
            leaves = {v for v in h.edges[e] if deg.get(v, 0) == 1}
            if leaves:
                h.edges[e] -= leaves
                for v in leaves:
                    h.vkind.pop(v, None)
                ops.append(SimplifyOp("leaf-prune", f"pruned {len(leaves)} leaf vertex(es) from {e}"))
                changed = True
        # drop hyperedges that no longer relate >= 2 vertices
        for e in list(h.edges):
            if len(h.edges[e]) <= 1:
                h.edges.pop(e)
                ops.append(SimplifyOp("leaf-prune", f"removed singleton hyperedge {e}"))
                changed = True
    return h, ops


def _shortest_cycle(g: nx.Graph) -> list[str] | None:
    basis = nx.minimum_cycle_basis(g)
    if not basis:
        return None
    return min(basis, key=len)


def cycle_collapse(h: Hypergraph) -> tuple[Hypergraph, SimplifyOp | None]:
    """Merge two primal vertices on a shortest cycle, reducing B1 by one."""
    g = bipartite(h)
    cyc = _shortest_cycle(g)
    if not cyc:
        return h, None
    primals = [undual(n) for n in cyc if not is_dual(n)]
    if len(primals) < 2:
        return h, None
    keep, drop = primals[0], primals[1]
    h = h.copy()
    for e, members in h.edges.items():
        if drop in members:
            members.discard(drop)
            members.add(keep)
    h.vkind.pop(drop, None)
    return h, SimplifyOp("cycle-collapse", f"merged {drop} into {keep}")


def simplify_scale(h: Hypergraph, collapses: int = 0) -> tuple[Hypergraph, list[SimplifyOp]]:
    """One simplified scale: prune branches, then collapse up to `collapses`
    minimal cycles (most entangled unknotted first)."""
    h, ops = leaf_prune(h)
    for _ in range(collapses):
        h, op = cycle_collapse(h)
        if op is None:
            break
        ops.append(op)
    return h, ops


def multiscale(h: Hypergraph, levels: int = 3) -> list[dict]:
    """A ladder of scales for the UI. Scale 0 is the full graph; scale 1 prunes
    branches; each further scale collapses one more cycle."""
    scales: list[dict] = []
    b0, b1 = betti(bipartite(h))
    scales.append({"scale": 0, "label": "full", "b0": b0, "b1": b1,
                   "hypergraph": h, "ops": []})

    pruned, ops = leaf_prune(h)
    pb0, pb1 = betti(bipartite(pruned))
    scales.append({"scale": 1, "label": "branches pruned", "b0": pb0, "b1": pb1,
                   "hypergraph": pruned, "ops": ops})

    cur = pruned
    for i in range(2, levels + 1):
        cur, op = cycle_collapse(cur)
        cb0, cb1 = betti(bipartite(cur))
        scales.append({"scale": i, "label": f"{i - 1} cycle(s) collapsed",
                       "b0": cb0, "b1": cb1, "hypergraph": cur,
                       "ops": [op] if op else []})
        if op is None:
            break
    return scales
