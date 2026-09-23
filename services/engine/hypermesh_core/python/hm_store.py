"""
hm_store.py — Python ctypes binding for libhypermesh

This module wraps the C library so the Python layer (hypermeshdb) can call the
native engine directly.

Usage:
    from hypermesh_core.python.hm_store import HmStore

    # Build index once from CSV
    HmStore.build(dir_path="/tmp/hm_db",
                  csv_path="server/swarm_data/hyperedges.csv",
                  bucket_seconds=10)

    # Open and query
    store = HmStore("/tmp/hm_db")
    result = store.range_query(100, 130)
    for he in result.hyperedges:
        print(he["event_ts"], he["members"], he["weight"])

    ranking = store.coalition_ranking()
    for entry in ranking:
        print(entry["node_id"], entry["appearances"])

    # Write path (WAL-backed)
    store.insert(event_ts=500, members=[1, 2, 3],
                 weight=0.9, mean_dist_m=25.0, formation="WEDGE")
    store.delete(event_ts=100, members=[4, 5])
    store.compact()           # rebuild TPI+FMI, truncate WAL
    print(store.wal_pending)  # 0 after compact

    store.close()
"""

import ctypes
import os
import sys
import platform
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Dict, Any, Optional

# ── Locate / load the shared library (lazy) ───────────────────────────────


class EngineNotInstalledError(RuntimeError):
    """
    Raised when the compiled HyperMesh core library cannot be located.

    The embedded engine is shipped inside the ``hypermesh`` wheel. If you are
    seeing this, the binary is missing for your platform. Install the engine
    extra::

        pip install "hypermesh[engine]"

    or build it from source::

        make -C hypermesh_core install
    """


def _lib_filename() -> str:
    return "libhypermesh.dylib" if platform.system() == "Darwin" else "libhypermesh.so"


def _candidate_paths() -> list[str]:
    """Ordered locations to search for the shared library."""
    lib_name = _lib_filename()
    here = os.path.dirname(__file__)
    candidates: list[str] = []
    # Explicit override wins.
    env_override = os.environ.get("HYPERMESH_ENGINE_LIB")
    if env_override:
        candidates.append(env_override)
    candidates.extend(
        [
            # Bundled as package data inside hypermesh_core/ (wheel install).
            os.path.join(here, "..", lib_name),
            os.path.join(here, "..", "lib", lib_name),
            # Installed into server/ (legacy make target).
            os.path.join(here, "..", "..", "server", lib_name),
            os.path.join(here, "..", "..", "server", "lib", lib_name),
            # Current working directory (dev convenience).
            os.path.join(os.getcwd(), lib_name),
            os.path.join(os.getcwd(), "hypermesh_core", lib_name),
        ]
    )
    return candidates


def _find_lib() -> str:
    for path in _candidate_paths():
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            return abs_path
    raise EngineNotInstalledError(
        f"Cannot find {_lib_filename()}: the HyperMesh embedded engine is not "
        f'installed.\n  Install it with:  pip install "hypermesh[engine]"\n'
        f"  Or build from source:  make -C hypermesh_core install\n"
        f"  Searched: {[os.path.abspath(p) for p in _candidate_paths()]}"
    )


def library_path() -> "Optional[str]":
    """Return the resolved engine library path, or ``None`` if not installed."""
    try:
        return _find_lib()
    except EngineNotInstalledError:
        return None


class _LazyLib:
    """
    Defers ``ctypes.CDLL`` loading until the first call into the engine.

    This lets ``import hypermesh`` / the remote client work on machines without
    the compiled core; :class:`EngineNotInstalledError` is raised only when the
    embedded engine is actually used.
    """

    def __init__(self) -> None:
        self.__dict__["_real"] = None

    def _ensure(self) -> "ctypes.CDLL":
        real = self.__dict__["_real"]
        if real is None:
            real = ctypes.CDLL(_find_lib())
            self.__dict__["_real"] = real
            _setup()  # configure argtypes/restypes on the freshly-loaded lib
        return real

    def __getattr__(self, name: str):
        return getattr(self._ensure(), name)


_lib = _LazyLib()

# ── Query kind constants (mirror HmQueryKind in parser.h) ────────────────

HM_PSI_FLOAT = 0   # REAL / FLOAT column
HM_PSI_INT   = 1   # INTEGER column

HM_PSI_EQ  = 0
HM_PSI_GT  = 1
HM_PSI_GEQ = 2
HM_PSI_LT  = 3
HM_PSI_LEQ = 4

# Map Phase-2 predicate operator codes → PSI operator codes (identical values)
_PRED_OP_TO_PSI = {0: HM_PSI_EQ, 1: HM_PSI_GT, 2: HM_PSI_GEQ, 3: HM_PSI_LT, 4: HM_PSI_LEQ}


class HmQueryKind(IntEnum):
    SHOW_TABLES   = 0
    TPI_RANGE     = 1
    FMI_LOOKUP    = 2
    FULL_SCAN     = 3
    GET_BY_RANGE  = 4
    PARSE_ERROR   = 5
    CREATE_TABLE  = 6
    DROP_TABLE    = 7
    # Phase 3: DML write statements
    INSERT        = 8
    DELETE        = 9
    UPDATE        = 10
    # Phase 4: PSI index DDL
    CREATE_INDEX  = 11
    DROP_INDEX    = 12
    SHOW_INDEXES  = 13
    # Phase 7: multi-table cross join
    JOIN          = 14

# ASCII Unit Separator used between fields in insert_vals / update_set
HM_FIELD_SEP = "\x1f"

# Fixed-width formation string length (mirrors HM_FORMATION_LEN in format.h).
_HM_FORMATION_LEN = 16
# In-memory member-count guard (mirrors HM_MAX_MEMBERS in format.h).
_HM_MAX_MEMBERS = 64

# ── Opaque pointer types ──────────────────────────────────────────────────

class _HmStore(ctypes.Structure): pass
class _HmRangeResult(ctypes.Structure): pass
class _HmCoalitionResult(ctypes.Structure): pass
class _HmQueryAst(ctypes.Structure): pass

