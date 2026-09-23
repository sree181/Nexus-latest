"""
hypermeshdb/ingest/ciso.py — CISO investigation data → HyperMesh DB pipeline.

This module replaces the ad-hoc ``demo/ciso_connector.py`` raw-HmStore path
with a first-class ingestion pipeline that uses:

  - ``CREATE NODE TABLE Machine`` (Path B generic node schema)
  - ``Connection.copy_from_df()``  (Phase 2 ingestion)
  - ``Loader``                     (Stage-2 hyperedge construction)

Two source formats are supported:

hyperedges.json (primary)
    Pre-processed hyperedge records with four types:
    ``C2_Contact``, ``Alert_Chain``, ``Process_Cluster``,
    ``Temporal_CoActivity``.  Each entry already names its machines via
    ``member_labels``.  This is the canonical source for DB ingestion.

investigation.json (secondary / C2-only)
    High-level summary of C2 co-contact events grouped by shared IP.
    Only ``C2_Contact`` type hyperedges can be reconstructed from this
    file.  Useful for quick smoke tests and UI demos.

Usage
-----
::

    from hypermeshdb.ingest.ciso import CISOIngestion

    ing = CISOIngestion(hyperedges_json="data/hyperedges.json")
    db  = ing.open_or_create("/tmp/ciso_db")
    stats = ing.ingest(db)
    print(stats)
    # {'machines': 3, 'hyperedges': 1788, 'types': {...}}

    # Query directly
    qr = db.execute(
        "MATCH HYPEREDGE (he:CoProximity) "
        "WHERE he.event_ts >= 1739145600 AND he.event_ts <= 1740009600 RETURN *"
    )
    for row in qr:
        print(row)

    db.close()

Standalone script
-----------------
::

    python -m hypermeshdb.ingest.ciso \\
        --hyperedges data/hyperedges.json \\
        --db /tmp/ciso_db \\
        --stats
"""

from __future__ import annotations

import calendar
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ── Lazy pandas import ────────────────────────────────────────────────────────

