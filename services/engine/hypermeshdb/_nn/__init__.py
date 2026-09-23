"""
_nn — the HyperMesh modeling layer (Phase 1: feature loading + tensor bridges).

This package turns a stored hypergraph (a hyperedge table + an optional node
table) into model-ready tensors for the major hypergraph / graph deep-learning
frameworks, behind a single high-level entry point :func:`prepare`.

The public, supported surface is re-exported from :mod:`hypermesh.nn`.

Pipeline
--------
1. :func:`featurize` — full table scan → :class:`~hypermeshdb._analytics.HypergraphPy`
   incidence, joined with node-table attributes (``X``), per-edge attributes
   (``E``) and an optional label vector (``y``).  Result: :class:`FeaturedHypergraph`.
2. Tensor bridges — :func:`to_torch`, :func:`to_pyg`, :func:`to_dgl`,
   :func:`to_dhg` convert a :class:`FeaturedHypergraph` into the native container
   of each framework.
3. :func:`prepare` — the one-call front door: DB → framework-native data.

Example
-------
>>> import hypermesh as hm
>>> db = hm.connect("/var/lib/hypermesh/data")
>>> data = hm.nn.prepare(db, "CoProximity", framework="pyg",
...                       node_table="Patient", label="label")
>>> data.x.shape, data.hyperedge_index.shape
"""

from __future__ import annotations

from ._bridges import to_dgl, to_dhg, to_pyg, to_torch
from ._data import FeaturedHypergraph, featurize, prepare
from ._models import FittedHGNN, fit, hgnn_operator
from ._reservoir import ReservoirClassifier, temporal_features

__all__ = [
    # Phase 1: feature loading + tensor bridges
    "FeaturedHypergraph",
    "featurize",
    "prepare",
    "to_torch",
    "to_pyg",
    "to_dgl",
    "to_dhg",
    # Phase 2: owned model + single-call training
    "fit",
    "FittedHGNN",
    "hgnn_operator",
    # Phase 2: temporal reservoir computing (LSM / ESN)
    "ReservoirClassifier",
    "temporal_features",
]
