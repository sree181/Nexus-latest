"""
generic.py — Generic file → HyperMesh hyperedge ingestion engine.

Supports two ingest modes:

MODE A — Tabular → Hyperedge  (CSV, TSV, XLSX, JSON, JSON-Lines, Parquet)
  Each row is an event; entity column values are resolved to integer node IDs
  and combined into a hyperedge.  Good for security telemetry, event logs.

MODE B — Native Hyperedge  (JSON-Lines with 'members' field, Cornell ARB tar.gz)
  Each row already IS a hyperedge with an explicit list of member node IDs.
  No entity column mapping needed.  Good for academic hypergraph datasets
  (DAWN, email-Enron, contact-high-school, …).

  Cornell ARB format (https://www.cs.cornell.edu/~arb/data/):
    <name>-nverts.txt    — one integer per line: simplex size
    <name>-simplices.txt — contiguous node IDs
    <name>-times.txt     — one timestamp per simplex
    <name>-node-labels.txt (optional) — "id  D00001  Drug Name"

Flow
────
1. load_records(file_bytes, filename)
   → (list[dict], total_row_count, metadata | None)
   metadata = {"ingest_mode": "native_hyperedge", "node_labels": {id: name}}

2. infer_schema(records, metadata, sample_n=200)
   → SchemaInference  (auto-detected column roles + sample hyperedges)
   For native mode: columns=[], ingest_mode="native_hyperedge", native_stats filled

3. run_ingestion(records, config, conn, entity_map_path, native_metadata)
   → IngestionResult  (inserted, errors, elapsed, entity_map saved)
"""
from __future__ import annotations

import datetime
import io
import json
import re
import tarfile
import time
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Type constants ────────────────────────────────────────────────────────────

COL_TIMESTAMP = "timestamp"
COL_ENTITY    = "entity"
COL_PROPERTY  = "property"
COL_SKIP      = "skip"

INGEST_TABULAR   = "tabular"
INGEST_NATIVE    = "native_hyperedge"

# Heuristic patterns for column-role detection
_TS_NAMES   = re.compile(r"(time|ts|timestamp|date|datetime|at|when|epoch)", re.I)
_ENTITY_NAMES = re.compile(
    r"(machine|device|host|endpoint|computer|"
    r"user|account|principal|"
    r"process|proc|sha|hash|"
    r"src_?ip|dst_?ip|remote_?ip|local_?ip|source_?ip|dest_?ip|ip_?addr|"
    r"domain|fqdn|url|uri)",
    re.I,
)
_SKIP_NAMES = re.compile(r"(index|row|seq|serial|line|num|#)", re.I)

_RE_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_RE_HASH = re.compile(r"^[0-9a-fA-F]{32,64}$")
_RE_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", re.I)

# Cornell ARB quarter-year timestamp range (year*4+quarter, 2000-2040)
_ARB_TS_MIN = 8001   # Q1 2000
_ARB_TS_MAX = 8200   # roughly Q4 2050
_Q1_2004_EPOCH = 1072915200  # 2004-01-01 00:00:00 UTC
_QUARTER_SECS  = 91 * 24 * 3600  # ~91 days per quarter

# Cornell ARB filename patterns
_ARB_NVERTS    = re.compile(r"nverts",    re.I)
_ARB_SIMPLICES = re.compile(r"simplices", re.I)
_ARB_TIMES     = re.compile(r"times",     re.I)
_ARB_LABELS    = re.compile(r"node.?labels|labels", re.I)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ColumnDef:
    name:          str
    role:          str           = COL_SKIP
    entity_type:   str           = "generic"
    sample_values: list[str]     = field(default_factory=list)
    null_pct:      float         = 0.0
    dtype_hint:    str           = "string"


@dataclass
class NativeStats:
    """Statistics returned for native hyperedge datasets."""
    total_simplices:         int
    multi_member_simplices:  int   # will be ingested (size >= 2)
    size_1_skipped:          int
    time_range_epoch:        list[int]          # [min_epoch, max_epoch]
    time_range_label:        list[str]          # human-readable
    unique_nodes:            int
    member_size_distribution: dict[str, int]   # "2": count, "3": count, …
    has_node_labels:         bool
    node_label_sample:       list[dict]         # [{id, name}, …] first 10


