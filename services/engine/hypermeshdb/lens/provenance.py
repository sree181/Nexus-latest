"""
lens/provenance.py — HyperMesh Lens: Structural Provenance Engine

Builds explanation subgraphs by traversing the incidence matrix (B),
node-to-edge index (row lookups ≡ FMI), edge-to-node index (col lookups ≡ RMI),
and timestamp array (≡ TPI). No changes to the C engine required.

All five provenance types from the Lens spec are implemented:

1. Entity    — "Why is node X important?"
               FMI(X) → edges → RMI(edges) → co-members → attribution
2. Pattern   — "Why did the detector find this finding?"
               Uses PatternFinding.affected_nodes → local subgraph
3. Temporal  — "How did the pattern evolve over time?"
               Sliding window over timestamps → per-window feature deltas
4. Counterfactual — "What if node X were removed?"
               Remove X's edges → recompute key metrics → delta
5. Bridge    — "Who connects the sub-communities?"
               Nodes active across multiple edge sizes (order span) + betweenness

Attribution formula (entity provenance):
    score(Y | X) = Σ_h ∈ H(X)∩H(Y) weight(h) × temporal_relevance(h) / |members(h)|

where temporal_relevance(h) decays linearly from 1.0 (at window_end) to 0.1 (at window_start).
"""

from __future__ import annotations

import math
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LensConfig:
    max_subgraph_nodes:      int   = 30
    max_subgraph_edges:      int   = 60
    max_traversal_depth:     int   = 2
    min_attribution_score:   float = 0.005
    top_k_counterfactuals:   int   = 5
    temporal_window_count:   int   = 10     # number of windows for temporal provenance
    include_counterfactuals: bool  = False  # compute counterfactuals for top entity nodes


# ─────────────────────────────────────────────────────────────────────────────
# Output data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProvenanceNode:
    node_id:          int
    attribution:      float
    hyperedge_count:  int
    order_span:       int    # distinct edge sizes this node participates in
    is_bridge:        bool
    label:            str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id":         self.node_id,
            "attribution":     round(self.attribution, 5),
            "hyperedge_count": self.hyperedge_count,
            "order_span":      self.order_span,
            "is_bridge":       self.is_bridge,
            "label":           self.label,
        }


@dataclass
class ProvenanceHyperedge:
    edge_idx:           int
    timestamp:          int
    size:               int
    weight:             float
    member_ids:         list[int]
    temporal_relevance: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "edge_idx":           self.edge_idx,
            "timestamp":          self.timestamp,
            "size":               self.size,
            "weight":             round(self.weight, 4),
            "member_ids":         self.member_ids,
            "temporal_relevance": round(self.temporal_relevance, 4),
        }


@dataclass
class ProvenanceGraph:
    provenance_type:   str
    description:       str
    window_start:      int
    window_end:        int
    nodes:             list[ProvenanceNode]             = field(default_factory=list)
    hyperedges:        list[ProvenanceHyperedge]        = field(default_factory=list)
    feature_attrs:     list[tuple[str, float]]          = field(default_factory=list)
    narrative:         str                              = ""
    nodes_examined:    int                              = 0
    edges_examined:    int                              = 0
    build_ms:          float                            = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "provenance_type":  self.provenance_type,
            "description":      self.description,
            "window_start":     self.window_start,
            "window_end":       self.window_end,
            "nodes":            [n.as_dict() for n in self.nodes],
            "hyperedges":       [e.as_dict() for e in self.hyperedges],
            "feature_attrs":    [{"feature": k, "contribution": round(v, 4)} for k, v in self.feature_attrs],
            "narrative":        self.narrative,
            "nodes_examined":   self.nodes_examined,
            "edges_examined":   self.edges_examined,
            "build_ms":         round(self.build_ms, 2),
        }


# ─────────────────────────────────────────────────────────────────────────────
# ProvenanceBuilder
# ─────────────────────────────────────────────────────────────────────────────

