"""
_data.py — feature loading for the HyperMesh modeling layer.

Defines :class:`FeaturedHypergraph` (a :class:`HypergraphPy` plus aligned node
features ``X``, edge features ``E`` and an optional label vector ``y``) and the
:func:`featurize` / :func:`prepare` entry points.

Design
------
* ``featurize`` performs the expensive work once: it materialises the incidence
  matrix, joins node-table attributes onto the node axis, and stacks per-edge
  metadata onto the edge axis.  Everything downstream (the tensor bridges) is a
  cheap, dependency-light transform of the resulting :class:`FeaturedHypergraph`.
* ``prepare`` is the single high-level function most users call: it wraps
  ``featurize`` and a tensor bridge so that going from a live database to
  framework-native tensors is one call.

Node features come from a node table (see ``CREATE NODE TABLE``).  Numeric
columns are passed through; ``TEXT`` columns are one-hot (default) or ordinal
encoded.  Nodes present in the hypergraph but missing from the node table are
imputed (zeros for numeric / one-hot, ``-1`` for ordinal).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .._analytics import HypergraphPy

# Column SQL types treated as numeric (everything else is categorical/TEXT).
_NUMERIC_TYPES = frozenset(
    {"INTEGER", "INT", "BIGINT", "SMALLINT", "REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL"}
)

# Per-edge metadata available directly from a HypergraphPy snapshot.
_EDGE_FIELDS = ("weight", "size", "event_ts")


# ── Container ──────────────────────────────────────────────────────────────────

@dataclass
class FeaturedHypergraph:
    """
    A :class:`~hypermeshdb._analytics.HypergraphPy` snapshot enriched with
    model-ready feature tensors, all aligned to the incidence matrix axes.

    Attributes
    ----------
    hg :
        The underlying sparse hypergraph (incidence ``B``, ``node_ids``,
        ``weights``, ``timestamps``, ``sizes``).
    X :
        Node feature matrix, shape ``(n_nodes, f_node)`` row-aligned to
        ``hg.node_ids``.  ``None`` if no features were requested/available.
    E :
        Edge feature matrix, shape ``(n_edges, f_edge)`` row-aligned to the
        hyperedge columns of ``hg.B``.  ``None`` if no edge features.
    y :
        Optional label vector, shape ``(n_nodes,)``, aligned to ``hg.node_ids``.
    node_feature_names / edge_feature_names :
        Human-readable column names for ``X`` / ``E``.
    classes_ :
        For a categorical label, the ordered list of class names such that
        ``y[i] == classes_.index(original_label_i)``.  ``None`` for numeric
        labels or when no label was requested.
    """

    hg: HypergraphPy
    X: np.ndarray | None = None
    E: np.ndarray | None = None
    y: np.ndarray | None = None
    node_feature_names: list[str] = field(default_factory=list)
    edge_feature_names: list[str] = field(default_factory=list)
    classes_: list[Any] | None = None

    @property
    def n_nodes(self) -> int:
        return self.hg.n_nodes

    @property
    def n_edges(self) -> int:
        return self.hg.n_edges

    @property
    def node_ids(self) -> np.ndarray:
        return self.hg.node_ids

    @property
    def B(self) -> Any:  # noqa: N802 - mirror HypergraphPy.B
        return self.hg.B

    def to(self, framework: str) -> Any:
        """
        Convert to a framework-native container.

        ``framework`` is one of ``"torch"``, ``"pyg"`` (``"torch_geometric"``),
        ``"dgl"`` or ``"dhg"``.  See :mod:`hypermesh.nn` for the exact shapes.
        """
        from . import _bridges

        key = framework.strip().lower()
        dispatch = {
            "torch": _bridges.to_torch,
            "pytorch": _bridges.to_torch,
            "pyg": _bridges.to_pyg,
            "torch_geometric": _bridges.to_pyg,
            "dgl": _bridges.to_dgl,
            "dhg": _bridges.to_dhg,
        }
        if key not in dispatch:
            raise ValueError(
                f"Unknown framework {framework!r}. "
                f"Expected one of: torch, pyg, dgl, dhg."
            )
        return dispatch[key](self)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        nf = self.X.shape[1] if self.X is not None else 0
        ef = self.E.shape[1] if self.E is not None else 0
        return (
            f"FeaturedHypergraph(n_nodes={self.n_nodes}, n_edges={self.n_edges}, "
            f"node_features={nf}, edge_features={ef}, "
            f"labelled={self.y is not None})"
        )


# ── Source / store coercion ────────────────────────────────────────────────────

def _as_hypergraph(source: Any, table: str | None) -> HypergraphPy:
    """Coerce *source* into a HypergraphPy (snapshot or live Connection)."""
    from .._analytics import HypergraphPy

    if isinstance(source, HypergraphPy):
        if table is not None:
            raise TypeError(
                "table= is only valid when passing a Connection; a HypergraphPy "
                "is already materialised for one table."
            )
        return source

    to_hg = getattr(source, "to_hypergraph", None)
    if callable(to_hg):
        return to_hg(table)

    raise TypeError(
        "Expected a HypergraphPy or an object with a to_hypergraph() method "
        f"(e.g. an embedded Connection); got {type(source).__name__}. "
        "Remote clients cannot build a hypergraph locally — fetch rows with "
        "execute(...) and pass them to hypermesh.build_hypergraph()."
    )


def _resolve_node_store(source: Any, db: Any, node_table: str) -> Any:
    """Locate the NodeStore for *node_table* on whichever object can provide it."""
    for candidate in (db, source):
        getter = getattr(candidate, "_get_node_store", None)
        if callable(getter):
            return getter(node_table)
    raise TypeError(
        f"node_table={node_table!r} requires an embedded Connection to read node "
        "attributes. Pass the Connection as the source, or via db=<Connection>."
    )


# ── Encoding helpers ────────────────────────────────────────────────────────────

def _is_numeric(col_type: str | None) -> bool:
    return (col_type or "").upper() in _NUMERIC_TYPES


def _encode_numeric(values: list[Any], impute: float) -> np.ndarray:
    out = np.empty(len(values), dtype=np.float64)
    for i, v in enumerate(values):
        try:
            out[i] = float(v) if v is not None and v != "" else impute
        except (TypeError, ValueError):
            out[i] = impute
    return out


def _encode_categorical(
    values: list[Any], how: str
) -> tuple[np.ndarray, list[str]]:
    """Return (encoded matrix-or-vector, feature-name suffixes)."""
    vocab = sorted({str(v) for v in values if v is not None and v != ""})
    index = {v: i for i, v in enumerate(vocab)}
    if how == "ordinal":
        col = np.full(len(values), -1.0, dtype=np.float64)
        for i, v in enumerate(values):
            if v is not None and v != "":
                col[i] = index[str(v)]
        return col.reshape(-1, 1), [""]  # single column, no value suffix
    # one-hot (default): missing → all-zero row
    mat = np.zeros((len(values), len(vocab)), dtype=np.float64)
    for i, v in enumerate(values):
        if v is not None and v != "":
            mat[i, index[str(v)]] = 1.0
    return mat, [f"={v}" for v in vocab]


# ── Public: featurize ───────────────────────────────────────────────────────────

def featurize(
    source: Any,
    table: str | None = None,
    *,
    node_table: str | None = None,
    node_features: list[str] | None = None,
    edge_features: tuple[str, ...] | list[str] | None = ("weight", "size", "event_ts"),
    label: str | None = None,
    categorical: str = "onehot",
    impute: float = 0.0,
    x: np.ndarray | None = None,
    db: Any = None,
) -> FeaturedHypergraph:
    """
    Build a :class:`FeaturedHypergraph` from a stored hypergraph.

    Parameters
    ----------
    source :
        An embedded :class:`~hypermeshdb.Connection`, or an already-materialised
        :class:`~hypermeshdb._analytics.HypergraphPy`.
    table :
        Hyperedge table to materialise (only valid with a Connection; defaults
        to the primary table).
    node_table :
        Node table whose attributes are joined onto the node axis to form ``X``.
        Requires a Connection (as ``source`` or ``db=``).  If omitted and ``x``
        is not given, ``X`` falls back to structural features
        (``degree``, ``weighted_degree``).
    node_features :
        Explicit list of node-table columns to use.  Defaults to every non-PK
        column (excluding the label column).
    edge_features :
        Subset of ``("weight", "size", "event_ts")`` to stack into ``E``.  Pass
        ``None`` or ``()`` for no edge features.
    label :
        Node-table column to use as the label vector ``y``.  TEXT columns are
        label-encoded (see ``classes_``).
    categorical :
        ``"onehot"`` (default) or ``"ordinal"`` encoding for TEXT features.
    impute :
        Fill value for missing numeric features / nodes absent from the table.
    x :
        Explicit node feature matrix, shape ``(n_nodes, f)`` aligned to
        ``hg.node_ids``.  Overrides the node-table join.
    db :
        Connection used to read node attributes when ``source`` is a
        HypergraphPy.

    Returns
    -------
    FeaturedHypergraph
    """
    if categorical not in ("onehot", "ordinal"):
        raise ValueError(f"categorical must be 'onehot' or 'ordinal', got {categorical!r}")

    hg = _as_hypergraph(source, table)
    n_nodes = hg.n_nodes
    node_ids = [int(v) for v in hg.node_ids]

    X: np.ndarray | None = None
    names: list[str] = []
    y: np.ndarray | None = None
    classes_: list[Any] | None = None

    # ── Node features ────────────────────────────────────────────────────────
    if x is not None:
        X = np.asarray(x, dtype=np.float64)
        if X.ndim != 2 or X.shape[0] != n_nodes:
            raise ValueError(
                f"x must have shape (n_nodes, f) = ({n_nodes}, f); got {X.shape}."
            )
        names = [f"x{i}" for i in range(X.shape[1])]
    elif node_table is not None:
        store = _resolve_node_store(source, db, node_table)
        col_defs = {c.name: c for c in store._columns}

        if node_features is None:
            cols = [c.name for c in store._columns if not c.is_pk and c.name != label]
        else:
            cols = list(node_features)

        # One node-table lookup per node, reused for features + label.
        records: list[dict[str, Any]] = []
        for nid in node_ids:
            rec = store.get_by_id(nid)
            records.append(rec["properties"] if rec else {})

        blocks: list[np.ndarray] = []
        for col in cols:
            cdef = col_defs.get(col)
            vals = [r.get(col) for r in records]
            if cdef is not None and _is_numeric(cdef.col_type):
                blocks.append(_encode_numeric(vals, impute).reshape(-1, 1))
                names.append(col)
            else:
                enc, suffixes = _encode_categorical(vals, categorical)
                blocks.append(enc)
                names.extend(col + s for s in suffixes)
        X = np.hstack(blocks) if blocks else np.zeros((n_nodes, 0), dtype=np.float64)

        if label is not None:
            cdef = col_defs.get(label)
            vals = [r.get(label) for r in records]
            if cdef is not None and _is_numeric(cdef.col_type):
                y = _encode_numeric(vals, impute)
            else:
                classes_ = sorted({str(v) for v in vals if v is not None and v != ""})
                lut = {c: i for i, c in enumerate(classes_)}
                y = np.array(
                    [lut.get(str(v), -1) if v is not None and v != "" else -1 for v in vals],
                    dtype=np.int64,
                )
    else:
        # Structural fallback — always available, never raises.
        degree = np.asarray(hg.B.sum(axis=1)).ravel().astype(np.float64)
        wdeg = np.asarray(hg.B @ hg.weights).ravel().astype(np.float64)
        X = np.column_stack([degree, wdeg])
        names = ["degree", "weighted_degree"]
        if label is not None:
            raise ValueError(
                "label= requires node_table= so the label column can be read."
            )

    # ── Edge features ────────────────────────────────────────────────────────
    E: np.ndarray | None = None
    edge_names: list[str] = []
    selected = [f for f in (edge_features or ()) if f in _EDGE_FIELDS]
    if selected:
        available = {
            "weight": np.asarray(hg.weights, dtype=np.float64),
            "size": np.asarray(hg.sizes, dtype=np.float64),
            "event_ts": np.asarray(hg.timestamps, dtype=np.float64),
        }
        E = np.column_stack([available[f] for f in selected])
        edge_names = list(selected)

    return FeaturedHypergraph(
        hg=hg,
        X=X,
        E=E,
        y=y,
        node_feature_names=names,
        edge_feature_names=edge_names,
        classes_=classes_,
    )


# ── Public: the single front-door function ──────────────────────────────────────

def prepare(
    source: Any,
    table: str | None = None,
    *,
    framework: str = "torch",
    node_table: str | None = None,
    node_features: list[str] | None = None,
    edge_features: tuple[str, ...] | list[str] | None = ("weight", "size", "event_ts"),
    label: str | None = None,
    categorical: str = "onehot",
    impute: float = 0.0,
    x: np.ndarray | None = None,
    db: Any = None,
) -> Any:
    """
    Go from a live database to framework-native, model-ready tensors in one call.

    Equivalent to ``featurize(...).to(framework)``.

    Parameters
    ----------
    framework :
        Target framework: ``"torch"`` (default), ``"pyg"``, ``"dgl"`` or
        ``"dhg"``.  All other parameters are forwarded to :func:`featurize`.

    Returns
    -------
    Any
        A ``dict`` of tensors for ``"torch"``/``"dhg"``, a
        ``torch_geometric.data.Data`` for ``"pyg"``, or a ``dgl`` heterograph
        for ``"dgl"``.

    Example
    -------
    >>> data = hm.nn.prepare(db, "CoProximity", framework="pyg",
    ...                      node_table="Patient", label="label")
    """
    fhg = featurize(
        source,
        table,
        node_table=node_table,
        node_features=node_features,
        edge_features=edge_features,
        label=label,
        categorical=categorical,
        impute=impute,
        x=x,
        db=db,
    )
    return fhg.to(framework)