def _pd():
    try:
        import pandas as pd
        return pd
    except ImportError:
        raise ImportError(
            "CISOIngestion requires pandas.  Run: pip install pandas"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_ts(ts_str: str) -> int:
    """Parse ``'YYYY-MM-DD HH:MM'`` → Unix epoch seconds (UTC)."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            return int(calendar.timegm(dt.timetuple()))
        except ValueError:
            continue
    return 0


def _encode_machines(member_labels_lists: list[list[str]]) -> dict[str, int]:
    """
    Build a stable machine-name → integer-ID mapping from all member_labels.
    IDs are assigned in sorted order of the machine name for determinism.
    """
    unique = sorted({
        m for labels in member_labels_lists for m in labels
    })
    return {name: idx for idx, name in enumerate(unique)}


# ── Machine schema ────────────────────────────────────────────────────────────

#: Column definitions for the Machine node table.
MACHINE_COLUMNS_DDL = (
    "machine_id INTEGER PRIMARY KEY, "
    "hostname   TEXT, "
    "os         TEXT DEFAULT '', "
    "role       TEXT DEFAULT ''"
)


# ── CISOIngestion ─────────────────────────────────────────────────────────────

class CISOIngestion:
    """
    Full CISO investigation → HyperMesh DB ingestion pipeline.

    Parameters
    ----------
    hyperedges_json:
        Path to ``data/hyperedges.json`` (preferred source — all 4 types).
    investigation_json:
        Path to ``data/investigation.json`` (C2-only alternative source).

    Exactly one of the two must be provided.
    """

    def __init__(
        self,
        *,
        hyperedges_json:   str | Path | None = None,
        investigation_json: str | Path | None = None,
    ) -> None:
        if hyperedges_json is None and investigation_json is None:
            raise ValueError(
                "CISOIngestion: provide either hyperedges_json or investigation_json."
            )
        if hyperedges_json is not None and investigation_json is not None:
            raise ValueError(
                "CISOIngestion: provide only one of hyperedges_json or investigation_json."
            )

        self._source: str
        self._path: Path

        if hyperedges_json is not None:
            self._source = "hyperedges"
            self._path   = Path(hyperedges_json)
        else:
            self._source = "investigation"
            self._path   = Path(investigation_json)  # type: ignore[arg-type]

        if not self._path.exists():
            raise FileNotFoundError(f"CISOIngestion: source file not found: {self._path}")

        self._payload: dict = json.loads(self._path.read_text())

    # ── Parsed records ────────────────────────────────────────────────────────

    def _parse_hyperedges_json(self) -> tuple[list[dict], dict[str, int]]:
        """
        Parse ``hyperedges.json`` → (hyperedge_records, machine_map).

        Each hyperedge record has:
            event_ts, members (list[int]), member_count, weight,
            mean_dist_m (0.0), formation (type string, max 32 chars)
        """
        machines_raw: list[str] = self._payload.get("machines", [])
        # Strip annotations like "\n(Linux/BAS)"
        machine_labels = [
            m.replace("\n", " ").split("(")[0].strip()
            for m in machines_raw
        ]

        events: list[dict] = self._payload.get("hyperedges", [])

        # Build the machine name → integer-ID map
        all_member_labels = [
            e.get("member_labels") or [
                machine_labels[i]
                for i in e.get("members", [])
                if i < len(machine_labels)
            ]
            for e in events
        ]
        # Supplement with top-level machine list
        all_member_labels.append(machine_labels)
        machine_map = _encode_machines(all_member_labels)

        records: list[dict] = []
        for evt in events:
            ml: list[str] = evt.get("member_labels") or [
                machine_labels[i]
                for i in evt.get("members", [])
                if i < len(machine_labels)
            ]
            ts  = _parse_ts(evt.get("first_ts", ""))
            ids = [machine_map[m] for m in ml if m in machine_map]
            if not ids:
                continue
            records.append({
                "event_ts":    ts,
                "members":     ids,
                "member_count": len(ids),
                "weight":      float(evt.get("event_count", 1)),
                "mean_dist_m": 0.0,
                "formation":   evt.get("type", "")[:32],
            })

        return records, machine_map

    def _parse_investigation_json(self) -> tuple[list[dict], dict[str, int]]:
        """
        Parse ``investigation.json`` → (hyperedge_records, machine_map).

        Only C2_Contact hyperedges are reconstructed (one per top_ip entry).
        """
        top_ips: list[dict] = self._payload.get("top_ips", [])

        # Collect all machine names to build stable IDs
        all_machines: list[list[str]] = [e.get("machines", []) for e in top_ips]
        machine_map  = _encode_machines(all_machines)

        records: list[dict] = []
        for entry in top_ips:
            machines = entry.get("machines", [])
            ids      = [machine_map[m] for m in machines if m in machine_map]
            if not ids:
                continue
            ts = _parse_ts(entry.get("first", ""))
            records.append({
                "event_ts":    ts,
                "members":     ids,
                "member_count": len(ids),
                "weight":      float(entry.get("total_events", 1)),
                "mean_dist_m": 0.0,
                "formation":   "C2_Contact",
            })

        return records, machine_map

    def records(self) -> tuple[list[dict], dict[str, int]]:
        """
        Return ``(hyperedge_records, machine_map)`` from the configured source.
        """
        if self._source == "hyperedges":
            return self._parse_hyperedges_json()
        return self._parse_investigation_json()

    # ── Seed CSV ──────────────────────────────────────────────────────────────

    def write_seed_csv(self, output_path: str | Path) -> Path:
        """
        Write a minimal seed CSV file that ``HmStore.build()`` needs to
        initialise the TPI index.  Uses the first 10 hyperedge records spread
        across different timestamps so the TPI has non-trivial bucket coverage.

        Parameters
        ----------
        output_path:
            Where to write the CSV (created / overwritten).

        Returns
        -------
        Path
            The path of the written file.
        """
        records, _ = self.records()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["event_ts", "members", "member_count",
                        "weight", "mean_dist_m", "formation"])
            for rec in records[:10]:
                w.writerow([
                    rec["event_ts"],
                    str(rec["members"]),
                    rec["member_count"],
                    rec["weight"],
                    rec["mean_dist_m"],
                    rec["formation"],
                ])

        return output_path

    # ── DB factory ────────────────────────────────────────────────────────────

    def open_or_create(self, db_dir: str | Path) -> Any:
        """
        Open an existing HyperMesh DB at *db_dir* or create a new one and
        ingest all CISO data.

        Returns
        -------
        hypermeshdb.Connection
            An open connection.  Caller is responsible for closing it.
        """
        _root = Path(__file__).parent.parent.parent
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))

        import hypermeshdb

        db_dir    = Path(db_dir)
        # Phase 6: detect existing DB by checking for any per-table partition
        # subdirectory that contains a built index.
        def _db_exists() -> bool:
            # Legacy flat layout (pre-Phase-6)
            if (db_dir / "bucket_directory.bin").exists():
                return True
            # Partitioned layout (Phase 6+)
            if db_dir.is_dir():
                for sub in db_dir.iterdir():
                    if sub.is_dir() and (sub / "bucket_directory.bin").exists():
                        return True
            return False

        if _db_exists():
            # Re-open existing DB; skip ingestion
            conn = hypermeshdb.connect(str(db_dir))
            print(f"[CISOIngestion] Reopened existing DB: {db_dir}", flush=True)
            return conn

        # First run: build TPI from seed CSV, then ingest everything
        seed_csv = db_dir.parent / "ciso_seed.csv"
        self.write_seed_csv(seed_csv)

        conn = hypermeshdb.connect(
            str(db_dir),
            hyperedges_csv = str(seed_csv),
            bucket_seconds = 3600,
        )
        self.ingest(conn)
        conn.compact()
        print(
            f"[CISOIngestion] DB created at {db_dir}  "
            f"records={conn.total_records}  wal_pending={conn.wal_pending}",
            flush=True,
        )
        return conn

    # ── Core ingestion ────────────────────────────────────────────────────────

    def ingest(self, conn: Any) -> dict[str, Any]:
        """
        Load all CISO machines and hyperedges into an already-open *conn*.

        Steps
        -----
        1. Register the ``Machine`` node table (``CREATE NODE TABLE``) if
           it does not already exist.
        2. Load machine nodes via ``copy_from_df``.
        3. Load hyperedge records via ``copy_from_df``.
        4. Return a stats dict.

        Parameters
        ----------
        conn:
            An open :class:`~hypermeshdb.Connection`.

        Returns
        -------
        dict
            Keys: ``machines``, ``hyperedges``, ``types``.
        """
        pd = _pd()
        he_records, machine_map = self.records()

        # ── Step 1: Machine node table ────────────────────────────────────
        existing_tables = [
            t["name"].upper()
            for t in conn._schema.list_node_tables()
        ]
        if "MACHINE" not in existing_tables:
            conn.execute(
                f"CREATE NODE TABLE Machine ({MACHINE_COLUMNS_DDL})"
            )

        # ── Step 2: Machine nodes ─────────────────────────────────────────
        machine_rows = [
            {"machine_id": mid, "hostname": name, "os": "", "role": "endpoint"}
            for name, mid in sorted(machine_map.items(), key=lambda kv: kv[1])
        ]
        machine_df = pd.DataFrame(machine_rows)
        machine_result = conn.copy_from_df(machine_df, "Machine")
        n_machines = machine_result.fetchone()["rows_loaded"]

        # ── Step 3: Hyperedges ────────────────────────────────────────────
        he_df = pd.DataFrame(he_records)
        he_result = conn.copy_from_df(he_df, "CoProximity")
        n_he  = he_result.fetchone()["rows_loaded"]

        # Count by formation (= type)
        type_counts: dict[str, int] = {}
        for rec in he_records:
            t = rec["formation"]
            type_counts[t] = type_counts.get(t, 0) + 1

        stats = {
            "machines":   n_machines,
            "hyperedges": n_he,
            "types":      type_counts,
        }

        print(
            f"[CISOIngestion] Loaded {n_machines} machines, "
            f"{n_he} hyperedges  {type_counts}",
            flush=True,
        )
        return stats

    # ── Convenience query helpers ─────────────────────────────────────────────

    @staticmethod
    def query_machines(conn: Any) -> list[dict]:
        """Return all Machine nodes as a list of frontend dicts."""
        try:
            store = conn._get_node_store("Machine")
            return store.get_all()
        except Exception:
            return []

    @staticmethod
    def query_hyperedges_by_range(
        conn: Any,
        ts_start: int,
        ts_end:   int,
    ) -> list[dict]:
        """
        Query the TPI for hyperedges in [ts_start, ts_end].
        Returns a list of plain dicts (JSON-serialisable).
        """
        qr = conn.execute(
            "MATCH HYPEREDGE (he:CoProximity) "
            f"WHERE he.event_ts >= {ts_start} AND he.event_ts <= {ts_end} RETURN *"
        )
        rows = []
        for row in qr:
            rows.append({
                "event_ts":    row["event_ts"],
                "members":     row["members"],
                "weight":      row["weight"],
                "formation":   row["formation"],
                "member_count": row.get("member_count", len(row["members"])) if hasattr(row, "get") else len(row["members"]),
            })
        return rows

    @staticmethod
    def db_stats(conn: Any) -> dict:
        """Return a compact stats dict from an open Connection."""
        return {
            "total_records": conn.total_records,
            "wal_pending":   conn.wal_pending,
            "bucket_count":  conn.bucket_count,
        }


# ── CLI entry-point ───────────────────────────────────────────────────────────

def _main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="CISO → HyperMesh DB ingestion (standalone)"
    )
    p.add_argument("--hyperedges",    default=None, help="Path to hyperedges.json")
    p.add_argument("--investigation", default=None, help="Path to investigation.json")
    p.add_argument("--db",            required=True, help="HyperMesh DB directory")
    p.add_argument("--stats",         action="store_true", help="Print DB stats after ingest")
    p.add_argument("--seed-csv",      default=None, help="Write seed CSV to this path and exit")
    args = p.parse_args()

    if args.hyperedges is None and args.investigation is None:
        p.error("Provide --hyperedges or --investigation.")

    ing = CISOIngestion(
        hyperedges_json    = args.hyperedges,
        investigation_json = args.investigation,
    )

    if args.seed_csv:
        path = ing.write_seed_csv(args.seed_csv)
        print(f"Seed CSV written → {path}")
        return

    conn = ing.open_or_create(args.db)
    if args.stats:
        stats = CISOIngestion.db_stats(conn)
        print(f"DB stats: {stats}")
    conn.close()


if __name__ == "__main__":
    _main()
