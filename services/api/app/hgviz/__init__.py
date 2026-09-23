"""hgviz: structure-aware hypergraph analysis for MeshAgent.

Implements the topological decomposition of Oliver, Zhang & Zhang,
"Structure-Aware Simplification for Hypergraph Visualization" (IEEE TVCG 2024,
arXiv:2407.19621), over MeshAgent's governed memory hypergraph:

  - the bipartite (Koenig) representation of the hypergraph,
  - decomposition into topological blocks, bridges and branches,
  - the entanglement index eta = B1/|V| per block (first Betti / vertices),
  - detection of forbidden sub-hypergraph bundles (sources of unavoidable overlap),
  - structure-aware simplification (leaf pruning + minimal cycle collapse)
    for multi-scale views.

The governance reinterpretation (blocks = coupled risk, bridges = single points
of propagation, branches = peripheral, eta = a coupling score) lives in the API
layer that consumes this.
"""

from .hypergraph import Hypergraph, bipartite
from .decompose import Decomposition, decompose, betti, entanglement
from .forbidden import ForbiddenBundle, forbidden_bundles
from .simplify import SimplifyOp, simplify_scale, multiscale

__all__ = [
    "Hypergraph",
    "bipartite",
    "Decomposition",
    "decompose",
    "betti",
    "entanglement",
    "ForbiddenBundle",
    "forbidden_bundles",
    "SimplifyOp",
    "simplify_scale",
    "multiscale",
]
