"""
_analytics.py — Phase 10 Hypergraph Analytics Engine for HyperMesh DB.

Computes a comprehensive suite of hypergraph measures over any table stored
in HyperMesh DB.  All computation uses NumPy / SciPy sparse matrices; no
changes to the C engine are required.

Architecture
------------
1. ``Connection.to_hypergraph(table)`` → :class:`HypergraphPy`
   Performs a full table scan and builds a sparse (n_nodes × n_edges)
   binary incidence matrix ``B``, plus per-edge metadata arrays.

2. ``Connection.analytics(table)`` → :class:`Analytics`
   Returns an ``Analytics`` object; every measure is a lazy method call.

3. ``Analytics.<measure>(...)`` → ``float | dict[int, float | int] | ...``
   Pure functions of ``(B, weights, timestamps, sizes)``.

Measures (27 total)
-------------------
Structural  : node_degree, hyperedge_size, density, redundancy,
              intersection_profile
Centrality  : weighted_degree, eigenvector_centrality, pagerank,
              katz_centrality, hedc
Spectral    : zhou_laplacian_eigenvalues, spectral_gap, cheeger_constant
Clustering  : zhou_clustering, pairwise_clustering, global_transitivity
Modularity  : hypermodularity
s-Walk      : s_adjacency, s_distance, s_closeness, s_betweenness,
              s_diameter, s_efficiency
Temporal    : hyperedge_persistence, burstiness, temporal_degree_entropy,
              activity_windows, temporal_changepoints

Dependencies
------------
  numpy, scipy  — required (``pip install scipy``)
  networkx      — optional, not used directly (available for callers)
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla


# ─────────────────────────────────────────────────────────────────────────────
# HypergraphPy: the canonical in-memory hypergraph representation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HypergraphPy:
    """
    Sparse in-memory representation of a hypergraph built from one
    HyperMesh DB table.

    Attributes
    ----------
    B : scipy.sparse.csr_matrix, shape (n_nodes, n_edges)
        Binary incidence matrix. ``B[i, j] = 1`` iff node ``node_ids[i]``
        belongs to hyperedge ``j``.
    node_ids : numpy.ndarray, shape (n_nodes,), dtype int64
        Original node IDs in the same row-order as ``B``.
    weights : numpy.ndarray, shape (n_edges,), dtype float64
        Per-edge weight (``weight`` column from the TPI store).
    timestamps : numpy.ndarray, shape (n_edges,), dtype int64
        Per-edge ``event_ts``.
    sizes : numpy.ndarray, shape (n_edges,), dtype int32
        Cardinality of each hyperedge (number of member nodes).
    """

    B:          sp.csr_matrix
    node_ids:   np.ndarray   # int64, shape (n_nodes,)
    weights:    np.ndarray   # float64, shape (n_edges,)
    timestamps: np.ndarray   # int64, shape (n_edges,)
    sizes:      np.ndarray   # int32, shape (n_edges,)

    @property
    def n_nodes(self) -> int:
        return self.B.shape[0]

    @property
    def n_edges(self) -> int:
        return self.B.shape[1]

    def __repr__(self) -> str:
        return (
            f"HypergraphPy(n_nodes={self.n_nodes}, n_edges={self.n_edges}, "
            f"density={self.B.nnz / max(1, self.n_nodes * self.n_edges):.4f})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Builder: raw scan rows → HypergraphPy
# ─────────────────────────────────────────────────────────────────────────────

def build_hypergraph(rows: list[Any]) -> HypergraphPy:
    """
    Build a :class:`HypergraphPy` from a list of :class:`~hypermeshdb.Row`
    objects returned by a full-table scan.

    Each row must expose ``row["event_ts"]``, ``row["members"]``,
    ``row["weight"]`` via bracket access.
    """
    if not rows:
        B = sp.csr_matrix((0, 0), dtype=np.uint8)
        return HypergraphPy(
            B=B,
            node_ids=np.array([], dtype=np.int64),
            weights=np.array([], dtype=np.float64),
            timestamps=np.array([], dtype=np.int64),
            sizes=np.array([], dtype=np.int32),
        )

    node_set: dict[int, int] = {}   # node_id → contiguous index
    weights:    list[float] = []
    timestamps: list[int]   = []
    sizes:      list[int]   = []
    coo_rows:   list[int]   = []    # node index
    coo_cols:   list[int]   = []    # edge index

    for ej, row in enumerate(rows):
        members_raw = row["members"]
        if isinstance(members_raw, str):
            try:
                members: list[int] = json.loads(members_raw)
            except Exception:
                members = [int(x.strip()) for x in members_raw.strip("[]").split(",") if x.strip()]
        elif isinstance(members_raw, (list, tuple)):
            members = [int(x) for x in members_raw]
        else:
            members = []

        for nid in members:
            if nid not in node_set:
                node_set[nid] = len(node_set)
            ni = node_set[nid]
            coo_rows.append(ni)
            coo_cols.append(ej)

        try:
            w = float(row["weight"])
        except (KeyError, TypeError):
            w = 1.0

        try:
            ts = int(row["event_ts"])
        except (KeyError, TypeError):
            ts = 0

        weights.append(w)
        timestamps.append(ts)
        sizes.append(len(members))

    n_nodes = len(node_set)
    n_edges = len(rows)

    data = np.ones(len(coo_rows), dtype=np.uint8)
    B = sp.csr_matrix(
        (data, (coo_rows, coo_cols)),
        shape=(n_nodes, n_edges),
        dtype=np.float64,
    )

    # Build node_ids array ordered by contiguous index
    ordered_nids = sorted(node_set, key=lambda x: node_set[x])
    node_ids_arr = np.array(ordered_nids, dtype=np.int64)

    return HypergraphPy(
        B=B,
        node_ids=node_ids_arr,
        weights=np.array(weights, dtype=np.float64),
        timestamps=np.array(timestamps, dtype=np.int64),
        sizes=np.array(sizes, dtype=np.int32),
    )


# ─────────────────────────────────────────────────────────────────────────────
# BFS helpers (used by s-walk measures)
# ─────────────────────────────────────────────────────────────────────────────

def _bfs_distances(src: int, adj: dict[int, set[int]]) -> dict[int, int]:
    """BFS from *src*; returns {node_index: distance} for all reachable nodes."""
    dist: dict[int, int] = {src: 0}
    queue: deque[int] = deque([src])
    while queue:
        u = queue.popleft()
        for v in adj.get(u, set()):
            if v not in dist:
                dist[v] = dist[u] + 1
                queue.append(v)
    return dist


def _brandes_bfs(
    src: int,
    n: int,
    adj: dict[int, set[int]],
) -> tuple[list[int], list[list[int]], np.ndarray, np.ndarray]:
    """
    Brandes BFS for betweenness centrality.

    Returns (stack, pred, sigma, dist) where:
      stack  — nodes in non-increasing BFS order
      pred   — predecessor lists
      sigma  — number of shortest paths from src
      dist   — shortest-path distances (-1 = unreachable)
    """
    stack: list[int] = []
    pred: list[list[int]] = [[] for _ in range(n)]
    sigma = np.zeros(n, dtype=float)
    sigma[src] = 1.0
    dist = np.full(n, -1, dtype=int)
    dist[src] = 0
    queue: deque[int] = deque([src])
    while queue:
        v = queue.popleft()
        stack.append(v)
        for w in adj.get(v, set()):
            if dist[w] < 0:
                queue.append(w)
                dist[w] = dist[v] + 1
            if dist[w] == dist[v] + 1:
                sigma[w] += sigma[v]
                pred[w].append(v)
    return stack, pred, sigma, dist


# ─────────────────────────────────────────────────────────────────────────────
# Analytics: all 25 measures
# ─────────────────────────────────────────────────────────────────────────────

class Analytics:
    """
    Hypergraph analytics engine for a single HyperMesh DB table.

    Obtain via ``db.analytics("TableName")`` or directly::

        hg = db.to_hypergraph("Events")
        an = Analytics(hg)
        print(an.density())
        print(an.spectral_gap())
        print(an.pagerank())

    All result dicts are keyed by the **original node IDs** as stored in
    the HyperMesh DB (not internal contiguous indices).

    Notes
    -----
    Heavy measures (eigenvector_centrality, pagerank, s_betweenness,
    s_distance on large graphs) may be slow on tables with thousands of
    nodes.  Use on representative sub-graphs or time-windows when needed.
    """

    def __init__(self, hg: HypergraphPy) -> None:
        self._hg = hg

    @property
    def hypergraph(self) -> HypergraphPy:
        """The underlying :class:`HypergraphPy`."""
        return self._hg

    # ── Internal helpers ──────────────────────────────────────────────────

    def _node_index(self, node_id: int) -> int:
        indices = np.where(self._hg.node_ids == node_id)[0]
        if len(indices) == 0:
            raise KeyError(f"node_id {node_id!r} not found in hypergraph")
        return int(indices[0])

    # ── 1. Structural measures ─────────────────────────────────────────────

    def node_degree(self, node_id: int | None = None) -> "dict[int, int] | int":
        """
        Number of hyperedges containing each node.

        Parameters
        ----------
        node_id :
            When given, return a single integer for that node.
            When *None* (default), return ``{node_id: degree}``.
        """
        hg = self._hg
        deg = np.asarray(hg.B.sum(axis=1)).ravel().astype(int)
        if node_id is not None:
            return int(deg[self._node_index(node_id)])
        return {int(nid): int(d) for nid, d in zip(hg.node_ids, deg)}

    def hyperedge_size(self, he_idx: int | None = None) -> "dict[int, int] | int":
        """
        Cardinality (number of member nodes) of each hyperedge.

        Parameters
        ----------
        he_idx :
            Zero-based edge index.  When *None* return ``{he_idx: size}``.
        """
        hg = self._hg
        if he_idx is not None:
            if he_idx < 0 or he_idx >= hg.n_edges:
                raise IndexError(f"he_idx {he_idx} out of range [0, {hg.n_edges})")
            return int(hg.sizes[he_idx])
        return {int(i): int(s) for i, s in enumerate(hg.sizes)}

    def density(self) -> float:
        """
        Incidence-matrix fill density: ``nnz / (n_nodes × n_edges)``.
        Returns 0.0 for an empty graph.
        """
        hg = self._hg
        if hg.n_nodes == 0 or hg.n_edges == 0:
            return 0.0
        return float(hg.B.nnz) / (hg.n_nodes * hg.n_edges)

    def redundancy(self) -> float:
        """
        Mean node co-membership, normalised by ``C(n_nodes, 2)``.

        Two nodes are "co-members" in every hyperedge they both appear in.
        High redundancy indicates a highly overlapping hypergraph structure.
        """
        hg = self._hg
        if hg.n_nodes < 2:
            return 0.0
        # B @ B.T: (n_nodes, n_nodes), entry [i,j] = number of edges containing both i and j
        NNco = (hg.B @ hg.B.T).tocsr()
        upper = sp.triu(NNco, k=1)
        total_pairs = hg.n_nodes * (hg.n_nodes - 1) / 2.0
        return float(upper.sum()) / total_pairs

    def intersection_profile(self) -> "dict[tuple[int, int], int]":
        """
        For each pair of hyperedges ``(i < j)``, the number of shared nodes.

        Returns a sparse dict ``{(i, j): count}`` (only pairs with count > 0).
        """
        hg = self._hg
        # B.T @ B has shape (n_edges, n_edges): entry (i,j) = |members_i ∩ members_j|
        EE = (hg.B.T @ hg.B).tocoo()
        result: dict[tuple[int, int], int] = {}
        for r, c, v in zip(EE.row, EE.col, EE.data):
            if r < c and v > 0:
                result[(int(r), int(c))] = int(v)
        return result

    # ── 2. Centrality measures ─────────────────────────────────────────────

    def weighted_degree(
        self, node_id: int | None = None
    ) -> "dict[int, float] | float":
        """
        Weighted degree: sum of weights of all hyperedges containing each node.
        """
        hg = self._hg
        wd = hg.B @ hg.weights
        if node_id is not None:
            return float(wd[self._node_index(node_id)])
        return {int(nid): float(w) for nid, w in zip(hg.node_ids, wd)}

    def eigenvector_centrality(
        self, tol: float = 1e-6, max_iter: int = 200
    ) -> "dict[int, float]":
        """
        Hypergraph eigenvector centrality (power iteration).

        Uses operator ``A = B @ diag(w/|e|) @ Bᵀ`` so that each edge
        contributes proportionally to its weight per member.  The dominant
        eigenvector of *A* gives the centrality scores.
        Scores are normalised to ``[0, 1]``.
        """
        hg = self._hg
        if hg.n_nodes == 0:
            return {}
        w_per_size = hg.weights / np.maximum(hg.sizes.astype(float), 1.0)
        W = sp.diags(w_per_size, format="csr")
        A = hg.B @ W @ hg.B.T   # symmetric (n_nodes, n_nodes)

        x = np.ones(hg.n_nodes, dtype=float)
        for _ in range(max_iter):
            x_new = A @ x
            norm = np.linalg.norm(x_new)
            if norm < 1e-15:
                break
            x_new /= norm
            if np.linalg.norm(x_new - x) < tol:
                x = x_new
                break
            x = x_new

        mx = x.max()
        if mx > 0:
            x /= mx
        return {int(nid): float(v) for nid, v in zip(hg.node_ids, x)}

    def pagerank(
        self, alpha: float = 0.85, tol: float = 1e-6, max_iter: int = 200
    ) -> "dict[int, float]":
        """
        Hypergraph PageRank via random-walk transition matrix.

        Transition: ``P = D_wd⁻¹ @ B @ D_e⁻¹ @ W_e @ Bᵀ`` where
        ``D_wd`` = weighted-degree diagonal (``B @ weights``), ``D_e`` = edge-size
        diagonal, ``W_e`` = edge-weight diagonal.  Using ``D_wd`` instead of the
        raw node degree ensures ``P`` is a proper row-stochastic matrix regardless
        of the edge weight distribution.

        PageRank vector: ``π = α·Pᵀπ + (1−α)/n``.
        """
        hg = self._hg
        n = hg.n_nodes
        if n == 0:
            return {}

        wd = hg.B @ hg.weights        # weighted degree per node
        wd_inv = np.where(wd > 0, 1.0 / wd, 0.0)
        D_wd_inv = sp.diags(wd_inv, format="csr")

        size_inv = np.where(hg.sizes > 0, 1.0 / hg.sizes.astype(float), 0.0)
        W_e = sp.diags(hg.weights * size_inv, format="csr")
        P = D_wd_inv @ hg.B @ W_e @ hg.B.T   # (n, n) row-stochastic

        pi = np.full(n, 1.0 / n)
        for _ in range(max_iter):
            pi_new = alpha * (P.T @ pi) + (1.0 - alpha) / n
            if np.linalg.norm(pi_new - pi, 1) < tol:
                pi = pi_new
                break
            pi = pi_new

        s = pi.sum()
        if s > 0:
            pi /= s
        return {int(nid): float(v) for nid, v in zip(hg.node_ids, pi)}

    def katz_centrality(self, alpha: float = 0.1) -> "dict[int, float]":
        """
        Katz centrality via sparse linear solve.

        Uses the clique-expansion adjacency ``A = B @ Bᵀ`` (self-loops
        removed) and solves ``(I − α·A) x = 1``.
        Falls back to power iteration if the system is ill-conditioned.
        Scores are L2-normalised.
        """
        hg = self._hg
        n = hg.n_nodes
        if n == 0:
            return {}

        A = (hg.B @ hg.B.T).tocsr()
        diag_vals = np.asarray(A.diagonal()).ravel()
        A = A - sp.diags(diag_vals, format="csr")   # remove self-loops

        I = sp.eye(n, format="csr")
        ones = np.ones(n)
        try:
            x = spla.spsolve(I - alpha * A, ones)
            if not np.all(np.isfinite(x)):
                raise ValueError("non-finite")
        except Exception:
            x = ones.copy()
            for _ in range(200):
                x = ones + alpha * (A @ x)

        norm = np.linalg.norm(x)
        if norm > 0:
            x /= norm
        return {int(nid): float(v) for nid, v in zip(hg.node_ids, x)}

    def hedc(self) -> "dict[int, float]":
        """
        Hyperedge Degree Centrality (HEDC).

        ``hedc(e) = |e| / n_nodes``.  Larger values mark edges that span a
        greater fraction of the node population.
        """
        hg = self._hg
        if hg.n_nodes == 0:
            return {}
        norm = hg.sizes.astype(float) / hg.n_nodes
        return {int(i): float(v) for i, v in enumerate(norm)}

    # ── 3. Spectral measures ───────────────────────────────────────────────

    def _zhou_laplacian(self) -> sp.csr_matrix:
        """
        Zhou (2006) normalised hypergraph Laplacian:
        ``Δ = I − D_v^{-½} B W D_e^{-1} Bᵀ D_v^{-½}``

        where ``D_v[v] = Σ_{e∋v} w_e`` (weighted degree), ``W = diag(weights)``,
        ``D_e = diag(sizes)``.

        This formulation is positive semidefinite (all eigenvalues ≥ 0) and
        equals the standard Zhou Laplacian from the 2006 paper.
        """
        hg = self._hg
        # Weighted degree: D_v[v] = Σ_{e∋v} w_e
        wd = hg.B @ hg.weights
        wd_inv_sqrt = np.where(wd > 0, 1.0 / np.sqrt(wd), 0.0)
        D_v_inv_sqrt = sp.diags(wd_inv_sqrt, format="csr")

        size_inv = np.where(hg.sizes > 0, 1.0 / hg.sizes.astype(float), 0.0)
        W_e = sp.diags(hg.weights * size_inv, format="csr")

        Theta = D_v_inv_sqrt @ hg.B @ W_e @ hg.B.T @ D_v_inv_sqrt
        return (sp.eye(hg.n_nodes, format="csr") - Theta).tocsr()

    def zhou_laplacian_eigenvalues(self, k: int = 6) -> "list[float]":
        """
        The *k* smallest eigenvalues of the Zhou normalised Laplacian.

        The smallest eigenvalue is always ≥ 0; it equals 0 for each
        connected component.

        Parameters
        ----------
        k : int
            Number of eigenvalues to return (default 6).
        """
        hg = self._hg
        n = hg.n_nodes
        if n <= 1:
            return [0.0] * min(k, max(n, 1))
        L = self._zhou_laplacian()
        k = min(k, n - 1)
        try:
            vals, _ = spla.eigsh(L, k=k, which="SM", tol=1e-6)
            vals = np.sort(np.real(vals))
        except Exception:
            vals = np.array([0.0])
        return [float(v) for v in vals]

    def spectral_gap(self) -> float:
        """
        Spectral gap ``λ₂ − λ₁`` of the Zhou Laplacian.

        Larger gap → stronger connectivity / faster mixing time.
        Returns 0.0 for degenerate (n ≤ 1) graphs.
        """
        vals = self.zhou_laplacian_eigenvalues(k=min(6, max(2, self._hg.n_nodes - 1)))
        if len(vals) < 2:
            return 0.0
        return float(vals[1] - vals[0])

    def cheeger_constant(self) -> float:
        """
        Cheeger constant estimate via the Cheeger inequality lower bound:
        ``h(G) ≥ λ₂ / 2``.

        Returns ``spectral_gap() / 2``.
        """
        return self.spectral_gap() / 2.0

    # ── 4. Clustering measures ─────────────────────────────────────────────

    def zhou_clustering(
        self, node_id: int | None = None
    ) -> "dict[int, float] | float":
        """
        Zhou (2006) hypergraph clustering coefficient.

        For node *v* (requires degree ≥ 2):

        .. code-block:: text

            c_v = Σ_{e ∋ v} |e|(|e|−1)  /  (d_v · (Σ_{e ∋ v} |e| − d_v))

        Returns 0.0 for isolated nodes or nodes with degree < 2 (the formula
        is undefined for degree 0 or 1).
        """
        hg = self._hg
        deg = np.asarray(hg.B.sum(axis=1)).ravel()
        size_sq = (hg.sizes * (hg.sizes - 1)).astype(float)
        numerator = hg.B @ size_sq
        sum_sizes = hg.B @ hg.sizes.astype(float)
        denom = deg * (sum_sizes - deg)
        # Require degree ≥ 2; otherwise clustering is undefined → return 0.0
        cc = np.where((denom > 0) & (deg >= 2), numerator / denom, 0.0)
        if node_id is not None:
            return float(cc[self._node_index(node_id)])
        return {int(nid): float(v) for nid, v in zip(hg.node_ids, cc)}

    def pairwise_clustering(
        self, node_id: int | None = None
    ) -> "dict[int, float] | float":
        """
        Pairwise clustering coefficient (Latapy et al. 2008 / Opsahl 2013).

        For node *v*:

        .. code-block:: text

            c_v = |closed pairs among neighbours of v|  /  d_v(d_v − 1)

        where "closed" means the pair also shares at least one hyperedge.
        Computed via the node co-occurrence matrix ``B @ Bᵀ``.
        """
        hg = self._hg
        # NNco[i,j] = number of edges containing both node-i and node-j
        NNco = (hg.B @ hg.B.T).tocsr()
        result: dict[int, float] = {}

        for vi in range(hg.n_nodes):
            row_arr = np.asarray(NNco.getrow(vi).todense()).ravel()
            row_arr[vi] = 0    # exclude self
            nbrs = np.where(row_arr > 0)[0]
            d = len(nbrs)
            if d < 2:
                result[int(hg.node_ids[vi])] = 0.0
                continue
            sub = NNco[np.ix_(nbrs, nbrs)].toarray()
            np.fill_diagonal(sub, 0)           # don't count self-loops
            closed = int((sub > 0).sum())       # both (u,w) and (w,u) counted
            result[int(hg.node_ids[vi])] = float(closed) / (d * (d - 1))

        if node_id is not None:
            if node_id not in result:
                raise KeyError(f"node_id {node_id!r} not found")
            return result[node_id]
        return result

    def global_transitivity(self) -> float:
        """
        Global (macro) transitivity: average pairwise clustering coefficient
        over all nodes.
        """
        cc = self.pairwise_clustering()
        vals = list(cc.values())
        return float(np.mean(vals)) if vals else 0.0

    # ── 5. Modularity ─────────────────────────────────────────────────────

    def hypermodularity(
        self, partition: "list[set[int]] | None" = None
    ) -> float:
        """
        Kamiński et al. (2019) hypermodularity.

        For a given partition ``{C₁, C₂, …}``:

        .. code-block:: text

            Q = Σ_e (w_e / W) · [|e ∩ C(e)| / |e|  −  Σ_c (vol_c/V)² · (|e ∩ c|/|e|)]

        where ``W`` = total edge weight, ``vol_c`` = sum of node degrees in
        community *c*, ``V`` = total volume (= Σ_v degree_v).

        When *partition* is ``None``, a spectral bisection via the Fiedler
        vector is used.
        """
        hg = self._hg
        n, m = hg.n_nodes, hg.n_edges
        if n == 0 or m == 0:
            return 0.0

        if partition is None:
            partition = self._spectral_partition()

        node_comm: dict[int, int] = {}
        for ci, comm in enumerate(partition):
            for nid in comm:
                node_comm[nid] = ci
        n_comm = len(partition)
        node_to_comm = np.array(
            [node_comm.get(int(nid), 0) for nid in hg.node_ids], dtype=int
        )

        deg = np.asarray(hg.B.sum(axis=1)).ravel()
        vol = np.zeros(n_comm)
        for vi in range(n):
            vol[node_to_comm[vi]] += deg[vi]
        total_vol = vol.sum()

        total_weight = hg.weights.sum()
        if total_weight == 0 or total_vol == 0:
            return 0.0

        B_csc = hg.B.tocsc()
        Q = 0.0
        for ej in range(m):
            col = B_csc.getcol(ej)
            ni = col.indices
            if len(ni) == 0:
                continue
            comms_in_edge = node_to_comm[ni]
            size_e = len(ni)
            w_frac = hg.weights[ej] / total_weight
            for ci in range(n_comm):
                cnt = int((comms_in_edge == ci).sum())
                Q += w_frac * (cnt / size_e - (vol[ci] / total_vol) ** 2)
        return float(Q)

    def _spectral_partition(self) -> "list[set[int]]":
        """Fiedler-vector bisection (used as null-model partition)."""
        hg = self._hg
        if hg.n_nodes < 2:
            return [{int(nid) for nid in hg.node_ids}]
        L = self._zhou_laplacian()
        try:
            k = min(2, hg.n_nodes - 1)
            vals, vecs = spla.eigsh(L, k=k, which="SM", tol=1e-6)
            order = np.argsort(np.real(vals))
            fiedler = np.real(vecs[:, order[min(1, k - 1)]])
        except Exception:
            fiedler = np.random.default_rng(0).standard_normal(hg.n_nodes)

        comm0 = {int(hg.node_ids[i]) for i in range(hg.n_nodes) if fiedler[i] >= 0}
        comm1 = {int(hg.node_ids[i]) for i in range(hg.n_nodes) if fiedler[i] < 0}
        result = [c for c in [comm0, comm1] if c]
        return result or [{int(nid) for nid in hg.node_ids}]

    # ── 6. s-Walk measures ─────────────────────────────────────────────────

    def _build_s_adj(self, s: int) -> "dict[int, set[int]]":
        """
        Build the s-adjacency adjacency list (node-index-based).

        Two nodes u, v are s-adjacent if they share ≥ s hyperedges.
        Uses ``B @ Bᵀ`` (n_nodes × n_nodes co-occurrence matrix).
        """
        hg = self._hg
        # B @ B.T → (n_nodes, n_nodes): entry [i,j] = edges shared by node-i and node-j
        NNco = (hg.B @ hg.B.T).tocoo()
        adj: dict[int, set[int]] = {i: set() for i in range(hg.n_nodes)}
        for i, j, v in zip(NNco.row, NNco.col, NNco.data):
            if i != j and v >= s:
                adj[i].add(j)
        return adj

    def s_adjacency(self, s: int = 1) -> "dict[tuple[int, int], int]":
        """
        s-adjacency relation.

        Two nodes are *s-adjacent* when they share at least *s* hyperedges.

        Returns ``{(node_id_u, node_id_v): shared_edge_count}`` for all
        s-adjacent pairs ``u < v``.
        """
        hg = self._hg
        # B @ B.T → (n_nodes, n_nodes): entry [i,j] = edges shared by node-i and node-j
        NNco = (hg.B @ hg.B.T).tocoo()
        result: dict[tuple[int, int], int] = {}
        for i, j, v in zip(NNco.row, NNco.col, NNco.data):
            if i < j and v >= s:
                result[(int(hg.node_ids[i]), int(hg.node_ids[j]))] = int(v)
        return result

    def s_distance(self, s: int = 1) -> "dict[tuple[int, int], int]":
        """
        s-distance: BFS shortest-path length in the s-adjacency graph.

        Returns ``{(node_id_u, node_id_v): distance}`` for all reachable
        pairs ``u < v``.  Unreachable pairs are omitted (implicit ∞).

        Warning: O(n²) — intended for small-to-medium graphs.
        """
        hg = self._hg
        adj = self._build_s_adj(s)
        result: dict[tuple[int, int], int] = {}
        for src in range(hg.n_nodes):
            dist = _bfs_distances(src, adj)
            for tgt, d in dist.items():
                if tgt > src:
                    result[(int(hg.node_ids[src]), int(hg.node_ids[tgt]))] = d
        return result

    def s_closeness(
        self, s: int = 1, node_id: int | None = None
    ) -> "dict[int, float] | float":
        """
        s-closeness centrality: ``|reachable| / Σ d(v, u)`` for all reachable *u*.

        Returns 0.0 for isolated nodes (no reachable neighbours).
        """
        hg = self._hg
        adj = self._build_s_adj(s)
        closeness: dict[int, float] = {}
        for vi in range(hg.n_nodes):
            dist = _bfs_distances(vi, adj)
            reachable = [d for tgt, d in dist.items() if tgt != vi]
            if reachable:
                closeness[int(hg.node_ids[vi])] = float(len(reachable)) / sum(reachable)
            else:
                closeness[int(hg.node_ids[vi])] = 0.0
        if node_id is not None:
            if node_id not in closeness:
                raise KeyError(f"node_id {node_id!r} not found")
            return closeness[node_id]
        return closeness

    def s_betweenness(
        self, s: int = 1, node_id: int | None = None
    ) -> "dict[int, float] | float":
        """
        s-betweenness centrality.

        Fraction of s-shortest-paths passing through each node, computed
        via the Brandes (2001) BFS algorithm adapted for the s-adjacency
        graph.  Normalised by ``(n−1)(n−2)``.
        """
        hg = self._hg
        n = hg.n_nodes
        adj = self._build_s_adj(s)
        betweenness = np.zeros(n, dtype=float)

        for src in range(n):
            stack, pred, sigma, dist = _brandes_bfs(src, n, adj)
            delta = np.zeros(n, dtype=float)
            while stack:
                w = stack.pop()
                for v in pred[w]:
                    if sigma[w] > 0:
                        delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
                if w != src:
                    betweenness[w] += delta[w]

        scale = 1.0 / ((n - 1) * (n - 2)) if n > 2 else 1.0
        betweenness *= scale
        result = {int(hg.node_ids[vi]): float(betweenness[vi]) for vi in range(n)}
        if node_id is not None:
            if node_id not in result:
                raise KeyError(f"node_id {node_id!r} not found")
            return result[node_id]
        return result

    def s_diameter(self, s: int = 1) -> int:
        """
        Diameter of the s-adjacency graph.

        Returns:
          - ``0``  for a single node or empty graph
          - ``-1`` if the graph is disconnected (some pair has no s-path)
          - the maximum s-shortest-path length otherwise
        """
        hg = self._hg
        n = hg.n_nodes
        if n <= 1:
            return 0
        adj = self._build_s_adj(s)
        max_dist = 0
        for src in range(n):
            dist = _bfs_distances(src, adj)
            if len(dist) < n:
                return -1
            local_max = max(dist.values(), default=0)
            if local_max > max_dist:
                max_dist = local_max
        return max_dist

    def s_efficiency(self, s: int = 1) -> float:
        """
        Global efficiency of the s-adjacency graph.

        ``E = (1 / n(n−1)) Σ_{u≠v} 1/d_s(u,v)``

        Unreachable pairs contribute 0 (not ∞⁻¹).
        """
        hg = self._hg
        n = hg.n_nodes
        if n < 2:
            return 0.0
        adj = self._build_s_adj(s)
        total = 0.0
        for src in range(n):
            dist = _bfs_distances(src, adj)
            for tgt, d in dist.items():
                if tgt != src and d > 0:
                    total += 1.0 / d
        pairs = n * (n - 1)
        return float(total) / pairs if pairs > 0 else 0.0

    # ── 7. Temporal measures ───────────────────────────────────────────────

    def hyperedge_persistence(self) -> "dict[int, float]":
        """
        Persistence of each unique member-set across time windows.

        For each distinct member-set ``M``, let ``T_M`` be the set of
        unique timestamps in which ``M`` appears, and let ``T_all`` be all
        unique timestamps in the table.

        ``persistence(e) = |T_M| / |T_all|``

        All hyperedges sharing the same member-set receive the same value.
        Returns ``{edge_index: fraction}``.
        """
        hg = self._hg
        if hg.n_edges == 0:
            return {}

        all_ts = np.unique(hg.timestamps)
        n_windows = len(all_ts)
        if n_windows == 0:
            return {i: 0.0 for i in range(hg.n_edges)}

        B_csc = hg.B.tocsc()
        member_sets: dict[frozenset, list[int]] = {}
        for j in range(hg.n_edges):
            col = B_csc.getcol(j)
            ms = frozenset(int(hg.node_ids[i]) for i in col.indices)
            member_sets.setdefault(ms, []).append(j)

        result: dict[int, float] = {}
        for ms, indices in member_sets.items():
            unique_ts = np.unique(hg.timestamps[indices])
            frac = len(unique_ts) / n_windows
            for idx in indices:
                result[idx] = frac
        return result

    def burstiness(self) -> float:
        """
        Burstiness coefficient of the hyperedge stream.

        ``B = (σ − μ) / (σ + μ)``  of the inter-event-time distribution
        (Kim & Jo, 2016 normalisation).

        Interpretation:
          - ``B ≈ 0``  → Poisson / memoryless stream
          - ``B > 0``  → bursty (events cluster in time)
          - ``B < 0``  → periodic / regular stream

        Returns ``float('nan')`` when fewer than 2 distinct timestamps exist.
        """
        hg = self._hg
        if hg.n_edges < 2:
            return float("nan")
        ts = np.sort(hg.timestamps.astype(float))
        iets = np.diff(ts)
        iets = iets[iets > 0]
        if len(iets) == 0:
            return float("nan")
        mu = float(iets.mean())
        sigma = float(iets.std())
        denom = sigma + mu
        return float((sigma - mu) / denom) if denom > 0 else 0.0

    def temporal_degree_entropy(
        self, node_id: int | None = None
    ) -> "dict[int, float] | float":
        """
        Temporal degree entropy for each node.

        Shannon entropy (bits) of a node's activity distribution over the
        sorted, unique timestamps in the table.

        - High entropy → activity spread evenly across time.
        - Low entropy  → activity concentrated in a few time steps.

        Parameters
        ----------
        node_id :
            When given, return a scalar for that node only.
        """
        hg = self._hg
        all_ts = np.unique(hg.timestamps)
        n_windows = len(all_ts)
        empty = {int(nid): 0.0 for nid in hg.node_ids}
        if n_windows == 0 or hg.n_nodes == 0:
            if node_id is not None:
                return 0.0
            return empty

        ts_index = {int(t): i for i, t in enumerate(all_ts)}
        B_csr = hg.B.tocsr()
        result: dict[int, float] = {}

        for vi in range(hg.n_nodes):
            edge_indices = B_csr.getrow(vi).indices
            if len(edge_indices) == 0:
                result[int(hg.node_ids[vi])] = 0.0
                continue
            counts = np.zeros(n_windows, dtype=float)
            for ej in edge_indices:
                counts[ts_index[int(hg.timestamps[ej])]] += 1.0
            p = counts / counts.sum()
            p = p[p > 0]
            result[int(hg.node_ids[vi])] = float(-np.sum(p * np.log2(p)))

        if node_id is not None:
            if node_id not in result:
                raise KeyError(f"node_id {node_id!r} not found")
            return result[node_id]
        return result

    # ── Temporal Changepoint & Activity-Window Analysis ───────────────────

    def activity_windows(
        self,
        window_size: int = 200,
        step: int = 50,
    ) -> list[dict]:
        """
        Slide a fixed-size window over the time-sorted event stream and
        compute per-window statistics.

        Each returned dict contains:
          window_idx, ts_start, ts_end, event_count, n_nodes, n_new_nodes,
          novelty_rate, mean_edge_size, density, mean_degree, top_entities

        ``n_new_nodes`` and ``novelty_rate`` are *cumulative* — they measure
        novelty relative to all preceding windows, making them strong signals
        for attack-phase transitions (e.g. reconnaissance → lateral movement).

        Parameters
        ----------
        window_size : int
            Number of (time-sorted) events per window.
        step : int
            Stride between consecutive windows.
        """
        hg = self._hg
        if hg.n_edges == 0:
            return []

        # Pre-compute member lists (one pass over CSC columns — fast).
        B_csc = hg.B.tocsc()
        edge_members: list[list[int]] = []
        for ej in range(hg.n_edges):
            col = B_csc.getcol(ej)
            edge_members.append([int(hg.node_ids[ni]) for ni in col.indices])

        ts_order = np.argsort(hg.timestamps)
        sizes    = hg.sizes                     # pre-computed per-edge sizes

        seen_nodes: set[int]               = set()
        seen_pairs: set[tuple[int, int]]   = set()
        windows: list[dict]                = []

        for start in range(0, hg.n_edges, step):
            end = min(start + window_size, hg.n_edges)
            window_eidxs = ts_order[start:end]
            n_we = int(len(window_eidxs))
            if n_we == 0:
                break

            node_deg: dict[int, int] = {}
            new_nodes_count = 0
            new_pairs       = 0
            total_pairs     = 0

            for ej in window_eidxs:
                members = edge_members[ej]
                for nid in members:
                    if nid not in seen_nodes:
                        seen_nodes.add(nid)
                        new_nodes_count += 1
                    node_deg[nid] = node_deg.get(nid, 0) + 1

                # Cap pair enumeration to avoid quadratic explosion on large edges.
                m = members[:8]
                for i in range(len(m)):
                    for j in range(i + 1, len(m)):
                        p = (min(m[i], m[j]), max(m[i], m[j]))
                        total_pairs += 1
                        if p not in seen_pairs:
                            new_pairs += 1
                            seen_pairs.add(p)

            n_nodes      = len(node_deg)
            mean_es      = float(np.mean(sizes[window_eidxs]))
            density      = sum(node_deg.values()) / (n_nodes * n_we) if n_nodes * n_we > 0 else 0.0
            novelty_rate = new_pairs / total_pairs if total_pairs > 0 else 0.0
            mean_deg     = sum(node_deg.values()) / n_nodes if n_nodes > 0 else 0.0
            top_ents     = sorted(node_deg.items(), key=lambda x: -x[1])[:3]
            ts_arr       = hg.timestamps[window_eidxs]

            windows.append({
                "window_idx":     len(windows),
                "ts_start":       int(ts_arr.min()),
                "ts_end":         int(ts_arr.max()),
                "event_count":    n_we,
                "n_nodes":        n_nodes,
                "n_new_nodes":    new_nodes_count,
                "novelty_rate":   round(novelty_rate, 4),
                "mean_edge_size": round(mean_es, 3),
                "density":        round(density, 6),
                "mean_degree":    round(mean_deg, 2),
                "top_entities":   [(int(nid), int(deg)) for nid, deg in top_ents],
            })

        return windows

    # ── helpers for temporal_changepoints ─────────────────────────────────

    @staticmethod
    def _cost_l2(signal: np.ndarray, start: int, end: int) -> float:
        """Sum-of-squared-deviations from segment mean (L2 cost)."""
        if end <= start:
            return 0.0
        seg = signal[start:end]
        return float(np.sum((seg - float(seg.mean())) ** 2))

    @staticmethod
    def _binary_seg(
        signal: np.ndarray,
        penalty: float,
        min_size: int,
    ) -> list[int]:
        """Recursive binary segmentation changepoint search."""
        n = len(signal)
        if n < 2 * min_size:
            return []

        total_cost = Analytics._cost_l2(signal, 0, n)
        best_cost  = float("inf")
        best_t     = -1

        for t in range(min_size, n - min_size + 1):
            c = Analytics._cost_l2(signal, 0, t) + Analytics._cost_l2(signal, t, n)
            if c < best_cost:
                best_cost = c
                best_t    = t

        if best_t < 0 or (total_cost - best_cost) < penalty:
            return []

        left  = Analytics._binary_seg(signal[:best_t], penalty, min_size)
        right = [best_t + r for r in Analytics._binary_seg(signal[best_t:], penalty, min_size)]
        return sorted(left + [best_t] + right)

    _SIGNAL_LABELS = {
        "novelty_rate":   "Novelty Rate",
        "n_new_nodes":    "New Entities / Window",
        "density":        "Local Density",
        "mean_edge_size": "Mean Hyperedge Size",
        "mean_degree":    "Mean Node Degree",
    }

    _SIGNAL_INTERP = {
        "novelty_rate": {
            "up":   "New entity pairs appearing at higher rate → possible reconnaissance or new attack infrastructure",
            "down": "Novelty rate decreasing → attacker has established entity set (persistence / exfiltration phase)",
        },
        "n_new_nodes": {
            "up":   "Surge of new entities → lateral movement or network expansion",
            "down": "Fewer new entities → attack consolidating within known infrastructure",
        },
        "density": {
            "up":   "Hyperedge density increasing → entities co-appearing more → coordinated C2 activity",
            "down": "Density decreasing → activity spreading out → possible exfiltration or staging",
        },
        "mean_edge_size": {
            "up":   "Larger hyperedges → more entities per event → process-chain expansion or mass activity",
            "down": "Smaller hyperedges → attacker narrowing focus to specific targets",
        },
        "mean_degree": {
            "up":   "Entities more active → accelerating attack pace or automated tooling",
            "down": "Activity slowing → dormancy, cleanup, or waiting phase",
        },
    }

    def temporal_changepoints(
        self,
        signal: str = "novelty_rate",
        method: str = "auto",
        window_size: int = 200,
        step: int = 50,
        min_size: int = 3,
        penalty: float = 3.0,
    ) -> dict:
        """
        Detect changepoints in a 1-D temporal signal derived from
        :meth:`activity_windows`.

        Parameters
        ----------
        signal : str
            One of ``novelty_rate | n_new_nodes | density |
            mean_edge_size | mean_degree``.
        method : str
            ``"auto"`` tries *ruptures* PELT first, falls back to
            binary segmentation.  ``"cusum"`` uses a CUSUM control chart.
            ``"binseg"`` forces pure-NumPy binary segmentation.
        window_size / step :
            Passed to :meth:`activity_windows`.
        min_size : int
            Minimum number of windows between two changepoints.
        penalty : float
            Regularisation penalty (higher → fewer changepoints).

        Returns
        -------
        dict with keys:
          ``signal``, ``method_used``, ``windows`` (list of window dicts),
          ``changepoints`` (list of rich dicts per changepoint),
          ``n_changepoints``.
        """
        if signal not in self._SIGNAL_LABELS:
            raise ValueError(
                f"signal must be one of {list(self._SIGNAL_LABELS)}; got {signal!r}"
            )

        windows = self.activity_windows(window_size=window_size, step=step)
        if len(windows) < 2 * min_size:
            return {
                "signal":         signal,
                "method_used":    "insufficient_data",
                "windows":        windows,
                "changepoints":   [],
                "n_changepoints": 0,
            }

        values = np.array([float(w[signal]) for w in windows], dtype=float)
        n      = len(values)

        # ── Changepoint detection ──────────────────────────────────────
        method_used = method
        cp_indices: list[int] = []

        if method in ("auto", "pelt"):
            try:
                import ruptures as rpt  # type: ignore[import]
                algo = rpt.Pelt(model="rbf", min_size=min_size).fit(
                    values.reshape(-1, 1)
                )
                # ruptures returns end-of-segment indices; convert to boundary
                bkps = algo.predict(pen=penalty * float(values.std() + 1e-10))
                cp_indices  = [b - 1 for b in bkps if 0 < b < n]
                method_used = "pelt"
            except ImportError:
                cp_indices  = self._binary_seg(values, penalty, min_size)
                method_used = "binseg_fallback"

        elif method == "binseg":
            cp_indices  = self._binary_seg(values, penalty, min_size)
            method_used = "binseg"

        elif method == "cusum":
            mu    = float(values.mean())
            sigma = float(values.std()) + 1e-10
            S_pos = S_neg = 0.0
            for i, x in enumerate(values):
                z     = (x - mu) / sigma
                S_pos = max(0.0, S_pos + z - 0.5)
                S_neg = max(0.0, S_neg - z - 0.5)
                if S_pos > penalty or S_neg > penalty:
                    cp_indices.append(i)
                    S_pos = S_neg = 0.0
                    tail  = values[i + 1:]
                    mu    = float(tail.mean()) if len(tail) > 0 else mu
                    sigma = float(tail.std()) + 1e-10 if len(tail) > 1 else sigma
            method_used = "cusum"

        # ── Build rich output ──────────────────────────────────────────
        global_std = float(values.std()) + 1e-10
        changepoints: list[dict] = []

        for cp in sorted(set(cp_indices)):
            if cp <= 0 or cp >= n:
                continue
            pre_seg  = values[:cp]
            post_seg = values[cp:]
            pre_mean  = float(pre_seg.mean())
            post_mean = float(post_seg.mean())
            delta     = post_mean - pre_mean
            direction = "up" if delta >= 0 else "down"
            z_score   = abs(delta) / global_std

            if z_score >= 2.0:
                severity = "critical"
            elif z_score >= 1.0:
                severity = "high"
            elif z_score >= 0.5:
                severity = "medium"
            else:
                severity = "low"

            interp = self._SIGNAL_INTERP.get(signal, {}).get(
                direction, "Significant change detected in temporal signal"
            )

            changepoints.append({
                "window_idx":  cp,
                "ts_start":    windows[cp]["ts_start"],
                "ts_end":      windows[cp]["ts_end"],
                "pre_mean":    round(pre_mean, 5),
                "post_mean":   round(post_mean, 5),
                "delta":       round(delta, 5),
                "direction":   direction,
                "z_score":     round(z_score, 2),
                "severity":    severity,
                "interpretation": interp,
            })

        return {
            "signal":         signal,
            "signal_label":   self._SIGNAL_LABELS[signal],
            "method_used":    method_used,
            "n_windows":      n,
            "windows":        windows,
            "changepoints":   changepoints,
            "n_changepoints": len(changepoints),
        }

    # ── Convenience ───────────────────────────────────────────────────────

    def summary(self) -> "dict[str, Any]":
        """
        Return a concise summary of key scalar measures.

        Useful for quick inspection: ``print(db.analytics('Events').summary())``.
        """
        hg = self._hg
        vals = self.zhou_laplacian_eigenvalues(k=min(6, max(2, hg.n_nodes - 1)))
        return {
            "n_nodes":           hg.n_nodes,
            "n_edges":           hg.n_edges,
            "density":           round(self.density(), 6),
            "redundancy":        round(self.redundancy(), 6),
            "spectral_gap":      round(self.spectral_gap(), 6),
            "cheeger_constant":  round(self.cheeger_constant(), 6),
            "global_transitivity": round(self.global_transitivity(), 6),
            "hypermodularity":   round(self.hypermodularity(), 6),
            "burstiness":        round(self.burstiness(), 6)
            if not np.isnan(self.burstiness()) else None,
            "s1_diameter":       self.s_diameter(s=1),
            "s1_efficiency":     round(self.s_efficiency(s=1), 6),
        }

    def __repr__(self) -> str:
        return (
            f"Analytics(n_nodes={self._hg.n_nodes}, n_edges={self._hg.n_edges})"
        )
