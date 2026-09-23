"""
HyperDx graph serving helper.

Loads ``data/Mayo/outputs/hypermesh_graph.json`` (produced by Mayo's
``export_for_hypermesh.py``) and serves a slim, browser-friendly version
to the Graph Explorer UI.

Key concerns:

* The raw file is ~17 MB; the slim version drops per-node ``features``
  dicts (heaviest payload) and converts ``member_ids`` strings into
  pure ``member_indices`` integers. Result ≈ 3 MB.
* ``readable_features`` is computed server-side once and cached so the
  UI gets clinician-readable strings (e.g. ``AIDS/HIV + cerebrovascular
  disease``) instead of raw tuples.
* Lookup tables for fast O(1) drill-down (edge → members,
  patient → edges) are precomputed at load time.

The module is import-safe even when the Mayo dataset is missing: all
endpoints simply return 404.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── Clinician-readable feature labels (mirrors data/Mayo/src/step10_explain.py
#    and scripts/ingest_mayo_v2.py — keep in sync if either changes) ──────────

FEATURE_LABELS = {
    "mi": "myocardial infarction", "chf": "congestive heart failure",
    "pvd": "peripheral vascular disease", "dementia": "dementia",
    "cvd": "cerebrovascular disease", "copd": "COPD",
    "ild": "interstitial lung disease", "asthma": "asthma",
    "ctd": "connective tissue disease", "diabetes": "diabetes mellitus",
    "pud": "peptic ulcer disease", "liver_failure": "liver failure",
    "ckd": "chronic kidney disease", "cancer": "solid malignancy",
    "leukemia": "leukemia", "lymphoma": "lymphoma", "aids": "AIDS/HIV",
    "hypertension": "hypertension", "dialysis": "dialysis",
    "immunodeficiency": "immunodeficiency",
    "valvular_dysf": "valvular dysfunction",
    "crp_final": "CRP", "ldh_final": "LDH", "ferritin_final": "Ferritin",
    "procalcitonin_final": "Procalcitonin", "wbc": "WBC",
    "lymph": "Lymphocytes", "neutrop": "Neutrophils", "plt": "Platelets",
    "hgb_low_final": "Hemoglobin", "albumin_final": "Albumin",
    "creatinine_final": "Creatinine", "ast_final": "AST",
    "alt_final": "ALT", "alp_final": "ALP", "inr_final": "INR",
    "bun_final": "BUN", "sodium": "Sodium", "potassium": "Potassium",
    "glucose": "Glucose", "calcium": "Calcium",
    "bicarbonate_final": "Bicarbonate", "dimer_final": "D-dimer",
    "magnesium_final": "Magnesium", "fibrinogen_final": "Fibrinogen",
    "pt_final": "PT", "htc_final": "Hematocrit",
    "who_region_res": "WHO region", "country_of_res": "country of residence",
    "ethnicity": "ethnicity", "race": "race", "ruca_bin": "rural/urban",
}

WHO_REGION = {
    "0.0": "AFR", "1.0": "AMR", "2.0": "EMR",
    "3.0": "EUR", "4.0": "SEAR", "5.0": "WPR",
}

FAMILY_DESC = {
    "F1_comorbidity":  "Comorbidity co-occurrence",
    "F2_lab_abnormal": "Lab-panel abnormality",
    "F3_volatility":   "Lab-range volatility",
    "F4_geo":          "Geo-epidemiologic",
}


def readable_features(feature_str: str, family: str | None = None) -> str:  # noqa: ARG001
    """Convert raw ``features`` cell → clinician-readable string."""
    if not feature_str or feature_str == "nan":
        return ""
    try:
        feat = str(feature_str).strip("()[]'\" ")
        parts = [p.strip().strip("'\"") for p in feat.split(",")]
        if len(parts) == 1 and "+" in parts[0]:
            parts = [p.strip() for p in parts[0].split("+")]
        nice: list[str] = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            handled = False
            for suffix in ("_HIGH", "_LOW"):
                if p.endswith(suffix):
                    base = p[: -len(suffix)]
                    arrow = "↑" if suffix == "_HIGH" else "↓"
                    nice.append(f"{FEATURE_LABELS.get(base, base)} {arrow}")
                    handled = True
                    break
            if handled:
                continue
            if "Range" in p:
                base = p.replace("Range", "").strip()
                nice.append(f"{FEATURE_LABELS.get(base, base)} volatile")
            elif "=" in p:
                k, v = p.split("=", 1)
                k = k.strip()
                v = v.strip()
                k_label = FEATURE_LABELS.get(k, k)
                if k == "who_region_res":
                    v = WHO_REGION.get(v, v)
                nice.append(f"{k_label}={v}")
            else:
                nice.append(FEATURE_LABELS.get(p, p))
        return " + ".join(nice)
    except Exception:
        return str(feature_str)[:80]


# ── Dataset registry ──────────────────────────────────────────────────────────
# Each entry maps a HyperMesh table name (case-insensitive) → on-disk graph file.

_REPO_ROOT = Path(__file__).resolve().parent.parent

DATASETS = {
    # User can override via env var HYPERDX_MAYO_GRAPH.
    "MAYOCLINICAL": Path(
        os.environ.get(
            "HYPERDX_MAYO_GRAPH",
            str(_REPO_ROOT / "data" / "Mayo" / "outputs" / "hypermesh_graph.json"),
        )
    ),
}


class _GraphCache:
    """In-memory cache of slim graphs + lookup tables, one per table.

    We pay the JSON parse + slim cost once at startup (lazy on first request)
    and then serve subsequent requests entirely from memory.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}

    def get(self, table: str) -> dict[str, Any] | None:
        tbl = table.upper()
        if tbl in self._cache:
            return self._cache[tbl]
        path = DATASETS.get(tbl)
        if path is None or not path.exists():
            log.info("HyperDx graph for table=%s not found at %s", tbl, path)
            return None
        with self._lock:
            if tbl in self._cache:                       # raced; another thread won
                return self._cache[tbl]
            log.info("Loading HyperDx graph for table=%s from %s", tbl, path)
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            slim = self._slim(raw)
            self._cache[tbl] = slim
            log.info(
                "HyperDx graph cached for table=%s: %d nodes / %d hyperedges",
                tbl, len(slim["nodes"]), len(slim["hyperedges"]),
            )
            return slim

    @staticmethod
    def _slim(raw: dict[str, Any]) -> dict[str, Any]:
        """Drop heavy node `features` dicts, convert member_ids → int indices."""
        # Build id → index map first (member_ids are strings, member_indices ints)
        id_to_idx = {n["id"]: int(n["index"]) for n in raw["nodes"]}

        # Slim nodes: drop features (the heavy per-node dict).
        slim_nodes: list[dict[str, Any]] = []
        for n in raw["nodes"]:
            slim_nodes.append({
                "id":       n["id"],
                "index":    int(n["index"]),
                "label":    n.get("label", "Unknown"),
                "label_id": int(n.get("label_id", -1)),
                "split":    n.get("split", "unknown"),
            })

        # Build hyperedges with:
        #   • compact member_indices (int[])
        #   • readable_features string
        #   • Drop redundant member_ids (string[]) — frontend uses indices only.
        slim_edges: list[dict[str, Any]] = []
        # patient_index -> [edge_id] lookup table for patient drill-down
        patient_edges: dict[int, list[int]] = {}

        for e in raw["hyperedges"]:
            # Prefer existing member_indices when present; fall back to mapping from ids.
            if "member_indices" in e and isinstance(e["member_indices"], list):
                members = [int(i) for i in e["member_indices"]]
            else:
                members = [id_to_idx[mid] for mid in e.get("member_ids", []) if mid in id_to_idx]

            edge_id = int(e["id"])
            family  = str(e.get("family", "unknown"))
            slim_edges.append({
                "id":                edge_id,
                "family":            family,
                "features":          e.get("features", ""),
                "readable_features": readable_features(e.get("features", ""), family),
                "support_train":     int(e.get("support_train", 0)),
                "support_all":       int(e.get("support_all", len(members))),
                "n_members":         int(e.get("n_members", len(members))),
                "color":             e.get("color"),
                "member_indices":    members,
                "attribution":       e.get("attribution", {}),
                "top_class":         e.get("top_class"),
                "max_attribution":   float(e.get("max_attribution", 0.0)),
            })

            for m in members:
                patient_edges.setdefault(m, []).append(edge_id)

        return {
            "meta":          raw["meta"],
            "nodes":         slim_nodes,
            "hyperedges":    slim_edges,
            "patient_edges": patient_edges,
        }


