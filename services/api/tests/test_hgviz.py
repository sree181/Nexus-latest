"""Tests for the hgviz analysis engine on hypergraphs with known topology.

Bipartite cycles have length >= 4 (2 primal + 2 dual), so the smallest block is
two hyperedges over two shared vertices. We assert Betti numbers, block/bridge/
branch classification, entanglement values, forbidden bundles, and that
simplification changes B1 exactly as the theory says."""

from __future__ import annotations

from app.hgviz import (
    Hypergraph,
    bipartite,
    decompose,
    betti,
    entanglement,
    forbidden_bundles,
)
from app.hgviz.simplify import leaf_prune, cycle_collapse, multiscale


def hg(**edges) -> Hypergraph:
    h = Hypergraph()
    for eid, members in edges.items():
        h.add_edge(eid, list(members))
    return h


# -- bipartite + betti ---------------------------------------------------------

def test_bipartite_shape():
    h = hg(e1=["a", "b", "c"])
    g = bipartite(h)
    assert g.number_of_nodes() == 4  # a, b, c, E::e1
    assert g.number_of_edges() == 3
    assert betti(g) == (1, 0)  # a tree, no cycles


def test_two_hyperedges_two_shared_is_one_cycle():
    h = hg(e1=["a", "b"], e2=["a", "b"])
    assert betti(bipartite(h)) == (1, 1)
    assert entanglement(h) == round(1 / 4, 4)


def test_three_shared_vertices_two_cycles():
    h = hg(e1=["a", "b", "c"], e2=["a", "b", "c"])
    b0, b1 = betti(bipartite(h))
    assert (b0, b1) == (1, 2)
    assert entanglement(h) == round(2 / 5, 4)


# -- decomposition -------------------------------------------------------------

def test_pure_tree_is_all_branch_no_blocks():
    h = hg(e1=["a", "b"], e2=["b", "c"])  # a path, no cycle
    d = decompose(h)
    assert d.blocks == []
    assert d.b1 == 0
    assert len(d.branches) >= 1


def test_block_plus_branch():
    h = hg(e1=["a", "b"], e2=["a", "b"], e3=["b", "d"])  # block on {a,b} + leaf d
    d = decompose(h)
    assert len(d.blocks) == 1
    assert d.blocks[0].b1 == 1
    # the {E::e3, b, d} tree touches exactly one block -> a branch
    assert len(d.branches) == 1
    assert len(d.bridges) == 0


def test_bridge_between_two_blocks():
    h = hg(
        e1=["a", "b"], e2=["a", "b"],       # block 1
        e3=["c", "d"], e4=["c", "d"],       # block 2
        e5=["b", "c"],                        # bridge
    )
    d = decompose(h)
    assert len(d.blocks) == 2
    assert len(d.bridges) == 1
    assert len(d.branches) == 0


# -- forbidden bundles ---------------------------------------------------------

def test_a1_two_edges_share_three_vertices():
    h = hg(e1=["a", "b", "c"], e2=["a", "b", "c", "d"])
    bundles = forbidden_bundles(h)
    kinds = {b.kind for b in bundles}
    assert "3-adjacent-bundle-2edges" in kinds


def test_a2_three_edges_share_two_vertices():
    h = hg(e1=["a", "b"], e2=["a", "b", "x"], e3=["a", "b", "y"])
    bundles = forbidden_bundles(h)
    a2 = [b for b in bundles if b.kind == "2-adjacent-bundle-3edges"]
    assert a2 and set(a2[0].shared) >= {"a", "b"}


def test_clean_hypergraph_has_no_bundles():
    h = hg(e1=["a", "b"], e2=["b", "c"], e3=["c", "d"])
    assert forbidden_bundles(h) == []


# -- simplification ------------------------------------------------------------

def test_leaf_prune_removes_branch_keeps_cycles():
    h = hg(e1=["a", "b"], e2=["a", "b"], e3=["b", "d"])  # d is a leaf
    before = betti(bipartite(h))[1]
    pruned, ops = leaf_prune(h)
    after = betti(bipartite(pruned))[1]
    assert after == before        # topology preserving
    assert "d" not in pruned.vertices
    assert ops


def test_cycle_collapse_reduces_b1_by_one():
    h = hg(e1=["a", "b"], e2=["a", "b"])
    before = betti(bipartite(h))[1]
    collapsed, op = cycle_collapse(h)
    after = betti(bipartite(collapsed))[1]
    assert op is not None
    assert after == before - 1


def test_multiscale_is_monotone_nonincreasing_in_b1():
    h = hg(
        e1=["a", "b"], e2=["a", "b"], e3=["c", "d"], e4=["c", "d"],
        e5=["b", "c"], e6=["c", "z"],  # a bridge and a branch
    )
    scales = multiscale(h, levels=3)
    b1s = [s["b1"] for s in scales]
    assert b1s == sorted(b1s, reverse=True)  # never increases
    assert scales[0]["label"] == "full"
