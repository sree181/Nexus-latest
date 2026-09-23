/*
 * hypermesh.h — Public C API for HyperMesh DB
 *
 * This is the only header the Python ctypes binding needs to know about.
 * All types are opaque pointers; data is accessed through accessor functions
 * so ctypes never needs to understand struct layouts.
 *
 * Build phase:
 *   hm_build_from_csv()   — parse CSV, write TPI + FMI binary files
 *
 * Query phase:
 *   hm_open() / hm_close()       — open index + WAL, close and free everything
 *   hm_range_query()             — TPI pushdown merged with WAL inserts/deletes
 *   hm_coalition_ranking()       — FMI full pass: nodes sorted by appearances
 *   hm_fmi_lookup()              — FMI point lookup: hyperedge IDs for a node
 *
 * Write phase (WAL-backed):
 *   hm_insert_record()    — append one hyperedge to the WAL (immediately visible)
 *   hm_delete_record()    — write a tombstone; record hidden from future queries
 *   hm_compact()          — rebuild TPI+FMI from WAL+index, truncate WAL
 *   hm_wal_pending()      — number of uncompacted WAL entries
 *
 * Result accessors (avoids pointer arithmetic in Python):
 *   hm_res_*   — access fields of a range query result
 *   hm_coal_*  — access fields of a coalition ranking result
 */

#ifndef HYPERMESH_H
#define HYPERMESH_H

#include <stdint.h>
#include "tpi.h"
#include "fmi.h"
#include "cypher_parser/parser.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ── Opaque handle (wraps HmTpiReader + HmFmiReader) ─────────────────────── */

typedef struct HmStore HmStore;

/* ── Build ───────────────────────────────────────────────────────────────── */

/*
 * Parse hyperedges_csv and build the TPI + FMI index into dir_path.
 * Creates dir_path if it does not exist.
 * bucket_seconds: TPI partition granularity (e.g. 10).
 * Returns 0 on success, -1 on error (check errno / hm_last_error()).
 */
int hm_build_from_csv(const char *dir_path,
                       const char *hyperedges_csv,
                       uint32_t    bucket_seconds);

/*
 * Initialise an empty store in dir_path without any CSV data.
 * Creates dir_path and writes valid empty TPI + FMI files (0 records) so that
 * hm_open() can be called immediately afterwards.  WAL is created lazily on
 * the first insert.  bucket_seconds == 0 is treated as 10.
 * Returns 0 on success, -1 on error.
 */
int hm_init_empty(const char *dir_path, uint32_t bucket_seconds);

/* ── Open / Close ────────────────────────────────────────────────────────── */

/*
 * Open an existing index in dir_path.
 * Loads bucket_directory.bin and fmi_nodes.bin into memory.
 * Returns NULL on error.
 */
HmStore *hm_open(const char *dir_path);

/* Close and free all resources. */
void hm_close(HmStore *store);

/* ── TPI Range Query ─────────────────────────────────────────────────────── */

/*
 * Execute a temporal range query [start_ts, end_ts] (inclusive).
 * Uses TPI bucket pushdown: reads only the relevant disk blocks.
 * Returns NULL on error. Caller must free with hm_range_result_free().
 */
HmRangeResult *hm_range_query(HmStore *store,
                               uint32_t start_ts,
                               uint32_t end_ts);

void hm_range_result_free(HmRangeResult *res);

/* Result accessors — safe to call from ctypes without struct knowledge */
uint32_t    hm_res_count(const HmRangeResult *r);
uint32_t    hm_res_timestamp(const HmRangeResult *r, uint32_t i);
uint8_t     hm_res_member_count(const HmRangeResult *r, uint32_t i);
float       hm_res_weight(const HmRangeResult *r, uint32_t i);
float       hm_res_mean_dist(const HmRangeResult *r, uint32_t i);
const char *hm_res_formation(const HmRangeResult *r, uint32_t i);
uint32_t    hm_res_member_id(const HmRangeResult *r, uint32_t i, uint32_t j);

/*
 * V2 property blob accessor.
 * hm_res_props     — pointer to the props bytes for result i (NULL if no props).
 * hm_res_props_len — byte length of the props blob for result i (0 if none).
 * The returned pointer is owned by the HmRangeResult and is invalidated by
 * hm_range_result_free().
 */
const uint8_t *hm_res_props    (const HmRangeResult *r, uint32_t i);
uint32_t       hm_res_props_len(const HmRangeResult *r, uint32_t i);

/* Query plan (real measured values) */
const char *hm_res_strategy(const HmRangeResult *r);
uint32_t    hm_res_buckets_scanned(const HmRangeResult *r);
uint32_t    hm_res_total_buckets(const HmRangeResult *r);
float       hm_res_speedup(const HmRangeResult *r);
uint64_t    hm_res_elapsed_us(const HmRangeResult *r);