@dataclass
class SchemaInference:
    columns:               list[ColumnDef]
    row_count:             int
    suggested_ts_col:      str | None
    suggested_entity_cols: list[str]
    sample_hyperedges:     list[dict]
    ingest_mode:           str            = INGEST_TABULAR
    native_stats:          NativeStats | None = None


@dataclass
class IngestionConfig:
    table_name:       str
    ts_column:        str         = ""
    entity_columns:   list[str]   = field(default_factory=list)
    property_columns: list[str]   = field(default_factory=list)
    entity_types:     dict[str, str] = field(default_factory=dict)
    formation_column: str | None  = None
    drop_existing:    bool        = True
    compact_after:    bool        = True
    rows_cap:         int | None  = None
    ingest_mode:      str         = INGEST_TABULAR


@dataclass
class IngestionResult:
    table:            str
    inserted:         int
    errors:           int
    elapsed_s:        float
    entity_count:     int
    entity_map:       dict[str, dict]
    entity_map_path:  str | None = None


# ── Entity registry ───────────────────────────────────────────────────────────

class EntityRegistry:
    def __init__(self) -> None:
        self._key_to_id: dict[str, int] = {}
        self._meta:      dict[int, dict] = {}
        self._next = 1

    def get_or_create(self, col: str, value: Any, etype: str = "generic") -> int:
        v = _clean_str(value)
        if not v:
            return 0
        key = f"{col}:{v.lower()}"
        if key not in self._key_to_id:
            nid = self._next
            self._key_to_id[key] = nid
            display, short = _derive_labels(v, etype)
            self._meta[nid] = {"type": etype, "raw": v, "display": display, "short": short}
            self._next += 1
        return self._key_to_id[key]

    def export_map(self) -> dict[str, dict]:
        return {str(nid): info for nid, info in self._meta.items()}

    def __len__(self) -> int:
        return self._next - 1


# ── Cornell ARB helpers ───────────────────────────────────────────────────────

def _looks_like_quarter_ts(t: int) -> bool:
    """Detect Cornell ARB quarter-year timestamps (year*4+quarter, range ~8000-8300)."""
    return _ARB_TS_MIN <= t <= _ARB_TS_MAX


def _quarter_to_epoch(t: int) -> int:
    """
    Convert Cornell ARB quarter timestamp to Unix epoch seconds.
    Formula: t = year * 4 + quarter  (quarter in 1..4)
    Example: 8017 = 2004*4+1 = Q1 2004 → 1072915200
    """
    year    = t // 4
    quarter = t % 4   # 0 means this is year*4+0 → treat as Q4 of previous year
    if quarter == 0:
        year    -= 1
        quarter  = 4
    quarters_from_2004 = (year - 2004) * 4 + (quarter - 1)
    return _Q1_2004_EPOCH + quarters_from_2004 * _QUARTER_SECS


def _epoch_to_label(epoch: int) -> str:
    try:
        return datetime.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%d")
    except Exception:
        return str(epoch)


