"""
hypermeshdb.ingest.strategies.mayo_clinical
===========================================
Ingestion strategy for the Mayo Clinic rare-disease hypergraph.

Reads the pre-computed artifacts from a Mayo pipeline output directory:
  • H_incidence.npz            – (N_patients × N_edges) sparse incidence matrix
  • hyperedge_metadata.csv     – per-edge family / feature / support metadata
  • split_metadata.json        – patient ordered IDs, splits, label_map

Creates one hyperedge table and one node table in the target DB:
  MayoClinical (Patient)
    PROPERTIES: edge_id, family, features, support_train, support_all,
                combo_size, cluster, chunk_index, total_chunks
  Patient  (node_id PK, record_id, label, disease, split)

Large hyperedges (>64 members) are split into contiguous non-overlapping
chunks so every row respects the HyperMesh member-count limit.

Parameters exposed to the UI
-----------------------------
  data_dir        Path to the Mayo pipeline root (contains an outputs/ sub-dir)
  table_name      Hyperedge table name (default: MayoClinical)
  node_table      Node table name (default: Patient)
  drop_existing   Drop existing tables before ingesting
  max_members     Maximum members per record (default: 64)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import scipy.sparse as sp

from .base import IngestStrategy, ParamSpec, StrategyResult

log = logging.getLogger(__name__)

DISEASE_MAP: dict[int, str] = {
    0: "Blastomycosis",
    1: "Cryptococcosis",
    2: "Histoplasmosis",
    3: "Mucormycosis",
    4: "Pneumocystosis",
    5: "Tuberculosis",
    6: "Control",
}

BUCKET_SECONDS    = 10
COMPACT_THRESHOLD = 1000
TS_STRIDE         = 200    # event_ts block size per original edge


class MayoClinicalStrategy(IngestStrategy):
    name        = "mayo_clinical"
    label       = "Mayo Rare-Disease Hypergraph"
    description = (
        "Ingest the Mayo Clinic rare-disease hypergraph (7 494 patients, "
        "406 clinical-phenotype hyperedges) from pipeline output artifacts."
    )
    category    = "clinical"
    param_specs = [
        ParamSpec(
            name        = "data_dir",
            label       = "Mayo data directory",
            type        = "string",
            default     = "data/Mayo",
            description = "Root directory of the Mayo pipeline (must contain an outputs/ sub-directory with H_incidence.npz etc.)",
            required    = True,
        ),
        ParamSpec(
            name        = "table_name",
            label       = "Hyperedge table name",
            type        = "string",
            default     = "MayoClinical",
            description = "Name for the created hyperedge table.",
        ),
        ParamSpec(
            name        = "node_table",
            label       = "Node table name",
            type        = "string",
            default     = "Patient",
            description = "Name for the created patient node table.",
        ),
        ParamSpec(
            name        = "drop_existing",
            label       = "Drop existing tables",
            type        = "boolean",
            default     = False,
            description = "If true, drop the hyperedge and node tables before re-ingesting.",
        ),
        ParamSpec(
            name        = "max_members",
            label       = "Max members per chunk",
            type        = "integer",
            default     = 64,
            description = "Maximum patients per hyperedge record.  Larger groups are split into sequential chunks.",
            min         = 2,
            max         = 64,
        ),
    ]

    # ─── private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _load(data_dir: Path) -> tuple:
        out = data_dir / "outputs"
        H   = sp.load_npz(out / "H_incidence.npz").tocsc()
        he_meta = pd.read_csv(out / "hyperedge_metadata.csv")
        with open(out / "split_metadata.json") as fh:
            split_meta = json.load(fh)
        ordered_ids: list[str] = [
            str(x)
            for x in split_meta["train_ids"]
            + split_meta["val_ids"]
            + split_meta["test_ids"]
        ]
        split_of: dict[str, str] = {}
        for pid in split_meta["train_ids"]:
            split_of[str(pid)] = "train"
        for pid in split_meta["val_ids"]:
            split_of[str(pid)] = "val"
        for pid in split_meta["test_ids"]:
            split_of[str(pid)] = "test"
        return H, he_meta, ordered_ids, split_of, split_meta

    @staticmethod
    def _build_hyperedges(
        H: sp.csc_matrix,
        he_meta: pd.DataFrame,
        max_members: int,
    ) -> pd.DataFrame:
        rows: list[dict] = []
        for edge_idx in range(H.shape[1]):
            members_all: list[int] = H.getcol(edge_idx).nonzero()[0].tolist()
            if not members_all:
                continue
            meta      = he_meta.iloc[edge_idx]
            n_chunks  = max(1, (len(members_all) + max_members - 1) // max_members)
            cluster_v = meta.get("cluster")
            cluster   = (
                ""
                if cluster_v is None
                or (isinstance(cluster_v, float) and np.isnan(cluster_v))
                else str(cluster_v)
            )
            for chunk_idx in range(n_chunks):
                chunk_members = members_all[
                    chunk_idx * max_members : (chunk_idx + 1) * max_members
                ]
                rows.append(
                    {
                        "event_ts":      edge_idx * TS_STRIDE + chunk_idx,
                        "members":       chunk_members,
                        "weight":        1.0,
                        "edge_id":       edge_idx,
                        "family":        str(meta.get("family", "")),
                        "features":      str(meta.get("features", "")),
                        "support_train": int(meta.get("support_train", 0)),
                        "support_all":   int(meta.get("support_all",   0)),
                        "combo_size":    int(meta.get("combo_size",    1)),
                        "cluster":       cluster,
                        "chunk_index":   chunk_idx,
                        "total_chunks":  n_chunks,
                    }
                )
        return pd.DataFrame(rows)

    @staticmethod
    def _build_nodes(
        ordered_ids: list[str],
        split_of:    dict[str, str],
        data_dir:    Path,
        split_meta:  dict | None = None,
    ) -> pd.DataFrame:
        out = data_dir / "outputs"
        label_map   = (split_meta or {}).get("label_map", {})
        name_to_int = {str(k): int(v) for k, v in label_map.items()}

        label_lookup:   dict[str, int] = {}
        disease_lookup: dict[str, str] = {}
        try:
            pp = pd.read_parquet(out / "preprocessed_structured.parquet")
            for col in ("label", "disease_class", "class"):
                if col in pp.columns:
                    id_col = (
                        "record_id" if "record_id" in pp.columns else pp.columns[0]
                    )
                    for _, row in pp[[id_col, col]].iterrows():
                        rid = str(row[id_col])
                        val = row[col]
                        if pd.isna(val):
                            lbl, dis = -1, ""
                        elif isinstance(val, str):
                            lbl = name_to_int.get(val, -1)
                            dis = val
                        else:
                            lbl = int(val)
                            dis = DISEASE_MAP.get(lbl, "")
                        label_lookup[rid]   = lbl
                        disease_lookup[rid] = dis
                    break
        except Exception:
            pass

        rows = []
        for vertex_idx, record_id in enumerate(ordered_ids):
            rid = str(record_id)
            lbl = label_lookup.get(rid, -1)
            rows.append(
                {
                    "node_id":   vertex_idx,
                    "record_id": rid,
                    "label":     lbl,
                    "disease":   disease_lookup.get(rid, DISEASE_MAP.get(lbl, "")),
                    "split":     split_of.get(rid, "unknown"),
                }
            )
        return pd.DataFrame(rows)

    # ─── public interface ─────────────────────────────────────────────────────

    def run(
        self,
        config:      dict[str, Any],
        conn:        Any,
        db_dir:      str,
        progress_cb: Callable[[float, str], None] | None = None,
    ) -> StrategyResult:
        def _progress(pct: float, msg: str) -> None:
            if progress_cb:
                progress_cb(pct, msg)
            else:
                log.info("  [%.0f%%] %s", pct * 100, msg)

        t_start = time.perf_counter()
        errors  = 0

        data_dir     = Path(config.get("data_dir", "data/Mayo"))
        table_name   = str(config.get("table_name",   "MayoClinical"))
        node_table   = str(config.get("node_table",    "Patient"))
        drop_existing = bool(config.get("drop_existing", False))
        max_members  = int(config.get("max_members",    64))
        max_members  = min(max(2, max_members), self.MAX_MEMBERS)

        required = data_dir / "outputs" / "H_incidence.npz"
        if not required.exists():
            raise FileNotFoundError(
                f"H_incidence.npz not found at {required}. "
                "Check the data_dir parameter."
            )

        # ── load ──────────────────────────────────────────────────────────────
        _progress(0.05, "Loading Mayo artifacts …")
        H, he_meta, ordered_ids, split_of, split_meta = self._load(data_dir)

        # ── transform ─────────────────────────────────────────────────────────
        _progress(0.15, "Building hyperedge rows …")
        he_df    = self._build_hyperedges(H, he_meta, max_members)
        nodes_df = self._build_nodes(ordered_ids, split_of, data_dir, split_meta)

        # ── create tables ─────────────────────────────────────────────────────
        _progress(0.25, "Creating DB tables …")

        if drop_existing:
            for stmt in (
                f"DROP HYPEREDGE TABLE {table_name}",
                f"DROP NODE TABLE {node_table}",
            ):
                try:
                    conn.execute(stmt)
                except Exception:
                    pass

        try:
            conn.execute(
                f"""