/* ── FMI Coalition Ranking ───────────────────────────────────────────────── */

/*
 * Return all nodes sorted descending by coalition appearance count.
 * Single pass over fmi_nodes.bin — O(node_count log node_count).
 * Caller must free with hm_coalition_result_free().
 */
HmCoalitionResult *hm_get_coalition_ranking(HmStore *store);
void               hm_coalition_result_free(HmCoalitionResult *cr);

uint32_t hm_coal_count(const HmCoalitionResult *cr);
uint32_t hm_coal_node_id(const HmCoalitionResult *cr, uint32_t i);
uint32_t hm_coal_appearances(const HmCoalitionResult *cr, uint32_t i);

/* ── FMI Point Lookup ────────────────────────────────────────────────────── */

/*
 * Low-level: return raw hyperedge sequence IDs for node_id from the FMI index.
 * O(log node_count) binary search + one pread().
 * *out_count is set to the number returned.
 * Returns a heap-allocated array; caller must free() it.
 * Returns NULL and out_count=0 if node_id not found.
 * NOTE: Does NOT merge WAL entries. Use hm_fmi_query() for a complete result.
 */
uint32_t *hm_fmi_lookup(HmStore *store,
                          uint32_t  node_id,
                          uint32_t *out_count);

/*
 * Full FMI point query: binary search + seq-ID record fetch + WAL merge.
 *
 * Algorithm (O(log N + degree + WAL_size)):
 *   1. hm_fmi_reader_lookup() → sorted seq-IDs  (O(log node_count + degree))
 *   2. Sequential scan of hyperedges.bin, collect records at those seq-IDs,
 *      stop as soon as all are found               (O(degree × avg_record_size))
 *   3. Apply WAL tombstones to TPI results         (O(degree × WAL_size))
 *   4. Append WAL INSERT records containing node_id (O(WAL_size))
 *
 * Returns HmRangeResult* with plan.strategy == "FMI_LOOKUP".
 * Caller must free with hm_range_result_free().
 * Returns NULL on OOM.
 */
HmRangeResult *hm_fmi_query(HmStore *store, uint32_t node_id);

/* ── Index metadata ──────────────────────────────────────────────────────── */

uint32_t hm_total_records(HmStore *store);
uint32_t hm_bucket_count(HmStore *store);
uint32_t hm_bucket_seconds(HmStore *store);
uint32_t hm_node_count(HmStore *store);

/* ── Full-scan baseline (for benchmarking only) ─────────────────────────── */

/*
 * Identical semantics to hm_range_query() but deliberately bypasses the TPI
 * bucket directory.  Reads ALL records in hyperedges.bin sequentially and
 * filters to [start_ts, end_ts] in a single pass.
 *
 * Use this as the denominator in speedup measurements:
 *   speedup = hm_full_scan_range() / hm_range_query()
 *
 * plan.strategy will be "FULL_SCAN" and plan.speedup_factor == 1.0.
 * Caller must free with hm_range_result_free().
 */
HmRangeResult *hm_full_scan_range(HmStore *store,
                                   uint32_t start_ts,
                                   uint32_t end_ts);

/* ── Cypher Parser — heap-allocated AST ─────────────────────────────────── */

/*
 * Parse cypher and return a heap-allocated HmQueryAst.
 * Never returns NULL. Caller must free with hm_query_ast_free().
 * On parse error: ast->kind == HM_QUERY_PARSE_ERROR, ast->error is set.
 */
HmQueryAst *hm_parse_query_alloc(const char *cypher);

/* Free a heap-allocated HmQueryAst. */
void hm_query_ast_free(HmQueryAst *ast);

/* Accessors — safe to call from ctypes without exposing the struct layout. */
int         hm_ast_kind    (const HmQueryAst *ast);
uint32_t    hm_ast_ts_start(const HmQueryAst *ast);
uint32_t    hm_ast_ts_end  (const HmQueryAst *ast);
uint32_t    hm_ast_node_id (const HmQueryAst *ast);
const char *hm_ast_table   (const HmQueryAst *ast);
const char *hm_ast_alias   (const HmQueryAst *ast);
const char *hm_ast_error   (const HmQueryAst *ast);
/*
 * hm_ast_props — raw PROPERTIES(...) body captured from CREATE HYPEREDGE TABLE.
 * Returns a comma-separated "name TYPE, name TYPE" string, or "" if absent.
 */
const char *hm_ast_props   (const HmQueryAst *ast);

