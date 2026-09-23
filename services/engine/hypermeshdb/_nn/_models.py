"""
_models.py — owned hypergraph neural network + the single-call training API.

This is the Phase 2 surface of the HyperMesh modeling layer.  It ships a
*torch-only* spectral hypergraph neural network (no PyG / DGL / DHG required)
and collapses "build a model + train it" into one function, :func:`fit`.

The convolution operator is the HGNN propagation matrix (Feng et al., 2019)::

    Θ = D_v^{-1/2} · B · (W · D_e^{-1}) · Bᵀ · D_v^{-1/2}

which is exactly ``I − Δ`` for the Zhou (2006) normalised hypergraph Laplacian
``Δ`` already used by :mod:`hypermesh.analytics` — so the network rests on the
same spectral foundation as the rest of the engine.

A single forward layer computes ``σ(Θ · X · Θ_w)``; ``SpectralHGNN`` stacks
``num_layers`` of them.  Training is full-batch / transductive (the whole graph
is in memory), which is the standard setting for HGNN node classification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from ._bridges import _require

if TYPE_CHECKING:
    from .._analytics import HypergraphPy


# ── Propagation operator ────────────────────────────────────────────────────────

def hgnn_operator(hg: HypergraphPy) -> Any:
    """
    Build the sparse HGNN propagation operator ``Θ`` (SciPy CSR, shape
    ``(n_nodes, n_nodes)``) for *hg*.

    ``Θ = D_v^{-1/2} B (W D_e^{-1}) Bᵀ D_v^{-1/2}`` — identical to ``I − Δ`` for
    the engine's Zhou normalised Laplacian ``Δ``.
    """
    from scipy import sparse as sp

    # Unweighted hypergraphs (edges inserted without an explicit weight) default
    # to unit edge weights, otherwise D_v collapses to zero and Θ vanishes.
    w = np.asarray(hg.weights, dtype=float)
    if not np.any(w > 0):
        w = np.ones(hg.n_edges, dtype=float)

    wd = hg.B @ w  # weighted node degree
    with np.errstate(divide="ignore"):
        inv_sqrt = np.where(wd > 0, 1.0 / np.sqrt(wd), 0.0)
        size_inv = np.where(hg.sizes > 0, 1.0 / hg.sizes.astype(float), 0.0)
    d_v = sp.diags(inv_sqrt, format="csr")

    w_e = sp.diags(w * size_inv, format="csr")

    theta = d_v @ hg.B @ w_e @ hg.B.T @ d_v
    return theta.tocsr()


def _theta_to_torch(theta_csr: Any, torch: Any, device: Any) -> Any:
    """Convert a SciPy CSR operator to a coalesced torch sparse float tensor."""
    coo = theta_csr.tocoo()
    indices = torch.tensor(np.vstack([coo.row, coo.col]), dtype=torch.long)
    values = torch.tensor(coo.data, dtype=torch.float32)
    n = theta_csr.shape[0]
    return torch.sparse_coo_tensor(indices, values, size=(n, n)).coalesce().to(device)


# ── Module factory (built lazily so importing the package needs no torch) ───────

def _build_module_class(torch: Any) -> type:
    nn = torch.nn

    class SpectralHGNN(nn.Module):
        """
        Owned spectral hypergraph neural network.

        Each layer applies ``dropout → linear → Θ·(·) → activation``; the final
        layer omits the activation so the outputs are logits (classification) or
        raw values (regression).
        """

        def __init__(
            self,
            in_dim: int,
            hidden_dim: int,
            out_dim: int,
            num_layers: int = 2,
            dropout: float = 0.5,
        ) -> None:
            super().__init__()
            if num_layers < 1:
                raise ValueError("num_layers must be >= 1")
            dims = [in_dim] + [hidden_dim] * (num_layers - 1) + [out_dim]
            self.lins = nn.ModuleList(
                nn.Linear(dims[i], dims[i + 1]) for i in range(num_layers)
            )
            self.dropout = float(dropout)
            self.num_layers = num_layers

        def forward(self, x: Any, theta: Any) -> Any:
            last = self.num_layers - 1
            for i, lin in enumerate(self.lins):
                x = nn.functional.dropout(x, p=self.dropout, training=self.training)
                x = lin(x)
                x = torch.sparse.mm(theta, x)
                if i != last:
                    x = nn.functional.relu(x)
            return x

    return SpectralHGNN


# ── Fitted-model wrapper ─────────────────────────────────────────────────────────

@dataclass
class FittedHGNN:
    """
    A trained :class:`SpectralHGNN` plus everything needed to score the graph.

    Transductive: predictions are produced for the same graph the model was
    fit on (the standard HGNN setting).  Use :meth:`predict_dict` to get
    predictions keyed by original node ID.
    """

    model: Any
    theta: Any
    x: Any
    node_ids: np.ndarray
    task: str  # "classification" | "regression"
    classes_: list[Any] | None = None
    train_idx: np.ndarray | None = None
    val_idx: np.ndarray | None = None
    history: dict[str, list[float]] = field(default_factory=dict)
    _torch: Any = None

    # ── core inference ──────────────────────────────────────────────────────
    def _forward(self) -> Any:
        torch = self._torch
        self.model.eval()
        with torch.no_grad():
            return self.model(self.x, self.theta)

    def logits(self) -> np.ndarray:
        return self._forward().cpu().numpy()

    def predict_proba(self) -> np.ndarray:
        """Softmax class probabilities, shape ``(n_nodes, n_classes)``."""
        if self.task != "classification":
            raise ValueError("predict_proba() is only defined for classification.")
        torch = self._torch
        return torch.softmax(self._forward(), dim=1).cpu().numpy()

    def predict(self) -> np.ndarray:
        """
        Per-node predictions aligned to ``node_ids``.

        Classification → array of original class labels (or class indices if the
        label was already numeric).  Regression → array of predicted values.
        """
        out = self._forward()
        if self.task == "classification":
            idx = out.argmax(dim=1).cpu().numpy()
            if self.classes_ is not None:
                return np.array([self.classes_[i] for i in idx], dtype=object)
            return idx
        return out.squeeze(-1).cpu().numpy()

    def predict_dict(self) -> dict[int, Any]:
        """Predictions keyed by original node ID."""
        preds = self.predict()
        return {int(n): preds[i] for i, n in enumerate(self.node_ids)}

    def embed(self) -> np.ndarray:
        """
        Node embeddings from the penultimate layer, shape
        ``(n_nodes, hidden_dim)``.  Falls back to logits for a 1-layer model.
        """
        torch = self._torch
        nn = torch.nn
        self.model.eval()
        with torch.no_grad():
            x = self.x
            last = self.model.num_layers - 1
            for i, lin in enumerate(self.model.lins):
                x = lin(x)
                x = torch.sparse.mm(self.theta, x)
                if i == last - 1:
                    return x.cpu().numpy()
                if i != last:
                    x = nn.functional.relu(x)
            return x.cpu().numpy()

    # ── evaluation ──────────────────────────────────────────────────────────
    def evaluate(self, y: np.ndarray, split: str = "val") -> dict[str, float]:
        """
        Score against the supplied label vector *y* (aligned to ``node_ids``).

        ``split`` selects ``"train"``, ``"val"`` or ``"all"`` (all labelled
        nodes).  Returns ``{"accuracy": ...}`` for classification or
        ``{"mse": ..., "mae": ...}`` for regression.
        """
        idx = self._split_index(y, split)
        if self.task == "classification":
            pred = self._forward().argmax(dim=1).cpu().numpy()
            acc = float(np.mean(pred[idx] == y[idx])) if len(idx) else float("nan")
            return {"accuracy": acc, "n": int(len(idx))}
        pred = self._forward().squeeze(-1).cpu().numpy()
        err = pred[idx] - y[idx]
        return {
            "mse": float(np.mean(err ** 2)) if len(idx) else float("nan"),
            "mae": float(np.mean(np.abs(err))) if len(idx) else float("nan"),
            "n": int(len(idx)),
        }

    def _split_index(self, y: np.ndarray, split: str) -> np.ndarray:
        if split == "train" and self.train_idx is not None:
            return self.train_idx
        if split == "val" and self.val_idx is not None:
            return self.val_idx
        if split == "all":
            return _labelled_index(y, self.task)
        # fall back to all labelled if the requested split is unavailable
        return _labelled_index(y, self.task)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"FittedHGNN(task={self.task!r}, n_nodes={len(self.node_ids)}, "
            f"classes={len(self.classes_) if self.classes_ else 'n/a'})"
        )


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _labelled_index(y: np.ndarray, task: str) -> np.ndarray:
    if task == "classification":
        return np.flatnonzero(y >= 0)
    return np.flatnonzero(np.isfinite(y))


def _train_val_split(
    labelled: np.ndarray, val_fraction: float, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    if len(labelled) <= 2 or val_fraction <= 0.0:
        return labelled, labelled
    perm = rng.permutation(labelled)
    n_val = max(1, int(round(len(labelled) * val_fraction)))
    n_val = min(n_val, len(labelled) - 1)  # keep >=1 for training
    return perm[n_val:], perm[:n_val]


# ── The single high-level training call ─────────────────────────────────────────

def fit(
    source: Any,
    table: str | None = None,
    *,
    node_table: str | None = None,
    node_features: list[str] | None = None,
    label: str | None = None,
    hidden_dim: int = 32,
    num_layers: int = 2,
    dropout: float = 0.5,
    epochs: int = 200,
    lr: float = 0.01,
    weight_decay: float = 5e-4,
    val_fraction: float = 0.2,
    task: str = "auto",
    standardize: bool = True,
    seed: int = 0,
    device: str | None = None,
    categorical: str = "onehot",
    impute: float = 0.0,
    x: Any = None,
    db: Any = None,
    verbose: bool = False,
) -> FittedHGNN:
    """
    Train an owned spectral HGNN on a stored hypergraph — in one call.

    Goes from a live database (or a :class:`FeaturedHypergraph`) to a trained,
    ready-to-score :class:`FittedHGNN`.  Equivalent to
    ``featurize(...)`` → build ``SpectralHGNN`` → train → wrap.

    Parameters
    ----------
    source, table, node_table, node_features, label, categorical, impute, x, db :
        Forwarded to :func:`~hypermeshdb._nn._data.featurize` (a
        :class:`FeaturedHypergraph` may also be passed directly as ``source``).
        ``label`` is required (it provides the supervision target).
    hidden_dim, num_layers, dropout :
        Network architecture.
    epochs, lr, weight_decay, val_fraction, seed :
        Optimisation.  The best-validation parameters are restored at the end.
    task :
        ``"auto"`` (default), ``"classification"`` or ``"regression"``.  ``auto``
        picks classification for categorical labels, regression for numeric.
    standardize :
        Z-score node features column-wise before training (default ``True``).
    device :
        Torch device string (defaults to CPU).

    Returns
    -------
    FittedHGNN
    """
    torch = _require("torch", "ml")
    from ._data import FeaturedHypergraph, featurize

    if isinstance(source, FeaturedHypergraph):
        fhg = source
    else:
        fhg = featurize(
            source, table, node_table=node_table, node_features=node_features,
            label=label, categorical=categorical, impute=impute, x=x, db=db,
            edge_features=None,
        )
    if fhg.y is None:
        raise ValueError(
            "fit() needs labels. Pass label=<node-table column> (and node_table=) "
            "so a supervision target is available."
        )

    # Resolve task.
    resolved = task
    if task == "auto":
        resolved = "classification" if fhg.classes_ is not None else "regression"
    if resolved not in ("classification", "regression"):
        raise ValueError(f"task must be 'auto', 'classification' or 'regression', got {task!r}")

    dev = torch.device(device or "cpu")
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)

    theta = _theta_to_torch(hgnn_operator(fhg.hg), torch, dev)
    X_np = np.asarray(fhg.X, dtype=np.float32)
    if standardize and X_np.shape[1] > 0:
        mu = X_np.mean(axis=0)
        sd = X_np.std(axis=0)
        sd[sd == 0] = 1.0
        X_np = (X_np - mu) / sd
    X = torch.as_tensor(X_np, device=dev)
    y_np = np.asarray(fhg.y)

    labelled = _labelled_index(y_np, resolved)
    if len(labelled) == 0:
        raise ValueError("No labelled nodes found to train on.")
    train_idx, val_idx = _train_val_split(labelled, val_fraction, rng)

    if resolved == "classification":
        out_dim = int(fhg.y.max()) + 1 if fhg.classes_ is None else len(fhg.classes_)
        out_dim = max(out_dim, int(y_np[labelled].max()) + 1)
        y = torch.as_tensor(y_np, dtype=torch.long, device=dev)
        loss_fn = torch.nn.functional.cross_entropy
    else:
        out_dim = 1
        y = torch.as_tensor(y_np.astype(np.float32), device=dev)
        loss_fn = torch.nn.functional.mse_loss

    SpectralHGNN = _build_module_class(torch)
    model = SpectralHGNN(X.shape[1], hidden_dim, out_dim, num_layers, dropout).to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)

    tr = torch.as_tensor(train_idx, dtype=torch.long, device=dev)
    va = torch.as_tensor(val_idx, dtype=torch.long, device=dev)

    history: dict[str, list[float]] = {"train_loss": [], "val_score": []}
    best_score = -np.inf  # higher-is-better (accuracy or negative mse)
    best_state = None

    for epoch in range(epochs):
        model.train()
        opt.zero_grad()
        out = model(X, theta)
        if resolved == "classification":
            loss = loss_fn(out[tr], y[tr])
        else:
            loss = loss_fn(out[tr].squeeze(-1), y[tr])
        loss.backward()
        opt.step()

        model.eval()
        with torch.no_grad():
            out = model(X, theta)
            if resolved == "classification":
                pred = out.argmax(dim=1)
                score = float((pred[va] == y[va]).float().mean()) if len(val_idx) else 0.0
            else:
                err = out[va].squeeze(-1) - y[va]
                score = float(-(err ** 2).mean()) if len(val_idx) else 0.0

        history["train_loss"].append(float(loss.item()))
        history["val_score"].append(score)
        if score >= best_score:
            best_score = score
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        if verbose and (epoch % max(1, epochs // 10) == 0):
            print(f"[fit] epoch {epoch:4d}  train_loss={loss.item():.4f}  val={score:.4f}")

    if best_state is not None:
        model.load_state_dict(best_state)

    return FittedHGNN(
        model=model,
        theta=theta,
        x=X,
        node_ids=np.asarray(fhg.node_ids),
        task=resolved,
        classes_=fhg.classes_,
        train_idx=train_idx,
        val_idx=val_idx,
        history=history,
        _torch=torch,
    )
