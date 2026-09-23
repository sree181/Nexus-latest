/*
 * tpi.h — Temporal Property Index: writer and reader
 *
 * Writer: accumulates HmRecord structs in memory, then flushes to
 *         bucket_directory.bin + hyperedges.bin + fmi_nodes.bin + fmi_adjacency.bin.
 *
 * Reader: loads the bucket directory into memory at open() time, then
 *         performs O(log B) binary-searched, pread()-based range queries.
 *
 * Format versioning:
 *   The reader detects V1 vs V2 from HmBucketDirHeader.version and applies
 *   the appropriate record-stride formula.  The writer always emits V2.
 *   V1 databases are upgraded to V2 automatically on the next compaction.
 */

#ifndef HM_TPI_H
#define HM_TPI_H

#include <stdint.h>
#include <stddef.h>
#include "format.h"

/* ── In-memory record (used only during index build / compaction) ─────────── */

typedef struct {
    uint32_t event_ts;
    uint8_t  member_count;
    float    weight;
    float    mean_dist_m;
    char     formation[HM_FORMATION_LEN];
    uint32_t members[HM_MAX_MEMBERS];
    /* V2 extension: opaque property blob */
    uint32_t props_len;
    uint8_t  props[HM_MAX_PROPS_LEN];
} HmRecord;

/* ── TPI Writer ──────────────────────────────────────────────────────────── */

typedef struct {
    uint32_t  bucket_seconds;
    HmRecord *records;    /* dynamic array, grown with realloc              */
    uint32_t  count;      /* number of records inserted so far              */
    uint32_t  capacity;   /* current allocated capacity                     */
} HmTpiWriter;

/* Allocate a new writer. Returns NULL on OOM. */
HmTpiWriter *hm_tpi_writer_new(uint32_t bucket_seconds);

/*
 * Append one record. Returns 0 on success, -1 on OOM.
 * The writer copies all fields from *rec (including props).
 */
int hm_tpi_writer_insert(HmTpiWriter *w, const HmRecord *rec);

/*
 * Sort all records by event_ts, write bucket_directory.bin + hyperedges.bin
 * (V2 format) into dir_path, then build and write fmi_nodes.bin + fmi_adjacency.bin.
 * Returns 0 on success, -1 on I/O or OOM error (errno is set).
 */
int hm_tpi_writer_flush(HmTpiWriter *w, const char *dir_path);
void hm_tpi_cleanup_tmp_files(const char *dir_path);

/* Free all memory owned by the writer (does not close any files). */
void hm_tpi_writer_free(HmTpiWriter *w);

/* ── Query plan returned with every range result ─────────────────────────── */

typedef struct {
    uint8_t  timed_out;        /* 1 if the query aborted on the deadline    */
    char     strategy[32];     /* "TPI_BUCKET_PUSHDOWN" or "FULL_SCAN"     */
    uint32_t buckets_scanned;  /* actual buckets read from disk             */
    uint32_t total_buckets;    /* total buckets in the index                */
    float    speedup_factor;   /* total_buckets / buckets_scanned           */
    uint64_t elapsed_us;       /* wall-clock microseconds for the query     */
} HmQueryPlan;

/* ── Range query result ──────────────────────────────────────────────────── */

/*
 * Flat, ctypes-friendly result struct. All arrays are heap-allocated and
 * must be freed with hm_range_result_free().
 *
 * For hyperedge i (0 ≤ i < count):
 *   timestamp    = timestamps[i]
 *   member_count = member_counts[i]
 *   weight       = weights[i]
 *   mean_dist_m  = mean_dists[i]
 *   formation    = &formations_flat[i * HM_FORMATION_LEN]   (null-terminated)
 *   member j     = member_ids_flat[member_offsets[i] + j]
 *
 * V2 property blob (NULL for V1 databases or records with no props):
 *   props data   = props_flat + props_offsets[i]  (props_lens[i] bytes)
 *   If props_flat == NULL, no properties were stored (V1 or empty props).
 */
typedef struct {
    uint32_t  count;
    uint32_t *timestamps;
    uint8_t  *member_counts;
    float    *weights;
    float    *mean_dists;
    char     *formations_flat;  /* count × HM_FORMATION_LEN bytes           */
    uint32_t *member_ids_flat;  /* all member IDs concatenated               */
    uint32_t *member_offsets;   /* member_offsets[i] = start in flat array  */
    /* V2 property blobs (NULL for V1 results or records with no props) */
    uint8_t  *props_flat;       /* concatenated property blobs              */
    uint32_t *props_offsets;    /* props_offsets[i] = byte offset in props_flat */
    uint32_t *props_lens;       /* props_lens[i] = byte length for record i */
    HmQueryPlan plan;
} HmRangeResult;

/* ── TPI Reader ──────────────────────────────────────────────────────────── */

typedef struct {
    HmBucketDirHeader  header;
    HmBucketDirEntry  *directory;      /* loaded fully into memory at open() */
    int                he_fd;          /* file descriptor for hyperedges.bin */
    uint8_t            format_version; /* HM_VERSION or HM_VERSION_V2       */
} HmTpiReader;

/*
 * Open the TPI index in dir_path. Loads the bucket directory into memory.
 * Accepts both V1 (HM_VERSION) and V2 (HM_VERSION_V2) format.
 * Returns NULL on error (errno set).
 */
HmTpiReader *hm_tpi_reader_open(const char *dir_path);

/*
 * Execute a temporal range query [start_ts, end_ts] (inclusive).
 * Binary-searches the bucket directory, then pread()s only the relevant
 * contiguous byte ranges from hyperedges.bin.
 * Returns a heap-allocated HmRangeResult, or NULL on error.
 * Caller must free with hm_range_result_free().
 *
 * V1 stores: props_flat/props_offsets/props_lens are NULL / all-zero.
 * V2 stores: props arrays are populated from the property blob in each record.
 */
HmRangeResult *hm_tpi_range_query(HmTpiReader *r,
                                   uint32_t start_ts,
                                   uint32_t end_ts);

/* Free all memory inside a range result (then frees the result itself). */
void hm_range_result_free(HmRangeResult *res);

/* Close the reader and free all memory it owns. */
void hm_tpi_reader_close(HmTpiReader *r);

#endif /* HM_TPI_H */
