"""
_bridges.py — tensor bridges for the HyperMesh modeling layer.

Each bridge converts a :class:`~hypermeshdb._nn._data.FeaturedHypergraph` into
the native container of a deep-learning framework.  The sparse incidence matrix
``B`` (shape ``n_nodes × n_edges``) is expressed in each framework's preferred
hypergraph encoding:

``to_torch``  dict of tensors: sparse ``incidence`` + ``hyperedge_index``
              ``[2, nnz]`` (row 0 = node position, row 1 = hyperedge position).
``to_pyg``    ``torch_geometric.data.Data`` with ``hyperedge_index`` /
              ``hyperedge_weight`` (the convention used by ``HypergraphConv``).
``to_dgl``    a DGL heterograph with ``node`` and ``hyperedge`` node types and
              ``in`` / ``has`` incidence relations.
``to_dhg``    dict with a ``dhg.Hypergraph`` plus feature/label tensors.

Optional dependencies are imported lazily; each bridge raises a clear
``ImportError`` naming the relevant ``pip install`` extra if a backend is
missing.  All bridges use 0-based incidence *positions*, with original node IDs
preserved in ``node_ids``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._data import FeaturedHypergraph


def _require(module: str, extra: str) -> Any:
    """Import *module* or raise a helpful ImportError naming the install extra."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - depends on env
        raise ImportError(
            f"{module} is required for this bridge. "
            f'Install it with:  pip install "hypermesh[{extra}]"  '
            f"(or:  pip install {module})"
        ) from exc


def _positions(hg: Any) -> tuple[Any, Any, Any]:
    """Return COO (row positions, col positions, data) of the incidence matrix."""
    coo = hg.B.tocoo()
    return coo.row, coo.col, coo.data


# ── torch ───────────────────────────────────────────────────────────────────────

def to_torch(fhg: FeaturedHypergraph) -> dict[str, Any]:
    """
    Convert to a framework-agnostic ``dict`` of PyTorch tensors.

    Keys
    ----
    ``incidence`` : sparse COO tensor ``(n_nodes, n_edges)``.
    ``hyperedge_index`` : ``LongTensor`` ``[2, nnz]`` (node pos, hyperedge pos).
    ``hyperedge_weight`` : ``FloatTensor`` ``(n_edges,)``.
    ``node_ids`` : ``LongTensor`` ``(n_nodes,)`` — original node IDs.
    ``x`` / ``edge_attr`` / ``y`` : present iff the corresponding features exist.
    ``num_nodes`` / ``num_edges`` : ints.
    """
    torch = _require("torch", "ml")
    hg = fhg.hg
    row, col, _ = _positions(hg)

    node_pos = torch.as_tensor(row, dtype=torch.long)
    edge_pos = torch.as_tensor(col, dtype=torch.long)
    hyperedge_index = torch.stack([node_pos, edge_pos], dim=0)
    values = torch.ones(hyperedge_index.shape[1], dtype=torch.float32)
    incidence = torch.sparse_coo_tensor(
        hyperedge_index, values, size=(hg.n_nodes, hg.n_edges)
    ).coalesce()

    out: dict[str, Any] = {
        "incidence": incidence,
        "hyperedge_index": hyperedge_index,
        "hyperedge_weight": torch.as_tensor(hg.weights, dtype=torch.float32),
        "node_ids": torch.as_tensor(hg.node_ids, dtype=torch.long),
        "num_nodes": hg.n_nodes,
        "num_edges": hg.n_edges,
    }
    if fhg.X is not None:
        out["x"] = torch.as_tensor(fhg.X, dtype=torch.float32)
    if fhg.E is not None:
        out["edge_attr"] = torch.as_tensor(fhg.E, dtype=torch.float32)
    if fhg.y is not None:
        dtype = torch.long if fhg.classes_ is not None else torch.float32
        out["y"] = torch.as_tensor(fhg.y, dtype=dtype)
    return out


# ── PyTorch Geometric ─────────────────────────────────────────────────────────