_CACHE = _GraphCache()


# ── Public API used by hypermeshdb/_api.py ────────────────────────────────────

def get_graph(table: str) -> dict[str, Any] | None:
    """Return slim graph for ``table`` (None if dataset unavailable)."""
    return _CACHE.get(table)


def get_graph_payload(table: str) -> dict[str, Any] | None:
    """Slim payload for the bubble view (no per-edge member arrays).

    Member arrays remain available via ``get_edge_members``.  Keeping them
    out of the initial payload saves ~1 MB on the wire for the 406-edge
    Mayo graph.
    """
    g = get_graph(table)
    if g is None:
        return None
    edges_no_members = [
        {k: v for k, v in e.items() if k != "member_indices"}
        for e in g["hyperedges"]
    ]
    return {
        "meta":       g["meta"],
        "nodes":      g["nodes"],
        "hyperedges": edges_no_members,
    }


def get_graph_full(table: str) -> dict[str, Any] | None:
    """Full slim payload INCLUDING per-edge ``member_indices``.

    Used by the polygon/hull/metro Lens view which renders ALL hyperedges
    at once and therefore needs every member list up front. Adds ≈ 80 KB
    over ``get_graph_payload`` for the 406-edge Mayo graph (still well
    under 4 MB total — acceptable).
    """
    g = get_graph(table)
    if g is None:
        return None
    return {
        "meta":       g["meta"],
        "nodes":      g["nodes"],
        "hyperedges": g["hyperedges"],   # already include member_indices
    }


