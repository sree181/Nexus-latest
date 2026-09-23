"""Topological decomposition of a hypergraph via its bipartite representation:
blocks (cyclic, entangled), bridges (connect two blocks), branches (peripheral
trees), plus Betti numbers and the entanglement index.

Following Oliver, Zhang & Zhang (TVCG 2024): topological blocks are the
multi-edge (cyclic) biconnected components of the bipartite graph; the tree part
that connects/hangs off them splits into bridges (>=2 block roots) and branches
(<=1). The entanglement index eta(T) = B1(T)/|V(T)| measures how cycle-dense a
block is, and is where unavoidable polygon overlaps must occur.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from .hypergraph import Hypergraph, bipartite, is_dual


def betti(g: nx.Graph) -> tuple[int, int]:
    """(B0, B1) for a graph: connected components, and independent cycles
    B1 = |E| - |V| + B0."""
    b0 = nx.number_connected_components(g)
    b1 = g.number_of_edges() - g.number_of_nodes() + b0
    return b0, b1


@dataclass
class Block:
    id: str
    nodes: set[str]
    primal: int          # entities in the block
    dual: int            # hyperedges in the block
    b1: int              # independent cycles
    eta: float           # entanglement index B1 / |V(T)|


@dataclass
class Decomposition:
    b0: int
    b1: int
    blocks: list[Block] = field(default_factory=list)
    bridges: list[set[str]] = field(default_factory=list)
    branches: list[set[str]] = field(default_factory=list)
    articulation: set[str] = field(default_factory=set)
    # node id -> structure label ("block:0" | "bridge:1" | "branch:2")
    node_structure: dict[str, str] = field(default_factory=dict)

    def cycles_total(self) -> int:
        return sum(b.b1 for b in self.blocks)


def decompose(h: Hypergraph) -> Decomposition:
    g = bipartite(h)
    if g.number_of_nodes() == 0:
        return Decomposition(b0=0, b1=0)

    b0, b1 = betti(g)
    art = set(nx.articulation_points(g))

    blocks: list[Block] = []
    tree_edges: list[tuple[str, str]] = []
    node_block: dict[str, set[int]] = {}

    for comp in nx.biconnected_components(g):
        sub = g.subgraph(comp)
        n, m = sub.number_of_nodes(), sub.number_of_edges()
        if n == 2:
            u, v = tuple(comp)
            tree_edges.append((u, v))
            continue
        # cyclic biconnected component => a topological block
        bid = len(blocks)
        primal = sum(1 for x in comp if not is_dual(x))
        dual = n - primal
        blk_b1 = m - n + 1
        blocks.append(Block(
            id=f"block:{bid}", nodes=set(comp), primal=primal, dual=dual,
            b1=blk_b1, eta=round(blk_b1 / n, 4),
        ))
        for x in comp:
            node_block.setdefault(x, set()).add(bid)

    dec = Decomposition(b0=b0, b1=b1, blocks=blocks, articulation=art)
    for blk in blocks:
        for x in blk.nodes:
            dec.node_structure[x] = blk.id

    # Tree part: connected components of the tree-edge subgraph. Classify each by
    # how many distinct blocks it touches (via block-member nodes it contains).
    tg = nx.Graph()
    tg.add_edges_from(tree_edges)
    for comp in nx.connected_components(tg):
        touched: set[int] = set()
        for x in comp:
            touched |= node_block.get(x, set())
        struct = "bridge" if len(touched) >= 2 else "branch"
        idx = len(dec.bridges) if struct == "bridge" else len(dec.branches)
        (dec.bridges if struct == "bridge" else dec.branches).append(set(comp))
        for x in comp:
            # keep block label on articulation points; label pure tree nodes only
            dec.node_structure.setdefault(x, f"{struct}:{idx}")

    return dec


def entanglement(h: Hypergraph) -> float:
    """Whole-hypergraph entanglement: total independent cycles over vertices+edges
    of the bipartite graph. Per-block eta is on each Block."""
    g = bipartite(h)
    if g.number_of_nodes() == 0:
        return 0.0
    _, b1 = betti(g)
    return round(b1 / g.number_of_nodes(), 4)
