/*
 * format.h — HyperMesh DB On-Disk Binary Format
 *
 * All multi-byte integers are little-endian (native on x86-64 and ARM64).
 * All structs are packed (no padding bytes) using __attribute__((packed)).
 *
 * ── Format versions ──────────────────────────────────────────────────────────
 *
 * V1 (HM_VERSION = 1): original format
 *   hyperedge record = HmHyperedgeHeader + member_ids[member_count]
 *
 * V2 (HM_VERSION_V2 = 2): adds user-defined property blob
 *   hyperedge record = HmHyperedgeHeaderV2 + member_ids[member_count] + props[props_len]
 *
 * V1 databases are read-only with V2 code; they are automatically upgraded to
 * V2 on the next compaction (hm_compact / hm_compact_with_ttl).
 *
 * ── File layout ──────────────────────────────────────────────────────────────
 *
 * bucket_directory.bin  (version stored in HmBucketDirHeader.version)
 *   [HmBucketDirHeader]
 *   [HmBucketDirEntry × header.bucket_count]
 *
 * hyperedges.bin  (V1)
 *   [HmHyperedgeHeader][uint32_t member_ids × header.member_count] ...
 *
 * hyperedges.bin  (V2)
 *   [HmHyperedgeHeaderV2][uint32_t member_ids × member_count][uint8_t props × props_len] ...
 *
 * fmi_nodes.bin         (unchanged across V1/V2)
 *   [HmFmiHeader]
 *   [HmFmiNodeEntry × header.node_count]
 *
 * fmi_adjacency.bin     (unchanged across V1/V2)
 *   [uint32_t × header.total_appearances]  -- hyperedge sequence IDs
 *
 * wal.bin  (version stored in HmWalFileHeader.version)
 *   V1: [HmWalFileHeader][HmWalEntryHeader   + uint32_t members × member_count] × N
 *   V2: [HmWalFileHeader][HmWalEntryHeaderV2 + uint32_t members × member_count
 *                                             + uint8_t  props   × props_len  ] × N
 */

#ifndef HM_FORMAT_H
#define HM_FORMAT_H

#include <stdint.h>

/* ── Constants ───────────────────────────────────────────────────────────── */

#define HM_MAGIC         0x42444D48u  /* "HMDB" in little-endian           */
#define HM_VERSION       1u           /* V1: legacy — read-only with V2 code */
#define HM_VERSION_V2    2u           /* V2: property-blob format          */

/*
 * HM_MAX_MEMBERS: C-level array guard for in-memory HmRecord / HmWalEntry.
 * The on-disk format uses uint8_t member_count (0–255), so the logical upper
 * bound is 255.  The C guard can be raised without changing the on-disk format.
 * NOTE: the patent specification claims "no upper bound"; this constant is an
 * implementation-level safety limit, not an architectural constraint.
 */
#define HM_MAX_MEMBERS         64u   /* C in-memory guard                  */
#define HM_MAX_MEMBERS_LOGICAL 255u  /* on-disk format limit (uint8_t)     */

#define HM_FORMATION_LEN 16u         /* bytes for formation string (incl \0) */

/*
 * HM_MAX_PROPS_LEN: maximum byte length of the property blob per hyperedge.
 * Chosen to be edge-deployment safe: 256 bytes is ample for numeric + short
 * string sensor metadata without threatening stack/WAL size on ARM SBCs.
 */
#define HM_MAX_PROPS_LEN 256u

/* ── bucket_directory.bin ────────────────────────────────────────────────── */

typedef struct __attribute__((packed)) {
    uint32_t magic;           /* must equal HM_MAGIC                        */
    uint8_t  version;         /* HM_VERSION or HM_VERSION_V2                */
    uint32_t bucket_seconds;  /* TPI partition granularity in seconds        */
    uint32_t bucket_count;    /* number of BucketDirEntry records following  */
    uint32_t total_records;   /* total hyperedge records across all buckets  */
} HmBucketDirHeader;

typedef struct __attribute__((packed)) {
    uint32_t bucket_id;      /* floor(event_ts / bucket_seconds)            */
    uint64_t file_offset;    /* byte offset into hyperedges.bin             */
    uint32_t record_count;   /* number of hyperedge records in this bucket  */
} HmBucketDirEntry;

/* ── hyperedges.bin — V1 record header ─────────────────────────────────── */

typedef struct __attribute__((packed)) {
    uint32_t event_ts;                  /* mission timestamp in seconds     */
    uint8_t  member_count;              /* number of node IDs that follow   */
    float    weight;                    /* coalition strength                */
    float    mean_dist_m;               /* mean pairwise distance (metres)  */
    char     formation[HM_FORMATION_LEN]; /* null-terminated formation name */
} HmHyperedgeHeader;

/* V1 on-disk record size: header + member_count × uint32_t */
#define HM_RECORD_BYTES(mc) \
    (sizeof(HmHyperedgeHeader) + (size_t)(mc) * sizeof(uint32_t))

/* ── hyperedges.bin — V2 record header ─────────────────────────────────── */

/*
 * V2 adds props_len: the number of property-blob bytes that immediately follow
 * the member ID array.  When props_len == 0 the V2 record is byte-identical to
 * the V1 record (same header size + same member array) except for the extra
 * uint32_t field — hence V1 and V2 are NOT wire-compatible.
 */
typedef struct __attribute__((packed)) {
    uint32_t event_ts;
    uint8_t  member_count;
    float    weight;
    float    mean_dist_m;
    char     formation[HM_FORMATION_LEN];
    uint32_t props_len;   /* byte length of the property blob that follows  */
} HmHyperedgeHeaderV2;