CREATE HYPEREDGE TABLE {table_name} ({node_table})
  PROPERTIES (
    edge_id       INT,
    family        TEXT,
    features      TEXT,
    support_train INT,
    support_all   INT,
    combo_size    INT,
    cluster       TEXT,
    chunk_index   INT,
    total_chunks  INT
  )
  BUCKET_SECONDS {BUCKET_SECONDS}
  COMPACT_THRESHOLD {COMPACT_THRESHOLD}
"""
            )
        except Exception as exc:
            log.warning("  Hyperedge table DDL: %s", exc)
            errors += 1

        try:
            conn.execute(
                f"""
CREATE NODE TABLE {node_table} (
  node_id   INTEGER PRIMARY KEY,
  record_id TEXT,
  label     INTEGER,
  disease   TEXT,
  split     TEXT
)
"""
            )
        except Exception as exc:
            log.warning("  Node table DDL: %s", exc)

        # ── ingest hyperedges ─────────────────────────────────────────────────
        _progress(0.35, f"Ingesting {len(he_df):,} hyperedge rows …")
        he_result = conn.copy_from_df(he_df, table_name)
        he_row    = (he_result.fetchone() or {})
        n_loaded  = int(he_row.get("rows_loaded", len(he_df)))
        n_skipped = int(he_row.get("rows_skipped", 0))
        errors   += n_skipped

        # ── ingest nodes ──────────────────────────────────────────────────────
        _progress(0.70, f"Ingesting {len(nodes_df):,} patient nodes …")
        conn.copy_from_df(nodes_df, node_table)

        # ── compact ───────────────────────────────────────────────────────────
        _progress(0.85, "Compacting WAL …")
        conn.compact(table=table_name)

        # ── entity map for workbench UI ───────────────────────────────────────
        _progress(0.92, "Writing entity map …")
        entity_map: dict[str, dict] = {}
        for _, row in nodes_df.iterrows():
            nid = str(row["node_id"])
            entity_map[nid] = {
                "type":    "patient",
                "raw":     row["record_id"],
                "display": f"Patient {row['record_id']}",
                "short":   str(row["record_id"])[:8],
                "disease": row["disease"],
                "split":   row["split"],
            }
        em_path = Path(db_dir) / f"{table_name.upper()}_entity_map.json"
        with open(em_path, "w") as fh:
            json.dump(entity_map, fh, separators=(",", ":"))

        # ── done ──────────────────────────────────────────────────────────────
        _progress(1.0, "Done.")
        elapsed = time.perf_counter() - t_start

        families = sorted(he_df["family"].unique().tolist())

        return StrategyResult(
            table           = table_name,
            entities        = len(nodes_df),
            hyperedges      = n_loaded,
            errors          = errors,
            formations      = families,
            entity_map_path = str(em_path),
            elapsed_s       = elapsed,
            notes           = [
                f"Original edges: {len(he_meta)} → {len(he_df)} rows after chunking (max {max_members} members)",
                f"Node table: {node_table} ({len(nodes_df)} patients)",
                f"Families: {', '.join(families)}",
            ],
        )
