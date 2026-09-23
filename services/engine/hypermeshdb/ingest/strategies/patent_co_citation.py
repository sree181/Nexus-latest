"""
Patent Co-Citation Strategy
============================
Transforms Minesoft/PatBase design patent Excel exports into
CO_CITATION hyperedges.

Model
-----
For each primary patent P that cites [B1, B2, ..., Bn]:
  → hyperedge members = {B1, B2, ..., Bn}   (the CITED patents)
  → formation = CO_CITATION
  → weight    = n / max_n   (normalised depth)
  → event_ts  = P's publication date

Why this model?
  A hyperedge groups patents that are cited TOGETHER, not a star
  around the primary patent.  This creates genuine polyadic structure:
  node_degree(B) = how many contexts B was cited in.
  Co-occurrence(B1,B2) = how often they appear in the same reference list.
  This is the foundation for cluster analysis, gap detection and
  novelty scoring.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import openpyxl

from .base import IngestStrategy, ParamSpec, StrategyResult, ProgressCallback

BUCKET_SECONDS = 86400 * 30

COL_PUB_NUM  = 0
COL_PUB_DATE = 1
COL_TITLE    = 4
COL_ASSIGNEE = 7
COL_US_CLASS = 12
COL_BACKWARD = 16
COL_FORWARD  = 17


class PatentCoCitationStrategy(IngestStrategy):
    name        = "patent_co_citation"
    label       = "Patent Co-Citation Clusters"
    description = (
        "Groups patents cited together by the same primary patent into "
        "polyadic hyperedges, enabling cluster and proximity analysis."
    )
    category    = "patent"
    param_specs = [
        ParamSpec(
            name="excel_files", label="Excel Files (Design1–6.xlsx)",
            type="file_list",
            default=[],
            description=(
                "Paths to Minesoft/PatBase Excel exports. "
                "Each file must contain a sheet named 'Export'."
            ),
            required=True,
        ),
        ParamSpec(
            name="target_table", label="Target Table Name",
            type="string", default="PATENT_CO_CITATION",
            description="Name of the HyperMesh table to create.",
            required=True,
        ),
        ParamSpec(
            name="include_forward", label="Include Forward Citations",
            type="boolean", default=True,
            description=(
                "Also create CO_FORWARD_CITATION edges from the forward "
                "citation lists (patents citing the primary)."
            ),
        ),
        ParamSpec(
            name="min_members", label="Minimum Cited Patents per Hyperedge",
            type="integer", default=2, min=2, max=63,
            description=(
                "Drop hyperedges with fewer than this many cited patents. "
                "Singleton citations carry no polyadic signal."
            ),
        ),
        ParamSpec(
            name="drop_existing", label="Drop Existing Table",
            type="boolean", default=True,
            description="Drop the target table before ingestion if it exists.",
        ),
    ]

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_epoch(val) -> int:
        if isinstance(val, datetime):
            return int(val.replace(tzinfo=timezone.utc).timestamp())
        try:
            dt = datetime.fromisoformat(str(val).replace(" ", "T").split(".")[0])
            return int(dt.replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            return 0

    @staticmethod
    def _parse_citations(raw) -> list[str]:
        if not raw:
            return []
        return [p.strip() for p in str(raw).split("\n") if p.strip()]

    # ── main ──────────────────────────────────────────────────────────────────

    def run(
        self,
        config:      dict,
        conn:        Any,
        db_dir:      str,
        progress_cb: ProgressCallback = lambda p, m: None,
    ) -> StrategyResult:

        excel_files     = config.get("excel_files", [])
        table           = config.get("target_table", "PATENT_CO_CITATION").upper()
        include_forward = config.get("include_forward", True)
        min_members     = int(config.get("min_members", 2))
        drop_existing   = config.get("drop_existing", True)

        progress_cb(0.02, "Parsing Excel files …")
        notes: list[str] = []

        # ── Phase 1: parse Excel ──────────────────────────────────────────────
        entity_to_id: dict[str, int] = {}
        entity_meta:  dict[int, dict] = {}
        next_id = [1]

        def get_or_create(pnum: str, title="", assignee="", us_class="") -> int:
            key = pnum.strip()
            if not key:
                return -1
            if key not in entity_to_id:
                entity_to_id[key] = next_id[0]
                entity_meta[next_id[0]] = {
                    "type":     "primary_patent" if title else "patent",
                    "raw":      key,
                    "display":  key,
                    "short":    key[:24],
                    "title":    title,
                    "assignee": assignee,
                    "us_class": us_class,
                }
                next_id[0] += 1
            elif title and not entity_meta[entity_to_id[key]].get("title"):
                m = entity_meta[entity_to_id[key]]
                m.update({"type": "primary_patent", "title": title, "assignee": assignee, "us_class": us_class})
            return entity_to_id[key]

        raw_hedges: list[dict] = []
        n_files = len(excel_files)

        for fi, xls_path in enumerate(excel_files):
            if not os.path.exists(xls_path):
                notes.append(f"SKIP (not found): {xls_path}")
                continue
            progress_cb(0.02 + 0.30 * (fi / n_files), f"Reading {Path(xls_path).name} …")
            wb = openpyxl.load_workbook(str(xls_path), read_only=True, data_only=True)
            ws = wb["Export"]

            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i == 0:
                    continue
                pub_num = row[COL_PUB_NUM]
                if not pub_num:
                    continue
                ncols    = len(row)
                pub_num  = str(pub_num).strip()
                pub_date = row[COL_PUB_DATE] if ncols > COL_PUB_DATE else None
                title    = str(row[COL_TITLE])    if ncols > COL_TITLE    and row[COL_TITLE]    else ""
                assignee = str(row[COL_ASSIGNEE]) if ncols > COL_ASSIGNEE and row[COL_ASSIGNEE] else ""
                us_class = str(row[COL_US_CLASS]) if ncols > COL_US_CLASS and row[COL_US_CLASS] else ""
                ts       = self._to_epoch(pub_date)

                # Register the primary patent (with full metadata)
                primary_id = get_or_create(pub_num, title, assignee, us_class)

                backward = self._parse_citations(row[COL_BACKWARD]) if ncols > COL_BACKWARD else []
                forward  = self._parse_citations(row[COL_FORWARD])  if ncols > COL_FORWARD  else []

                # CO_CITATION: primary patent + its backward citations together.
                # Including the primary as anchor means it IS a hyperedge member,
                # so Graph Explorer can navigate from it to its citation community.
                if len(backward) >= min_members:
                    raw_hedges.append({
                        "ts":        ts,
                        "formation": "CO_CITATION",
                        "weight_n":  len(backward),
                        "members":   [primary_id] + [get_or_create(c) for c in backward],
                    })

                # CO_FORWARD_CITATION: primary patent + all patents that cite it.
                if include_forward and len(forward) >= min_members:
                    raw_hedges.append({
                        "ts":        ts,
                        "formation": "CO_FORWARD_CITATION",
                        "weight_n":  len(forward),
                        "members":   [primary_id] + [get_or_create(c) for c in forward],
                    })

            wb.close()

        # Normalise weights
        max_w = max((h["weight_n"] for h in raw_hedges), default=1) or 1
        for h in raw_hedges:
            h["weight"] = round(h["weight_n"] / max_w, 6)

        notes.append(
            f"Parsed {len(entity_to_id)} entities, "
            f"{len(raw_hedges)} raw hyperedges from {n_files} file(s)"
        )

        # ── Phase 2: drop + create table ─────────────────────────────────────
        progress_cb(0.35, f"Preparing table {table} …")

        if drop_existing:
            # Remove the entire directory so no stale compacted files remain.
            self.drop_table_directory(db_dir, table)
            try:
                conn.execute(f"DROP HYPEREDGE TABLE {table}")
            except Exception:
                pass

        conn.execute(
            f"CREATE HYPEREDGE TABLE {table} (Entity, Entity) "
            f"BUCKET_SECONDS {BUCKET_SECONDS}"
        )

        # ── Phase 3: expand oversized edges, insert ───────────────────────────
        all_hedges: list[dict] = []
        for h in raw_hedges:
            all_hedges.extend(self.split_hedge(h))

        progress_cb(0.40, f"Inserting {len(all_hedges)} hyperedges …")

        COMMIT_EVERY = 5000
        inserted = errors = 0
        t0 = time.time()

        conn.begin()
        for idx, h in enumerate(all_hedges):
            try:
                w = float(h.get("weight", 0.0))
                members_sql = ",".join(str(m) for m in h["members"] if m != -1)
                if not members_sql:
                    continue
                conn.execute(
                    f"INSERT INTO {table} (event_ts, members, weight, mean_dist_m, formation) "
                    f"VALUES ({h['ts']}, [{members_sql}], {w:.6f}, 0.0, '{h['formation']}')"
                )
                inserted += 1
            except Exception as exc:
                errors += 1
                if errors <= 5:
                    notes.append(f"Row {idx} error: {exc}")

            if (idx + 1) % COMMIT_EVERY == 0:
                conn.commit()
                conn.begin()
                frac = 0.40 + 0.50 * ((idx + 1) / len(all_hedges))
                progress_cb(frac, f"Inserted {inserted:,} / {len(all_hedges):,} …")

        conn.commit()

        # ── Phase 4: entity map ───────────────────────────────────────────────
        progress_cb(0.92, "Writing entity map …")
        emap_path = self.write_entity_map(entity_meta, db_dir, table)

        elapsed = time.time() - t0
        formations = list({h["formation"] for h in all_hedges})

        progress_cb(1.0, "Done")
        return StrategyResult(
            table=table,
            entities=len(entity_to_id),
            hyperedges=inserted,
            errors=errors,
            formations=formations,
            entity_map_path=emap_path,
            elapsed_s=elapsed,
            notes=notes,
        )