def _load_cornell_arb(file_bytes: bytes, is_zip: bool) -> tuple[list[dict], int, dict]:
    """
    Parse a Cornell ARB tar.gz or zip archive into native hyperedge records.
    Returns (records, total_count, metadata).
    """
    nverts_txt = simplices_txt = times_txt = labels_txt = None

    def _classify(name: str, content: bytes) -> None:
        nonlocal nverts_txt, simplices_txt, times_txt, labels_txt
        base = Path(name).name.lower()
        if _ARB_NVERTS.search(base) and "simplices" not in base:
            nverts_txt    = content.decode("utf-8", errors="replace")
        elif _ARB_SIMPLICES.search(base):
            simplices_txt = content.decode("utf-8", errors="replace")
        elif _ARB_TIMES.search(base):
            times_txt     = content.decode("utf-8", errors="replace")
        elif _ARB_LABELS.search(base):
            labels_txt    = content.decode("utf-8", errors="replace")

    if is_zip:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            for name in zf.namelist():
                if not name.endswith(".txt"):
                    continue
                _classify(name, zf.read(name))
    else:
        try:
            with tarfile.open(fileobj=io.BytesIO(file_bytes), mode="r:*") as tf:
                for member in tf.getmembers():
                    if not member.name.endswith(".txt"):
                        continue
                    fobj = tf.extractfile(member)
                    if fobj:
                        _classify(member.name, fobj.read())
        except tarfile.TarError as exc:
            raise ValueError(f"Cannot open archive: {exc}") from exc

    missing = [n for n, v in [("nverts", nverts_txt), ("simplices", simplices_txt), ("times", times_txt)] if v is None]
    if missing:
        raise ValueError(
            f"Cornell ARB archive is missing required files: {missing}. "
            "Expected files matching *nverts.txt, *simplices.txt, *times.txt"
        )

    # Parse node labels
    node_labels: dict[int, str] = {}
    if labels_txt:
        for line in labels_txt.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) >= 2:
                try:
                    nid   = int(parts[0])
                    label = parts[2].strip() if len(parts) > 2 else parts[1].strip()
                    node_labels[nid] = label
                except ValueError:
                    pass

    # Parse data
    nverts_list   = [int(x.strip()) for x in nverts_txt.splitlines()    if x.strip()]
    times_list    = [int(x.strip()) for x in times_txt.splitlines()     if x.strip()]
    simplices_iter = iter(int(x.strip()) for x in simplices_txt.splitlines() if x.strip())

    # Detect timestamp type
    all_ts_quarter = all(_looks_like_quarter_ts(t) for t in times_list[:50]) if times_list else False

    records: list[dict] = []
    for n, t in zip(nverts_list, times_list):
        members = [next(simplices_iter) for _ in range(n)]
        epoch   = _quarter_to_epoch(t) if all_ts_quarter else t
        records.append({"event_ts": epoch, "members": members, "simplex_size": n})

    meta = {
        "ingest_mode":  INGEST_NATIVE,
        "node_labels":  node_labels,
        "quarter_ts":   all_ts_quarter,
    }
    return records, len(records), meta


# ── File loaders ──────────────────────────────────────────────────────────────