/* V2 on-disk record size: header + member_count × uint32_t + props_len bytes */
#define HM_RECORD_BYTES_V2(mc, props_len) \
    (sizeof(HmHyperedgeHeaderV2) \
     + (size_t)(mc) * sizeof(uint32_t) \
     + (size_t)(props_len))

/* ── fmi_nodes.bin ───────────────────────────────────────────────────────── */

typedef struct __attribute__((packed)) {
    uint32_t node_count;         /* number of HmFmiNodeEntry records        */
    uint32_t total_appearances;  /* total uint32_t values in adjacency file */
} HmFmiHeader;

typedef struct __attribute__((packed)) {
    uint32_t node_id;        /* the node identifier                         */
    uint64_t list_offset;    /* byte offset into fmi_adjacency.bin          */
    uint32_t count;          /* number of hyperedge seq-IDs for this node   */
} HmFmiNodeEntry;

/* fmi_adjacency.bin: tightly-packed uint32_t sequence IDs, no header */

/* ── wal.bin ─────────────────────────────────────────────────────────────── */

/*
 * Write-Ahead Log for durable inserts and soft deletes.
 *
 * V1 file layout:
 *   [HmWalFileHeader]
 *   [HmWalEntryHeader   + uint32_t members × member_count] × N
 *
 * V2 file layout:
 *   [HmWalFileHeader]
 *   [HmWalEntryHeaderV2 + uint32_t members × member_count
 *                       + uint8_t  props   × props_len  ] × N
 *
 * The WAL is automatically upgraded from V1 to V2 on the next compaction.
 * V1 WALs can still be used for hm_insert_record() calls (props_len = 0).
 * hm_insert_record_v2() with non-empty props requires a V2 WAL.
 *
 * Crash safety: entry bytes are pwrite()-ed then fdatasync()-ed before
 * returning to the caller.  A partial last entry (crash mid-write) is
 * detected by EOF during scan and silently discarded.
 */

#define HM_WAL_MAGIC      0x4C41574Du  /* "WALM" little-endian */
#define HM_WAL_VERSION    1u            /* V1: legacy           */
#define HM_WAL_VERSION_V2 2u            /* V2: with props_len   */
#define HM_WAL_VERSION_V3 3u            /* V3: V2 + per-record CRC32 */
#define HM_WAL_VERSION_V4 4u            /* V4: V3 + atomic batch records */

#define HM_WAL_INSERT   1u   /* entry type: new hyperedge        */
#define HM_WAL_DELETE   2u   /* entry type: soft-delete tombstone */
#define HM_WAL_BATCH    4u   /* record type: atomic group-commit frame */

typedef struct __attribute__((packed)) {
    uint32_t magic;    /* must equal HM_WAL_MAGIC    */
    uint8_t  version;  /* HM_WAL_VERSION or HM_WAL_VERSION_V2 */
} HmWalFileHeader;

/* V1 WAL entry header */
typedef struct __attribute__((packed)) {
    uint8_t  type;                     /* HM_WAL_INSERT or HM_WAL_DELETE  */
    uint32_t event_ts;
    uint8_t  member_count;
    float    weight;
    float    mean_dist_m;
    char     formation[HM_FORMATION_LEN];
} HmWalEntryHeader;

/* V2 WAL entry header — adds props_len field */
typedef struct __attribute__((packed)) {
    uint8_t  type;
    uint32_t event_ts;
    uint8_t  member_count;
    float    weight;
    float    mean_dist_m;
    char     formation[HM_FORMATION_LEN];
    uint32_t props_len;   /* byte length of props blob following member IDs */
} HmWalEntryHeaderV2;

/*
 * V3 WAL entry header — adds crc32 for torn-write / corruption detection.
 *
 * crc32 is the CRC-32 (IEEE 802.3, poly 0xEDB88320) of the entire record with
 * the crc32 field itself zeroed, i.e. CRC over:
 *     [this header with crc32 = 0] ++ [member IDs] ++ [props blob]
 *
 * On open, a record whose recomputed CRC does not match (a torn final write or
 * silent corruption) terminates the scan; the WAL file is then truncated to the
 * end of the last valid record so subsequent appends stay contiguous.
 */
typedef struct __attribute__((packed)) {
    uint8_t  type;
    uint32_t event_ts;
    uint8_t  member_count;
    float    weight;
    float    mean_dist_m;
    char     formation[HM_FORMATION_LEN];
    uint32_t props_len;
    uint32_t crc32;       /* CRC-32 of (header|crc=0) ++ members ++ props */
} HmWalEntryHeaderV3;

/*
 * V4 atomic batch frame (group commit).
 *
 * A whole transaction is written as ONE physical record: this header followed
 * by `payload_len` bytes containing `entry_count` sub-entries, each serialized
 * as [HmWalEntryHeaderV2 ++ members ++ props]. A single `crc32` covers the
 * header (with crc32 zeroed) and the entire payload, so on recovery the batch
 * is strictly all-or-nothing: a torn or corrupt frame is discarded in full and
 * the WAL is truncated to the end of the last intact record. One fdatasync
 * makes the entire transaction durable.
 *
 * The batch record's leading `type` byte (HM_WAL_BATCH) lets the scanner
 * distinguish it from a normal V3 single-entry record, so a V4 WAL may freely
 * interleave single inserts/deletes and batch frames.
 */
typedef struct __attribute__((packed)) {
    uint8_t  type;        /* == HM_WAL_BATCH                                 */
    uint32_t entry_count; /* number of sub-entries in the payload            */
    uint32_t payload_len; /* byte length of the payload following the header */
    uint32_t crc32;       /* CRC-32 of (header|crc=0) ++ payload             */
} HmWalBatchHeader;

#endif /* HM_FORMAT_H */
