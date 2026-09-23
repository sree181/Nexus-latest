"""Turn an hgviz analysis of a Hypergraph into the API response models. This is
where the topology becomes governance signals: blocks -> coupled risk with an
entanglement score, bridges -> single-point-of-propagation cut recommendations,
forbidden bundles -> genuine unavoidable-coupling hotspots."""

from __future__ import annotations

from .hgviz import Hypergraph, decompose, entanglement, forbidden_bundles
from .hgviz.decompose import Decomposition
from .hgviz.hypergraph import dual, is_dual, undual
from .hgviz.simplify import multiscale
from .models import (
    BlockOut,
    BridgeOut,
    DecompositionOut,
    ForbiddenOut,
    Hyperedge,
    HgVertex,
    HypergraphOut,
    ScaleOut,
)


def hypergraph_out(h: Hypergraph, d: Decomposition | None = None) -> HypergraphOut:
    d = d or decompose(h)
    vertices = [
        HgVertex(id=v, kind=h.vkind.get(v, "entity"),
                 structure=d.node_structure.get(v, "branch:0"))
        for v in sorted(h.vertices)
    ]
    edges = [
        Hyperedge(id=e, members=sorted(members),
                  structure=d.node_structure.get(dual(e), "branch:0"))
        for e, members in h.edges.items()
    ]
    return HypergraphOut(vertices=vertices, edges=edges)


def _block_of(node: str, d: Decomposition) -> str | None:
    s = d.node_structure.get(node)
    return s if s and s.startswith("block:") else None


def decomposition_out(h: Hypergraph) -> DecompositionOut:
    d = decompose(h)
    bundles = forbidden_bundles(h)

    # attribute each forbidden bundle to the block its hyperedges sit in
    forbidden_per_block: dict[str, int] = {}
    forbidden_out: list[ForbiddenOut] = []
    for b in bundles:
        forbidden_out.append(ForbiddenOut(kind=b.kind, edges=b.edges, shared=b.shared))
        for e in b.edges:
            blk = _block_of(dual(e), d)
            if blk:
                forbidden_per_block[blk] = forbidden_per_block.get(blk, 0) + 1
                break

    blocks = [
        BlockOut(id=b.id, primal=b.primal, dual=b.dual, b1=b.b1, eta=b.eta,
                 forbidden=forbidden_per_block.get(b.id, 0))
        for b in sorted(d.blocks, key=lambda x: x.eta, reverse=True)
    ]

    bridges: list[BridgeOut] = []
    for i, members in enumerate(d.bridges):
        entities = sorted(undual(n) for n in members if not is_dual(n))
        hop = ", ".join(entities) if entities else "the connector"
        bridges.append(BridgeOut(
            id=f"bridge:{i}",
            members=entities,
            recommendation=(
                f"{hop} is the only link between two coupled regions. "
                "Forgetting it decouples them with a single action."
            ),
        ))

    return DecompositionOut(
        b0=d.b0,
        b1=d.b1,
        entanglement=entanglement(h),
        blocks=blocks,
        bridges=bridges,
        branches=len(d.branches),
        forbidden=forbidden_out,
    )


def scales_out(h: Hypergraph, levels: int = 3) -> list[ScaleOut]:
    out: list[ScaleOut] = []
    for s in multiscale(h, levels=levels):
        sh: Hypergraph = s["hypergraph"]
        out.append(ScaleOut(
            scale=s["scale"], label=s["label"], b0=s["b0"], b1=s["b1"],
            graph=hypergraph_out(sh),
        ))
    return out
