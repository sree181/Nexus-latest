"""The hypergraph and its bipartite (Koenig) representation.

A hypergraph H = (V, E): V a set of vertices (entities), E a set of hyperedges
each an >=1-subset of V. The bipartite graph G = (X union Y, D) has a primal
node per vertex and a dual node per hyperedge, with an edge for every incidence.
All topological analysis in this package runs on G, per the paper.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

DUAL_PREFIX = "E::"  # namespace dual (hyperedge) nodes so they cannot clash with primals


@dataclass
class Hypergraph:
    """An undirected hypergraph. `edges` maps a hyperedge id to its member vertices.
    `vkind` optionally records each vertex's entity kind (class, pkg, sink, ...)
    for the renderer; it does not affect the topology."""

    edges: dict[str, set[str]] = field(default_factory=dict)
    vkind: dict[str, str] = field(default_factory=dict)

    @property
    def vertices(self) -> set[str]:
        out: set[str] = set()
        for members in self.edges.values():
            out |= members
        return out

    def add_edge(self, edge_id: str, members: list[str] | set[str]) -> None:
        self.edges[edge_id] = set(members)

    def degree(self, vertex: str) -> int:
        return sum(1 for m in self.edges.values() if vertex in m)

    def incident_edges(self, vertex: str) -> list[str]:
        return [e for e, m in self.edges.items() if vertex in m]

    def cardinality(self, edge_id: str) -> int:
        return len(self.edges[edge_id])

    def copy(self) -> "Hypergraph":
        return Hypergraph(
            edges={e: set(m) for e, m in self.edges.items()},
            vkind=dict(self.vkind),
        )


def dual(edge_id: str) -> str:
    return f"{DUAL_PREFIX}{edge_id}"


def is_dual(node: str) -> bool:
    return node.startswith(DUAL_PREFIX)


def undual(node: str) -> str:
    return node[len(DUAL_PREFIX):] if is_dual(node) else node


def bipartite(h: Hypergraph) -> nx.Graph:
    """Build the Koenig bipartite graph. Primal nodes carry bipartite=0, dual
    nodes bipartite=1. Isolated hyperedges (cardinality 0) are skipped."""
    g = nx.Graph()
    for v in h.vertices:
        g.add_node(v, bipartite=0, kind=h.vkind.get(v, "entity"))
    for e, members in h.edges.items():
        d = dual(e)
        g.add_node(d, bipartite=1, kind="hyperedge")
        for v in members:
            g.add_edge(d, v)
    return g