/*
 * Phase 2: predicate / RETURN / ORDER BY / LIMIT accessors.
 *
 * hm_ast_pred_count  — number of property predicates (0..HM_MAX_PREDICATES).
 * hm_ast_pred_col    — column name for predicate i (uppercase).
 * hm_ast_pred_op     — operator code (HM_PRED_EQ / GT / GEQ / LT / LEQ).
 * hm_ast_pred_val    — value as a decimal or string literal (raw string).
 * hm_ast_pred_is_str — 1 if the value was a quoted string literal, else 0.
 *
 * hm_ast_return_cols — comma-separated RETURN column list; "" means RETURN *.
 * hm_ast_order_col   — ORDER BY column name; "" means no ordering.
 * hm_ast_order_desc  — 1 = DESC, 0 = ASC.
 * hm_ast_limit_n     — LIMIT N; 0 = no limit.
 */
uint32_t    hm_ast_pred_count (const HmQueryAst *ast);
const char *hm_ast_pred_col   (const HmQueryAst *ast, uint32_t i);
uint8_t     hm_ast_pred_op    (const HmQueryAst *ast, uint32_t i);
const char *hm_ast_pred_val   (const HmQueryAst *ast, uint32_t i);
uint8_t     hm_ast_pred_is_str(const HmQueryAst *ast, uint32_t i);
const char *hm_ast_return_cols(const HmQueryAst *ast);
const char *hm_ast_order_col  (const HmQueryAst *ast);
uint8_t     hm_ast_order_desc (const HmQueryAst *ast);
uint32_t    hm_ast_limit_n    (const HmQueryAst *ast);

/*
 * Phase 3: DML accessors.
 *
 * hm_ast_insert_cols — comma-separated column list from INSERT INTO ... (cols)
 *   e.g. "EVENT_TS,MEMBERS,WEIGHT,FORMATION,CONFIDENCE,SEVERITY,LABEL"
 *
 * hm_ast_insert_vals — 0x1F-separated value list for the first VALUES tuple.
 *   e.g. "1000\x1F[1,2]\x1F0.9\x1FWEDGE\x1F0.95\x1F3\x1Falpha"
 *   Python splits on chr(0x1F) to recover individual values.
 *
 * hm_ast_update_set  — alternating COL\x1Fval pairs from the SET clause.
 *   e.g. "CONFIDENCE\x1F0.99\x1FLABEL\x1Fbeta"
 *   Python splits on chr(0x1F) and takes pairs [0::2], [1::2].
 */
const char *hm_ast_insert_cols(const HmQueryAst *ast);
const char *hm_ast_insert_vals(const HmQueryAst *ast);
const char *hm_ast_update_set (const HmQueryAst *ast);
/* Phase 4: PSI DDL */
const char *hm_ast_index_col  (const HmQueryAst *ast);

/* ── Phase 7: Join AST accessors ────────────────────────────────────────────
 *
 * Second HYPEREDGE pattern (only meaningful when kind == HM_QUERY_JOIN):
 */
const char *hm_ast_table2    (const HmQueryAst *ast);
const char *hm_ast_alias2    (const HmQueryAst *ast);
uint32_t    hm_ast_ts_start2 (const HmQueryAst *ast);
uint32_t    hm_ast_ts_end2   (const HmQueryAst *ast);
uint32_t    hm_ast_node_id2  (const HmQueryAst *ast);
/* Per-predicate alias (which alias each pred_col[i] belongs to): */
const char *hm_ast_pred_alias(const HmQueryAst *ast, uint32_t i);
/* Cross-alias join conditions: */
uint8_t     hm_ast_join_count    (const HmQueryAst *ast);
const char *hm_ast_join_lhs_alias(const HmQueryAst *ast, uint32_t i);
const char *hm_ast_join_lhs_col  (const HmQueryAst *ast, uint32_t i);
const char *hm_ast_join_rhs_alias(const HmQueryAst *ast, uint32_t i);
const char *hm_ast_join_rhs_col  (const HmQueryAst *ast, uint32_t i);
uint8_t     hm_ast_join_op       (const HmQueryAst *ast, uint32_t i);

/* Phase 8: per-table TTL / autocompact threshold from CREATE TABLE DDL */
uint32_t    hm_ast_compact_threshold(const HmQueryAst *ast);

/*
 * ── Phase 4: Property Secondary Index (PSI) ───────────────────────────────
 *
 * The PSI API is exposed here so Python ctypes can call it directly.
 * Full documentation in psi.h.
 */
#include "psi.h"

/* ── Concurrent access configuration ────────────────────────────────────── */

/*
 * Configure automatic WAL compaction.
 *
 * After every successful hm_insert_record() or hm_delete_record() call, if the
 * WAL entry count reaches or exceeds threshold, the engine automatically calls
 * hm_compact() before returning to the caller (holding the write lock —
 * no deadlock risk).
 *
 * Set threshold = 0 to disable automatic compaction (the default).
 * A typical edge deployment might use threshold = 200–500.
 */
void hm_set_autocompact(HmStore *store, uint32_t threshold);