def get_edge_members(table: str, edge_id: int) -> dict[str, Any] | None:
    """Members of a specific hyperedge (indices + matching node summaries)."""
    g = get_graph(table)
    if g is None:
        return None
    edge = next((e for e in g["hyperedges"] if e["id"] == edge_id), None)
    if edge is None:
        return None
    by_idx = {n["index"]: n for n in g["nodes"]}
    members = [by_idx[i] for i in edge["member_indices"] if i in by_idx]
    return {
        "edge_id":         edge_id,
        "family":          edge["family"],
        "features":        edge["features"],
        "readable":        edge["readable_features"],
        "support_train":   edge["support_train"],
        "support_all":     edge["support_all"],
        "top_class":       edge["top_class"],
        "max_attribution": edge["max_attribution"],
        "members":         members,
    }


def get_patient_edges(table: str, patient_index: int) -> dict[str, Any] | None:
    """All hyperedges containing a given patient (by 0-based index)."""
    g = get_graph(table)
    if g is None:
        return None
    edge_ids = g["patient_edges"].get(patient_index, [])
    node = next((n for n in g["nodes"] if n["index"] == patient_index), None)
    if node is None:
        return None
    edges_by_id = {e["id"]: e for e in g["hyperedges"]}
    # Sort by max_attribution desc — most discriminative first.
    edges = [edges_by_id[eid] for eid in edge_ids if eid in edges_by_id]
    edges.sort(key=lambda e: e["max_attribution"], reverse=True)
    return {
        "patient":   node,
        "n_edges":   len(edges),
        "edges": [{k: v for k, v in e.items() if k != "member_indices"} for e in edges],
    }


@lru_cache(maxsize=8)
def get_overlap_edges(table: str, min_overlap: int = 5) -> list[dict[str, int]]:
    """Compute inter-hyperedge overlap (shared patients).

    Returns list of ``{source, target, weight}`` for hyperedge pairs that
    share ``>= min_overlap`` patients.  Heavy to compute (≈100 ms for 406 edges
    × ~600 avg members) so memoised per (table, threshold).
    """
    g = get_graph(table)
    if g is None:
        return []
    edges = g["hyperedges"]
    # patient_index → list of edge_ids
    patient_edges = g["patient_edges"]

    # Pair-counts indexed by (smaller_eid, larger_eid)
    counts: dict[tuple[int, int], int] = {}
    for eids in patient_edges.values():
        # eids may contain duplicates if an edge was double-added; dedupe defensively.
        eids = sorted(set(eids))
        for i in range(len(eids)):
            for j in range(i + 1, len(eids)):
                key = (eids[i], eids[j])
                counts[key] = counts.get(key, 0) + 1

    overlaps: list[dict[str, int]] = []
    for (a, b), w in counts.items():
        if w >= min_overlap:
            overlaps.append({"source": a, "target": b, "weight": w})
    # Sort by weight desc for predictable rendering order.
    overlaps.sort(key=lambda x: -x["weight"])
    log.info("HyperDx overlap[%s]: %d pairs ≥ %d", table, len(overlaps), min_overlap)
    return overlaps
