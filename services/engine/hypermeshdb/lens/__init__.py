"""
hypermeshdb.lens — HyperMesh Lens: Structural Provenance & Explainability

Explains every pattern detection result by tracing actual hypergraph topology.
Every explanation is a real subgraph, not a statistical approximation.

Public API
----------
ProvenanceBuilder   — builds explanation subgraphs using FMI/TPI/incidence matrix
ProvenanceGraph     — the explanation object (nodes, hyperedges, narrative)
ProvenanceNode      — a node in the explanation subgraph with attribution score
ProvenanceHyperedge — a hyperedge in the explanation subgraph
LensConfig          — configuration for provenance traversal
"""

from .provenance import (
    ProvenanceBuilder,
    ProvenanceGraph,
    ProvenanceNode,
    ProvenanceHyperedge,
    LensConfig,
)

__all__ = [
    "ProvenanceBuilder",
    "ProvenanceGraph",
    "ProvenanceNode",
    "ProvenanceHyperedge",
    "LensConfig",
]
