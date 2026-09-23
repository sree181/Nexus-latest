"""
hypermeshdb.ingest.strategies.mde_baseline
==========================================
Microsoft Defender for Endpoint (MDE) telemetry → HyperMesh hyperedge pipeline.

Source format
-------------
Advanced Hunting CSV export from Microsoft 365 Defender.
Each row is one telemetry event with 66 columns including:

  Event Time              – ISO-8601 timestamp  (e.g. 2026-02-09T06:13:54.865)
  Machine Id              – SHA-1 of device GUID
  Computer Name           – hostname
  Action Type             – event verb (ProcessCreated, ConnectionSuccess, …)
  Initiating Process File Name
  Initiating Process SHA1
  Initiating Process Account Name
  Initiating Process Account Domain
  Remote IP               – populated for network events
  File Name               – populated for file events
  Folder Path             – populated for file events

Hyperedge formation
-------------------
Each row becomes ONE hyperedge.  Members are derived from the key entity
columns that are non-null in that row:

  [machine]  Computer Name           (always present)
  [process]  Initiating Process SHA1 → displayed as File Name
  [account]  domain\\accountname      (when non-null)
  [ip]       Remote IP               (when non-null, network events only)

  formation  = Action Type category  (see _FORMATION_MAP below)
  event_ts   = parsed Event Time     (UTC epoch seconds)
  weight     = 1.0 per event

Entity deduplication uses a stable key: ``type:raw_value``.
The entity map is compatible with the existing SYS_NANXCV entity map format.

Time-windowed grouping (optional)
----------------------------------
When ``window_minutes > 0``, events that share the same
(machine, process, account, formation) tuple within a time window are
collapsed into a single hyperedge with weight = event_count.  This reduces
hyperedge count from ~100 K → ~10–20 K for typical MDE exports and
aligns temporal resolution with SNN feature extraction windows.

Usage
-----
Via UI (IngestLab):
    Strategy name: "mde_baseline"

Via Python:
    from hypermeshdb.ingest.strategies.mde_baseline import MDEBaselineStrategy
    import hypermeshdb

    conn = hypermeshdb.connect("data")
    result = MDEBaselineStrategy().run(
        config={
            "table_name":      "NANXCV_BASELINE",
            "csv_paths":       ["data/Events_Log/nanxcv00f89340g 1.csv"],
            "window_minutes":  5,
            "exclude_cymulate": True,
        },
        conn=conn,
        db_dir="data",
    )
    print(result)
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import IngestStrategy, ParamSpec, StrategyResult

# ── Action Type → Formation category ─────────────────────────────────────────
#
# Collapses 100+ MDE action types into 8 semantically distinct formation
# labels.  This is important for:
#   • SNN feature vectors (distribution over formation types per window)
#   • Temporal changepoint detection (shifts in formation mix signal attacks)
#   • Hyperedge visualisation (colour-by-formation)

_FORMATION_MAP: dict[str, str] = {
    # Process lifecycle
    "ProcessCreated":                        "PROCESS_EXEC",
    "ProcessTerminated":                     "PROCESS_EXEC",
    # File operations
    "FileCreated":                           "FILE_OPS",
    "FileDeleted":                           "FILE_OPS",
    "FileDeletionActivityWasObserved":       "FILE_OPS",
    "FileModified":                          "FILE_OPS",
    "FileRenamed":                           "FILE_OPS",
    "FileAccessDenied":                      "FILE_OPS",
    # Network connections
    "ConnectionSuccess":                     "NETWORK_CONN",
    "ConnectionFailed":                      "NETWORK_CONN",
    "NetworkSignatureInspected":             "NETWORK_CONN",
    "NetworkFilterConnectionInfoProtocolNonStandardPort": "NETWORK_CONN",
    "InboundInternetScanInspected":          "NETWORK_CONN",
    "ListeningConnectionCreated":            "NETWORK_CONN",
    # DNS
    "DnsConnectionInspected":               "DNS_LOOKUP",
    # HTTP
    "HttpConnectionInspected":              "HTTP_TRAFFIC",
    # Registry
    "RegistryValueSet":                     "REGISTRY_OPS",
    "RegistryValueDeleted":                 "REGISTRY_OPS",
    "RegistryKeyCreated":                   "REGISTRY_OPS",
    "RegistryKeyDeleted":                   "REGISTRY_OPS",
    "RegistryKeyRenamed":                   "REGISTRY_OPS",
    # Logon / auth
    "LogonSuccess":                         "LOGON",
    "LogonFailed":                          "LOGON",
    "LogonAttempted":                       "LOGON",
    # Script / code execution
    "ScriptContent":                        "SCRIPT_EXEC",
    "AntivirusDetection":                   "SCRIPT_EXEC",
    "AntivirusRemediation":                 "SCRIPT_EXEC",
    "SuspiciousScriptDetected":             "SCRIPT_EXEC",
    # Image load
    "ImageLoaded":                          "MODULE_LOAD",
    # Named pipe
    "NamedPipeEvent":                       "IPC_PIPE",
    # Unix specifics
    "UnixFileAccess":                       "FILE_OPS",
    "UnixPipedCommandExecution":            "SCRIPT_EXEC",
    "UnixExploratoryCommand":               "SCRIPT_EXEC",
    "UnixScriptExecution":                  "SCRIPT_EXEC",
    "UnixExecutablePermissionAdded":        "SCRIPT_EXEC",
}


def _action_to_formation(action_type: str) -> str:
    """Map a raw ActionType string to one of 8 formation categories."""
    mapped = _FORMATION_MAP.get(action_type)
    if mapped:
        return mapped
    at = action_type.lower()
    if "process" in at:  return "PROCESS_EXEC"
    if "file" in at:     return "FILE_OPS"
    if "network" in at or "connection" in at: return "NETWORK_CONN"
    if "dns" in at:      return "DNS_LOOKUP"
    if "http" in at:     return "HTTP_TRAFFIC"
    if "registry" in at: return "REGISTRY_OPS"
    if "logon" in at:    return "LOGON"
    if "script" in at:   return "SCRIPT_EXEC"
    return "GENERIC"


# ── Timestamp parser ──────────────────────────────────────────────────────────

import datetime as _dt
import calendar as _cal

def _parse_ts(s: str) -> int:
    """Parse ISO-8601 string (with or without timezone/microseconds) → UTC epoch."""
    s = (s or "").strip()
    if not s:
        return 0
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = _dt.datetime.strptime(s[:26], fmt)
            return int(_cal.timegm(dt.timetuple()))
        except ValueError:
            continue
    return 0


def _clean(v: Any) -> str:
    """Strip and normalise a cell value; return '' for nulls."""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("", "none", "nan", "null", "n/a") else s


# ── Entity registry ───────────────────────────────────────────────────────────

class _EntityRegistry:
    """
    Stable string → integer entity ID mapper.
    Keys are namespaced by entity type so the same string used in two
    different column roles gets a different ID.

    Compatible with the SYS_NANXCV entity_map JSON format:
        { "<id>": {"type": ..., "raw": ..., "display": ..., "short": ...} }
    """

    def __init__(self) -> None:
        self._key_to_id: dict[str, int] = {}
        self._meta: dict[int, dict] = {}
        self._next_id = 1

    def get_or_create(self, etype: str, raw: str) -> int:
        if not raw:
            return 0
        key = f"{etype}:{raw.lower()}"
        if key not in self._key_to_id:
            nid = self._next_id
            self._next_id += 1
            self._key_to_id[key] = nid
            display, short = _derive_labels(etype, raw)
            self._meta[nid] = {
                "type":    etype,
                "raw":     raw,
                "display": display,
                "short":   short,
            }
        return self._key_to_id[key]

    def export_map(self) -> dict[str, dict]:
        return {str(nid): info for nid, info in self._meta.items()}

    def __len__(self) -> int:
        return self._next_id - 1


def _derive_labels(etype: str, raw: str) -> tuple[str, str]:
    """Return (display, short) labels given entity type and raw value."""
    if etype == "machine":
        # Computer Name is already human-readable
        return raw, raw[:20]
    if etype == "process":
        # Prefer the filename portion of a path
        fname = Path(raw).name if ("/" in raw or "\\" in raw) else raw
        return fname, fname[:20]
    if etype == "account":
        # Strip domain prefix  "domain\\user" → "user"
        parts = re.split(r"[/\\]", raw)
        name  = parts[-1] if parts else raw
        return name, name[:20]
    if etype == "ip":
        return raw, raw[:20]
    return raw, raw[:20]


# ── Cymulate detection ────────────────────────────────────────────────────────

_CYMULATE_RE = re.compile(r"cymulate", re.I)

def _is_cymulate_row(row: dict) -> bool:
    """Return True if any key field references Cymulate tooling."""
    fields = (
        row.get("Initiating Process File Name", ""),
        row.get("Initiating Process Folder Path", ""),
        row.get("Process Command Line", ""),
        row.get("Folder Path", ""),
        row.get("File Name", ""),
        row.get("Initiating Process Command Line", ""),
    )
    return any(_CYMULATE_RE.search(f or "") for f in fields)


# ── Core record parser ────────────────────────────────────────────────────────

@dataclass
class _RawHyperedge:
    event_ts:   int
    machine:    str
    process:    str       # Initiating Process File Name (human-readable)
    proc_sha1:  str       # Initiating Process SHA1 (used as entity key)
    account:    str       # domain\\user
    remote_ip:  str
    formation:  str
    weight:     float     = 1.0


def _parse_row(row: dict) -> _RawHyperedge | None:
    """Convert one MDE CSV row dict to a _RawHyperedge.  Returns None if unusable."""
    event_ts = _parse_ts(_clean(row.get("Event Time", "")))
    if not event_ts:
        return None

    machine = _clean(row.get("Computer Name", "")) or _clean(row.get("Machine Id", ""))
    if not machine:
        return None

    # Process: use SHA1 as the stable entity key, file name for display
    proc_sha1 = _clean(row.get("Initiating Process SHA1", ""))
    proc_fname = _clean(row.get("Initiating Process File Name", ""))
    # Fall back to file name as key if no SHA1
    proc_key = proc_sha1 or proc_fname

    # Account: "domain\\user"
    domain  = _clean(row.get("Initiating Process Account Domain", ""))
    accname = _clean(row.get("Initiating Process Account Name", ""))
    if domain and accname:
        account = f"{domain}\\{accname}"
    elif accname:
        account = accname
    else:
        account = ""

    remote_ip = _clean(row.get("Remote IP", ""))
    action    = _clean(row.get("Action Type", ""))
    formation = _action_to_formation(action) if action else "GENERIC"

    return _RawHyperedge(
        event_ts  = event_ts,
        machine   = machine.lower(),      # normalise hostname case
        process   = proc_fname,
        proc_sha1 = proc_key,
        account   = account.lower() if account else "",
        remote_ip = remote_ip,
        formation = formation,
    )


# ── Window aggregation ────────────────────────────────────────────────────────

def _bucket_ts(epoch: int, window_s: int) -> int:
    """Round epoch down to the nearest window boundary."""
    return (epoch // window_s) * window_s


# ── File loader (CSV + XLSX) ──────────────────────────────────────────────────

def _load_file(path: Path) -> list[dict]:
    """
    Load an MDE Advanced Hunting export (CSV or XLSX) into a list of row dicts.
    Returns only rows where 'Computer Name' or 'Event Time' are plausibly valid
    (screens out corrupted rows sometimes present in large XLSX exports).
    """
    suffix = path.suffix.lower()

    if suffix in (".xlsx", ".xls"):
        try:
            import pandas as pd
            # Use dtype=str to avoid pandas type coercion issues on mixed columns
            df = pd.read_excel(str(path), dtype=str, keep_default_na=False)
            # Drop rows where Event Time is clearly garbage (longer than 35 chars)
            if "Event Time" in df.columns:
                df = df[df["Event Time"].str.len().le(35)]
            return df.to_dict(orient="records")
        except ImportError:
            raise ValueError("XLSX support requires pandas+openpyxl: pip install pandas openpyxl")

    # Default: CSV
    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(row)
    return rows


def _date_str_to_epoch(s: str, start: bool) -> int:
    """
    Convert 'YYYY-MM-DD' to a UTC epoch integer.
    start=True  → midnight at start of day (00:00:00)
    start=False → end of day (23:59:59)
    Returns 0 if the string is empty or unparseable (= no filter).
    """
    s = s.strip()
    if not s:
        return 0
    try:
        dt = _dt.datetime.strptime(s[:10], "%Y-%m-%d")
        if not start:
            dt = dt.replace(hour=23, minute=59, second=59)
        return int(_cal.timegm(dt.timetuple()))
    except ValueError:
        return 0


# ── Strategy ──────────────────────────────────────────────────────────────────

class MDEBaselineStrategy(IngestStrategy):
    """
    Microsoft Defender for Endpoint CSV → HyperMesh Hyperedge table.

    Designed specifically for the CISO SNN+Hypergraph story:
    - Each row (or time-windowed group) becomes one hyperedge.
    - Members: [machine, process, account, ip?]
    - Formation: categorised Action Type (8 classes for SNN feature extraction)
    - Compatible entity map format with existing SYS_NANXCV table.
    """

    name        = "mde_baseline"
    label       = "MDE Baseline Telemetry"
    description = (
        "Ingest Microsoft Defender for Endpoint (MDE) Advanced Hunting CSV exports "
        "as time-windowed hyperedges.  Supports baseline and attack-phase datasets."
    )
    category    = "security"

    param_specs = [
        ParamSpec(
            name        = "table_name",
            label       = "Target Table Name",
            type        = "string",
            default     = "NANXCV_BASELINE",
            description = "HyperMesh table to create (e.g. NANXCV_BASELINE, SYS_NANXCV)",
            required    = True,
        ),
        ParamSpec(
            name        = "csv_paths",
            label       = "CSV / XLSX File Paths",
            type        = "file_list",
            default     = [],
            description = "One or more MDE Advanced Hunting CSV or XLSX export files.",
            required    = True,
        ),
        ParamSpec(
            name        = "window_minutes",
            label       = "Time Window (minutes)",
            type        = "integer",
            default     = 5,
            min         = 0,
            max         = 60,
            description = (
                "Group events sharing the same (machine, process, account, formation) "
                "within this many minutes into one hyperedge.  0 = one hyperedge per row."
            ),
        ),
        ParamSpec(
            name        = "exclude_cymulate",
            label       = "Exclude Cymulate Events",
            type        = "boolean",
            default     = True,
            description = (
                "If True, filter Cymulate from every file. If a list of bools, one flag "
                "per csv_paths entry (same order). Example: [True,True,True,False] for "
                "multi-file fleet ingest with Cymulate off on the last file only."
            ),
        ),
        ParamSpec(
            name        = "include_ip_entity",
            label       = "Include Remote IP as Member",
            type        = "boolean",
            default     = True,
            description = (
                "Add Remote IP as a 4th hyperedge member for network events. "
                "Useful for seeing lateral movement patterns."
            ),
        ),
        ParamSpec(
            name        = "date_from",
            label       = "Date From (YYYY-MM-DD, optional)",
            type        = "string",
            default     = "",
            description = "Only ingest events on or after this UTC date. Leave blank for no filter.",
        ),
        ParamSpec(
            name        = "date_to",
            label       = "Date To (YYYY-MM-DD, optional)",
            type        = "string",
            default     = "",
            description = "Only ingest events on or before this UTC date. Leave blank for no filter.",
        ),
        ParamSpec(
            name        = "drop_existing",
            label       = "Drop Existing Table",
            type        = "boolean",
            default     = True,
            description = "If True, delete the table directory before ingestion.",
        ),
    ]

    # ── main entry point ───────────────────────────────────────────────────────

    def run(
        self,
        config:      dict,
        conn:        Any,
        db_dir:      str,
        progress_cb  = lambda p, m: None,
    ) -> StrategyResult:
        t0 = time.time()

        table          = str(config.get("table_name", "MDE_BASELINE")).upper().replace(" ", "_")
        csv_paths      = config.get("csv_paths") or []
        window_min     = int(config.get("window_minutes", 5))
        window_s       = window_min * 60 if window_min > 0 else 0
        _excl = config.get("exclude_cymulate", True)
        if isinstance(_excl, (list, tuple)):
            excl_cym_list = [bool(x) for x in _excl]
            if len(excl_cym_list) < len(csv_paths):
                excl_cym_list.extend(
                    [excl_cym_list[-1]] * (len(csv_paths) - len(excl_cym_list))
                )
            excl_cym_list = excl_cym_list[: len(csv_paths)]
        else:
            excl_cym_list = [bool(_excl)] * len(csv_paths)
        incl_ip        = bool(config.get("include_ip_entity", True))
        drop_existing  = bool(config.get("drop_existing", True))

        # Optional date window filter (epoch bounds)
        ts_from = _date_str_to_epoch(str(config.get("date_from", "") or ""), start=True)
        ts_to   = _date_str_to_epoch(str(config.get("date_to",   "") or ""), start=False)

        if not csv_paths:
            raise ValueError("MDEBaselineStrategy: no CSV paths provided.")

        # ── Step 1: Drop & recreate table ─────────────────────────────────────
        progress_cb(0.02, f"Preparing table {table}…")
        if drop_existing:
            self.drop_table_directory(db_dir, table)
        try:
            conn.execute(f"DROP HYPEREDGE TABLE {table}")
        except Exception:
            pass
        conn.execute(f"CREATE HYPEREDGE TABLE {table} (Entity, Entity)")

        # ── Step 2: Load all CSV files ─────────────────────────────────────────
        progress_cb(0.05, f"Loading {len(csv_paths)} CSV file(s)…")
        all_raw: list[_RawHyperedge] = []
        skipped_cymulate = 0
        load_errors      = 0

        for i, fpath in enumerate(csv_paths):
            p = Path(fpath)
            excl_cym = excl_cym_list[i] if i < len(excl_cym_list) else excl_cym_list[-1]
            if not p.exists():
                load_errors += 1
                progress_cb(0.05, f"  ⚠ File not found: {p}")
                continue
            progress_cb(0.05, f"  Reading {p.name}…")
            try:
                rows = _load_file(p)
                for row in rows:
                    if excl_cym and _is_cymulate_row(row):
                        skipped_cymulate += 1
                        continue
                    he = _parse_row(row)
                    if he is None:
                        continue
                    # Apply date filter if specified
                    if ts_from and he.event_ts < ts_from:
                        continue
                    if ts_to and he.event_ts > ts_to:
                        continue
                    all_raw.append(he)
            except Exception as exc:
                load_errors += 1
                progress_cb(0.05, f"  ⚠ Error reading {p.name}: {exc}")

        total_raw = len(all_raw)
        progress_cb(0.20, f"Parsed {total_raw:,} usable rows "
                          f"({skipped_cymulate:,} Cymulate rows excluded).")

        if not all_raw:
            raise ValueError(
                f"MDEBaselineStrategy: no usable rows found in {csv_paths}. "
                "Check column names match MDE Advanced Hunting export format."
            )

        # ── Step 3: Build entity registry ─────────────────────────────────────
        progress_cb(0.25, "Building entity registry…")
        reg = _EntityRegistry()

        for he in all_raw:
            reg.get_or_create("machine", he.machine)
            if he.proc_sha1:
                nid = reg.get_or_create("process", he.proc_sha1)
                # Backfill display name if this SHA1 was registered without one
                meta = reg._meta.get(nid)
                if meta and not meta["display"] and he.process:
                    meta["display"] = he.process
                    meta["short"]   = he.process[:20]
            if he.account:
                reg.get_or_create("account", he.account)
            if incl_ip and he.remote_ip:
                reg.get_or_create("ip", he.remote_ip)

        progress_cb(0.35, f"Entity registry: {len(reg):,} unique entities.")

        # ── Step 4: Aggregate into hyperedges ──────────────────────────────────
        progress_cb(0.40, "Aggregating into hyperedges…")

        if window_s > 0:
            hyperedges = _aggregate_windowed(all_raw, reg, window_s, incl_ip)
        else:
            hyperedges = _build_per_row(all_raw, reg, incl_ip)

        progress_cb(0.55, f"Formed {len(hyperedges):,} hyperedges "
                          f"(window={window_min}min, raw={total_raw:,}).")

        # ── Step 5: Insert into DB ─────────────────────────────────────────────
        progress_cb(0.60, f"Inserting {len(hyperedges):,} hyperedges into {table}…")
        inserted = _insert_hyperedges(conn, table, hyperedges, progress_cb)

        # ── Step 6: Compact ────────────────────────────────────────────────────
        progress_cb(0.90, "Compacting table…")
        try:
            conn.compact(table=table)
        except Exception:
            pass

        # ── Step 7: Persist entity map ─────────────────────────────────────────
        progress_cb(0.95, "Saving entity map…")
        entity_map = reg.export_map()
        entity_map_path = self.write_entity_map(reg._meta, db_dir, table)

        elapsed = round(time.time() - t0, 2)
        formations = sorted({h["formation"] for h in hyperedges})

        progress_cb(1.0,
            f"Done.  {inserted:,} hyperedges, {len(reg):,} entities, "
            f"{elapsed}s.  Table: {table}"
        )

        date_note = ""
        if ts_from or ts_to:
            import datetime as _dt2
            lo = _dt2.datetime.utcfromtimestamp(ts_from).strftime('%Y-%m-%d') if ts_from else "start"
            hi = _dt2.datetime.utcfromtimestamp(ts_to).strftime('%Y-%m-%d')   if ts_to   else "end"
            date_note = f"Date filter: {lo} → {hi}"

        return StrategyResult(
            table           = table,
            entities        = len(reg),
            hyperedges      = inserted,
            errors          = load_errors,
            formations      = formations,
            entity_map_path = entity_map_path,
            elapsed_s       = elapsed,
            notes           = [
                f"Raw rows parsed: {total_raw:,}",
                f"Cymulate rows excluded: {skipped_cymulate:,}",
                f"Time window: {window_min} min",
                *(([date_note]) if date_note else []),
                f"Formations: {', '.join(formations)}",
            ],
        )


# ── Aggregation helpers ───────────────────────────────────────────────────────

def _build_per_row(
    raw:    list[_RawHyperedge],
    reg:    _EntityRegistry,
    incl_ip: bool,
) -> list[dict]:
    """One hyperedge per raw event row."""
    hedges: list[dict] = []
    for he in raw:
        members = _members_for(he, reg, incl_ip)
        if len(members) < 2:
            continue
        hedges.append({
            "event_ts":    he.event_ts,
            "members":     members,
            "member_count": len(members),
            "weight":      1.0,
            "formation":   he.formation,
        })
    return hedges


def _aggregate_windowed(
    raw:     list[_RawHyperedge],
    reg:     _EntityRegistry,
    window_s: int,
    incl_ip:  bool,
) -> list[dict]:
    """
    Group events by (bucket_ts, machine_id, proc_id, account_id, formation)
    within each time window, summing weight.  Remote IPs within the same
    window are accumulated in a single hyperedge only if they are consistent.
    """
    from collections import defaultdict

    # key → (event_ts_bucket, members_set, weight_sum)
    buckets: dict[tuple, list] = defaultdict(lambda: [0, {}, 0.0])

    for he in raw:
        machine_id  = reg.get_or_create("machine", he.machine)
        proc_id     = reg.get_or_create("process", he.proc_sha1) if he.proc_sha1 else 0
        account_id  = reg.get_or_create("account", he.account) if he.account else 0
        ip_id       = (reg.get_or_create("ip", he.remote_ip)
                       if incl_ip and he.remote_ip else 0)

        bucket = _bucket_ts(he.event_ts, window_s)
        key    = (bucket, machine_id, proc_id, account_id, he.formation)

        entry = buckets[key]
        entry[0] = bucket              # ts_bucket
        # Accumulate member IDs (use dict for stable ordering; value is count)
        for mid in [machine_id, proc_id, account_id, ip_id]:
            if mid:
                entry[1][mid] = entry[1].get(mid, 0) + 1
        entry[2] += 1.0               # weight = event count

    hedges: list[dict] = []
    for (bucket, _, _, _, formation), entry in buckets.items():
        members = list(entry[1].keys())   # unique member IDs, ordered
        if len(members) < 2:
            continue
        hedges.append({
            "event_ts":     bucket,
            "members":      members,
            "member_count": len(members),
            "weight":       entry[2],
            "formation":    formation,
        })

    # Sort by timestamp for TPI efficiency
    hedges.sort(key=lambda h: h["event_ts"])
    return hedges


def _members_for(
    he:      _RawHyperedge,
    reg:     _EntityRegistry,
    incl_ip: bool,
) -> list[int]:
    """Build the ordered, deduplicated member list for a single raw hyperedge."""
    seen: set[int] = set()
    members: list[int] = []

    def _add(nid: int) -> None:
        if nid and nid not in seen:
            seen.add(nid)
            members.append(nid)

    _add(reg.get_or_create("machine", he.machine))
    if he.proc_sha1:
        _add(reg.get_or_create("process", he.proc_sha1))
    if he.account:
        _add(reg.get_or_create("account", he.account))
    if incl_ip and he.remote_ip:
        _add(reg.get_or_create("ip", he.remote_ip))

    return members


# ── DB insertion ──────────────────────────────────────────────────────────────

_MAX_MEMBERS = 64   # mirrors HM_MAX_MEMBERS in C core


def _split_hedge(h: dict) -> list[dict]:
    """
    Split a hyperedge that exceeds _MAX_MEMBERS into valid chunks.
    Anchor = first member (the machine); rest are sliced into 63-member chunks.
    """
    members = h["members"]
    if len(members) <= _MAX_MEMBERS:
        return [h]
    anchor   = members[0]
    rest     = members[1:]
    chunk_sz = _MAX_MEMBERS - 1
    chunks   = []
    for i in range(0, len(rest), chunk_sz):
        chunks.append({**h, "members": [anchor] + rest[i : i + chunk_sz]})
    return chunks


def _insert_hyperedges(
    conn,
    table:   str,
    hedges:  list[dict],
    progress_cb,
) -> int:
    """
    Bulk-insert hyperedges in transactional batches.
    Oversized hyperedges (> 64 members) are automatically split.
    Returns the total number of hyperedges inserted.
    """
    BATCH = 500
    COMPACT_EVERY = 25_000
    inserted = errors = 0

    # Expand any oversized hyperedges before counting
    expanded: list[dict] = []
    for h in hedges:
        expanded.extend(_split_hedge(h))
    n = len(expanded)

    conn.execute("BEGIN")
    try:
        for i, h in enumerate(expanded):
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
                    import logging
                    logging.getLogger(__name__).warning(
                        "insert error row %d: %s | sql=%s", i, exc, sql[:200]
                    )

            if (i + 1) % BATCH == 0:
                conn.execute("COMMIT")
                conn.execute("BEGIN")
                pct = 0.60 + 0.28 * ((i + 1) / n)
                progress_cb(pct, f"  Inserted {inserted:,}/{n:,}…")

            if inserted > 0 and inserted % COMPACT_EVERY == 0:
                conn.execute("COMMIT")
                try:
                    conn.compact(table=table)
                except Exception:
                    pass
                conn.execute("BEGIN")

        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    return inserted
