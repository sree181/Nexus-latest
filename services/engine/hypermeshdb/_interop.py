"""
_interop.py — graph-library interoperability for HyperMesh DB.

Bridges the in-memory :class:`~hypermeshdb._analytics.HypergraphPy` snapshot
to the wider hypergraph / network-science ecosystem and to portable on-disk
exchange formats.

Exporters
---------
``to_networkx``    NetworkX graph (bipartite star expansion or weighted clique
                   expansion).
``to_hypernetx``   HyperNetX ``Hypergraph``.
``to_xgi``         XGI ``Hypergraph``.
``to_pandas``      pandas ``DataFrame`` (incidence edge-list or dense matrix).

Exchange formats
----------------
``to_hif`` / ``write_hif`` / ``from_hif`` / ``read_hif``
                   HIF (Hypergraph Interchange Format) JSON — the portable,
                   library-agnostic standard for sharing hypergraphs.
``to_graphml`` / ``write_graphml``
                   GraphML (via the NetworkX projection).

Every function accepts either a :class:`HypergraphPy` *or* anything exposing a
``to_hypergraph(table)`` method (an embedded :class:`~hypermeshdb.Connection`),
so both of these work::

    import hypermesh as hm
    hg = db.to_hypergraph("CoProximity")
    g  = hm.interop.to_networkx(hg)              # from a snapshot
    g  = hm.interop.to_networkx(db, table="CoProximity")  # from a connection

Optional dependencies are imported lazily; each exporter raises a clear
``ImportError`` naming the relevant ``pip install`` extra if the backend
library is missing.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from ._analytics import HypergraphPy

__all__ = [
    "to_networkx",
    "to_hypernetx",
    "to_xgi",
    "to_pandas",
    "to_hif",
    "write_hif",
    "from_hif",
    "read_hif",
    "to_graphml",
    "write_graphml",
]


# ── Source coercion ───────────────────────────────────────────────────────────

def _as_hypergraph(source: Any, table: str | None) -> HypergraphPy:
    """
    Coerce *source* into a :class:`HypergraphPy`.

    Accepts a ``HypergraphPy`` directly, or any object exposing
    ``to_hypergraph(table)`` (e.g. an embedded ``Connection``).
    """
    from ._analytics import HypergraphPy

    if isinstance(source, HypergraphPy):
        if table is not None:
            raise TypeError(
                "table= is only valid when passing a Connection; "
                "a HypergraphPy is already materialised for one table."
            )
        return source

    to_hg = getattr(source, "to_hypergraph", None)
    if callable(to_hg):
        return cast("HypergraphPy", to_hg(table))

    raise TypeError(
        "Expected a HypergraphPy or an object with a to_hypergraph() method "
        f"(e.g. an embedded Connection); got {type(source).__name__}. "
        "Remote clients cannot build a hypergraph locally — fetch rows with "
        "execute(...) and pass them to hypermesh.analytics.build_hypergraph()."
    )


def _require(module: str, extra: str) -> Any:
    """Import *module* or raise a helpful ImportError naming the install extra."""
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - depends on env
        raise ImportError(
            f"{module} is required for this exporter. "
            f'Install it with:  pip install "hypermesh[{extra}]"  '
            f"(or:  pip install {module})"
        ) from exc


# ── Incidence helpers ─────────────────────────────────────────────────────────

def _edge_members(hg: HypergraphPy) -> list[list[int]]:
    """
    Return, for each hyperedge column ``j``, the list of original node IDs that
    belong to it (order follows the incidence matrix row order).
    """
    if hg.n_edges == 0:
        return []
    csc = hg.B.tocsc()
    node_ids = hg.node_ids
    members: list[list[int]] = []
    indptr = csc.indptr
    indices = csc.indices
    for j in range(hg.n_edges):
        rows = indices[indptr[j]:indptr[j + 1]]
        members.append([int(node_ids[r]) for r in rows])
    return members


def _edge_attrs(hg: HypergraphPy, j: int) -> dict[str, float | int]:
    """Per-edge attribute dict (weight / timestamp / size)."""
    return {
        "weight": float(hg.weights[j]) if j < len(hg.weights) else 0.0,
        "event_ts": int(hg.timestamps[j]) if j < len(hg.timestamps) else 0,
        "size": int(hg.sizes[j]) if j < len(hg.sizes) else 0,
    }


# ── NetworkX ──────────────────────────────────────────────────────────────────

def to_networkx(
    source: Any,
    *,
    table: str | None = None,
    kind: str = "bipartite",
    node_prefix: str = "n",
    edge_prefix: str = "e",
) -> Any:
    """
    Project the hypergraph onto a NetworkX graph.

    Parameters
    ----------
    source :
        A :class:`HypergraphPy` or an embedded ``Connection``.
    table :
        Table name (only when *source* is a ``Connection``).
    kind :
        ``"bipartite"`` (default) — lossless *star* expansion: one node per
        hypergraph node and one node per hyperedge, with an incidence edge
        between a node and each hyperedge it belongs to. Hyperedge nodes carry
        ``weight`` / ``event_ts`` / ``size`` attributes and ``bipartite=1``;
        member nodes carry ``bipartite=0``.

        ``"clique"`` — weighted *clique* (two-section) expansion: every pair of
        co-members of a hyperedge is connected; parallel edges accumulate into a
        ``weight`` attribute and a ``multiplicity`` count. Lossy (hyperedge
        identity is dropped) but convenient for ordinary graph algorithms.

    Returns
    -------
    networkx.Graph
        Undirected graph. Node IDs are prefixed strings (``"n42"`` for member
        node ``42``, ``"e7"`` for hyperedge ``7``) so the two namespaces never
        collide in the bipartite projection.
    """
    nx = _require("networkx", "interop")
    hg = _as_hypergraph(source, table)
    members = _edge_members(hg)

    if kind in ("bipartite", "star"):
        g = nx.Graph()
        seen_nodes: set[int] = set()
        for j, mem in enumerate(members):
            enode = f"{edge_prefix}{j}"
            g.add_node(enode, bipartite=1, kind="hyperedge", **_edge_attrs(hg, j))
            for nid in mem:
                if nid not in seen_nodes:
                    g.add_node(f"{node_prefix}{nid}", bipartite=0, kind="node",
                               node_id=nid)
                    seen_nodes.add(nid)
                g.add_edge(f"{node_prefix}{nid}", enode)
        return g

    if kind in ("clique", "two-section", "twosection"):
        g = nx.Graph()
        for j, mem in enumerate(members):
            w = float(hg.weights[j]) if j < len(hg.weights) else 1.0
            for a_idx in range(len(mem)):
                na = f"{node_prefix}{mem[a_idx]}"
                if not g.has_node(na):
                    g.add_node(na, node_id=mem[a_idx])
                for b_idx in range(a_idx + 1, len(mem)):
                    nb = f"{node_prefix}{mem[b_idx]}"
                    if g.has_edge(na, nb):
                        g[na][nb]["weight"] += w
                        g[na][nb]["multiplicity"] += 1
                    else:
                        g.add_edge(na, nb, weight=w, multiplicity=1)
        return g

    raise ValueError(
        f"Unknown kind={kind!r}; expected 'bipartite' or 'clique'."
    )


# ── HyperNetX ───────────────────────────────────────────────────────────────--

def to_hypernetx(source: Any, *, table: str | None = None) -> Any:
    """
    Convert to a HyperNetX :class:`hypernetx.Hypergraph`.

    Hyperedge identifiers are strings ``"e{j}"``; the per-edge ``weight`` /
    ``event_ts`` / ``size`` are attached as edge properties when the installed
    HyperNetX version supports them (graceful fallback otherwise).
    """
    hnx = _require("hypernetx", "interop")
    hg = _as_hypergraph(source, table)
    members = _edge_members(hg)

    setsystem = {f"e{j}": [int(n) for n in mem] for j, mem in enumerate(members)}
    edge_props = {f"e{j}": _edge_attrs(hg, j) for j in range(len(members))}

    # Newer HyperNetX accepts edge_properties, but some HyperNetX/pandas version
    # combinations raise internally when properties are supplied. Fall back to a
    # plain construction (membership only) so the export always succeeds.
    try:
        return hnx.Hypergraph(setsystem, edge_properties=edge_props)
    except Exception:  # noqa: BLE001 - tolerate backend version drift
        return hnx.Hypergraph(setsystem)


# ── XGI ─────────────────────────────────────────────────────────────────────--

def to_xgi(source: Any, *, table: str | None = None) -> Any:
    """
    Convert to an XGI :class:`xgi.Hypergraph`.

    Each hyperedge is added with integer id ``j`` and ``weight`` / ``event_ts``
    / ``size`` edge attributes.
    """
    xgi = _require("xgi", "interop")
    hg = _as_hypergraph(source, table)
    members = _edge_members(hg)

    H = xgi.Hypergraph()
    for j, mem in enumerate(members):
        if not mem:
            continue
        H.add_edge(mem, id=j, **_edge_attrs(hg, j))
    return H


# ── pandas ────────────────────────────────────────────────────────────────────

def to_pandas(
    source: Any,
    *,
    table: str | None = None,
    orient: str = "edgelist",
) -> Any:
    """
    Convert to a pandas ``DataFrame``.

    Parameters
    ----------
    orient :
        ``"edgelist"`` (default) — one row per node↔hyperedge incidence with
        columns ``edge, node, weight, event_ts``. Compact and the natural input
        for most downstream tools.

        ``"incidence"`` — dense ``n_nodes × n_edges`` 0/1 incidence matrix
        (index = node IDs, columns = ``e{j}``). Convenient but materialises a
        full dense matrix; avoid on large/sparse hypergraphs.
    """
    pd = _require("pandas", "pandas")
    hg = _as_hypergraph(source, table)
    members = _edge_members(hg)

    if orient == "edgelist":
        records: list[dict[str, Any]] = []
        for j, mem in enumerate(members):
            attrs = _edge_attrs(hg, j)
            for nid in mem:
                records.append({
                    "edge": j,
                    "node": nid,
                    "weight": attrs["weight"],
                    "event_ts": attrs["event_ts"],
                })
        return pd.DataFrame.from_records(
            records, columns=["edge", "node", "weight", "event_ts"]
        )

    if orient == "incidence":
        dense = hg.B.toarray() if hg.n_edges else hg.B
        return pd.DataFrame(
            dense,
            index=[int(n) for n in hg.node_ids],
            columns=[f"e{j}" for j in range(hg.n_edges)],
        )

    raise ValueError(
        f"Unknown orient={orient!r}; expected 'edgelist' or 'incidence'."
    )


# ── HIF (Hypergraph Interchange Format) ─────────────────────────────────────--

def to_hif(source: Any, *, table: str | None = None) -> dict[str, Any]:
    """
    Serialise to a HIF (Hypergraph Interchange Format) document.

    HIF is the library-agnostic JSON standard for hypergraphs, readable by
    HyperNetX, XGI and others. The returned ``dict`` has the canonical keys:

    - ``network-type``: ``"undirected"``
    - ``incidences``: list of ``{"edge", "node"}`` membership records
    - ``edges``: list of ``{"edge", "attrs": {weight, event_ts, size}}``
    - ``nodes``: list of ``{"node"}`` (includes isolated nodes)
    - ``metadata``: provenance (``source`` = ``"hypermesh"``)
    """
    hg = _as_hypergraph(source, table)
    members = _edge_members(hg)

    incidences = [
        {"edge": j, "node": nid}
        for j, mem in enumerate(members)
        for nid in mem
    ]
    edges = [{"edge": j, "attrs": _edge_attrs(hg, j)} for j in range(len(members))]
    nodes = [{"node": int(n)} for n in hg.node_ids]

    return {
        "network-type": "undirected",
        "metadata": {"source": "hypermesh", "n_nodes": hg.n_nodes,
                     "n_edges": hg.n_edges},
        "incidences": incidences,
        "edges": edges,
        "nodes": nodes,
    }


def write_hif(source: Any, path: str, *, table: str | None = None,
              indent: int | None = None) -> str:
    """Write a HIF document to *path* (JSON). Returns the path written."""
    doc = to_hif(source, table=table)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=indent)
    return path


def from_hif(data: dict[str, Any] | str) -> HypergraphPy:
    """
    Rebuild a :class:`HypergraphPy` from a HIF document.

    *data* may be a parsed ``dict`` or a JSON string. Edge ``weight`` /
    ``event_ts`` attributes are restored when present; missing values default to
    ``0``. Node and edge identifiers are remapped to contiguous indices, with
    original node IDs preserved in ``node_ids``.
    """
    import numpy as np
    import scipy.sparse as sp

    from ._analytics import HypergraphPy

    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise TypeError("HIF data must be a dict or a JSON string.")

    incidences: Iterable[dict[str, Any]] = data.get("incidences", [])
    edge_attr_map: dict[Any, dict[str, Any]] = {
        rec["edge"]: dict(rec.get("attrs", {}))
        for rec in data.get("edges", [])
        if "edge" in rec
    }

    # Contiguous remapping for both edges and nodes.
    edge_index: dict[Any, int] = {}
    node_index: dict[int, int] = {}
    coo_rows: list[int] = []
    coo_cols: list[int] = []

    # Seed node ordering with any declared nodes (preserves isolated nodes).
    for rec in data.get("nodes", []):
        nid = int(rec["node"])
        if nid not in node_index:
            node_index[nid] = len(node_index)

    for rec in incidences:
        eid = rec["edge"]
        nid = int(rec["node"])
        if eid not in edge_index:
            edge_index[eid] = len(edge_index)
        if nid not in node_index:
            node_index[nid] = len(node_index)
        coo_rows.append(node_index[nid])
        coo_cols.append(edge_index[eid])

    n_nodes = len(node_index)
    n_edges = len(edge_index)

    if n_edges == 0:
        B = sp.csr_matrix((n_nodes, 0), dtype=np.uint8)
    else:
        data_arr = np.ones(len(coo_rows), dtype=np.uint8)
        B = sp.coo_matrix(
            (data_arr, (coo_rows, coo_cols)), shape=(n_nodes, n_edges)
        ).tocsr()
        B.data[:] = 1  # collapse any duplicate incidences to binary

    node_ids = np.zeros(n_nodes, dtype=np.int64)
    for nid, idx in node_index.items():
        node_ids[idx] = nid

    weights = np.zeros(n_edges, dtype=np.float64)
    timestamps = np.zeros(n_edges, dtype=np.int64)
    sizes = np.zeros(n_edges, dtype=np.int32)
    for eid, idx in edge_index.items():
        attrs = edge_attr_map.get(eid, {})
        weights[idx] = float(attrs.get("weight", 0.0))
        timestamps[idx] = int(attrs.get("event_ts", 0))
        sizes[idx] = int(attrs.get("size", 0))
    # Fill sizes from incidences where not provided.
    if n_edges:
        col_counts = np.asarray(B.sum(axis=0)).ravel().astype(np.int32)
        sizes = np.where(sizes > 0, sizes, col_counts)

    return HypergraphPy(
        B=B,
        node_ids=node_ids,
        weights=weights,
        timestamps=timestamps,
        sizes=sizes,
    )


def read_hif(path: str) -> HypergraphPy:
    """Read a HIF JSON file from *path* and rebuild a :class:`HypergraphPy`."""
    with open(path, encoding="utf-8") as fh:
        return from_hif(json.load(fh))


# ── GraphML ───────────────────────────────────────────────────────────────────

def to_graphml(
    source: Any,
    path: str,
    *,
    table: str | None = None,
    kind: str = "bipartite",
) -> str:
    """
    Write the hypergraph to a GraphML file via its NetworkX projection.

    *kind* is forwarded to :func:`to_networkx` (``"bipartite"`` or ``"clique"``).
    Returns the path written.
    """
    nx = _require("networkx", "interop")
    g = to_networkx(source, table=table, kind=kind)
    nx.write_graphml(g, path)
    return path


# Backwards-friendly alias.
write_graphml = to_graphml