HmStorePtr           = ctypes.POINTER(_HmStore)
HmRangeResultPtr     = ctypes.POINTER(_HmRangeResult)
HmCoalitionResultPtr = ctypes.POINTER(_HmCoalitionResult)
HmQueryAstPtr        = ctypes.POINTER(_HmQueryAst)

# ── Function signatures ───────────────────────────────────────────────────

def _setup():
    c = _lib

    c.hm_build_from_csv.argtypes  = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
    c.hm_build_from_csv.restype   = ctypes.c_int

    c.hm_open.argtypes   = [ctypes.c_char_p]
    c.hm_open.restype    = HmStorePtr

    c.hm_close.argtypes  = [HmStorePtr]
    c.hm_close.restype   = None

    # TPI range query
    c.hm_range_query.argtypes = [HmStorePtr, ctypes.c_uint32, ctypes.c_uint32]
    c.hm_range_query.restype  = HmRangeResultPtr

    c.hm_range_result_free.argtypes = [HmRangeResultPtr]
    c.hm_range_result_free.restype  = None

    # Result accessors
    c.hm_res_count.argtypes       = [HmRangeResultPtr]; c.hm_res_count.restype       = ctypes.c_uint32
    c.hm_res_timestamp.argtypes   = [HmRangeResultPtr, ctypes.c_uint32]; c.hm_res_timestamp.restype   = ctypes.c_uint32
    c.hm_res_member_count.argtypes= [HmRangeResultPtr, ctypes.c_uint32]; c.hm_res_member_count.restype= ctypes.c_uint8
    c.hm_res_weight.argtypes      = [HmRangeResultPtr, ctypes.c_uint32]; c.hm_res_weight.restype      = ctypes.c_float
    c.hm_res_mean_dist.argtypes   = [HmRangeResultPtr, ctypes.c_uint32]; c.hm_res_mean_dist.restype   = ctypes.c_float
    c.hm_res_formation.argtypes   = [HmRangeResultPtr, ctypes.c_uint32]; c.hm_res_formation.restype   = ctypes.c_char_p
    c.hm_res_member_id.argtypes   = [HmRangeResultPtr, ctypes.c_uint32, ctypes.c_uint32]
    c.hm_res_member_id.restype    = ctypes.c_uint32
    c.hm_res_strategy.argtypes    = [HmRangeResultPtr]; c.hm_res_strategy.restype    = ctypes.c_char_p
    c.hm_res_buckets_scanned.argtypes = [HmRangeResultPtr]; c.hm_res_buckets_scanned.restype = ctypes.c_uint32
    c.hm_res_total_buckets.argtypes   = [HmRangeResultPtr]; c.hm_res_total_buckets.restype   = ctypes.c_uint32
    c.hm_res_speedup.argtypes     = [HmRangeResultPtr]; c.hm_res_speedup.restype     = ctypes.c_float
    c.hm_res_elapsed_us.argtypes  = [HmRangeResultPtr]; c.hm_res_elapsed_us.restype  = ctypes.c_uint64

    # FMI coalition ranking
    c.hm_get_coalition_ranking.argtypes  = [HmStorePtr]
    c.hm_get_coalition_ranking.restype   = HmCoalitionResultPtr
    c.hm_coalition_result_free.argtypes  = [HmCoalitionResultPtr]
    c.hm_coalition_result_free.restype   = None
    c.hm_coal_count.argtypes       = [HmCoalitionResultPtr]; c.hm_coal_count.restype       = ctypes.c_uint32
    c.hm_coal_node_id.argtypes     = [HmCoalitionResultPtr, ctypes.c_uint32]; c.hm_coal_node_id.restype     = ctypes.c_uint32
    c.hm_coal_appearances.argtypes = [HmCoalitionResultPtr, ctypes.c_uint32]; c.hm_coal_appearances.restype = ctypes.c_uint32

    # FMI point lookup (raw seq-IDs only — low-level)
    c.hm_fmi_lookup.argtypes = [HmStorePtr, ctypes.c_uint32,
                                  ctypes.POINTER(ctypes.c_uint32)]
    c.hm_fmi_lookup.restype  = ctypes.POINTER(ctypes.c_uint32)

    # FMI full query (binary search + record fetch + WAL merge — use this)
    c.hm_fmi_query.argtypes = [HmStorePtr, ctypes.c_uint32]
    c.hm_fmi_query.restype  = HmRangeResultPtr

    # Metadata
    c.hm_total_records.argtypes  = [HmStorePtr]; c.hm_total_records.restype  = ctypes.c_uint32
    c.hm_bucket_count.argtypes   = [HmStorePtr]; c.hm_bucket_count.restype   = ctypes.c_uint32
    c.hm_bucket_seconds.argtypes = [HmStorePtr]; c.hm_bucket_seconds.restype = ctypes.c_uint32
    c.hm_node_count.argtypes     = [HmStorePtr]; c.hm_node_count.restype     = ctypes.c_uint32

    # Full-scan baseline (benchmark use only)
    c.hm_full_scan_range.argtypes = [HmStorePtr, ctypes.c_uint32, ctypes.c_uint32]
    c.hm_full_scan_range.restype  = HmRangeResultPtr

    c.hm_last_error.argtypes = []
    c.hm_last_error.restype  = ctypes.c_char_p

    # Write path — WAL-backed insert / delete / compact
    c.hm_insert_record.argtypes = [
        HmStorePtr,
        ctypes.c_uint32,                      # event_ts
        ctypes.POINTER(ctypes.c_uint32),      # members[]
        ctypes.c_uint8,                        # member_count
        ctypes.c_float,                        # weight
        ctypes.c_float,                        # mean_dist_m
        ctypes.c_char_p,                       # formation
    ]
    c.hm_insert_record.restype = ctypes.c_int

    c.hm_delete_record.argtypes = [
        HmStorePtr,
        ctypes.c_uint32,                      # event_ts
        ctypes.POINTER(ctypes.c_uint32),      # members[]
        ctypes.c_uint8,                        # member_count
    ]
    c.hm_delete_record.restype = ctypes.c_int

    # P1.2: atomic group commit (one batch frame, one fdatasync, all-or-nothing)
    c.hm_commit_batch.argtypes = [
        HmStorePtr,
        ctypes.c_uint32,                      # n
        ctypes.POINTER(ctypes.c_uint8),       # types[]
        ctypes.POINTER(ctypes.c_uint32),      # event_ts[]
        ctypes.POINTER(ctypes.c_uint8),       # member_counts[]
        ctypes.POINTER(ctypes.c_uint32),      # members_flat[]
        ctypes.POINTER(ctypes.c_uint32),      # member_offsets[]
        ctypes.POINTER(ctypes.c_float),       # weights[]
        ctypes.POINTER(ctypes.c_float),       # mean_dists[]
        ctypes.c_char_p,                       # formations_flat (n*HM_FORMATION_LEN)
    ]
    c.hm_commit_batch.restype = ctypes.c_int

    c.hm_compact.argtypes  = [HmStorePtr]
    c.hm_compact.restype   = ctypes.c_int

    c.hm_compact_with_ttl.argtypes = [HmStorePtr, ctypes.c_uint32]
    c.hm_compact_with_ttl.restype  = ctypes.c_int

    c.hm_set_autocompact.argtypes = [HmStorePtr, ctypes.c_uint32]
    c.hm_set_autocompact.restype  = None

    # P1.4: cooperative per-query timeout + result flag
    c.hm_set_query_timeout_ms.argtypes = [HmStorePtr, ctypes.c_uint32]
    c.hm_set_query_timeout_ms.restype  = None
    c.hm_res_timed_out.argtypes        = [HmRangeResultPtr]
    c.hm_res_timed_out.restype         = ctypes.c_uint8

    c.hm_wal_pending.argtypes = [HmStorePtr]
    c.hm_wal_pending.restype  = ctypes.c_uint32

    # Cypher parser — heap-allocated AST + accessors
    c.hm_parse_query_alloc.argtypes = [ctypes.c_char_p]
    c.hm_parse_query_alloc.restype  = HmQueryAstPtr

    c.hm_query_ast_free.argtypes = [HmQueryAstPtr]
    c.hm_query_ast_free.restype  = None

    c.hm_ast_kind.argtypes     = [HmQueryAstPtr]; c.hm_ast_kind.restype     = ctypes.c_int
    c.hm_ast_ts_start.argtypes = [HmQueryAstPtr]; c.hm_ast_ts_start.restype = ctypes.c_uint32
    c.hm_ast_ts_end.argtypes   = [HmQueryAstPtr]; c.hm_ast_ts_end.restype   = ctypes.c_uint32
    c.hm_ast_node_id.argtypes  = [HmQueryAstPtr]; c.hm_ast_node_id.restype  = ctypes.c_uint32
    c.hm_ast_table.argtypes    = [HmQueryAstPtr]; c.hm_ast_table.restype    = ctypes.c_char_p
    c.hm_ast_alias.argtypes    = [HmQueryAstPtr]; c.hm_ast_alias.restype    = ctypes.c_char_p
    c.hm_ast_error.argtypes    = [HmQueryAstPtr]; c.hm_ast_error.restype    = ctypes.c_char_p
    # V2: PROPERTIES clause body from CREATE HYPEREDGE TABLE
    c.hm_ast_props.argtypes    = [HmQueryAstPtr]; c.hm_ast_props.restype    = ctypes.c_char_p

    # Phase 2: predicate / RETURN / ORDER BY / LIMIT accessors
    c.hm_ast_pred_count.argtypes  = [HmQueryAstPtr]
    c.hm_ast_pred_count.restype   = ctypes.c_uint32
    c.hm_ast_pred_col.argtypes    = [HmQueryAstPtr, ctypes.c_uint32]
    c.hm_ast_pred_col.restype     = ctypes.c_char_p
    c.hm_ast_pred_op.argtypes     = [HmQueryAstPtr, ctypes.c_uint32]
    c.hm_ast_pred_op.restype      = ctypes.c_uint8
    c.hm_ast_pred_val.argtypes    = [HmQueryAstPtr, ctypes.c_uint32]
    c.hm_ast_pred_val.restype     = ctypes.c_char_p
    c.hm_ast_pred_is_str.argtypes = [HmQueryAstPtr, ctypes.c_uint32]
    c.hm_ast_pred_is_str.restype  = ctypes.c_uint8
    c.hm_ast_return_cols.argtypes = [HmQueryAstPtr]
    c.hm_ast_return_cols.restype  = ctypes.c_char_p
    c.hm_ast_order_col.argtypes   = [HmQueryAstPtr]
    c.hm_ast_order_col.restype    = ctypes.c_char_p
    c.hm_ast_order_desc.argtypes  = [HmQueryAstPtr]
    c.hm_ast_order_desc.restype   = ctypes.c_uint8
    c.hm_ast_limit_n.argtypes     = [HmQueryAstPtr]
    c.hm_ast_limit_n.restype      = ctypes.c_uint32

    # Phase 3: DML accessors
    c.hm_ast_insert_cols.argtypes = [HmQueryAstPtr]
    c.hm_ast_insert_cols.restype  = ctypes.c_char_p
    c.hm_ast_insert_vals.argtypes = [HmQueryAstPtr]
    c.hm_ast_insert_vals.restype  = ctypes.c_char_p
    c.hm_ast_update_set.argtypes  = [HmQueryAstPtr]
    c.hm_ast_update_set.restype   = ctypes.c_char_p

    # Phase 4: PSI DDL accessor
    c.hm_ast_index_col.argtypes   = [HmQueryAstPtr]
    c.hm_ast_index_col.restype    = ctypes.c_char_p

    # Phase 7: Join AST accessors — second HYPEREDGE pattern
    c.hm_ast_table2.argtypes     = [HmQueryAstPtr]; c.hm_ast_table2.restype     = ctypes.c_char_p
    c.hm_ast_alias2.argtypes     = [HmQueryAstPtr]; c.hm_ast_alias2.restype     = ctypes.c_char_p
    c.hm_ast_ts_start2.argtypes  = [HmQueryAstPtr]; c.hm_ast_ts_start2.restype  = ctypes.c_uint32
    c.hm_ast_ts_end2.argtypes    = [HmQueryAstPtr]; c.hm_ast_ts_end2.restype    = ctypes.c_uint32
    c.hm_ast_node_id2.argtypes   = [HmQueryAstPtr]; c.hm_ast_node_id2.restype   = ctypes.c_uint32

    # Phase 7: per-predicate alias and join conditions
    c.hm_ast_pred_alias.argtypes     = [HmQueryAstPtr, ctypes.c_uint32]
    c.hm_ast_pred_alias.restype      = ctypes.c_char_p
    c.hm_ast_join_count.argtypes     = [HmQueryAstPtr]; c.hm_ast_join_count.restype = ctypes.c_uint8
    c.hm_ast_join_lhs_alias.argtypes = [HmQueryAstPtr, ctypes.c_uint32]
    c.hm_ast_join_lhs_alias.restype  = ctypes.c_char_p
    c.hm_ast_join_lhs_col.argtypes   = [HmQueryAstPtr, ctypes.c_uint32]
    c.hm_ast_join_lhs_col.restype    = ctypes.c_char_p
    c.hm_ast_join_rhs_alias.argtypes = [HmQueryAstPtr, ctypes.c_uint32]
    c.hm_ast_join_rhs_alias.restype  = ctypes.c_char_p
    c.hm_ast_join_rhs_col.argtypes   = [HmQueryAstPtr, ctypes.c_uint32]
    c.hm_ast_join_rhs_col.restype    = ctypes.c_char_p
    c.hm_ast_join_op.argtypes        = [HmQueryAstPtr, ctypes.c_uint32]
    c.hm_ast_join_op.restype         = ctypes.c_uint8

    # Phase 8: compact_threshold accessor
    c.hm_ast_compact_threshold.argtypes = [HmQueryAstPtr]
    c.hm_ast_compact_threshold.restype  = ctypes.c_uint32

    # Phase 6: empty-store initialiser
    c.hm_init_empty.argtypes = [ctypes.c_char_p, ctypes.c_uint32]
    c.hm_init_empty.restype  = ctypes.c_int

    # Phase 4: PSI file I/O (called from Python; no HmStore pointer needed)
    # hm_psi_write(dir, table, col, col_type, entries_ptr, n) → int
    c.hm_psi_write.argtypes = [
        ctypes.c_char_p,   # dir
        ctypes.c_char_p,   # table
        ctypes.c_char_p,   # col
        ctypes.c_uint8,    # col_type
        ctypes.c_void_p,   # HmPsiEntry* (may be NULL if n==0)
        ctypes.c_uint32,   # n
    ]
    c.hm_psi_write.restype = ctypes.c_int

    # hm_psi_lookup(dir, table, col, op, threshold, out_ts, max_out, out_count) → int
    c.hm_psi_lookup.argtypes = [
        ctypes.c_char_p,                      # dir
        ctypes.c_char_p,                      # table
        ctypes.c_char_p,                      # col
        ctypes.c_uint8,                       # op
        ctypes.c_float,                       # threshold
        ctypes.POINTER(ctypes.c_uint32),      # out_ts
        ctypes.c_uint32,                      # max_out
        ctypes.POINTER(ctypes.c_uint32),      # out_count
    ]
    c.hm_psi_lookup.restype = ctypes.c_int

    # hm_psi_exists(dir, table, col) → int
    c.hm_psi_exists.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
    c.hm_psi_exists.restype  = ctypes.c_int

    # hm_psi_remove(dir, table, col) → int
    c.hm_psi_remove.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
    c.hm_psi_remove.restype  = ctypes.c_int

    # hm_psi_filename(dir, table, col, buf, bufsz) → char*
    c.hm_psi_filename.argtypes = [
        ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
        ctypes.c_char_p, ctypes.c_size_t,
    ]
    c.hm_psi_filename.restype = ctypes.c_char_p

    # V2: hm_insert_record_v2 — with user-defined property blob
    c.hm_insert_record_v2.argtypes = [
        HmStorePtr,
        ctypes.c_uint32,                      # event_ts
        ctypes.POINTER(ctypes.c_uint32),      # members[]
        ctypes.c_uint8,                        # member_count
        ctypes.c_float,                        # weight
        ctypes.c_float,                        # mean_dist_m
        ctypes.c_char_p,                       # formation
        ctypes.c_char_p,                       # props (opaque bytes)
        ctypes.c_uint32,                       # props_len
    ]
    c.hm_insert_record_v2.restype = ctypes.c_int

    # V2: property blob result accessors
    c.hm_res_props.argtypes     = [HmRangeResultPtr, ctypes.c_uint32]
    c.hm_res_props.restype      = ctypes.POINTER(ctypes.c_uint8)  # raw bytes, NOT c_char_p (null-safe)
    c.hm_res_props_len.argtypes = [HmRangeResultPtr, ctypes.c_uint32]
    c.hm_res_props_len.restype  = ctypes.c_uint32

