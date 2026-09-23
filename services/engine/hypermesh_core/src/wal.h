/*
 * wal.h — HyperMesh DB Write-Ahead Log
 *
 * The WAL provides durable inserts and soft deletes without rewriting the
 * immutable TPI binary index.  On every hm_range_query() call the engine
 * merges TPI results with WAL inserts and filters out WAL tombstones
 * transparently.
 *
 * Format versioning:
 *   V1 WAL (HM_WAL_VERSION    = 1): no property blob
 *   V2 WAL (HM_WAL_VERSION_V2 = 2): HmWalEntryHeaderV2 + props blob
 *   V3 WAL (HM_WAL_VERSION_V3 = 3): V2 + per-record CRC32 (torn/corrupt recovery)
 *   V4 WAL (HM_WAL_VERSION_V4 = 4): V3 + atomic batch frames (group commit)
 *
 *   New WAL files are always created as V4.
 *   Existing V1/V2/V3 WAL files are opened in their on-disk read mode; they are
 *   upgraded to V4 automatically at the next hm_compact() call.
 *
 *   hm_insert_record() (no props) works on both V1 and V2 WALs.
 *   hm_insert_record_v2() with non-empty props requires a V2 WAL.
 *
 * Call hm_compact() to rebuild TPI+FMI from (TPI ∪ WAL inserts − WAL
 * tombstones) and then truncate the WAL back to the file header.
 */

#ifndef HM_WAL_H
#define HM_WAL_H

#include <stdint.h>
#include <stddef.h>
#include "format.h"
#include "tpi.h"   /* for HmRecord */

/* ── In-memory WAL entry ──────────────────────────────────────────────────── */

typedef struct {
    uint8_t  type;          /* HM_WAL_INSERT or HM_WAL_DELETE                */
    uint32_t event_ts;
    uint8_t  member_count;
    float    weight;
    float    mean_dist_m;
    char     formation[HM_FORMATION_LEN];
    uint32_t members[HM_MAX_MEMBERS];
    /* V2 extension: opaque property blob */
    uint32_t props_len;
    uint8_t  props[HM_MAX_PROPS_LEN];
} HmWalEntry;

/* ── WAL handle ───────────────────────────────────────────────────────────── */

typedef struct {
    int      fd;                /* open O_RDWR | O_CREAT; -1 if closed       */
    char     path[512];
    uint32_t entry_count;       /* validated count from scan at open          */
    uint8_t  format_version;    /* HM_WAL_VERSION .. HM_WAL_VERSION_V4         */
} HmWal;

/*
 * Open (or create) wal.bin in dir_path.
 * Scans any existing entries to establish entry_count and detect corruption.
 * New files are created as V2 (HM_WAL_VERSION_V2).
 * Existing V1 files are opened for reading in V1 mode; they upgrade to V2
 * after the next compaction.
 * Returns NULL on I/O error.
 */
HmWal *hm_wal_open(const char *dir_path);

/*
 * Append an INSERT entry.  Writes the entry bytes then calls fdatasync so
 * the entry is either fully on disk or not visible at all after a crash.
 * Returns 0 on success, -1 on error.
 *
 * On a V1 WAL: rec->props_len must be 0 (props not supported in V1 format).
 *              Pass a record with props_len = 0 or use hm_insert_record().
 * On a V2 WAL: rec->props (up to HM_MAX_PROPS_LEN bytes) is written.
 */
int hm_wal_append_insert(HmWal *wal, const HmRecord *rec);

/*
 * Append a DELETE tombstone for the exact hyperedge identified by
 * (event_ts, members[0..member_count-1]).  member order is irrelevant —
 * matching is order-independent at query time.
 * Returns 0 on success, -1 on error.
 */
int hm_wal_append_delete(HmWal          *wal,
                          uint32_t        event_ts,
                          uint8_t         member_count,
                          const uint32_t *members);

/*
 * Atomic group commit: append `n` entries (inserts and/or deletes) as a single
 * crash-atomic batch frame, made durable with ONE fdatasync.
 *
 * On a V4 WAL the entries are written inside an HmWalBatchHeader frame with a
 * single CRC over the whole batch — recovery either sees all `n` entries or
 * none. On an older WAL (V1–V3, no batch record type) this falls back to
 * writing the entries individually followed by one fdatasync: still a single
 * sync (group commit), but only the V4 path is strictly all-or-nothing.
 *
 * `n == 0` is a no-op returning 0. Returns 0 on success, -1 on error.
 */
int hm_wal_append_batch(HmWal *wal, const HmWalEntry *entries, uint32_t n);

/*
 * Read ALL entries from the WAL.  Caller receives a heap-allocated array
 * of *out_count HmWalEntry structs; caller must free().
 * Returns NULL (and *out_count = 0) if the WAL is empty or on OOM.
 */
HmWalEntry *hm_wal_scan_all(HmWal *wal, uint32_t *out_count);

/*
 * Read WAL entries whose event_ts lies in [start_ts, end_ts] (inclusive).
 * Writes up to out_cap entries into caller-provided out[].
 * Returns the actual number of entries written.
 */
uint32_t hm_wal_scan_range(HmWal      *wal,
                             uint32_t    start_ts,
                             uint32_t    end_ts,
                             HmWalEntry *out,
                             uint32_t    out_cap);

/*
 * Truncate wal.bin to just the V2 file header (post-compaction).
 * entry_count is reset to 0.  If the WAL was V1, its header is upgraded to V2.
 * Returns 0 on success, -1 on error.
 */
int hm_wal_truncate(HmWal *wal);

/* Close the WAL file descriptor and free the HmWal struct. */
void hm_wal_close(HmWal *wal);

#endif /* HM_WAL_H */