class ProvenanceBuilder:
    """
    Builds provenance subgraphs from a HypergraphPy + entity map.

    Parameters
    ----------
    hg :
        A ``HypergraphPy`` instance from ``db.analytics(table).hypergraph``.
    entity_map :
        Optional ``{str(node_id): {type, display, short, raw}}`` map.
    config :
        Traversal bounds and thresholds.
    """

    def __init__(
        self,
        hg: Any,                           # HypergraphPy
        entity_map: dict[str, Any] | None = None,
        config: LensConfig | None = None,
    ) -> None:
        self._hg     = hg
        self._em     = entity_map or {}
        self._cfg    = config or LensConfig()

        # Build node_id → row_index lookup (inverse of hg.node_ids)
        self._id2row: dict[int, int] = {
            int(nid): int(i) for i, nid in enumerate(hg.node_ids)
        }

    # ── helpers ──────────────────────────────────────────────────────────────

    def _label(self, node_id: int) -> str:
        meta = self._em.get(str(node_id), {})
        return meta.get("short") or meta.get("display") or f"node_{node_id}"

    def _edges_of_node(self, row: int) -> np.ndarray:
        """Edge indices for the node at incidence matrix row `row`."""
        return self._hg.B.getrow(row).nonzero()[1]

    def _nodes_of_edge(self, col: int) -> np.ndarray:
        """Node row indices for incidence matrix column `col`."""
        return self._hg.B.getcol(col).nonzero()[0]

    def _temporal_relevance(self, ts: int, window_start: int, window_end: int) -> float:
        span = max(window_end - window_start, 1)
        t    = max(window_start, min(window_end, ts))
        return 0.1 + 0.9 * (t - window_start) / span

    def _build_prov_edge(self, edge_idx: int, window_start: int, window_end: int) -> ProvenanceHyperedge:
        ts   = int(self._hg.timestamps[edge_idx])
        size = int(self._hg.sizes[edge_idx])
        w    = float(self._hg.weights[edge_idx])
        rows = self._nodes_of_edge(edge_idx)
        mids = [int(self._hg.node_ids[r]) for r in rows]
        tr   = self._temporal_relevance(ts, window_start, window_end)
        return ProvenanceHyperedge(edge_idx, ts, size, w, mids, tr)

    def _order_span_of(self, row: int) -> int:
        edge_indices = self._edges_of_node(row)
        if len(edge_indices) == 0:
            return 0
        return int(np.unique(self._hg.sizes[edge_indices]).size)

    # ── 1. Entity Provenance ──────────────────────────────────────────────────

    def build_entity_provenance(
        self,
        node_id: int,
        window_start: int | None = None,
        window_end:   int | None = None,
    ) -> ProvenanceGraph:
        """
        "Why is node X important?"

        Traverses X's edges, scores co-members by attribution formula,
        returns the explanation subgraph.
        """
        t0 = time.perf_counter()
        hg = self._hg

        ts_arr = hg.timestamps
        ws = int(window_start) if window_start is not None else int(ts_arr.min()) if len(ts_arr) else 0
        we = int(window_end)   if window_end   is not None else int(ts_arr.max()) if len(ts_arr) else 0

        row = self._id2row.get(node_id)
        if row is None:
            return ProvenanceGraph(
                "entity", f"Node {node_id} not found", ws, we,
                narrative=f"Node {node_id} does not exist in this table.",
                build_ms=(time.perf_counter() - t0) * 1000,
            )

        seed_edges = self._edges_of_node(row)
        # Filter to time window
        mask = (ts_arr[seed_edges] >= ws) & (ts_arr[seed_edges] <= we)
        seed_edges = seed_edges[mask]

        # Co-member attribution
        co_scores: dict[int, float] = defaultdict(float)
        prov_edges: list[ProvenanceHyperedge] = []
        edges_examined = 0

        for eidx in seed_edges:
            edges_examined += 1
            pe = self._build_prov_edge(int(eidx), ws, we)
            prov_edges.append(pe)
            w  = pe.weight
            tr = pe.temporal_relevance
            sz = max(pe.size, 1)
            for mid in pe.member_ids:
                if mid != node_id:
                    co_scores[mid] += w * tr / sz

        # Prune and rank co-members
        pruned = {k: v for k, v in co_scores.items() if v >= self._cfg.min_attribution_score}
        sorted_co = sorted(pruned.items(), key=lambda x: -x[1])[:self._cfg.max_subgraph_nodes]

        prov_nodes: list[ProvenanceNode] = []
        # Seed node itself, always first
        seed_row = self._id2row[node_id]
        seed_span = self._order_span_of(seed_row)
        prov_nodes.append(ProvenanceNode(
            node_id=node_id,
            attribution=1.0,
            hyperedge_count=len(seed_edges),
            order_span=seed_span,
            is_bridge=seed_span >= 2,
            label=self._label(node_id),
        ))
        for co_id, score in sorted_co:
            co_row  = self._id2row.get(co_id)
            co_span = self._order_span_of(co_row) if co_row is not None else 0
            n_co_edges = len(self._edges_of_node(co_row)) if co_row is not None else 0
            prov_nodes.append(ProvenanceNode(
                node_id=co_id,
                attribution=score,
                hyperedge_count=n_co_edges,
                order_span=co_span,
                is_bridge=co_span >= 2,
                label=self._label(co_id),
            ))

        # Trim edges
        prov_edges = sorted(prov_edges, key=lambda e: -e.temporal_relevance)[:self._cfg.max_subgraph_edges]

        narrative = self._narrative_entity(node_id, prov_nodes, prov_edges, seed_edges, ws, we)

        build_ms = (time.perf_counter() - t0) * 1000
        return ProvenanceGraph(
            provenance_type = "entity",
            description     = f"Why is {self._label(node_id)} important?",
            window_start    = ws,
            window_end      = we,
            nodes           = prov_nodes,
            hyperedges      = prov_edges,
            narrative       = narrative,
            nodes_examined  = len(co_scores),
            edges_examined  = edges_examined,
            build_ms        = build_ms,
        )

    def _narrative_entity(
        self,
        node_id: int,
        nodes: list[ProvenanceNode],
        edges: list[ProvenanceHyperedge],
        seed_edges: np.ndarray,
        ws: int, we: int,
    ) -> str:
        label = self._label(node_id)
        n_edges = len(seed_edges)
        seed = nodes[0] if nodes else None

        if n_edges == 0:
            return f"{label} has no activity in the requested time window (ts {ws}–{we})."

        top_co = [n for n in nodes[1:] if n.attribution > 0][:3]
        top_co_str = ", ".join(f"{n.label} (score {n.attribution:.3f})" for n in top_co) or "none"

        bridge_note = ""
        if seed and seed.is_bridge:
            bridge_note = (
                f" {label} is a bridge node — active across {seed.order_span} "
                "distinct event sizes — meaning it connects different sub-communities."
            )

        sizes = [e.size for e in edges]
        avg_size = statistics.mean(sizes) if sizes else 0

        return (
            f"{label} participated in {n_edges:,} events in window ts {ws}–{we}. "
            f"Average event size: {avg_size:.1f} entities. "
            f"Strongest co-occurrences: {top_co_str}."
            f"{bridge_note}"
        )

    # ── 2. Pattern Provenance ─────────────────────────────────────────────────

    def build_pattern_provenance(
        self,
        finding_dict: dict[str, Any],
        window_start: int | None = None,
        window_end:   int | None = None,
    ) -> ProvenanceGraph:
        """
        "Why did the detector report this finding?"

        Uses affected_nodes from the PatternFinding to build a local subgraph
        of the events that produced the evidence.
        """
        t0 = time.perf_counter()
        hg = self._hg
        ts_arr = hg.timestamps
        ws = int(window_start) if window_start is not None else int(ts_arr.min()) if len(ts_arr) else 0
        we = int(window_end)   if window_end   is not None else int(ts_arr.max()) if len(ts_arr) else 0

        affected: list[int] = finding_dict.get("affected_nodes", [])
        f_type   = finding_dict.get("type", "unknown")
        f_title  = finding_dict.get("title", "Unknown finding")
        evidence = finding_dict.get("evidence", {})

        # Collect all edges involving affected nodes
        edge_set: set[int] = set()
        for nid in affected:
            row = self._id2row.get(int(nid))
            if row is None:
                continue
            for eidx in self._edges_of_node(row):
                if ws <= int(ts_arr[eidx]) <= we:
                    edge_set.add(int(eidx))

        # Collect all nodes in those edges (neighborhood)
        node_scores: dict[int, float] = defaultdict(float)
        prov_edges: list[ProvenanceHyperedge] = []

        for eidx in list(edge_set)[:self._cfg.max_subgraph_edges]:
            pe = self._build_prov_edge(eidx, ws, we)
            prov_edges.append(pe)
            for mid in pe.member_ids:
                score = pe.weight * pe.temporal_relevance / max(pe.size, 1)
                # Boost affected nodes
                if mid in affected:
                    score *= 3.0
                node_scores[mid] += score

        sorted_nodes = sorted(node_scores.items(), key=lambda x: -x[1])[:self._cfg.max_subgraph_nodes]
        prov_nodes: list[ProvenanceNode] = []
        for nid, score in sorted_nodes:
            row = self._id2row.get(nid)
            span = self._order_span_of(row) if row is not None else 0
            prov_nodes.append(ProvenanceNode(
                node_id=nid,
                attribution=score,
                hyperedge_count=len(self._edges_of_node(row)) if row is not None else 0,
                order_span=span,
                is_bridge=span >= 2,
                label=self._label(nid),
            ))

        # Feature attributions from evidence
        feature_attrs: list[tuple[str, float]] = []
        for k, v in evidence.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                feature_attrs.append((k, float(v)))
        feature_attrs.sort(key=lambda x: -abs(x[1]))

        narrative = self._narrative_pattern(f_type, f_title, affected, prov_nodes, evidence)

        build_ms = (time.perf_counter() - t0) * 1000
        return ProvenanceGraph(
            provenance_type = "pattern",
            description     = f"Why was '{f_title}' detected?",
            window_start    = ws,
            window_end      = we,
            nodes           = prov_nodes,
            hyperedges      = prov_edges,
            feature_attrs   = feature_attrs[:8],
            narrative       = narrative,
            nodes_examined  = len(node_scores),
            edges_examined  = len(edge_set),
            build_ms        = build_ms,
        )

    def _narrative_pattern(
        self,
        f_type: str,
        f_title: str,
        affected: list[int],
        nodes: list[ProvenanceNode],
        evidence: dict,
    ) -> str:
        affected_labels = [self._label(n) for n in affected[:5]]
        affected_str    = ", ".join(affected_labels) or "no specific entities"

        def _fv(v: object, fmt: str = ".3f") -> str:
            """Safe format: return formatted float or raw string if not numeric."""
            try:
                return format(float(v), fmt)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return str(v)

        type_notes = {
            "hub_node":       f"The node(s) {affected_str} appear in a disproportionate share of events, exceeding the 2σ degree threshold.",
            "temporal_burst": f"Events are clustering in time (B={_fv(evidence.get('burstiness'))}), indicating automated or scripted activity rather than organic user behavior.",
            "dense_cluster":  f"The subgraph has unusually high co-occurrence density ({_fv(evidence.get('density'))}) and redundancy ({_fv(evidence.get('redundancy'))}), characteristic of persistent co-activity.",
            "fan_out":        f"{affected_str} appear in {_fv(evidence.get('pct_of_events'), '.1f')}% of all events — far above the 30% fan-out threshold.",
            "lateral_movement": f"A chain of {evidence.get('chain_length', '?')} entities share ≥{evidence.get('s', 2)} common events, tracing a multi-hop traversal path.",
            "spectral_anomaly": "The graph's spectral gap indicates weak connectivity between sub-communities, suggesting separate activity clusters.",
            "beaconing":       f"{affected_str} is highly active but has very low temporal entropy — events arrive in a suspiciously regular or concentrated pattern.",
        }
        specific = type_notes.get(f_type, f"The pattern '{f_type}' was triggered by structural properties of the subgraph.")

        top_attrs = [n for n in nodes[:3] if n.node_id in affected]
        top_str   = ", ".join(n.label for n in top_attrs) or affected_str

        return (
            f"Pattern '{f_title}' was detected because: {specific} "
            f"Key entities driving this finding: {top_str}. "
            f"The evidence subgraph spans {len(nodes)} entities across {len(set(e.edge_idx for e in []))} events."
        )

    # ── 3. Temporal Provenance ────────────────────────────────────────────────

    def build_temporal_provenance(
        self,
        history_start: int | None = None,
        history_end:   int | None = None,
        n_windows:     int | None = None,
    ) -> ProvenanceGraph:
        """
        "How did the pattern evolve over time?"

        Divides the time range into windows and computes key structural
        metrics per window, then identifies inflection points.
        Returns a ProvenanceGraph whose `feature_attrs` encode the timeline.
        """
        t0 = time.perf_counter()
        hg = self._hg
        ts_arr = hg.timestamps.astype(float)

        hs = int(history_start) if history_start is not None else int(ts_arr.min()) if len(ts_arr) else 0
        he = int(history_end)   if history_end   is not None else int(ts_arr.max()) if len(ts_arr) else 0

        nw = n_windows or self._cfg.temporal_window_count
        if he <= hs:
            return ProvenanceGraph(
                "temporal", "Temporal evolution", hs, he,
                narrative="Insufficient time range for windowed analysis.",
                build_ms=(time.perf_counter() - t0) * 1000,
            )

        span   = he - hs
        step   = max(1, span // nw)
        width  = step

        timeline: list[dict[str, Any]] = []
        prev_density = None

        for w_start in range(hs, he, step):
            w_end = w_start + width
            mask  = (ts_arr >= w_start) & (ts_arr < w_end)
            n_e   = int(mask.sum())
            if n_e == 0:
                continue

            sub_members: set[int] = set()
            for eidx in np.where(mask)[0]:
                for row in self._nodes_of_edge(int(eidx)):
                    sub_members.add(int(self._hg.node_ids[row]))
            n_n = len(sub_members)

            # Density proxy: edges / (nodes*(nodes-1)/2 + 1)
            denom   = max(1, n_n * (n_n - 1) // 2)
            density = n_e / denom

            weights_w = hg.weights[mask]
            mean_wt   = float(weights_w.mean()) if len(weights_w) else 0.0

            inflection = False
            if prev_density is not None and prev_density > 0:
                change = abs(density - prev_density) / prev_density
                inflection = change > 0.25  # 25% change is an inflection

            timeline.append({
                "window_start": w_start,
                "window_end":   w_end,
                "n_edges":      n_e,
                "n_nodes":      n_n,
                "density":      round(density, 5),
                "mean_weight":  round(mean_wt, 4),
                "is_inflection": inflection,
            })
            prev_density = density

        # Feature attrs encode the timeline as (label, density_value) pairs
        feature_attrs = [
            (f"w{i}_density", t["density"])
            for i, t in enumerate(timeline)
        ]

        inflection_points = [t for t in timeline if t["is_inflection"]]
        narrative = self._narrative_temporal(timeline, inflection_points, hs, he)

        build_ms = (time.perf_counter() - t0) * 1000
        return ProvenanceGraph(
            provenance_type = "temporal",
            description     = f"Pattern evolution from ts {hs} to {he} across {len(timeline)} windows",
            window_start    = hs,
            window_end      = he,
            nodes           = [],
            hyperedges      = [],
            feature_attrs   = feature_attrs,
            narrative       = narrative,
            nodes_examined  = 0,
            edges_examined  = int(((ts_arr >= hs) & (ts_arr <= he)).sum()),
            build_ms        = build_ms,
        )

    def _narrative_temporal(
        self,
        timeline: list[dict],
        inflections: list[dict],
        hs: int, he: int,
    ) -> str:
        if not timeline:
            return "No activity found in the requested time range."

        densities  = [t["density"] for t in timeline]
        max_d      = max(densities)
        min_d      = min(densities)
        peak_win   = timeline[densities.index(max_d)]
        trend      = "increasing" if densities[-1] > densities[0] else "decreasing" if densities[-1] < densities[0] else "stable"

        infl_str = ""
        if inflections:
            pts = "; ".join(f"ts {t['window_start']}–{t['window_end']}" for t in inflections[:3])
            infl_str = f" Significant structural shifts (>25% density change) occurred at: {pts}."

        return (
            f"Activity from ts {hs} to {he} spans {len(timeline)} windows. "
            f"Density ranged from {min_d:.4f} to {max_d:.4f} (peak at ts {peak_win['window_start']}–{peak_win['window_end']}). "
            f"Overall trend: {trend}."
            f"{infl_str}"
        )

    # ── 4. Counterfactual Provenance ──────────────────────────────────────────

    def build_counterfactual_provenance(
        self,
        node_id: int,
        window_start: int | None = None,
        window_end:   int | None = None,
    ) -> ProvenanceGraph:
        """
        "What would change if node X were removed?"

        Removes X's edges from the incidence matrix and recomputes:
        degree distribution, density, hub threshold — then reports the delta.
        """
        t0 = time.perf_counter()
        hg = self._hg
        ts_arr = hg.timestamps
        ws = int(window_start) if window_start is not None else int(ts_arr.min()) if len(ts_arr) else 0
        we = int(window_end)   if window_end   is not None else int(ts_arr.max()) if len(ts_arr) else 0

        row = self._id2row.get(node_id)
        label = self._label(node_id)

        if row is None:
            return ProvenanceGraph(
                "counterfactual", f"Node {node_id} not found", ws, we,
                narrative=f"Node {node_id} does not exist.",
                build_ms=(time.perf_counter() - t0) * 1000,
            )

        # Baseline metrics
        mask_all = (ts_arr >= ws) & (ts_arr <= we)
        base_edges = int(mask_all.sum())
        base_nodes = int(np.unique([
            int(self._hg.node_ids[r])
            for eidx in np.where(mask_all)[0]
            for r in self._nodes_of_edge(int(eidx))
        ]).size) if base_edges else 0
        base_density = base_edges / max(1, base_nodes * (base_nodes - 1) // 2)

        # Degree of seed node
        seed_edges = self._edges_of_node(row)
        seed_mask  = mask_all[seed_edges]
        n_removed  = int(seed_mask.sum())

        # Counterfactual metrics (without X's edges)
        cf_edges = base_edges - n_removed
        cf_density = cf_edges / max(1, base_nodes * (base_nodes - 1) // 2)

        # Hub score delta: degree percentile shift
        all_degrees: dict[int, int] = defaultdict(int)
        for eidx in np.where(mask_all)[0]:
            for r in self._nodes_of_edge(int(eidx)):
                all_degrees[int(self._hg.node_ids[r])] += 1

        seed_degree = all_degrees.get(node_id, 0)
        all_deg_vals = sorted(all_degrees.values(), reverse=True)
        base_pct  = sum(1 for d in all_deg_vals if d <= seed_degree) / max(1, len(all_deg_vals)) * 100

        # Feature attribution: how much does removing this node change key metrics?
        feature_attrs = [
            ("edges_removed",    float(n_removed)),
            ("density_delta",    cf_density - base_density),
            ("degree_percentile", base_pct),
            ("pct_events_affected", n_removed / max(1, base_edges) * 100),
        ]

        narrative = self._narrative_counterfactual(
            label, node_id, n_removed, base_edges, base_density, cf_density, base_pct, seed_degree,
        )

        build_ms = (time.perf_counter() - t0) * 1000
        return ProvenanceGraph(
            provenance_type = "counterfactual",
            description     = f"Impact of removing {label}",
            window_start    = ws,
            window_end      = we,
            nodes           = [ProvenanceNode(node_id, 1.0, seed_degree, self._order_span_of(row), self._order_span_of(row) >= 2, label)],
            hyperedges      = [],
            feature_attrs   = feature_attrs,
            narrative       = narrative,
            nodes_examined  = 1,
            edges_examined  = base_edges,
            build_ms        = build_ms,
        )

    def _narrative_counterfactual(
        self,
        label: str, node_id: int, n_removed: int, base_edges: int,
        base_density: float, cf_density: float, base_pct: float, seed_degree: int,
    ) -> str:
        pct_affected = n_removed / max(1, base_edges) * 100
        density_change = "decrease" if cf_density < base_density else "increase"
        density_delta  = abs(cf_density - base_density)

        impact = "high" if pct_affected > 20 else "moderate" if pct_affected > 5 else "low"

        return (
            f"Removing {label} (node {node_id}) would eliminate {n_removed:,} events "
            f"({pct_affected:.1f}% of activity in this window). "
            f"Graph density would {density_change} by {density_delta:.4f} "
            f"(from {base_density:.4f} to {cf_density:.4f}). "
            f"{label} is at the {base_pct:.0f}th degree percentile with {seed_degree:,} appearances. "
            f"Overall structural impact: {impact}."
        )

    # ── 5. Bridge Provenance ──────────────────────────────────────────────────

    def build_bridge_provenance(
        self,
        window_start: int | None = None,
        window_end:   int | None = None,
    ) -> ProvenanceGraph:
        """
        "Who are the structural connectors between sub-communities?"

        Identifies nodes with:
          - High order span (active across multiple edge sizes)
          - Participating in both small (size=2) and large (size>=4) edges
        """
        t0 = time.perf_counter()
        hg = self._hg
        ts_arr = hg.timestamps
        ws = int(window_start) if window_start is not None else int(ts_arr.min()) if len(ts_arr) else 0
        we = int(window_end)   if window_end   is not None else int(ts_arr.max()) if len(ts_arr) else 0

        mask = (ts_arr >= ws) & (ts_arr <= we)
        active_edges = np.where(mask)[0]

        # Per-node: collect edge sizes in window
        node_sizes: dict[int, set[int]] = defaultdict(set)
        node_edge_count: dict[int, int] = defaultdict(int)

        for eidx in active_edges:
            sz = int(hg.sizes[eidx])
            for r in self._nodes_of_edge(int(eidx)):
                nid = int(hg.node_ids[r])
                node_sizes[nid].add(sz)
                node_edge_count[nid] += 1

        # Bridge score: order_span × log(1 + degree)
        bridge_scores: list[tuple[int, float]] = []
        for nid, sizes_set in node_sizes.items():
            span  = len(sizes_set)
            deg   = node_edge_count[nid]
            score = span * math.log1p(deg)
            if span >= 2:  # must span at least 2 different sizes to be a bridge
                bridge_scores.append((nid, score))

        bridge_scores.sort(key=lambda x: -x[1])
        top_bridges = bridge_scores[:self._cfg.max_subgraph_nodes]

        prov_nodes: list[ProvenanceNode] = []
        for nid, score in top_bridges:
            span = len(node_sizes[nid])
            deg  = node_edge_count[nid]
            prov_nodes.append(ProvenanceNode(
                node_id=nid,
                attribution=score,
                hyperedge_count=deg,
                order_span=span,
                is_bridge=True,
                label=self._label(nid),
            ))

        narrative = self._narrative_bridge(prov_nodes, ws, we)

        build_ms = (time.perf_counter() - t0) * 1000
        return ProvenanceGraph(
            provenance_type = "bridge",
            description     = "Structural bridge nodes connecting sub-communities",
            window_start    = ws,
            window_end      = we,
            nodes           = prov_nodes,
            hyperedges      = [],
            feature_attrs   = [(n.label, n.attribution) for n in prov_nodes[:8]],
            narrative       = narrative,
            nodes_examined  = len(node_sizes),
            edges_examined  = len(active_edges),
            build_ms        = build_ms,
        )

    def _narrative_bridge(self, nodes: list[ProvenanceNode], ws: int, we: int) -> str:
        if not nodes:
            return "No bridge nodes identified — the graph may have a single connected community."

        top = nodes[:3]
        top_str = "; ".join(
            f"{n.label} (order_span={n.order_span}, {n.hyperedge_count} events)"
            for n in top
        )
        n_bridges = sum(1 for n in nodes if n.is_bridge)
        return (
            f"{n_bridges} bridge node(s) identified in ts {ws}–{we}. "
            f"Top bridges: {top_str}. "
            "Bridge nodes are active across multiple event sizes, connecting sub-communities "
            "that would otherwise be structurally isolated. In security contexts, bridges are "
            "often pivot points, shared credentials, or common parent processes."
        )

    # ── 6. Full Explanation ───────────────────────────────────────────────────

    def build_full_explanation(
        self,
        findings: list[dict[str, Any]],
        window_start: int | None = None,
        window_end:   int | None = None,
    ) -> dict[str, Any]:
        """
        One-call comprehensive explanation combining all provenance types.

        Returns a dict with sections: pattern, top_entity, bridge, temporal.
        """
        t0 = time.perf_counter()
        ts_arr = self._hg.timestamps
        ws = int(window_start) if window_start is not None else int(ts_arr.min()) if len(ts_arr) else 0
        we = int(window_end)   if window_end   is not None else int(ts_arr.max()) if len(ts_arr) else 0

        sections: list[dict[str, Any]] = []

        # 1. Top pattern finding provenance
        top_finding = findings[0] if findings else None
        if top_finding:
            pg = self.build_pattern_provenance(top_finding, ws, we)
            sections.append({
                "section":   "pattern",
                "title":     f"Top finding: {top_finding.get('title', 'Unknown')}",
                "score":     float(top_finding.get("evidence", {}).get("z_score") or 0),
                "narrative": pg.narrative,
                "data":      pg.as_dict(),
            })

        # 2. Top entity (highest degree node)
        hg = self._hg
        mask = (hg.timestamps >= ws) & (hg.timestamps <= we)
        if mask.any():
            deg_counts: dict[int, int] = defaultdict(int)
            for eidx in np.where(mask)[0]:
                for r in self._nodes_of_edge(int(eidx)):
                    nid = int(hg.node_ids[r])
                    deg_counts[nid] += 1
            if deg_counts:
                top_nid = max(deg_counts, key=lambda k: deg_counts[k])
                ep = self.build_entity_provenance(top_nid, ws, we)
                sections.append({
                    "section":   "top_entity",
                    "title":     f"Most connected entity: {self._label(top_nid)}",
                    "score":     float(deg_counts[top_nid]),
                    "narrative": ep.narrative,
                    "data":      ep.as_dict(),
                })

        # 3. Bridge analysis
        bp = self.build_bridge_provenance(ws, we)
        sections.append({
            "section":   "bridge",
            "title":     "Bridge nodes (structural connectors)",
            "score":     float(bp.nodes[0].attribution) if bp.nodes else 0.0,
            "narrative": bp.narrative,
            "data":      bp.as_dict(),
        })

        # 4. Temporal evolution
        tp = self.build_temporal_provenance(ws, we)
        sections.append({
            "section":   "timeline",
            "title":     "Temporal evolution",
            "score":     0.0,
            "narrative": tp.narrative,
            "data":      tp.as_dict(),
        })

        # Assemble full narrative
        full_narrative = " | ".join(s["narrative"] for s in sections if s["narrative"])

        build_ms = (time.perf_counter() - t0) * 1000
        return {
            "sections":       sections,
            "full_narrative": full_narrative,
            "window_start":   ws,
            "window_end":     we,
            "build_ms":       round(build_ms, 2),
        }
