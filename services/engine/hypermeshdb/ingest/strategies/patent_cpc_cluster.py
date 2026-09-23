"""
Patent CPC Cluster Strategy
==============================
Groups patents sharing the same US Design Class into hyperedges.

Model
-----
For each unique class code C with patents [P1, P2, ..., Pk]:
  → hyperedge members = {P1, P2, ..., Pk}
  → formation = CPC_CLUSTER
  → weight    = k / max_k   (class density, normalised)
  → event_ts  = most recent patent date in the cluster

Why this model?
  Reveals the design space topology — which patent classes are
  crowded, which are under-explored, and how patents bridge classes.
  Multi-class patents (those appearing in multiple hyperedges)
  are cross-domain innovators.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import openpyxl

from .base import IngestStrategy, ParamSpec, StrategyResult, ProgressCallback

BUCKET_SECONDS = 86400 * 365

COL_PUB_NUM  = 0
COL_PUB_DATE = 1
COL_TITLE    = 4
COL_ASSIGNEE = 7
COL_US_CLASS = 12


class PatentCPCClusterStrategy(IngestStrategy):
    name        = "patent_cpc_cluster"
    label       = "CPC / US-Class Design Clusters"
    description = (
        "Groups patents by their US design classification code, exposing "
        "crowded design spaces and cross-domain innovators."
    )
    category    = "patent"
    param_specs = [
        ParamSpec(
            name="excel_files", label="Excel Files (Design1–6.xlsx)",
            type="file_list", default=[],
            description="Paths to Minesoft/PatBase Excel exports.",
            required=True,
        ),
        ParamSpec(
            name="target_table", label="Target Table Name",
            type="string", default="PATENT_CPC_CLUSTER",
            required=True,
        ),
        ParamSpec(
            name="class_level", label="Class Granularity Level",
            type="string", default="subclass",
            options=["class", "subclass", "full"],
            description=(
                "'class' uses D14, 'subclass' uses D14/100, "
                "'full' uses the complete code."
            ),
        ),
        ParamSpec(
            name="min_patents", label="Minimum Patents per Cluster",
            type="integer", default=2, min=2,
            description="Ignore single-patent classes.",
        ),
        ParamSpec(
            name="drop_existing", label="Drop Existing Table",
            type="boolean", default=True,
        ),
    ]

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
    def _trim_class(code: str, level: str) -> str:
        """Trim a class code to the requested granularity."""
        code = code.strip()
        if level == "class":
            return code.split("/")[0].split(".")[0]
        if level == "subclass":
            parts = code.split("/")
            if len(parts) >= 2:
                return f"{parts[0]}/{parts[1].split('.')[0]}"
            return parts[0]
        return code

    def run(
        self,
        config:      dict,
        conn:        Any,
        db_dir:      str,
        progress_cb: ProgressCallback = lambda p, m: None,
    ) -> StrategyResult:

        excel_files  = config.get("excel_files", [])
        table        = config.get("target_table", "PATENT_CPC_CLUSTER").upper()
        class_level  = config.get("class_level", "subclass")
        min_patents  = int(config.get("min_patents", 2))
        drop_existing = config.get("drop_existing", True)
        notes: list[str] = []

        progress_cb(0.02, "Parsing Excel files …")

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
                    "type":     "patent",
                    "raw":      key,
                    "display":  key,
                    "short":    key[:24],
                    "title":    title,
                    "assignee": assignee,
                    "us_class": us_class,
                }
                next_id[0] += 1
            elif title and not entity_meta[entity_to_id[key]].get("title"):
                entity_meta[entity_to_id[key]].update(
                    {"title": title, "assignee": assignee, "us_class": us_class}
                )
            return entity_to_id[key]

        # class_code → [(patent_id, ts)]
        clusters: dict[str, list[tuple[int, int]]] = defaultdict(list)
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
                raw_class = str(row[COL_US_CLASS]) if ncols > COL_US_CLASS and row[COL_US_CLASS] else ""

                if not raw_class:
                    continue

                pid = get_or_create(pub_num, title, assignee, raw_class)
                ts  = self._to_epoch(pub_date)

                # Each class code in a semicolon/comma-separated list
                for code_raw in raw_class.replace(";", ",").split(","):
                    code = self._trim_class(code_raw, class_level)
                    if code:
                        clusters[code].append((pid, ts))

            wb.close()

        # Build hyperedges
        raw_hedges: list[dict] = []
        for code, entries in clusters.items():
            # deduplicate patents within same cluster
            seen: set[int] = set()
            unique: list[tuple[int, int]] = []
            for pid, ts in entries:
                if pid not in seen:
                    seen.add(pid)
                    unique.append((pid, ts))
            if len(unique) < min_patents:
                continue
            latest_ts = max(ts for _, ts in unique)
            raw_hedges.append({
                "ts":        latest_ts,
                "formation": "CPC_CLUSTER",
                "weight_n":  len(unique),
                "members":   [pid for pid, _ in unique],
                "class":     code,
            })

        max_w = max((h["weight_n"] for h in raw_hedges), default=1) or 1
        for h in raw_hedges:
            h["weight"] = round(h["weight_n"] / max_w, 6)

        notes.append(
            f"Clusters: {len(raw_hedges)} classes, "
            f"{len(entity_to_id)} unique patents"
        )

        progress_cb(0.35, f"Preparing table {table} …")
        if drop_existing:
            self.drop_table_directory(db_dir, table)
            try:
                conn.execute(f"DROP HYPEREDGE TABLE {table}")
            except Exception:
                pass
        conn.execute(
            f"CREATE HYPEREDGE TABLE {table} (Entity, Entity) "
            f"BUCKET_SECONDS {BUCKET_SECONDS}"
        )

        all_hedges: list[dict] = []
        for h in raw_hedges:
            all_hedges.extend(self.split_hedge(h))

        progress_cb(0.40, f"Inserting {len(all_hedges)} cluster hyperedges …")

        COMMIT_EVERY = 2000
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
                progress_cb(
                    0.40 + 0.50 * ((idx + 1) / max(len(all_hedges), 1)),
                    f"Inserted {inserted:,} …"
                )
        conn.commit()

        progress_cb(0.92, "Writing entity map …")
        emap_path = self.write_entity_map(entity_meta, db_dir, table)
        progress_cb(1.0, "Done")
        return StrategyResult(
            table=table,
            entities=len(entity_to_id),
            hyperedges=inserted,
            errors=errors,
            formations=["CPC_CLUSTER"],
            entity_map_path=emap_path,
            elapsed_s=time.time() - t0,
            notes=notes,
        )
