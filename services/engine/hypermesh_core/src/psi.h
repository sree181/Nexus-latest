/*
 * psi.h — Property Secondary Index for HyperMesh DB
 *
 * ── Overview ──────────────────────────────────────────────────────────────────
 *
 * The PSI accelerates WHERE-predicate queries on user-defined property columns
 * (FLOAT / INTEGER).  It is the third index pillar alongside:
 *
 *   TPI  — Temporal Property Index   (WHERE event_ts BETWEEN T1 AND T2)
 *   FMI  — Fast Membership Index     (WHERE N IN he.members)
 *   PSI  — Property Secondary Index  (WHERE he.CONFIDENCE >= 0.8)
 *
 * ── File format ───────────────────────────────────────────────────────────────
 *
 *   psi_<TABLE>_<COL>.bin
 *   ┌───────────────────────────────────────────────────────┐
 *   │  HmPsiHeader  (16 bytes)                              │
 *   │    magic        uint32  HM_PSI_MAGIC (0x50534900)     │
 *   │    version      uint32  HM_PSI_VERSION (1)            │
 *   │    entry_count  uint32  number of entries             │
 *   │    col_type     uint8   HM_PSI_FLOAT or HM_PSI_INT   │
 *   │    _pad[3]      uint8                                 │
 *   ├───────────────────────────────────────────────────────┤
 *   │  HmPsiEntry[entry_count]  (8 bytes each)              │
 *   │    value    float32  — property value (for INT: cast) │
 *   │    event_ts uint32   — hyperedge timestamp            │
 *   │                                                       │
 *   │  Entries are sorted ascending by value, then by       │
 *   │  event_ts for stability.                              │
 *   └───────────────────────────────────────────────────────┘
 *
 * ── Edge-hardware notes ───────────────────────────────────────────────────────
 *
 * The entire PSI for a column is rebuilt atomically during compact().
 * No partial updates, no write-ahead log for the PSI itself — the main WAL
 * already guarantees durability of the underlying records.
 *
 * Memory: lookup uses a stack-allocated binary search (zero heap).
 * Storage: 8 bytes × N entries per indexed column.
 */

#ifndef HM_PSI_H
#define HM_PSI_H

#include <stdint.h>
#include <stddef.h>   /* size_t */

/* ── Magic / version ─────────────────────────────────────────────────────── */

#define HM_PSI_MAGIC    0x50534900u  /* "PSI\0" */
#define HM_PSI_VERSION  1u

/* ── Column type tags ────────────────────────────────────────────────────── */

#define HM_PSI_FLOAT    0   /* REAL  column — value stored as float32 */
#define HM_PSI_INT      1   /* INTEGER column — value cast to float32  */

/* ── Predicate operator codes (mirror parser.h HM_PRED_*) ───────────────── */

#define HM_PSI_EQ   0
#define HM_PSI_GT   1
#define HM_PSI_GEQ  2
#define HM_PSI_LT   3
#define HM_PSI_LEQ  4

/* ── On-disk structures ──────────────────────────────────────────────────── */

#pragma pack(push, 1)

typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t entry_count;
    uint8_t  col_type;   /* HM_PSI_FLOAT or HM_PSI_INT */
    uint8_t  _pad[3];
} HmPsiHeader;  /* 16 bytes */

typedef struct {
    float    value;     /* property value                   */
    uint32_t event_ts;  /* hyperedge timestamp              */
} HmPsiEntry;   /* 8 bytes */

#pragma pack(pop)

/* ── Filename helper ─────────────────────────────────────────────────────── */

/*
 * hm_psi_filename — Write the canonical PSI filename for (table, col) into
 * buf[bufsz].  Table and column names are uppercased automatically.
 *
 * Format:  psi_<TABLE>_<COL>.bin
 * Returns: buf on success, NULL if the path exceeds bufsz.
 */
char *hm_psi_filename(const char *dir,
                      const char *table,
                      const char *col,
                      char       *buf,
                      size_t      bufsz);

/* ── Write ───────────────────────────────────────────────────────────────── */

/*
 * hm_psi_write — Build and persist a PSI for one property column.
 *
 * Entries are sorted by value (ascending) before writing; the caller does
 * not need to pre-sort them.
 *
 * Parameters:
 *   dir        — store directory path
 *   table      — hyperedge table name (will be uppercased in filename)
 *   col        — property column name (will be uppercased in filename)
 *   col_type   — HM_PSI_FLOAT or HM_PSI_INT
 *   entries    — array of (value, event_ts) pairs (unsorted, may be NULL if n==0)
 *   n          — number of entries
 *
 * Returns 0 on success, -1 on I/O error (check hm_last_error()).
 */
int hm_psi_write(const char       *dir,
                 const char       *table,
                 const char       *col,
                 uint8_t           col_type,
                 const HmPsiEntry *entries,
                 uint32_t          n);

/* ── Lookup ──────────────────────────────────────────────────────────────── */

/*
 * hm_psi_lookup — Binary-search the PSI for entries satisfying (value op threshold).
 *
 * Parameters:
 *   dir        — store directory path
 *   table      — hyperedge table name
 *   col        — property column name
 *   op         — HM_PSI_EQ / GT / GEQ / LT / LEQ
 *   threshold  — comparison value
 *   out_ts     — caller-allocated output buffer for matching event_ts values
 *   max_out    — capacity of out_ts
 *   out_count  — set to number of matching entries on success
 *
 * Returns:
 *    0  — success (out_count set, out_ts filled up to max_out)
 *   -1  — PSI file not found or corrupt (caller falls back to full scan)
 *   -2  — output buffer too small (out_count set to total matches; caller
 *          may reallocate and retry, or fall back)
 */
int hm_psi_lookup(const char *dir,
                  const char *table,
                  const char *col,
                  uint8_t     op,
                  float       threshold,
                  uint32_t   *out_ts,
                  uint32_t    max_out,
                  uint32_t   *out_count);

/* ── Existence check ─────────────────────────────────────────────────────── */

/*
 * hm_psi_exists — Returns 1 if a PSI file exists for (table, col), 0 otherwise.
 */
int hm_psi_exists(const char *dir, const char *table, const char *col);

/* ── Remove ──────────────────────────────────────────────────────────────── */

/*
 * hm_psi_remove — Delete the PSI file for (table, col).
 * Returns 0 on success (or file already absent), -1 on error.
 */
int hm_psi_remove(const char *dir, const char *table, const char *col);

#endif /* HM_PSI_H */
