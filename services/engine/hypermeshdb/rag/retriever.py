"""
rag/retriever.py — HyperGraphRAG Retriever

Uses the incidence matrix (≡ FMI) and timestamp array (≡ TPI) to retrieve
the most relevant hyperedges for a parsed query.

No external dependencies — pure NumPy operations on the HypergraphPy object
returned by db.analytics(table).

Retrieval strategy
------------------
1. Entity-centric: for each resolved node_id, get all edge indices (FMI row lookup)
2. Temporal scoping: filter edges to [time_start, time_end]
3. Keyword scoring: score edges by member-label keyword overlap
4. Fusion: combine entity + temporal scores; deduplicate; rank by final score
5. Top-k selection: return top-k RetrievedEdge objects
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class RetrievedEdge:
    edge_idx:      int
    timestamp:     int
    size:          int
    weight:        float
    formation:     str
    member_ids:    list[int]
    member_labels: list[str]           # human-readable names from entity map
    member_types:  list[str]
    score:         float               # retrieval relevance score
    properties:    dict[str, Any]      = field(default_factory=dict)

    def hedge_tag(self) -> str:
        return f"HEDGE-{self.edge_idx}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "edge_idx":      self.edge_idx,
            "hedge_tag":     self.hedge_tag(),
            "timestamp":     self.timestamp,
            "size":          self.size,
            "weight":        round(self.weight, 4),
            "formation":     self.formation,
            "member_ids":    self.member_ids,
            "member_labels": self.member_labels,
            "member_types":  self.member_types,
            "score":         round(self.score, 4),
            "properties":    self.properties,
        }


# ── HyperGraphRetriever ───────────────────────────────────────────────────────

class HyperGraphRetriever:
    """
    Retrieves relevant hyperedges from a HypergraphPy object.

    Parameters
    ----------
    hg :
        HypergraphPy instance from ``db.analytics(table).hypergraph``.
    entity_map :
        Dict ``{str(node_id): {type, display, short, raw}}``.
    top_k :
        Maximum number of edges to return.
    """

    def __init__(
        self,
        hg: Any,
        entity_map: dict[str, Any] | None = None,
        top_k: int = 40,
    ) -> None:
        self._hg  = hg
        self._em  = entity_map or {}
        self._k   = top_k
        # Build node_id → row_index map
        self._id2row: dict[int, int] = {
            int(nid): int(i) for i, nid in enumerate(hg.node_ids)
        }

    # ── Public API ────────────────────────────────────────────────────────────

    def retrieve(
        self,
        node_ids:   list[int]  | None = None,
        time_start: int        | None = None,
        time_end:   int        | None = None,
        keywords:   list[str]  | None = None,
        min_weight: float      | None = None,
        top_k:      int        | None = None,
    ) -> list[RetrievedEdge]:
        """
        Retrieve top-k hyperedges relevant to the given parameters.

        Parameters
        ----------
        node_ids : node IDs to anchor retrieval (from entity extraction).
        time_start / time_end : epoch timestamps for temporal scoping.
        keywords : keywords for soft-matching member labels.
        min_weight : minimum edge weight filter.
        top_k : override default top-k.
        """
        hg = self._hg
        k  = top_k or self._k
        n_edges = hg.B.shape[1]

        # Base score array — starts at 0
        scores = np.zeros(n_edges, dtype=np.float64)

        # ── 1. Entity-centric: boost edges containing requested node_ids ──
        if node_ids:
            for nid in node_ids:
                row = self._id2row.get(nid)
                if row is None:
                    continue
                edge_indices = hg.B.getrow(row).nonzero()[1]
                scores[edge_indices] += 2.0   # strong signal

        # ── 2. Temporal relevance: decay for older events ─────────────────
        ts = hg.timestamps.astype(float)
        t_start = float(time_start) if time_start is not None else float(ts.min()) if len(ts) else 0.0
        t_end   = float(time_end)   if time_end   is not None else float(ts.max()) if len(ts) else 1.0
        span    = max(t_end - t_start, 1.0)

        # Linear temporal relevance: edges at t_end → 1.0, at t_start → 0.1
        tr = np.clip(0.1 + 0.9 * (ts - t_start) / span, 0.0, 1.0)
        scores += tr * 0.5

        # ── 3. Temporal scoping: hard-zero edges outside time window ──────
        if time_start is not None:
            scores[ts < time_start] = -1.0
        if time_end is not None:
            scores[ts > time_end] = -1.0

        # ── 4. Weight contribution ────────────────────────────────────────
        w = hg.weights.astype(float)
        # Normalise weights to [0, 1]
        w_max = float(w.max()) if w.max() > 0 else 1.0
        scores += (w / w_max) * 0.3

        # ── 5. Min-weight hard filter ─────────────────────────────────────
        if min_weight is not None:
            scores[w < min_weight] = -1.0

        # ── 6. Keyword scoring (soft — adds bonus for member label overlap) ─
        if keywords:
            kw_set = {k.lower() for k in keywords}
            # Vectorised: scan edges in top-200 by score first, then score
            candidate_indices = np.argsort(-scores)[:200]
            for eidx in candidate_indices:
                if scores[eidx] < 0:
                    continue
                rows = hg.B.getcol(int(eidx)).nonzero()[0]
                for r in rows:
                    meta   = self._em.get(str(int(hg.node_ids[r])), {})
                    label  = (meta.get("short") or meta.get("display") or "").lower()
                    if any(kw in label for kw in kw_set):
                        scores[int(eidx)] += 0.4
                        break

        # ── 7. Select top-k with valid scores ────────────────────────────
        valid_mask    = scores >= 0
        valid_indices = np.where(valid_mask)[0]
        if len(valid_indices) == 0:
            # Fallback: return most recent k edges ignoring all filters
            valid_indices = np.argsort(-ts)[:k]
            scores        = tr  # use temporal relevance as fallback score

        top_indices = valid_indices[np.argsort(-scores[valid_indices])[:k]]

        return [self._build_edge(int(eidx), float(scores[eidx])) for eidx in top_indices]

    # ── Internals ────────────────────────────────────────────────────────────

    def _infer_formation(self, member_types: list[str]) -> str:
        """Infer a human-readable formation label from member entity types."""
        type_set = set(t.lower() for t in member_types)
        has_machine  = "machine"  in type_set or "endpoint" in type_set
        has_process  = "process"  in type_set
        has_account  = "account"  in type_set
        has_ip       = "ip"       in type_set or "network" in type_set
        has_drone    = "drone"    in type_set
        has_plant    = "plant"    in type_set or "weed" in type_set

        if has_drone:                              return "DRONE_EVENT"
        if has_plant:                              return "PLANT_CLUSTER"
        if has_machine and has_process and has_account: return "ENDPOINT_CLUSTER"
        if has_machine and has_process:            return "PROCESS_EVENT"
        if has_machine and has_account:            return "AUTH_EVENT"
        if has_machine and has_ip:                 return "NETWORK_EVENT"
        if has_machine:                            return "MACHINE_EVENT"
        if has_process:                            return "PROCESS_EVENT"
        return "CO_OCCURRENCE"

    def _build_edge(self, eidx: int, score: float) -> RetrievedEdge:
        hg   = self._hg
        rows = hg.B.getcol(eidx).nonzero()[0]

        member_ids:    list[int]  = []
        member_labels: list[str]  = []
        member_types:  list[str]  = []

        for r in rows:
            nid  = int(hg.node_ids[r])
            meta = self._em.get(str(nid), {})
            member_ids.append(nid)
            member_labels.append(meta.get("short") or meta.get("display") or f"node_{nid}")
            member_types.append(meta.get("type", "entity"))

        # Formation is not stored in the analytics HypergraphPy layer —
        # derive a meaningful label from member type composition instead.
        formation = self._infer_formation(member_types)
        props: dict[str, Any] = {}

        return RetrievedEdge(
            edge_idx      = eidx,
            timestamp     = int(hg.timestamps[eidx]),
            size          = int(hg.sizes[eidx]),
            weight        = float(hg.weights[eidx]),
            formation     = str(formation),
            member_ids    = member_ids,
            member_labels = member_labels,
            member_types  = member_types,
            score         = score,
            properties    = props,
        )