def to_pyg(fhg: FeaturedHypergraph) -> Any:
    """
    Convert to a ``torch_geometric.data.Data`` using the ``HypergraphConv``
    convention: ``hyperedge_index`` ``[2, nnz]`` plus ``hyperedge_weight``.
    """
    _require("torch", "ml-pyg")
    geom = _require("torch_geometric", "ml-pyg")
    Data = geom.data.Data

    t = to_torch(fhg)
    data = Data()
    data.num_nodes = fhg.n_nodes
    data.hyperedge_index = t["hyperedge_index"]
    data.hyperedge_weight = t["hyperedge_weight"]
    data.node_ids = t["node_ids"]
    if "x" in t:
        data.x = t["x"]
    if "edge_attr" in t:
        data.hyperedge_attr = t["edge_attr"]
    if "y" in t:
        data.y = t["y"]
    return data


# ── DGL ──────────────────────────────────────────────────────────────────────

def to_dgl(fhg: FeaturedHypergraph) -> Any:
    """
    Convert to a DGL heterograph with ``node`` and ``hyperedge`` node types.

    Relations: ``("node", "in", "hyperedge")`` and its reverse
    ``("hyperedge", "has", "node")``.  Node features land on ``node`` data
    ``"x"``; edge features on ``hyperedge`` data ``"x"``; the per-hyperedge
    weight on ``hyperedge`` data ``"weight"``.
    """
    dgl = _require("dgl", "ml-dgl")
    torch = _require("torch", "ml-dgl")
    hg = fhg.hg
    row, col, _ = _positions(hg)

    node_pos = torch.as_tensor(row, dtype=torch.long)
    edge_pos = torch.as_tensor(col, dtype=torch.long)
    g = dgl.heterograph(
        {
            ("node", "in", "hyperedge"): (node_pos, edge_pos),
            ("hyperedge", "has", "node"): (edge_pos, node_pos),
        },
        num_nodes_dict={"node": hg.n_nodes, "hyperedge": hg.n_edges},
    )
    g.nodes["hyperedge"].data["weight"] = torch.as_tensor(hg.weights, dtype=torch.float32)
    if fhg.X is not None:
        g.nodes["node"].data["x"] = torch.as_tensor(fhg.X, dtype=torch.float32)
    if fhg.E is not None:
        g.nodes["hyperedge"].data["x"] = torch.as_tensor(fhg.E, dtype=torch.float32)
    if fhg.y is not None:
        dtype = torch.long if fhg.classes_ is not None else torch.float32
        g.nodes["node"].data["y"] = torch.as_tensor(fhg.y, dtype=dtype)
    g.nodes["node"].data["node_id"] = torch.as_tensor(hg.node_ids, dtype=torch.long)
    return g


# ── DHG (DeepHypergraph) ─────────────────────────────────────────────────────

def to_dhg(fhg: FeaturedHypergraph) -> dict[str, Any]:
    """
    Convert to a ``dict`` holding a ``dhg.Hypergraph`` and aligned tensors.

    Keys: ``hypergraph`` (``dhg.Hypergraph`` over 0-based vertex positions),
    ``x`` / ``y`` (tensors or ``None``), ``node_ids`` (``LongTensor``).
    """
    torch = _require("torch", "ml-dhg")
    dhg = _require("dhg", "ml-dhg")
    hg = fhg.hg

    csc = hg.B.tocsc()
    indptr, indices = csc.indptr, csc.indices
    e_list = [
        [int(p) for p in indices[indptr[j]:indptr[j + 1]]]
        for j in range(hg.n_edges)
    ]
    e_weight = [float(w) for w in hg.weights]
    graph = dhg.Hypergraph(hg.n_nodes, e_list, e_weight=e_weight)

    x = torch.as_tensor(fhg.X, dtype=torch.float32) if fhg.X is not None else None
    y = None
    if fhg.y is not None:
        dtype = torch.long if fhg.classes_ is not None else torch.float32
        y = torch.as_tensor(fhg.y, dtype=dtype)
    return {
        "hypergraph": graph,
        "x": x,
        "y": y,
        "node_ids": torch.as_tensor(hg.node_ids, dtype=torch.long),
    }
