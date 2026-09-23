"""
_reservoir.py — temporal reservoir computing (LSM / Echo State Network).

A dependency-light (NumPy-only) reservoir model for temporal hypergraph data,
plus :func:`temporal_features` — the bridge that turns a time range of a
hyperedge table into a per-window feature *sequence* the reservoir consumes.

Reservoir computing (Liquid State Machine / Echo State Network) drives a fixed,
random recurrent reservoir with an input sequence and trains only a cheap linear
readout (ridge regression) on the reservoir states.  It captures temporal
dynamics without backpropagation-through-time, which suits streaming hypergraph
telemetry (the original CISO anomaly pipeline used exactly this idea).

Usage
-----
>>> import hypermesh as hm
>>> # one feature sequence per time range:
>>> seq = hm.nn.temporal_features(db, "CoProximity", window_seconds=3600)
>>> clf = hm.nn.ReservoirClassifier(n_reservoir=200, seed=0)
>>> clf.fit([seq_a, seq_b, ...], [0, 1, ...])     # many labelled sequences
>>> clf.predict([new_seq])
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .._analytics import HypergraphPy

_FEATURES = ("n_edges", "mean_size", "mean_weight", "sum_weight", "n_active_nodes")


# ── HyperMesh → sequence bridge ─────────────────────────────────────────────────

def _as_hypergraph(source: Any, table: str | None) -> HypergraphPy:
    from .._analytics import HypergraphPy

    if isinstance(source, HypergraphPy):
        return source
    to_hg = getattr(source, "to_hypergraph", None)
    if callable(to_hg):
        return to_hg(table)
    raise TypeError(
        "Expected a HypergraphPy or an embedded Connection; got "
        f"{type(source).__name__}."
    )


def temporal_features(
    source: Any,
    table: str | None = None,
    *,
    window_seconds: int = 3600,
    start: int | None = None,
    end: int | None = None,
    features: tuple[str, ...] = _FEATURES,
) -> np.ndarray:
    """
    Turn a hyperedge table into a per-window feature sequence.

    The time span ``[start, end)`` is split into fixed ``window_seconds``
    buckets; each bucket yields one feature row computed from the hyperedges
    whose ``event_ts`` falls inside it.

    Returns
    -------
    numpy.ndarray
        Shape ``(n_windows, len(features))``.  Empty windows are all-zero rows,
        so the sequence is dense and evenly spaced in time.

    Available features
    ------------------
    ``n_edges``, ``mean_size``, ``mean_weight``, ``sum_weight``,
    ``n_active_nodes``.
    """
    unknown = set(features) - set(_FEATURES)
    if unknown:
        raise ValueError(f"Unknown feature(s): {sorted(unknown)}. Choose from {_FEATURES}.")
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive.")

    hg = _as_hypergraph(source, table)
    if hg.n_edges == 0:
        return np.zeros((0, len(features)), dtype=np.float64)

    ts = np.asarray(hg.timestamps, dtype=np.int64)
    lo = int(ts.min()) if start is None else int(start)
    hi = int(ts.max()) + 1 if end is None else int(end)
    if hi <= lo:
        hi = lo + 1
    edges = (ts >= lo) & (ts < hi)

    n_windows = int(np.ceil((hi - lo) / window_seconds))
    bucket = np.clip((ts - lo) // window_seconds, 0, n_windows - 1)
    csc = hg.B.tocsc()  # for per-edge member lookup

    out = np.zeros((n_windows, len(features)), dtype=np.float64)
    for w in range(n_windows):
        sel = np.flatnonzero(edges & (bucket == w))
        if len(sel) == 0:
            continue
        sizes = hg.sizes[sel].astype(float)
        weights = hg.weights[sel].astype(float)
        row: dict[str, float] = {
            "n_edges": float(len(sel)),
            "mean_size": float(sizes.mean()),
            "mean_weight": float(weights.mean()),
            "sum_weight": float(weights.sum()),
        }
        if "n_active_nodes" in features:
            nodes: set[int] = set()
            for j in sel:
                nodes.update(int(r) for r in csc.indices[csc.indptr[j]:csc.indptr[j + 1]])
            row["n_active_nodes"] = float(len(nodes))
        out[w] = [row[f] for f in features]
    return out


# ── Echo State Network ──────────────────────────────────────────────────────────

class ReservoirClassifier:
    """
    Echo State Network classifier (a Liquid State Machine for sequence data).

    A fixed random reservoir transforms each input sequence into a state
    trajectory; the final (or mean) state is fed to a ridge-regression readout.
    Only the readout is trained, so fitting is fast and stable.

    Parameters
    ----------
    n_reservoir :
        Number of reservoir units.
    spectral_radius :
        Spectral radius of the recurrent weight matrix (< 1 for the echo-state
        property).
    sparsity :
        Fraction of non-zero recurrent connections.
    leak :
        Leaking rate of the reservoir state update (0 < leak <= 1).
    ridge :
        L2 regularisation strength for the readout.
    input_scaling :
        Scale applied to the random input weights.
    readout :
        ``"last"`` (final state) or ``"mean"`` (time-averaged state).
    seed :
        RNG seed for reproducible reservoirs.
    """

    def __init__(
        self,
        n_reservoir: int = 200,
        spectral_radius: float = 0.9,
        sparsity: float = 0.1,
        leak: float = 0.3,
        ridge: float = 1e-3,
        input_scaling: float = 1.0,
        readout: str = "last",
        seed: int = 0,
    ) -> None:
        if readout not in ("last", "mean"):
            raise ValueError("readout must be 'last' or 'mean'")
        if not 0.0 < leak <= 1.0:
            raise ValueError("leak must be in (0, 1]")
        self.n_reservoir = int(n_reservoir)
        self.spectral_radius = float(spectral_radius)
        self.sparsity = float(sparsity)
        self.leak = float(leak)
        self.ridge = float(ridge)
        self.input_scaling = float(input_scaling)
        self.readout = readout
        self.seed = int(seed)

        self._W_in: np.ndarray | None = None
        self._W: np.ndarray | None = None
        self._W_out: np.ndarray | None = None
        self.classes_: np.ndarray | None = None
        self.n_features_: int | None = None

    # ── reservoir construction ──────────────────────────────────────────────
    def _init_reservoir(self, n_features: int) -> None:
        rng = np.random.default_rng(self.seed)
        self.n_features_ = n_features
        self._W_in = (rng.uniform(-1.0, 1.0, (self.n_reservoir, n_features))
                      * self.input_scaling)
        W = rng.uniform(-1.0, 1.0, (self.n_reservoir, self.n_reservoir))
        mask = rng.uniform(0.0, 1.0, W.shape) > self.sparsity
        W[mask] = 0.0
        radius = np.max(np.abs(np.linalg.eigvals(W)))
        if radius > 0:
            W *= self.spectral_radius / radius
        self._W = W

    def _run(self, seq: np.ndarray) -> np.ndarray:
        """Drive the reservoir with one sequence; return the readout state."""
        assert self._W is not None and self._W_in is not None
        seq = np.atleast_2d(np.asarray(seq, dtype=np.float64))
        state = np.zeros(self.n_reservoir, dtype=np.float64)
        states = np.empty((len(seq), self.n_reservoir), dtype=np.float64)
        for t, u in enumerate(seq):
            pre = self._W_in @ u + self._W @ state
            state = (1.0 - self.leak) * state + self.leak * np.tanh(pre)
            states[t] = state
        if len(seq) == 0:
            return np.zeros(self.n_reservoir, dtype=np.float64)
        return states.mean(axis=0) if self.readout == "mean" else states[-1]

    def _design_matrix(self, sequences: list[np.ndarray]) -> np.ndarray:
        rows = [self._run(s) for s in sequences]
        S = np.vstack(rows) if rows else np.zeros((0, self.n_reservoir))
        # augment with bias column
        return np.hstack([S, np.ones((S.shape[0], 1))])

    # ── public API ──────────────────────────────────────────────────────────
    def fit(self, sequences: list[np.ndarray], labels: Any) -> ReservoirClassifier:
        """
        Fit the readout on labelled sequences.

        ``sequences`` is a list of ``(T_i, n_features)`` arrays (variable length
        allowed); ``labels`` is the matching class label per sequence.
        """
        labels = np.asarray(labels)
        if len(sequences) != len(labels):
            raise ValueError("sequences and labels must have equal length.")
        if len(sequences) == 0:
            raise ValueError("Need at least one sequence to fit.")
        n_features = np.atleast_2d(sequences[0]).shape[1]
        self._init_reservoir(n_features)

        self.classes_ = np.unique(labels)
        cls_index = {c: i for i, c in enumerate(self.classes_)}
        Y = np.zeros((len(labels), len(self.classes_)), dtype=np.float64)
        for i, lab in enumerate(labels):
            Y[i, cls_index[lab]] = 1.0

        S = self._design_matrix(sequences)
        # ridge: W_out = (SᵀS + λI)^{-1} Sᵀ Y
        A = S.T @ S + self.ridge * np.eye(S.shape[1])
        self._W_out = np.linalg.solve(A, S.T @ Y)
        return self

    def decision_function(self, sequences: list[np.ndarray]) -> np.ndarray:
        if self._W_out is None:
            raise RuntimeError("ReservoirClassifier is not fitted yet; call fit().")
        return self._design_matrix(sequences) @ self._W_out

    def predict(self, sequences: list[np.ndarray]) -> np.ndarray:
        """Predicted class label per sequence."""
        scores = self.decision_function(sequences)  # raises if unfitted
        assert self.classes_ is not None
        return self.classes_[scores.argmax(axis=1)]

    def score(self, sequences: list[np.ndarray], labels: Any) -> float:
        """Mean classification accuracy."""
        return float(np.mean(self.predict(sequences) == np.asarray(labels)))

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        fitted = self._W_out is not None
        return (
            f"ReservoirClassifier(n_reservoir={self.n_reservoir}, "
            f"spectral_radius={self.spectral_radius}, fitted={fitted})"
        )