# NOTE: _setup() is invoked lazily by _LazyLib._ensure() on first engine use,
# so importing this module never requires the compiled library to be present.

# ── Python wrapper ────────────────────────────────────────────────────────

class QueryTimeout(RuntimeError):
    """Raised when a query exceeds HMDB_QUERY_TIMEOUT_MS and is aborted."""


@dataclass
class QueryPlan:
    strategy: str
    buckets_scanned: int
    total_buckets: int
    speedup_factor: float
    elapsed_us: int
    timed_out: bool = False

@dataclass
class RangeResult:
    hyperedges: List[Dict[str, Any]]
    plan: QueryPlan

# ── Phase 2: predicate operator constants (mirror HM_PRED_* in parser.h) ─────

class HmPredOp(IntEnum):
    EQ  = 0  # =
    GT  = 1  # >
    GEQ = 2  # >=
    LT  = 3  # <
    LEQ = 4  # <=

    def symbol(self) -> str:
        return {0: "=", 1: ">", 2: ">=", 3: "<", 4: "<="}[int(self)]


@dataclass
class ParsedQuery:
    """Python representation of an HmQueryAst returned by the C parser."""
    kind:      HmQueryKind
    table:     str
    alias:     str
    ts_start:  int
    ts_end:    int
    node_id:   int
    error:     str
    props_csv: str  = ""  # raw PROPERTIES(...) body from CREATE HYPEREDGE TABLE

    # Phase 2 fields ──────────────────────────────────────────────────────────
    # Each entry: {"col": str, "op": HmPredOp, "val": str, "is_str": bool}
    predicates: Optional[List[Dict[str, Any]]] = None

    return_cols: str  = ""    # comma-separated column list, "" = RETURN *
    order_col:   str  = ""    # ORDER BY column, "" = no ordering
    order_desc:  bool = False  # True = DESC
    limit_n:     int  = 0     # LIMIT N, 0 = no limit
    skip_n:      int  = 0     # SKIP / OFFSET N, 0 = no skip. Applied Python-side
                              # (stripped from the query before the C parser sees
                              # it) between ORDER BY and LIMIT.
    bool_where:  Any  = None  # Boolean WHERE tree (hypermeshdb._where.Node) for
                              # OR / NOT / <> predicates the C grammar can't
                              # express. When set, the WHERE was stripped before
                              # the C parser (→ full scan) and is evaluated
                              # Python-side in _post_process.

    # Phase 3: DML fields ──────────────────────────────────────────────────────
    # INSERT INTO <table> (insert_cols) VALUES (insert_vals)
    # insert_vals is HM_FIELD_SEP-delimited: "val0\x1fval1\x1fval2"
    insert_cols: str = ""
    insert_vals: str = ""

    # UPDATE <table> SET update_set WHERE ...
    # update_set: alternating "COL\x1fval\x1fCOL\x1fval"
    update_set:  str = ""

    # Phase 4: PSI index DDL
    # CREATE INDEX ON <table> (<index_col>)
    # DROP   INDEX ON <table> (<index_col>)
    index_col: str = ""

    # Phase 7: join fields ─────────────────────────────────────────────────────
    # Second HYPEREDGE pattern (kind == JOIN only):
    #   table2    — second table name (uppercased)
    #   alias2    — second alias identifier
    #   ts_start2 — TPI lower bound for table2 (0 = no lower bound)
    #   ts_end2   — TPI upper bound for table2 (0xFFFFFFFF = no upper bound)
    #   node_id2  — FMI node for table2 (0 = no FMI lookup)
    table2:    str = ""
    alias2:    str = ""
    ts_start2: int = 0
    ts_end2:   int = 0xFFFFFFFF
    node_id2:  int = 0

    # Per-predicate alias (which alias each predicate[i] belongs to).
    # Populated in join queries; empty string means alias-agnostic.
    # Parallel to the `predicates` list; index i maps to predicates[i]["alias"].
    # (Stored directly inside each predicate dict as key "alias" for convenience.)

    # Cross-alias join conditions: list of dicts with keys:
    #   lhs_alias, lhs_col, rhs_alias, rhs_col, op (HmPredOp)
    join_predicates: Optional[List[Dict[str, Any]]] = None

    # Phase 8: per-table autocompact threshold from CREATE TABLE DDL
    # 0 = disabled (default); positive value = WAL entry threshold
    compact_threshold: int = 0

    def __post_init__(self):
        if self.predicates is None:
            self.predicates = []