/*
 * Configure a cooperative per-query time budget in milliseconds.
 *
 * When set (> 0), the hot scan/merge loops of hm_range_query(),
 * hm_full_scan_range() and hm_fmi_query() poll a deadline and abort early once
 * it is exceeded, returning the partial result built so far with
 * plan.timed_out == 1 (read via hm_res_timed_out()). This prevents a single
 * pathological query from holding the read lock indefinitely.
 *
 * timeout_ms == 0 disables the deadline (the default).
 */
void hm_set_query_timeout_ms(HmStore *store, uint32_t timeout_ms);

/* 1 if the query that produced this result aborted on its deadline, else 0. */
uint8_t hm_res_timed_out(const HmRangeResult *r);

/* ── Write path (WAL-backed) ─────────────────────────────────────────────── */

/*
 * Insert a single hyperedge into the WAL (V1-compatible, no user properties).
 * The record is immediately visible to subsequent hm_range_query() calls
 * (no compaction required).  members must have exactly member_count entries.
 * formation is null-terminated; truncated to HM_FORMATION_LEN-1 characters.
 * Returns 0 on success, -1 on I/O error.
 */
int hm_insert_record(HmStore        *store,
                      uint32_t        event_ts,
                      const uint32_t *members,
                      uint8_t         member_count,
                      float           weight,
                      float           mean_dist_m,
                      const char     *formation);

/*
 * Insert a hyperedge with a user-defined property blob (V2).
 * props points to props_len bytes of opaque property data.  The caller is
 * responsible for encoding (e.g. struct.pack in Python).
 * props / props_len may be NULL / 0 — equivalent to hm_insert_record().
 * Returns 0 on success, -1 on error (check hm_last_error()).
 *
 * Requires a V2 WAL (created automatically for new stores).  Existing V1
 * stores are upgraded to V2 on the next hm_compact() call; until then, pass
 * props = NULL / props_len = 0 to insert without properties.
 */
int hm_insert_record_v2(HmStore        *store,
                         uint32_t        event_ts,
                         const uint32_t *members,
                         uint8_t         member_count,
                         float           weight,
                         float           mean_dist_m,
                         const char     *formation,
                         const uint8_t  *props,
                         uint32_t        props_len);

/*
 * Write a DELETE tombstone to the WAL.
 * All hyperedges matching (event_ts, same member set, order-independent)
 * will be hidden from subsequent queries.
 * Returns 0 on success, -1 on I/O error.
 */
int hm_delete_record(HmStore        *store,
                      uint32_t        event_ts,
                      const uint32_t *members,
                      uint8_t         member_count);

/*
 * Atomic group commit of a whole transaction: write `n` ops (inserts/deletes,
 * given as parallel flat arrays) to the WAL as ONE crash-atomic batch frame
 * under a single write lock and a single fdatasync. All-or-nothing on recovery
 * and isolated from concurrent readers. See hypermesh.c for array semantics.
 * Returns 0 on success, -1 on error; n == 0 is a no-op.
 */
int hm_commit_batch(HmStore        *store,
                     uint32_t        n,
                     const uint8_t  *types,
                     const uint32_t *event_ts,
                     const uint8_t  *member_counts,
                     const uint32_t *members_flat,
                     const uint32_t *member_offsets,
                     const float    *weights,
                     const float    *mean_dists,
                     const char     *formations_flat);

/*
 * Compact the index:
 *   1. Full-scan TPI + all WAL entries.
 *   2. Apply WAL tombstones (remove matching records).
 *   3. Rebuild bucket_directory.bin, hyperedges.bin, fmi_nodes.bin,
 *      fmi_adjacency.bin in dir_path (overwrites existing files).
 *   4. Truncate wal.bin back to the file header.
 *   5. Reload TPI + FMI readers from the new files.
 * Returns 0 on success, -1 on error.
 * Safe to call on an empty WAL (no-op except the rebuild still occurs).
 * Thread-safe: acquires the write lock internally.
 */
int hm_compact(HmStore *store);

/*
 * Compact with TTL: identical to hm_compact() but additionally discards all
 * records (from both TPI and WAL) whose event_ts < min_event_ts.
 *
 * Use this to implement a rolling-window retention policy on edge devices:
 *   uint32_t keep_from = current_time - window_seconds;
 *   hm_compact_with_ttl(store, keep_from);
 *
 * Returns 0 on success, -1 on error.
 * Thread-safe: acquires the write lock internally.
 */
int hm_compact_with_ttl(HmStore *store, uint32_t min_event_ts);

/*
 * Return the number of uncompacted WAL entries (inserts + tombstones).
 * Use as a signal for when to call hm_compact().
 */
uint32_t hm_wal_pending(HmStore *store);

/* ── Error string ────────────────────────────────────────────────────────── */

/* Human-readable description of the last error (thread-local). */
const char *hm_last_error(void);

#ifdef __cplusplus
}
#endif

#endif /* HYPERMESH_H */
