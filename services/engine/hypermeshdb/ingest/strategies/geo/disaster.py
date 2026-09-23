"""
hypermeshdb.ingest.strategies.geo.disaster
============================================
Catastrophe Edition · Day 8 — load per-building damage scores into a
HyperMesh hyperedge table.

Source format
-------------
Parquet table produced by ``scripts/cat/validate.py`` with one row per
building and columns:
    building_id, source, lon, lat, area_m2,
    dino_cos_dist, visual_diff, severity, xbd_label, dist_to_water_m

Hypergraph schema (formation_vocab=geo_v1)
------------------------------------------
Entity types:
    event     : 1     (e.g. Hurricane Helene 2024)
    aoi       : 1     (e.g. Big Bend FL coastal)
    building  : N     (one per row in scores parquet)

Hyperedges:
    1                                     EVENT_AT_AOI(event, aoi)
    ceil(N / (MAX_MEMBERS - 1))           AOI_HOSTS_BUILDING(aoi, *buildings)
    N                                     DAMAGE_PAIR(building, event)        ← weight = severity

Per-building rich attributes (severity, label, distance, encoder distances)
are written to a sidecar Parquet so they can be queried alongside the
hypergraph without hitting the C core for property scans.

Sidecar files (always next to db_dir):
    <TABLE>_entity_map.json
    <TABLE>_attributes.parquet
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..base import IngestStrategy, ParamSpec, StrategyResult


# ── Helene landfall ─────────────────────────────────────────────────────────
HELENE_LANDFALL_TS = int(datetime(2024, 9, 26, 23, 10, 0).timestamp())  # 7:10 PM EDT


class GeoDisasterStrategy(IngestStrategy):
    """Ingest a per-building damage Parquet into a HyperMesh hyperedge table."""

    name        = "geo_disaster"
    label       = "Geospatial Disaster Damage"
    description = "Load satellite-derived per-building damage scores into a temporal hypergraph."
    category    = "geospatial"
    param_specs = [
        ParamSpec(
            name="table_name", label="Table Name", type="string",
            default="HELENE_BIG_BEND_2024", required=True,
            description="HyperMesh table name (uppercase, no spaces).",
        ),
        ParamSpec(
            name="scores_parquet", label="Scores Parquet Path", type="string",
            default="data/geo_v1/scores/big_bend_scores_with_distance.parquet",
            required=True,
            description="Output of scripts/cat/validate.py — one row per building.",
        ),
        ParamSpec(
            name="event_id", label="Event ID", type="string",
            default="helene_2024", required=True,
        ),
        ParamSpec(
            name="event_name", label="Event Display Name", type="string",
            default="Hurricane Helene 2024",
        ),
        ParamSpec(
            name="event_kind", label="Event Kind", type="string",
            default="hurricane",
            options=["hurricane", "wildfire", "earthquake", "flood", "tornado"],
        ),
        ParamSpec(
            name="landfall_ts", label="Landfall (UTC epoch seconds)", type="integer",
            default=HELENE_LANDFALL_TS,
            description="Used as event_ts on the EVENT_AT_AOI hyperedge.",
        ),
        ParamSpec(
            name="aoi_id", label="AOI ID", type="string",
            default="big_bend", required=True,
        ),
        ParamSpec(
            name="aoi_name", label="AOI Display Name", type="string",
            default="Big Bend FL coastal",
        ),
        ParamSpec(
            name="aoi_bbox", label="AOI bbox [minlon,minlat,maxlon,maxlat]", type="string",
            default="-83.50,29.55,-83.15,29.80",
        ),
        ParamSpec(
            name="drop_existing", label="Drop Existing Table", type="boolean",
            default=True,
        ),
    ]

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_bbox(s: str) -> list[float]:
        parts = [p.strip() for p in s.replace("[", "").replace("]", "").split(",")]
        return [float(p) for p in parts]

    @staticmethod
    def _short_id(s: str, n: int = 16) -> str:
        return s if len(s) <= n else s[:n - 1] + "…"

    # ── main ─────────────────────────────────────────────────────────────────

    def run(
        self,
        config:      dict,
        conn:        Any,
        db_dir:      str,
        progress_cb=lambda p, m: None,
    ) -> StrategyResult:
        t0 = time.time()
        notes: list[str] = []

        table         = str(config.get("table_name", "HELENE_BIG_BEND_2024")).upper().replace(" ", "_")
        scores_path   = Path(config.get("scores_parquet", ""))
        event_id      = str(config["event_id"])
        event_name    = str(config.get("event_name", event_id))
        event_kind    = str(config.get("event_kind", "hurricane"))
        landfall_ts   = int(config.get("landfall_ts", HELENE_LANDFALL_TS))
        aoi_id        = str(config["aoi_id"])
        aoi_name      = str(config.get("aoi_name", aoi_id))
        bbox          = self._parse_bbox(str(config.get("aoi_bbox", "-83.50,29.55,-83.15,29.80")))
        drop_existing = bool(config.get("drop_existing", True))

        if not scores_path.exists():
            raise FileNotFoundError(f"scores parquet not found: {scores_path}")

        # ── Step 1 — drop + create
        progress_cb(0.02, f"Preparing table {table}…")
        if drop_existing:
            self.drop_table_directory(db_dir, table)
        try:
            conn.execute(f"DROP HYPEREDGE TABLE {table}")
        except Exception:
            pass
        conn.execute(f"CREATE HYPEREDGE TABLE {table} (Entity, Entity)")

        # ── Step 2 — read scores
        progress_cb(0.05, f"Reading {scores_path.name}…")
        df = pd.read_parquet(scores_path)
        n_buildings = len(df)
        print(f"[ingest] {n_buildings} buildings to ingest", flush=True)

        # ── Step 3 — entity registry
        # ID 1 = event, ID 2 = aoi, ID 3.. = buildings
        entity_meta: dict[int, dict] = {
            1: {
                "type":    "event",
                "raw":     event_id,
                "display": event_name,
                "short":   event_id,
                "kind":    event_kind,
                "landfall_ts": landfall_ts,
            },
            2: {
                "type":    "aoi",
                "raw":     aoi_id,
                "display": aoi_name,
                "short":   aoi_id,
                "bbox":    bbox,
            },
        }
        building_ids: list[int] = []
        for i, row in enumerate(df.itertuples(index=False)):
            eid = 3 + i
            building_ids.append(eid)
            entity_meta[eid] = {
                "type":    "building",
                "raw":     str(row.building_id),
                "display": self._short_id(str(row.building_id)),
                "short":   self._short_id(str(row.building_id), 12),
                "lon":     float(row.lon),
                "lat":     float(row.lat),
                "area_m2": float(row.area_m2),
                "source":  str(row.source),
            }

        # ── Step 4 — build hyperedges
        progress_cb(0.30, f"Building hyperedges…")
        hedges: list[dict] = []

        # 4.1  EVENT_AT_AOI
        hedges.append({
            "event_ts":  landfall_ts,
            "members":   [1, 2],
            "weight":    1.0,
            "formation": "EVENT_AT_AOI",
        })

        # 4.2  AOI_HOSTS_BUILDING — wide hyperedge, anchor = aoi
        host_h = {
            "event_ts":  landfall_ts,
            "members":   [2] + building_ids,
            "weight":    1.0,
            "formation": "AOI_HOSTS_BUILDING",
        }
        hedges.extend(self.split_hedge(host_h))

        # 4.3  DAMAGE_PAIR — one per building
        for eid, row in zip(building_ids, df.itertuples(index=False)):
            hedges.append({
                "event_ts":  landfall_ts,
                "members":   [eid, 1],
                "weight":    float(row.severity),
                "formation": "DAMAGE_PAIR",
            })

        print(f"[ingest] hyperedges to write: "
              f"{sum(h['formation']=='EVENT_AT_AOI' for h in hedges)} EVENT_AT_AOI, "
              f"{sum(h['formation']=='AOI_HOSTS_BUILDING' for h in hedges)} AOI_HOSTS_BUILDING (after split), "
              f"{sum(h['formation']=='DAMAGE_PAIR' for h in hedges)} DAMAGE_PAIR", flush=True)

        # ── Step 5 — bulk insert
        progress_cb(0.40, f"Inserting {len(hedges):,} hyperedges…")
        BATCH = 500
        inserted = errors = 0
        conn.execute("BEGIN")
        try:
            for i, h in enumerate(hedges):
                members_str = ",".join(str(m) for m in h["members"])
                sql = (
                    f"INSERT INTO {table} (event_ts, members, weight, formation) "
                    f"VALUES ({h['event_ts']}, [{members_str}], "
                    f"{h['weight']}, '{h['formation']}')"
                )
                try:
                    conn.execute(sql)
                    inserted += 1
                except Exception as exc:
                    errors += 1
                    if errors <= 5:
                        print(f"[ingest] insert error #{errors}: {exc}  sql={sql[:160]}", flush=True)

                if (i + 1) % BATCH == 0:
                    conn.execute("COMMIT")
                    conn.execute("BEGIN")
                    pct = 0.40 + 0.45 * ((i + 1) / len(hedges))
                    progress_cb(pct, f"  Inserted {inserted:,}/{len(hedges):,}…")

            conn.execute("COMMIT")
        except Exception:
            try:    conn.execute("ROLLBACK")
            except Exception: pass
            raise

        # ── Step 6 — compact + finalise
        progress_cb(0.88, "Compacting…")
        try:
            conn.compact(table=table)
        except Exception as exc:
            notes.append(f"compact warning: {exc}")

        # ── Step 7 — write entity map
        progress_cb(0.92, "Writing entity map…")
        entity_map_path = self.write_entity_map(entity_meta, db_dir, table)

        # ── Step 8 — write rich-attribute sidecar
        progress_cb(0.96, "Writing attribute sidecar…")
        attr_path = Path(db_dir) / f"{table}_attributes.parquet"
        attrs = df.copy()
        attrs.insert(0, "entity_id", building_ids)
        attrs["event_id"] = event_id
        attrs["aoi_id"]   = aoi_id
        attrs["table"]    = table
        attrs.to_parquet(attr_path, index=False)
        print(f"[ingest] attributes → {attr_path}  ({len(attrs)} rows)", flush=True)

        elapsed = time.time() - t0
        progress_cb(1.0, f"Done in {elapsed:.1f}s")

        return StrategyResult(
            table           = table,
            entities        = len(entity_meta),
            hyperedges      = inserted,
            errors          = errors,
            formations      = ["EVENT_AT_AOI", "AOI_HOSTS_BUILDING", "DAMAGE_PAIR"],
            entity_map_path = entity_map_path,
            elapsed_s       = elapsed,
            notes           = notes + [
                f"event={event_id} ({event_name})",
                f"aoi={aoi_id}  bbox={bbox}",
                f"sidecar={attr_path}",
            ],
        )