class HmStore:
    """
    Python handle to an open HyperMesh index directory.
    Use as a context manager or call .close() explicitly.
    """

    # ── Build (class method) ──────────────────────────────────────────────

    @staticmethod
    def init_empty(dir_path: str, bucket_seconds: int = 10) -> None:
        """
        Initialise an empty TPI + FMI store in dir_path without any CSV data.
        Creates dir_path and writes valid empty index files (0 records) so that
        HmStore(dir_path) can be opened immediately afterwards.
        Raises RuntimeError on failure.
        """
        rc = _lib.hm_init_empty(
            dir_path.encode("utf-8") if isinstance(dir_path, str) else dir_path,
            ctypes.c_uint32(max(1, int(bucket_seconds))),
        )
        if rc < 0:
            err = _lib.hm_last_error()
            raise RuntimeError(
                f"hm_init_empty('{dir_path}') failed: "
                f"{err.decode() if err else 'unknown error'}"
            )

    @classmethod
    def build(cls, dir_path: str, csv_path: str, bucket_seconds: int = 10) -> None:
        """
        Build a new TPI + FMI index from a hyperedges CSV file.
        Overwrites any existing index in dir_path.
        Raises RuntimeError on failure.
        """
        rc = _lib.hm_build_from_csv(
            dir_path.encode(),
            csv_path.encode(),
            ctypes.c_uint32(bucket_seconds),
        )
        if rc != 0:
            err = _lib.hm_last_error()
            raise RuntimeError(
                f"hm_build_from_csv failed: {err.decode() if err else 'unknown'}"
            )

    # ── Open ──────────────────────────────────────────────────────────────

    def __init__(self, dir_path: str):
        self._ptr = _lib.hm_open(dir_path.encode())
        if not self._ptr:
            err = _lib.hm_last_error()
            raise RuntimeError(
                f"hm_open('{dir_path}') failed: "
                f"{err.decode() if err else 'check that index was built first'}"
            )
        self._dir_path = dir_path

        # P1.4: apply a cooperative server-side query timeout. 0 / unset → 30s
        # default; explicit "0" disables it. Bad values fall back to the default.
        try:
            self._query_timeout_ms = int(os.environ.get("HMDB_QUERY_TIMEOUT_MS", "30000"))
        except (TypeError, ValueError):
            self._query_timeout_ms = 30000
        if self._query_timeout_ms < 0:
            self._query_timeout_ms = 0
        _lib.hm_set_query_timeout_ms(self._ptr, ctypes.c_uint32(self._query_timeout_ms))

    def set_query_timeout_ms(self, timeout_ms: int) -> None:
        """Set the cooperative per-query time budget (0 disables the deadline)."""
        self._query_timeout_ms = max(0, int(timeout_ms))
        _lib.hm_set_query_timeout_ms(self._ptr, ctypes.c_uint32(self._query_timeout_ms))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self._ptr:
            _lib.hm_close(self._ptr)
            self._ptr = None

    # ── WAL / write path ──────────────────────────────────────────────────

    @property
    def wal_pending(self) -> int:
        """Number of uncompacted WAL entries (inserts + tombstones)."""
        return int(_lib.hm_wal_pending(self._ptr))

    def insert(self,
               event_ts: int,
               members: List[int],
               weight: float = 0.0,
               mean_dist_m: float = 0.0,
               formation: str = "") -> None:
        """
        Append one hyperedge to the WAL. Immediately visible to all subsequent
        range_query() calls. Raises RuntimeError on I/O failure.
        """
        mc = len(members)
        if mc == 0:
            raise ValueError("members must be a non-empty list of node IDs")
        arr = (ctypes.c_uint32 * mc)(*members)
        rc = _lib.hm_insert_record(
            self._ptr,
            ctypes.c_uint32(event_ts),
            arr,
            ctypes.c_uint8(mc),
            ctypes.c_float(weight),
            ctypes.c_float(mean_dist_m),
            formation.encode("utf-8", errors="replace"),
        )
        if rc != 0:
            err = _lib.hm_last_error()
            raise RuntimeError(
                f"hm_insert_record failed: {err.decode() if err else 'unknown'}"
            )

    def insert_v2(self,
                   event_ts:    int,
                   members:     List[int],
                   weight:      float = 0.0,
                   mean_dist_m: float = 0.0,
                   formation:   str   = "",
                   props:       Optional[bytes] = None) -> None:
        """
        Append one hyperedge with an optional user-defined property blob (V2).
        *props* is an opaque byte string produced by the Python SDK's property
        serialiser.  Pass None or b'' for records without user properties.
        Raises RuntimeError on I/O failure or if props require a V2 WAL.
        """
        mc = len(members)
        if mc == 0:
            raise ValueError("members must be a non-empty list of node IDs")
        arr = (ctypes.c_uint32 * mc)(*members)
        props_bytes   = props if props else b""
        props_len     = len(props_bytes)
        rc = _lib.hm_insert_record_v2(
            self._ptr,
            ctypes.c_uint32(event_ts),
            arr,
            ctypes.c_uint8(mc),
            ctypes.c_float(weight),
            ctypes.c_float(mean_dist_m),
            formation.encode("utf-8", errors="replace"),
            props_bytes if props_len else None,
            ctypes.c_uint32(props_len),
        )
        if rc != 0:
            err = _lib.hm_last_error()
            raise RuntimeError(
                f"hm_insert_record_v2 failed: {err.decode() if err else 'unknown'}"
            )

    def delete(self, event_ts: int, members: List[int]) -> None:
        """
        Write a DELETE tombstone to the WAL. Any hyperedge matching
        (event_ts, same member set — order-independent) is hidden from
        subsequent range_query() calls. Raises RuntimeError on I/O failure.
        """
        mc = len(members)
        arr = (ctypes.c_uint32 * max(mc, 1))(*members)
        rc = _lib.hm_delete_record(
            self._ptr,
            ctypes.c_uint32(event_ts),
            arr,
            ctypes.c_uint8(mc),
        )
        if rc != 0:
            err = _lib.hm_last_error()
            raise RuntimeError(
                f"hm_delete_record failed: {err.decode() if err else 'unknown'}"
            )

    def commit_batch(self, ops) -> None:
        """
        Atomically commit a list of insert/delete ops as a single WAL batch
        frame: one fdatasync, all-or-nothing on a crash, and isolated from
        concurrent readers (no partial transaction is ever observable).

        Each op is a tuple describing one hyperedge:
            ("insert", event_ts, members, weight, mean_dist_m, formation)
            ("delete", event_ts, members)
        weight / mean_dist_m / formation are optional for inserts.

        On a V4 WAL the whole batch is crash-atomic. On an older WAL it falls
        back to per-entry appends (still correct, just not all-or-nothing).
        Raises RuntimeError on I/O failure.
        """
        n = len(ops)
        if n == 0:
            return

        types = (ctypes.c_uint8  * n)()
        ev    = (ctypes.c_uint32 * n)()
        mcs   = (ctypes.c_uint8  * n)()
        offs  = (ctypes.c_uint32 * n)()
        ws    = (ctypes.c_float  * n)()
        mds   = (ctypes.c_float  * n)()
        forms = bytearray(n * _HM_FORMATION_LEN)
        members_flat: List[int] = []

        for i, op in enumerate(ops):
            kind    = op[0]
            members = list(op[2])
            mc      = len(members)
            if mc > _HM_MAX_MEMBERS:
                raise ValueError(
                    f"member_count {mc} exceeds HM_MAX_MEMBERS ({_HM_MAX_MEMBERS})"
                )
            types[i] = 2 if kind == "delete" else 1
            ev[i]    = int(op[1])
            mcs[i]   = mc
            offs[i]  = len(members_flat)
            members_flat.extend(int(m) for m in members)
            if kind != "delete":
                ws[i]  = float(op[3]) if len(op) > 3 and op[3] is not None else 0.0
                mds[i] = float(op[4]) if len(op) > 4 and op[4] is not None else 0.0
                formation = (op[5] if len(op) > 5 and op[5] else "")
                fb = formation.encode("utf-8", "replace")[:_HM_FORMATION_LEN - 1]
                base = i * _HM_FORMATION_LEN
                forms[base:base + len(fb)] = fb

        mf = (ctypes.c_uint32 * max(len(members_flat), 1))(*members_flat)
        rc = _lib.hm_commit_batch(
            self._ptr,
            ctypes.c_uint32(n),
            types, ev, mcs, mf, offs, ws, mds,
            bytes(forms),
        )
        if rc != 0:
            err = _lib.hm_last_error()
            raise RuntimeError(
                f"hm_commit_batch failed: {err.decode() if err else 'unknown'}"
            )

    def compact(self) -> None:
        """
        Rebuild TPI + FMI from (existing index ∪ WAL inserts − WAL tombstones),
        write new binary files in place, then truncate the WAL.
        After compact(), wal_pending == 0 and all changes are in the TPI.
        Thread-safe. Raises RuntimeError on failure.
        """
        rc = _lib.hm_compact(self._ptr)
        if rc != 0:
            err = _lib.hm_last_error()
            raise RuntimeError(
                f"hm_compact failed: {err.decode() if err else 'unknown'}"
            )

    def compact_with_ttl(self, min_event_ts: int) -> None:
        """
        Like compact(), but additionally discards all records (TPI + WAL)
        whose event_ts < min_event_ts.

        Typical rolling-window usage on an edge device::

            import time
            keep_from = int(time.time()) - 300  # discard records older than 5 min
            store.compact_with_ttl(keep_from)

        Thread-safe. Raises RuntimeError on failure.
        """
        rc = _lib.hm_compact_with_ttl(self._ptr, ctypes.c_uint32(min_event_ts))
        if rc != 0:
            err = _lib.hm_last_error()
            raise RuntimeError(
                f"hm_compact_with_ttl failed: {err.decode() if err else 'unknown'}"
            )

    def set_autocompact(self, threshold: int) -> None:
        """
        Configure automatic WAL compaction.

        After every successful insert or delete, if the WAL entry count
        reaches or exceeds *threshold*, the engine compacts automatically
        (holding the write lock — no deadlock risk).

        threshold=0 disables automatic compaction (the default).

        Typical edge deployment::

            store.set_autocompact(200)   # compact after every 200 WAL entries
        """
        _lib.hm_set_autocompact(self._ptr, ctypes.c_uint32(threshold))

    # ── Metadata ──────────────────────────────────────────────────────────

    @property
    def total_records(self) -> int:
        return _lib.hm_total_records(self._ptr)

    @property
    def bucket_count(self) -> int:
        return _lib.hm_bucket_count(self._ptr)

    @property
    def bucket_seconds(self) -> int:
        return _lib.hm_bucket_seconds(self._ptr)

    @property
    def node_count(self) -> int:
        return _lib.hm_node_count(self._ptr)

    # ── TPI Range Query ───────────────────────────────────────────────────

    # ── Shared result decoder ─────────────────────────────────────────────

    @staticmethod
    def _decode_range_result(raw) -> "RangeResult":
        """Decode a C HmRangeResult* into a Python RangeResult. Always frees raw."""
        try:
            count = _lib.hm_res_count(raw)
            hyperedges = []
            for i in range(count):
                mc = _lib.hm_res_member_count(raw, i)
                members = [int(_lib.hm_res_member_id(raw, i, j)) for j in range(mc)]
                fb = _lib.hm_res_formation(raw, i)
                # V2: read opaque property blob if present
                plen = int(_lib.hm_res_props_len(raw, i))
                pb   = _lib.hm_res_props(raw, i)
                # pb is a POINTER(c_uint8); copy exactly plen bytes (binary-safe)
                props_bytes = bytes(bytearray(pb[:plen])) if (pb and plen > 0) else None
                rec = {
                    "event_ts":     int(_lib.hm_res_timestamp(raw, i)),
                    "members":      members,
                    "member_count": int(mc),
                    "weight":       float(_lib.hm_res_weight(raw, i)),
                    "mean_dist_m":  float(_lib.hm_res_mean_dist(raw, i)),
                    "formation":    fb.decode("utf-8", errors="replace") if fb else "",
                }
                if props_bytes is not None:
                    rec["_props"] = props_bytes
                hyperedges.append(rec)
            sb = _lib.hm_res_strategy(raw)
            plan = QueryPlan(
                strategy        = sb.decode() if sb else "UNKNOWN",
                buckets_scanned = int(_lib.hm_res_buckets_scanned(raw)),
                total_buckets   = int(_lib.hm_res_total_buckets(raw)),
                speedup_factor  = float(_lib.hm_res_speedup(raw)),
                elapsed_us      = int(_lib.hm_res_elapsed_us(raw)),
                timed_out       = bool(_lib.hm_res_timed_out(raw)),
            )
        finally:
            _lib.hm_range_result_free(raw)
        return RangeResult(hyperedges=hyperedges, plan=plan)

    def range_query(self, start_ts: int, end_ts: int) -> "RangeResult":
        """
        Return all hyperedges with event_ts in [start_ts, end_ts].
        Uses TPI bucket pushdown — reads only relevant disk blocks.
        """
        raw = _lib.hm_range_query(self._ptr,
                                   ctypes.c_uint32(start_ts),
                                   ctypes.c_uint32(end_ts))
        if not raw:
            raise RuntimeError("hm_range_query returned NULL")
        res = self._decode_range_result(raw)
        if res.plan.timed_out:
            raise QueryTimeout(
                f"range query exceeded the {self._query_timeout_ms}ms time limit "
                f"(HMDB_QUERY_TIMEOUT_MS)"
            )
        return res

    # ── FMI Coalition Ranking ─────────────────────────────────────────────

    def coalition_ranking(self) -> List[Dict[str, int]]:
        """
        Return all nodes sorted descending by coalition appearance count.
        Single pass over the FMI — O(node_count log node_count).
        """
        raw = _lib.hm_get_coalition_ranking(self._ptr)
        if not raw:
            raise RuntimeError("hm_get_coalition_ranking returned NULL")
        try:
            count = _lib.hm_coal_count(raw)
            return [
                {
                    "node_id":     int(_lib.hm_coal_node_id(raw, i)),
                    "appearances": int(_lib.hm_coal_appearances(raw, i)),
                }
                for i in range(count)
            ]
        finally:
            _lib.hm_coalition_result_free(raw)

    # ── Full-scan baseline (benchmark use only) ──────────────────────────────

    def full_scan_range(self, start_ts: int, end_ts: int) -> "RangeResult":
        """
        Bypass the TPI bucket directory. Read ALL records sequentially and
        filter by [start_ts, end_ts]. Use as the denominator:
          speedup = full_scan.plan.elapsed_us / tpi.plan.elapsed_us
        """
        raw = _lib.hm_full_scan_range(self._ptr,
                                       ctypes.c_uint32(start_ts),
                                       ctypes.c_uint32(end_ts))
        if not raw:
            raise RuntimeError("hm_full_scan_range returned NULL")
        res = self._decode_range_result(raw)
        if res.plan.timed_out:
            raise QueryTimeout(
                f"full scan exceeded the {self._query_timeout_ms}ms time limit "
                f"(HMDB_QUERY_TIMEOUT_MS)"
            )
        return res

    # ── FMI Point Lookup ──────────────────────────────────────────────────

    def fmi_lookup(self, node_id: int) -> List[int]:
        """
        Return raw hyperedge sequence IDs for node_id (TPI only, no WAL).
        O(log node_count) binary search + one pread().
        Use fmi_query() for complete results including WAL.
        """
        out_count = ctypes.c_uint32(0)
        ptr = _lib.hm_fmi_lookup(self._ptr,
                                   ctypes.c_uint32(node_id),
                                   ctypes.byref(out_count))
        if not ptr or out_count.value == 0:
            return []
        ids = [int(ptr[i]) for i in range(out_count.value)]
        libc = ctypes.CDLL(None)
        libc.free(ptr)
        return ids

    def fmi_query(self, node_id: int) -> "RangeResult":
        """
        Full FMI point query: FMI binary search + seq-ID record fetch + WAL merge.

        Algorithm (O(log N + degree + WAL_size)):
          1. FMI binary search → sorted seq-IDs
          2. Sequential scan of hyperedges.bin collecting only those records
             (early-exit when all seq-IDs are found)
          3. WAL tombstones applied to TPI results
          4. WAL INSERT records containing node_id appended

        Returns RangeResult with plan.strategy == "FMI_LOOKUP".
        """
        raw = _lib.hm_fmi_query(self._ptr, ctypes.c_uint32(node_id))
        if not raw:
            err = _lib.hm_last_error()
            raise RuntimeError(f"hm_fmi_query failed: {err.decode() if err else 'unknown'}")
        res = self._decode_range_result(raw)
        if res.plan.timed_out:
            raise QueryTimeout(
                f"FMI query exceeded the {self._query_timeout_ms}ms time limit "
                f"(HMDB_QUERY_TIMEOUT_MS)"
            )
        return res

    # ── Cypher Parser ─────────────────────────────────────────────────────

    @staticmethod
    def parse_query(cypher: str) -> "ParsedQuery":
        """
        Parse a Cypher-like query string using the C recursive-descent parser.
        Returns a ParsedQuery dataclass.  Never raises; on syntax error
        returns ParsedQuery(kind=HmQueryKind.PARSE_ERROR, error=<message>).
        """
        raw = _lib.hm_parse_query_alloc(cypher.encode("utf-8", errors="replace"))
        if not raw:
            return ParsedQuery(
                kind=HmQueryKind.PARSE_ERROR, table="", alias="",
                ts_start=0, ts_end=0, node_id=0,
                error="hm_parse_query_alloc returned NULL",
            )
        try:
            kind_int = _lib.hm_ast_kind(raw)
            try:
                kind = HmQueryKind(kind_int)
            except ValueError:
                kind = HmQueryKind.PARSE_ERROR

            def _str(raw_bytes) -> str:
                return raw_bytes.decode("utf-8", errors="replace") if raw_bytes else ""

            # Phase 2: extract property predicates (including Phase 7 alias)
            pred_count = int(_lib.hm_ast_pred_count(raw))
            predicates = []
            for i in range(pred_count):
                op_raw = int(_lib.hm_ast_pred_op(raw, ctypes.c_uint32(i)))
                try:
                    op = HmPredOp(op_raw)
                except ValueError:
                    op = HmPredOp.EQ
                predicates.append({
                    "col":    _str(_lib.hm_ast_pred_col   (raw, ctypes.c_uint32(i))),
                    "op":     op,
                    "val":    _str(_lib.hm_ast_pred_val   (raw, ctypes.c_uint32(i))),
                    "is_str": bool(int(_lib.hm_ast_pred_is_str(raw, ctypes.c_uint32(i)))),
                    "alias":  _str(_lib.hm_ast_pred_alias (raw, ctypes.c_uint32(i))),
                })

            # Phase 7: join conditions
            join_count = int(_lib.hm_ast_join_count(raw))
            join_predicates = []
            for j in range(join_count):
                jj = ctypes.c_uint32(j)
                op_raw = int(_lib.hm_ast_join_op(raw, jj))
                try:
                    op = HmPredOp(op_raw)
                except ValueError:
                    op = HmPredOp.EQ
                join_predicates.append({
                    "lhs_alias": _str(_lib.hm_ast_join_lhs_alias(raw, jj)),
                    "lhs_col":   _str(_lib.hm_ast_join_lhs_col  (raw, jj)),
                    "rhs_alias": _str(_lib.hm_ast_join_rhs_alias(raw, jj)),
                    "rhs_col":   _str(_lib.hm_ast_join_rhs_col  (raw, jj)),
                    "op":        op,
                })

            return ParsedQuery(
                kind         = kind,
                table        = _str(_lib.hm_ast_table(raw)),
                alias        = _str(_lib.hm_ast_alias(raw)),
                ts_start     = int(_lib.hm_ast_ts_start(raw)),
                ts_end       = int(_lib.hm_ast_ts_end(raw)),
                node_id      = int(_lib.hm_ast_node_id(raw)),
                error        = _str(_lib.hm_ast_error(raw)),
                props_csv    = _str(_lib.hm_ast_props(raw)),
                predicates   = predicates,
                return_cols  = _str(_lib.hm_ast_return_cols(raw)),
                order_col    = _str(_lib.hm_ast_order_col(raw)),
                order_desc   = bool(int(_lib.hm_ast_order_desc(raw))),
                limit_n      = int(_lib.hm_ast_limit_n(raw)),
                # Phase 3: DML
                insert_cols  = _str(_lib.hm_ast_insert_cols(raw)),
                insert_vals  = _str(_lib.hm_ast_insert_vals(raw)),
                update_set   = _str(_lib.hm_ast_update_set(raw)),
                # Phase 4: PSI DDL
                index_col    = _str(_lib.hm_ast_index_col(raw)),
                # Phase 7: join fields
                table2           = _str(_lib.hm_ast_table2(raw)),
                alias2           = _str(_lib.hm_ast_alias2(raw)),
                ts_start2        = int(_lib.hm_ast_ts_start2(raw)),
                ts_end2          = int(_lib.hm_ast_ts_end2(raw)),
                node_id2         = int(_lib.hm_ast_node_id2(raw)),
                join_predicates  = join_predicates if join_predicates else None,
                # Phase 8: DDL compact_threshold
                compact_threshold = int(_lib.hm_ast_compact_threshold(raw)),
            )
        finally:
            _lib.hm_query_ast_free(raw)