def load_records(
    file_bytes: bytes,
    filename:   str,
) -> tuple[list[dict], int, dict | None]:
    """
    Load uploaded bytes into a list of row-dicts.

    Returns
    -------
    (records, total_row_count, metadata)
    metadata is None for tabular formats; for native hyperedge formats it is
    {"ingest_mode": "native_hyperedge", "node_labels": {id: name}}.
    """
    name = filename.lower()

    # ── Cornell ARB archives ─────────────────────────────────────────────────
    if name.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar")):
        return _load_cornell_arb(file_bytes, is_zip=False)

    # Bare .gz — try as tar.gz first, then skip to other handlers
    if name.endswith(".gz"):
        try:
            return _load_cornell_arb(file_bytes, is_zip=False)
        except Exception:
            pass
    if name.endswith(".zip"):
        # Try Cornell ARB first; fall back to zip-of-tabular-files
        try:
            return _load_cornell_arb(file_bytes, is_zip=True)
        except ValueError:
            pass  # not Cornell ARB — treat as tabular zip

    # ── CSV / TSV ────────────────────────────────────────────────────────────
    if name.endswith((".csv", ".tsv", ".txt")):
        sep = "\t" if name.endswith(".tsv") else ","
        try:
            import pandas as pd
            df = pd.read_csv(io.BytesIO(file_bytes), sep=sep, low_memory=False,
                             dtype=str, keep_default_na=False)
            return df.to_dict(orient="records"), len(df), None
        except ImportError:
            import csv as _csv
            text   = file_bytes.decode("utf-8-sig", errors="replace")
            reader = _csv.DictReader(io.StringIO(text), delimiter=sep)
            rows   = list(reader)
            return rows, len(rows), None

    # ── XLSX / XLS ───────────────────────────────────────────────────────────
    if name.endswith((".xlsx", ".xls")):
        try:
            import pandas as pd
            df = pd.read_excel(io.BytesIO(file_bytes), dtype=str, keep_default_na=False)
            return df.to_dict(orient="records"), len(df), None
        except ImportError:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
            ws = wb.active
            it = ws.iter_rows(values_only=True)
            raw_headers = next(it, [])
            headers = [str(h).strip() if h else f"col{i}" for i, h in enumerate(raw_headers)]
            rows = [{h: ("" if v is None else str(v)) for h, v in zip(headers, row)} for row in it]
            wb.close()
            return rows, len(rows), None

    # ── JSON ─────────────────────────────────────────────────────────────────
    if name.endswith(".json"):
        data = json.loads(file_bytes)
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = None
            for k in ("records", "data", "rows", "items", "results", "hyperedges"):
                if isinstance(data.get(k), list):
                    rows = data[k]; break
            if rows is None:
                rows = [data]
        else:
            rows = [data]
        # Detect native hyperedge JSON
        if rows and isinstance(rows[0], dict) and "members" in rows[0]:
            return rows, len(rows), {"ingest_mode": INGEST_NATIVE, "node_labels": {}}
        return rows, len(rows), None

    # ── JSON-Lines ───────────────────────────────────────────────────────────
    if name.endswith((".jsonl", ".ndjson")):
        rows = []
        for line in file_bytes.decode("utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        # Detect native hyperedge JSONL
        if rows and isinstance(rows[0], dict) and "members" in rows[0]:
            return rows, len(rows), {"ingest_mode": INGEST_NATIVE, "node_labels": {}}
        return rows, len(rows), None

    # ── Parquet ──────────────────────────────────────────────────────────────
    if name.endswith(".parquet"):
        try:
            import pandas as pd
            df = pd.read_parquet(io.BytesIO(file_bytes))
            return df.to_dict(orient="records"), len(df), None
        except ImportError:
            raise ValueError("Parquet support requires pandas + pyarrow: pip install pandas pyarrow")

    raise ValueError(
        f"Unsupported file format '{filename}'. "
        "Supported: .csv .tsv .xlsx .xls .json .jsonl .ndjson .parquet .tar.gz .tgz .zip"
    )


# ── Schema inference ──────────────────────────────────────────────────────────

def infer_schema(
    records:    list[dict],
    metadata:   dict | None = None,
    sample_n:   int = 200,
) -> SchemaInference:
    """
    Infer the role of each column (tabular) or compute dataset stats (native).
    """
    if not records:
        return SchemaInference(columns=[], row_count=0, suggested_ts_col=None,
                               suggested_entity_cols=[], sample_hyperedges=[])

    # ── Native hyperedge mode ─────────────────────────────────────────────────
    is_native = (
        (metadata and metadata.get("ingest_mode") == INGEST_NATIVE)
        or ("members" in records[0])
    )
    if is_native:
        return _infer_native(records, metadata or {})

    # ── Tabular mode ─────────────────────────────────────────────────────────
    sample = records[: min(sample_n, len(records))]
    keys   = list(records[0].keys())

    cols:              list[ColumnDef] = []
    ts_candidates:     list[str]       = []
    entity_candidates: list[str]       = []

    for k in keys:
        vals     = [_clean_str(r.get(k, "")) for r in sample]
        non_null = [v for v in vals if v]
        null_pct = 1.0 - len(non_null) / max(len(vals), 1)

        cdef = ColumnDef(name=k, sample_values=non_null[:5], null_pct=round(null_pct, 3))

        num_vals = sum(1 for v in non_null[:20] if _is_numeric(v))
        cdef.dtype_hint = "number" if num_vals > len(non_null[:20]) * 0.8 else "string"

        if _SKIP_NAMES.search(k) and cdef.dtype_hint == "number":
            cdef.role = COL_SKIP; cols.append(cdef); continue

        if _TS_NAMES.search(k) and non_null and _looks_like_ts(non_null[0]):
            cdef.role = COL_TIMESTAMP; ts_candidates.append(k); cols.append(cdef); continue

        if _ENTITY_NAMES.search(k) or (non_null and _looks_like_entity(non_null[:5])):
            cdef.role       = COL_ENTITY
            cdef.entity_type = _guess_entity_type(k, non_null[:3])
            entity_candidates.append(k); cols.append(cdef); continue

        cdef.role = COL_PROPERTY; cols.append(cdef)

    ts_col = ts_candidates[0] if ts_candidates else None

    reg = EntityRegistry()
    sample_hes: list[dict] = []
    for row in records[:3]:
        members = []
        for col in entity_candidates[:6]:
            nid = reg.get_or_create(col, row.get(col, ""), etype=_guess_entity_type(col, []))
            if nid:
                members.append(nid)
        ts_raw = _clean_str(row.get(ts_col, "")) if ts_col else ""
        sample_hes.append({
            "event_ts":    _to_unix(ts_raw) if ts_raw else 0,
            "members":     members,
            "member_count": len(members),
        })

    return SchemaInference(
        columns=cols, row_count=len(records),
        suggested_ts_col=ts_col,
        suggested_entity_cols=entity_candidates[:6],
        sample_hyperedges=sample_hes,
        ingest_mode=INGEST_TABULAR,
    )


def _infer_native(records: list[dict], metadata: dict) -> SchemaInference:
    """Compute statistics for a native hyperedge dataset."""
    node_labels: dict[int, str] = metadata.get("node_labels", {})

    sizes   = [len(r["members"]) for r in records]
    times   = [r["event_ts"] for r in records]
    size_ct = Counter(sizes)
    all_nodes: set[int] = set()
    for r in records:
        all_nodes.update(r["members"])

    multi  = sum(1 for s in sizes if s >= 2)
    skipped = len(sizes) - multi
    t_min, t_max = (min(times), max(times)) if times else (0, 0)

    # Sample hyperedges (first 3 with size >= 2)
    sample_hes = []
    for r in records:
        if len(r["members"]) >= 2:
            members_labeled = []
            for nid in r["members"][:8]:
                members_labeled.append({
                    "id":    nid,
                    "label": node_labels.get(nid, str(nid)),
                })
            sample_hes.append({
                "event_ts":      r["event_ts"],
                "members":       r["members"][:8],
                "member_count":  len(r["members"]),
                "members_labeled": members_labeled,
            })
            if len(sample_hes) >= 3:
                break

    # Member size distribution (cap at 20)
    dist = {str(k): v for k, v in sorted(size_ct.items()) if k >= 2}

    # Node label sample
    label_sample = [
        {"id": nid, "name": name}
        for nid, name in list(node_labels.items())[:10]
    ]

    native_stats = NativeStats(
        total_simplices=len(records),
        multi_member_simplices=multi,
        size_1_skipped=skipped,
        time_range_epoch=[t_min, t_max],
        time_range_label=[_epoch_to_label(t_min), _epoch_to_label(t_max)],
        unique_nodes=len(all_nodes),
        member_size_distribution=dist,
        has_node_labels=bool(node_labels),
        node_label_sample=label_sample,
    )

    return SchemaInference(
        columns=[], row_count=len(records),
        suggested_ts_col=None, suggested_entity_cols=[],
        sample_hyperedges=sample_hes,
        ingest_mode=INGEST_NATIVE,
        native_stats=native_stats,
    )


# ── Core ingestion ────────────────────────────────────────────────────────────

def run_ingestion(
    records:          list[dict],
    config:           IngestionConfig,
    conn,
    entity_map_path:  str | None = None,
    native_metadata:  dict | None = None,
) -> IngestionResult:
    """
    Transform records into hyperedges and bulk-insert into conn.
    Handles both tabular (Mode A) and native hyperedge (Mode B) modes.
    """
    t0  = time.time()
    tbl = config.table_name.upper().replace(" ", "_")
    rows = records[: config.rows_cap] if config.rows_cap else records

    # ── DDL ───────────────────────────────────────────────────────────────────
    if config.drop_existing:
        try:
            conn.execute(f"DROP HYPEREDGE TABLE {tbl}")
        except Exception:
            pass

    prop_defs = " ".join(f"{_safe_col(c)} TEXT," for c in config.property_columns[:8]).rstrip(",")
    ddl = (
        f"CREATE HYPEREDGE TABLE {tbl} (Entity, Entity) PROPS ({prop_defs})"
        if prop_defs else
        f"CREATE HYPEREDGE TABLE {tbl} (Entity, Entity)"
    )
    conn.execute(ddl)

    # ── Route to correct mode ─────────────────────────────────────────────────
    is_native = (
        config.ingest_mode == INGEST_NATIVE
        or (rows and "members" in rows[0])
    )

    if is_native:
        return _run_native_ingestion(rows, config, conn, tbl, t0, entity_map_path,
                                     native_metadata or {})
    return _run_tabular_ingestion(rows, config, conn, tbl, t0, entity_map_path)


def _run_native_ingestion(
    rows:             list[dict],
    config:           IngestionConfig,
    conn,
    tbl:              str,
    t0:               float,
    entity_map_path:  str | None,
    native_metadata:  dict,
) -> IngestionResult:
    """Insert pre-formed hyperedges directly — no entity column resolution."""
    node_labels: dict[int, str] = native_metadata.get("node_labels", {})
    inserted = errors = 0
    BATCH = 500
    COMPACT_EVERY = 20_000

    conn.execute("BEGIN")
    try:
        for i, row in enumerate(rows):
            members = row.get("members", [])
            if len(members) < 2:
                continue   # skip size-1 simplices

            event_ts  = int(row.get("event_ts", 0))
            member_str = ",".join(str(m) for m in members)
            sql = f"INSERT INTO {tbl} (event_ts, members) VALUES ({event_ts}, [{member_str}])"
            try:
                conn.execute(sql)
                inserted += 1
            except Exception as exc:
                errors += 1
                if errors <= 3:
                    import logging
                    logging.getLogger(__name__).warning("native row %d: %s", i, exc)

            if (i + 1) % BATCH == 0:
                conn.execute("COMMIT")
                conn.execute("BEGIN")

            if inserted > 0 and inserted % COMPACT_EVERY == 0:
                conn.execute("COMMIT")
                try:
                    conn.compact(table=tbl)
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

    if config.compact_after:
        try:
            conn.compact(table=tbl)
        except Exception:
            pass

    # Build entity map from node labels
    all_nodes: set[int] = set()
    for r in rows:
        all_nodes.update(r.get("members", []))

    entity_map: dict[str, dict] = {}
    for nid in all_nodes:
        name = node_labels.get(nid, str(nid))
        entity_map[str(nid)] = {
            "type":    "generic",
            "raw":     name,
            "display": name.title() if name != str(nid) else f"Node {nid}",
            "short":   (name[:20].title() if name != str(nid) else f"N{nid}"),
        }

    saved_path = _save_entity_map(entity_map, entity_map_path)
    return IngestionResult(
        table=tbl, inserted=inserted, errors=errors,
        elapsed_s=round(time.time() - t0, 2),
        entity_count=len(entity_map), entity_map=entity_map,
        entity_map_path=saved_path,
    )


def _run_tabular_ingestion(
    rows:             list[dict],
    config:           IngestionConfig,
    conn,
    tbl:              str,
    t0:               float,
    entity_map_path:  str | None,
) -> IngestionResult:
    """Build entity registry from column values then insert hyperedges."""
    reg       = EntityRegistry()
    prop_cols = config.property_columns[:8]
    col_list  = ["event_ts", "members"] + [_safe_col(c) for c in prop_cols]
    col_str   = ", ".join(col_list)
    inserted = errors = 0
    BATCH = 500

    # First pass: populate entity registry
    for row in rows:
        for col in config.entity_columns:
            etype = config.entity_types.get(col, "generic")
            reg.get_or_create(col, row.get(col), etype=etype)

    conn.execute("BEGIN")
    try:
        for i, row in enumerate(rows):
            ts_raw   = _clean_str(row.get(config.ts_column, ""))
            event_ts = _to_unix(ts_raw) if ts_raw else 0

            member_ids: list[int] = []
            for col in config.entity_columns:
                etype = config.entity_types.get(col, "generic")
                nid   = reg.get_or_create(col, row.get(col), etype=etype)
                if nid and nid not in member_ids:
                    member_ids.append(nid)

            if len(member_ids) < 2:
                member_ids = (member_ids + member_ids)[:2] if member_ids else [1, 1]

            members_str = ",".join(str(m) for m in member_ids)
            prop_vals   = [_sql_str(_clean_str(row.get(c, ""))[:128]) for c in prop_cols]
            vals_str    = ", ".join([str(event_ts), f"[{members_str}]"] + prop_vals)
            sql         = f"INSERT INTO {tbl} ({col_str}) VALUES ({vals_str})"

            try:
                conn.execute(sql)
                inserted += 1
            except Exception as exc:
                errors += 1
                if errors <= 3:
                    import logging
                    logging.getLogger(__name__).warning("tabular row %d: %s | sql=%s", i, exc, sql[:200])

            if (i + 1) % BATCH == 0:
                conn.execute("COMMIT")
                conn.execute("BEGIN")

        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    if config.compact_after:
        try:
            conn.compact(table=tbl)
        except Exception:
            pass

    entity_map  = reg.export_map()
    saved_path  = _save_entity_map(entity_map, entity_map_path)
    return IngestionResult(
        table=tbl, inserted=inserted, errors=errors,
        elapsed_s=round(time.time() - t0, 2),
        entity_count=len(reg), entity_map=entity_map,
        entity_map_path=saved_path,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_entity_map(entity_map: dict, path: str | None) -> str | None:
    if not path:
        return None
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(entity_map, indent=2), encoding="utf-8")
        return str(p)
    except Exception:
        return None


def _clean_str(v: Any) -> str:
    if v is None: return ""
    s = str(v).strip()
    return "" if s.lower() in ("none", "nan", "null", "") else s


def _sql_str(v: str) -> str:
    return f"'{v.replace(chr(39), chr(39)+chr(39)).replace(chr(10), ' ').replace(chr(13), ' ')}'"


def _safe_col(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)[:32].lower()


def _is_numeric(v: str) -> bool:
    try:
        float(v); return True
    except (ValueError, TypeError):
        return False


def _derive_labels(v: str, etype: str) -> tuple[str, str]:
    if etype == "account":
        parts   = v.replace("/", "\\").split("\\")
        display = parts[-1] if parts else v
        return display, display[:20]
    if etype == "process":
        fname = Path(v).name if ("/" in v or "\\" in v) else v
        return fname, fname[:20]
    return v, v[:20]


def _guess_entity_type(col: str, sample_vals: list[str]) -> str:
    cl = col.lower()
    if any(x in cl for x in ("machine", "device", "host", "endpoint", "computer")): return "machine"
    if any(x in cl for x in ("process", "proc", "sha", "hash", "file")):             return "process"
    if any(x in cl for x in ("user", "account", "principal", "acct")):               return "account"
    if any(x in cl for x in ("ip", "addr", "src", "dst", "remote", "local")):        return "ip"
    for v in sample_vals:
        if _RE_IPV4.match(v): return "ip"
        if _RE_HASH.match(v): return "process"
    return "generic"


def _looks_like_ts(v: str) -> bool:
    if not v: return False
    if re.match(r"^\d{10,13}$", v): return True
    if re.match(r"\d{4}-\d{2}-\d{2}", v): return True
    return False


def _looks_like_entity(vals: list[str]) -> bool:
    hits = sum(1 for v in vals if _RE_IPV4.match(v) or _RE_HASH.match(v) or _RE_GUID.match(v))
    return hits > len(vals) * 0.5


def _to_unix(v: str) -> int:
    v = v.strip()
    if re.match(r"^\d{10}$", v):  return int(v)
    if re.match(r"^\d{13}$", v):  return int(v) // 1000
    # Cornell ARB quarter-year (4-5 digit int in range 8000-8300)
    if re.match(r"^\d{4,5}$", v):
        t = int(v)
        if _looks_like_quarter_ts(t):
            return _quarter_to_epoch(t)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",   "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",   "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.datetime.strptime(v[:26], fmt)
            return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())
        except ValueError:
            continue
    return 0
