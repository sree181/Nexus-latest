/*
 * tpi.c — Temporal Property Index implementation
 *
 * Writer flush algorithm (always writes V2):
 *   1. qsort all records by event_ts  (gives bucket order + intra-bucket order)
 *   2. Single linear pass: write HmHyperedgeHeaderV2 + member_ids + props_blob
 *      for each record, tracking bucket boundaries to build the directory.
 *   3. Write bucket_directory.bin (V2 header + directory entries).
 *   4. Second pass over records: build FMI inverted index, write fmi files.
 *
 * Reader range_query algorithm (version-gated):
 *   1. Compute k_start = start_ts / bucket_seconds  (O(1))
 *   2. Compute k_end   = end_ts   / bucket_seconds  (O(1))
 *   3. Binary-search directory for k_start → first entry (O(log B))
 *   4. Linear scan directory from k_start to k_end, collect (offset, count)
 *   5. pread() each contiguous byte range from hyperedges.bin
 *   6. V1: stride = sizeof(HmHyperedgeHeader)  + mc * 4
 *      V2: stride = sizeof(HmHyperedgeHeaderV2) + mc * 4 + props_len
 *   7. Filter records to [start_ts, end_ts], populate HmRangeResult
 */

#include "tpi.h"
#include "fmi.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <time.h>

/* ── Internal helpers ────────────────────────────────────────────────────── */

static uint64_t now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)(ts.tv_nsec / 1000);
}

static int cmp_by_event_ts(const void *a, const void *b) {
    uint32_t ta = ((const HmRecord *)a)->event_ts;
    uint32_t tb = ((const HmRecord *)b)->event_ts;
    return (ta > tb) - (ta < tb);
}

/* ── Dynamic array helpers for building the bucket directory ─────────────── */

typedef struct {
    HmBucketDirEntry *data;
    uint32_t          count;
    uint32_t          cap;
} DirArray;

static int dir_push(DirArray *d,
                    uint32_t bucket_id,
                    uint64_t file_offset,
                    uint32_t record_count)
{
    if (d->count == d->cap) {
        uint32_t new_cap = (d->cap == 0) ? 32 : d->cap * 2;
        HmBucketDirEntry *p = realloc(d->data,
                                      new_cap * sizeof(HmBucketDirEntry));
        if (!p) return -1;
        d->data = p;
        d->cap  = new_cap;
    }
    d->data[d->count++] = (HmBucketDirEntry){bucket_id, file_offset,
                                              record_count};
    return 0;
}

/* ── FMI helpers (built inside flush) ───────────────────────────────────── */

typedef struct { uint32_t node_id; uint32_t seq_id; } FmiPair;

static int cmp_fmi_pair(const void *a, const void *b) {
    uint32_t na = ((const FmiPair *)a)->node_id;
    uint32_t nb = ((const FmiPair *)b)->node_id;
    if (na != nb) return (na > nb) - (na < nb);
    uint32_t sa = ((const FmiPair *)a)->seq_id;
    uint32_t sb = ((const FmiPair *)b)->seq_id;
    return (sa > sb) - (sa < sb);
}

static int write_fmi(const HmRecord *records, uint32_t count,
                     const char *dir_path)
{
    size_t total = 0;
    for (uint32_t i = 0; i < count; i++) total += records[i].member_count;

    FmiPair *pairs = malloc(total * sizeof(FmiPair));
    if (!pairs) return -1;

    size_t p = 0;
    for (uint32_t seq = 0; seq < count; seq++) {
        const HmRecord *r = &records[seq];
        for (uint8_t m = 0; m < r->member_count; m++) {
            pairs[p].node_id = r->members[m];
            pairs[p].seq_id  = seq;
            p++;
        }
    }

    qsort(pairs, total, sizeof(FmiPair), cmp_fmi_pair);

    uint32_t node_count = 0;
    for (size_t i = 0; i < total; i++) {
        if (i == 0 || pairs[i].node_id != pairs[i-1].node_id) node_count++;
    }

    HmFmiNodeEntry *nodes = malloc(node_count * sizeof(HmFmiNodeEntry));
    uint32_t       *adj   = malloc(total       * sizeof(uint32_t));
    if (!nodes || !adj) { free(pairs); free(nodes); free(adj); return -1; }

    uint32_t ni = 0;
    uint64_t adj_byte_offset = 0;
    for (size_t i = 0; i < total; ) {
        uint32_t cur_node = pairs[i].node_id;
        size_t   start    = i;
        while (i < total && pairs[i].node_id == cur_node) {
            adj[i] = pairs[i].seq_id;
            i++;
        }
        uint32_t c = (uint32_t)(i - start);
        nodes[ni].node_id     = cur_node;
        nodes[ni].list_offset = adj_byte_offset;
        nodes[ni].count       = c;
        adj_byte_offset      += c * sizeof(uint32_t);
        ni++;
    }

    free(pairs);

    char path[512];
    snprintf(path, sizeof(path), "%s/fmi_nodes.bin.tmp", dir_path);
    FILE *nf = fopen(path, "wb");
    if (!nf) { free(nodes); free(adj); return -1; }

    HmFmiHeader fhdr = {node_count, (uint32_t)total};
    if (fwrite(&fhdr,  sizeof(fhdr),  1,          nf) != 1 ||
        fwrite(nodes,  sizeof(*nodes), node_count, nf) != node_count)
    {
        fclose(nf); free(nodes); free(adj); return -1;
    }
    fclose(nf);
    free(nodes);

    snprintf(path, sizeof(path), "%s/fmi_adjacency.bin.tmp", dir_path);
    FILE *af = fopen(path, "wb");
    if (!af) { free(adj); return -1; }
    int ok = (fwrite(adj, sizeof(uint32_t), total, af) == total);
    fclose(af);
    free(adj);
    return ok ? 0 : -1;
}

/*
 * fsync a file by path (flushes its dirty data pages to stable storage).
 * Opening O_RDONLY is sufficient — fsync flushes the underlying inode's pages
 * regardless of which descriptor wrote them. Returns 0 on success, -1 on error.
 */
static int fsync_path(const char *path)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    int rc = fsync(fd);
    close(fd);
    return rc;
}

/*
 * fsync the containing directory so a rename() into it is itself durable.
 * Without this, a crash can lose the rename even after the file data is synced.
 */
static int fsync_dir(const char *dir_path)
{
    int dfd = open(dir_path, O_RDONLY
#ifdef O_DIRECTORY
                   | O_DIRECTORY
#endif
                   );
    if (dfd < 0) return -1;
    int rc = fsync(dfd);
    close(dfd);
    return rc;
}

/*
 * Atomically rename four .tmp files to their final names.
 * Called after all four tmp files have been written successfully.
 * Returns 0 on success, -1 if any rename fails (partial state possible,
 * but WAL is still intact so data is not lost — just requires re-compact).
 */
static int commit_tmp_files(const char *dir_path)
{
    char tmp[512], final[512];
    static const char *names[] = {
        "hyperedges.bin",
        "bucket_directory.bin",
        "fmi_nodes.bin",
        "fmi_adjacency.bin",
    };
    for (int i = 0; i < 4; i++) {
        snprintf(tmp,   sizeof(tmp),   "%s/%s.tmp",  dir_path, names[i]);
        snprintf(final, sizeof(final), "%s/%s",       dir_path, names[i]);
        if (rename(tmp, final) != 0) return -1;
    }
    return 0;
}

/* Remove any leftover .tmp files from a failed previous compaction. */
static void cleanup_tmp_files(const char *dir_path)
{
    char path[512];
    static const char *names[] = {
        "hyperedges.bin.tmp",
        "bucket_directory.bin.tmp",
        "fmi_nodes.bin.tmp",
        "fmi_adjacency.bin.tmp",
    };
    for (int i = 0; i < 4; i++) {
        snprintf(path, sizeof(path), "%s/%s", dir_path, names[i]);
        remove(path); /* ignore errors — file may not exist */
    }
}

/* ── Writer ──────────────────────────────────────────────────────────────── */

HmTpiWriter *hm_tpi_writer_new(uint32_t bucket_seconds) {
    HmTpiWriter *w = calloc(1, sizeof(HmTpiWriter));
    if (!w) return NULL;
    w->bucket_seconds = bucket_seconds;
    return w;
}

int hm_tpi_writer_insert(HmTpiWriter *w, const HmRecord *rec) {
    if (w->count == w->capacity) {
        uint32_t new_cap = (w->capacity == 0) ? 256 : w->capacity * 2;
        HmRecord *p = realloc(w->records, new_cap * sizeof(HmRecord));
        if (!p) return -1;
        w->records   = p;
        w->capacity  = new_cap;
    }
    w->records[w->count++] = *rec;
    return 0;
}

int hm_tpi_writer_flush(HmTpiWriter *w, const char *dir_path) {
    /* Clean up any .tmp files left by a previous failed compaction. */
    cleanup_tmp_files(dir_path);

    if (w->count == 0) {
        char path[512];
        int rc = 0;

        snprintf(path, sizeof(path), "%s/hyperedges.bin.tmp", dir_path);
        FILE *hef = fopen(path, "wb");
        if (!hef) return -1;
        fclose(hef);

        snprintf(path, sizeof(path), "%s/bucket_directory.bin.tmp", dir_path);
        FILE *bdf = fopen(path, "wb");
        if (!bdf) return -1;
        HmBucketDirHeader hdr;
        memset(&hdr, 0, sizeof(hdr));
        hdr.magic          = HM_MAGIC;
        hdr.version        = HM_VERSION_V2;   /* always write V2 */
        hdr.bucket_seconds = w->bucket_seconds;
        hdr.bucket_count   = 0;
        hdr.total_records  = 0;
        if (fwrite(&hdr, sizeof(hdr), 1, bdf) != 1) rc = -1;
        fclose(bdf);
        if (rc < 0) { cleanup_tmp_files(dir_path); return -1; }

        snprintf(path, sizeof(path), "%s/fmi_nodes.bin.tmp", dir_path);
        FILE *nf = fopen(path, "wb");
        if (!nf) { cleanup_tmp_files(dir_path); return -1; }
        HmFmiHeader fhdr = {0, 0};
        if (fwrite(&fhdr, sizeof(fhdr), 1, nf) != 1) rc = -1;
        fclose(nf);
        if (rc < 0) { cleanup_tmp_files(dir_path); return -1; }

        snprintf(path, sizeof(path), "%s/fmi_adjacency.bin.tmp", dir_path);
        FILE *af = fopen(path, "wb");
        if (!af) { cleanup_tmp_files(dir_path); return -1; }
        fclose(af);

        if (commit_tmp_files(dir_path) != 0) {
            cleanup_tmp_files(dir_path); return -1;
        }
        return 0;
    }

    /* ── Step 1: sort by event_ts ─────────────────────────────────────── */
    qsort(w->records, w->count, sizeof(HmRecord), cmp_by_event_ts);

    /* ── Step 2: write hyperedges.bin.tmp (V2) + build bucket directory ── */
    char he_path[512];
    snprintf(he_path, sizeof(he_path), "%s/hyperedges.bin.tmp", dir_path);
    FILE *hef = fopen(he_path, "wb");
    if (!hef) return -1;

    DirArray dir = {NULL, 0, 0};
    uint64_t cur_offset      = 0;
    uint32_t cur_bucket_id   = UINT32_MAX;
    uint64_t cur_bucket_off  = 0;
    uint32_t cur_bucket_cnt  = 0;

    for (uint32_t i = 0; i < w->count; i++) {
        const HmRecord *rec = &w->records[i];
        uint32_t bucket_id  = rec->event_ts / w->bucket_seconds;

        if (bucket_id != cur_bucket_id) {
            if (cur_bucket_id != UINT32_MAX) {
                if (dir_push(&dir, cur_bucket_id,
                             cur_bucket_off, cur_bucket_cnt) < 0)
                {
                    fclose(hef); free(dir.data);
                    cleanup_tmp_files(dir_path); return -1;
                }
            }
            cur_bucket_id  = bucket_id;
            cur_bucket_off = cur_offset;
            cur_bucket_cnt = 0;
        }

        /* Write V2 record: HmHyperedgeHeaderV2 + member IDs + props blob */
        HmHyperedgeHeaderV2 hdr;
        memset(&hdr, 0, sizeof(hdr));
        hdr.event_ts     = rec->event_ts;
        hdr.member_count = rec->member_count;
        hdr.weight       = rec->weight;
        hdr.mean_dist_m  = rec->mean_dist_m;
        strncpy(hdr.formation, rec->formation, HM_FORMATION_LEN - 1);
        uint32_t props_len = rec->props_len < HM_MAX_PROPS_LEN
                             ? rec->props_len : HM_MAX_PROPS_LEN;
        hdr.props_len = props_len;

        if (fwrite(&hdr, sizeof(hdr), 1, hef) != 1) {
            fclose(hef); free(dir.data);
            cleanup_tmp_files(dir_path); return -1;
        }
        size_t mc = rec->member_count;
        if (mc > 0 &&
            fwrite(rec->members, sizeof(uint32_t), mc, hef) != mc)
        {
            fclose(hef); free(dir.data);
            cleanup_tmp_files(dir_path); return -1;
        }
        if (props_len > 0 &&
            fwrite(rec->props, 1, props_len, hef) != props_len)
        {
            fclose(hef); free(dir.data);
            cleanup_tmp_files(dir_path); return -1;
        }

        cur_offset   += HM_RECORD_BYTES_V2(mc, props_len);
        cur_bucket_cnt++;
    }

    if (cur_bucket_id != UINT32_MAX) {
        if (dir_push(&dir, cur_bucket_id,
                     cur_bucket_off, cur_bucket_cnt) < 0)
        {
            fclose(hef); free(dir.data);
            cleanup_tmp_files(dir_path); return -1;
        }
    }
    fclose(hef);

    /* ── Step 3: write bucket_directory.bin.tmp (V2 header) ────────────── */
    char bd_path[512];
    snprintf(bd_path, sizeof(bd_path), "%s/bucket_directory.bin.tmp", dir_path);
    FILE *bdf = fopen(bd_path, "wb");
    if (!bdf) { free(dir.data); cleanup_tmp_files(dir_path); return -1; }

    HmBucketDirHeader bdhdr;
    memset(&bdhdr, 0, sizeof(bdhdr));
    bdhdr.magic          = HM_MAGIC;
    bdhdr.version        = HM_VERSION_V2;   /* always write V2 */
    bdhdr.bucket_seconds = w->bucket_seconds;
    bdhdr.bucket_count   = dir.count;
    bdhdr.total_records  = w->count;

    int ok = (fwrite(&bdhdr,    sizeof(bdhdr),     1,         bdf) == 1 &&
              fwrite(dir.data,  sizeof(*dir.data),  dir.count, bdf) == dir.count);
    fclose(bdf);
    free(dir.data);
    if (!ok) { cleanup_tmp_files(dir_path); return -1; }

    /* ── Step 4: write FMI .tmp files ────────────────────────────────────── */
    if (write_fmi(w->records, w->count, dir_path) != 0) {
        cleanup_tmp_files(dir_path); return -1;
    }

    /* ── Step 5: make the new index durable, then atomically swap it in ────
     *
     * Ordering matters for crash safety: every .tmp file's data must reach
     * stable storage BEFORE the rename, and the directory entry for the rename
     * must be synced too. The caller (compaction) only truncates the WAL after
     * this returns, so the invariant is:
     *     fsync(tmp files) → rename(tmp→final) → fsync(dir) → [caller] WAL trunc
     * A crash at any point leaves either the old index + intact WAL, or the new
     * index fully on disk — never a half-written index with a truncated WAL. */
    static const char *tmp_names[] = {
        "hyperedges.bin.tmp",
        "bucket_directory.bin.tmp",
        "fmi_nodes.bin.tmp",
        "fmi_adjacency.bin.tmp",
    };
    for (int i = 0; i < 4; i++) {
        char tp[512];
        snprintf(tp, sizeof(tp), "%s/%s", dir_path, tmp_names[i]);
        if (fsync_path(tp) != 0) { cleanup_tmp_files(dir_path); return -1; }
    }

    if (commit_tmp_files(dir_path) != 0) {
        cleanup_tmp_files(dir_path); return -1;
    }

    /* Best-effort durability of the rename itself. */
    fsync_dir(dir_path);
    return 0;
}

void hm_tpi_cleanup_tmp_files(const char *dir_path)
{
    cleanup_tmp_files(dir_path);
}

void hm_tpi_writer_free(HmTpiWriter *w) {
    if (!w) return;
    free(w->records);
    free(w);
}

/* ── Reader ──────────────────────────────────────────────────────────────── */

HmTpiReader *hm_tpi_reader_open(const char *dir_path) {
    char path[512];

    snprintf(path, sizeof(path), "%s/bucket_directory.bin", dir_path);
    FILE *bdf = fopen(path, "rb");
    if (!bdf) return NULL;

    HmBucketDirHeader hdr;
    if (fread(&hdr, sizeof(hdr), 1, bdf) != 1) { fclose(bdf); return NULL; }

    /* Accept both V1 and V2 */
    if (hdr.magic != HM_MAGIC ||
        (hdr.version != HM_VERSION && hdr.version != HM_VERSION_V2))
    {
        fclose(bdf); errno = EINVAL; return NULL;
    }

    HmBucketDirEntry *directory =
        malloc(hdr.bucket_count * sizeof(HmBucketDirEntry));
    if (!directory) { fclose(bdf); return NULL; }

    if (fread(directory, sizeof(HmBucketDirEntry),
              hdr.bucket_count, bdf) != hdr.bucket_count)
    {
        fclose(bdf); free(directory); return NULL;
    }
    fclose(bdf);

    snprintf(path, sizeof(path), "%s/hyperedges.bin", dir_path);
    int he_fd = open(path, O_RDONLY);
    if (he_fd < 0) { free(directory); return NULL; }

    HmTpiReader *r = malloc(sizeof(HmTpiReader));
    if (!r) { close(he_fd); free(directory); return NULL; }
    r->header         = hdr;
    r->directory      = directory;
    r->he_fd          = he_fd;
    r->format_version = hdr.version;
    return r;
}

void hm_tpi_reader_close(HmTpiReader *r) {
    if (!r) return;
    if (r->he_fd >= 0) close(r->he_fd);
    free(r->directory);
    free(r);
}

void hm_range_result_free(HmRangeResult *res) {
    if (!res) return;
    free(res->timestamps);
    free(res->member_counts);
    free(res->weights);
    free(res->mean_dists);
    free(res->formations_flat);
    free(res->member_ids_flat);
    free(res->member_offsets);
    free(res->props_flat);
    free(res->props_offsets);
    free(res->props_lens);
    free(res);
}

/* ── Read helpers ────────────────────────────────────────────────────────── */

/*
 * Extract the common fields from a V1 or V2 on-disk header into out_*.
 * Returns the total record stride (header + members + props_blob).
 * buf must point to at least sizeof(HmHyperedgeHeaderV2) bytes.
 */
static size_t parse_record_header(uint8_t format_version,
                                   const uint8_t *buf,
                                   uint32_t *out_ts,
                                   uint8_t  *out_mc,
                                   float    *out_weight,
                                   float    *out_mean_dist,
                                   char     *out_formation,
                                   uint32_t *out_props_len)
{
    if (format_version == HM_VERSION) {
        const HmHyperedgeHeader *h = (const HmHyperedgeHeader *)buf;
        *out_ts         = h->event_ts;
        *out_mc         = h->member_count;
        *out_weight     = h->weight;
        *out_mean_dist  = h->mean_dist_m;
        memcpy(out_formation, h->formation, HM_FORMATION_LEN);
        *out_props_len  = 0;
        return HM_RECORD_BYTES(h->member_count);
    } else {
        const HmHyperedgeHeaderV2 *h = (const HmHyperedgeHeaderV2 *)buf;
        *out_ts         = h->event_ts;
        *out_mc         = h->member_count;
        *out_weight     = h->weight;
        *out_mean_dist  = h->mean_dist_m;
        memcpy(out_formation, h->formation, HM_FORMATION_LEN);
        *out_props_len  = h->props_len;
        return HM_RECORD_BYTES_V2(h->member_count, h->props_len);
    }
}

static size_t hdr_size_for_version(uint8_t v) {
    return (v == HM_VERSION) ? sizeof(HmHyperedgeHeader)
                              : sizeof(HmHyperedgeHeaderV2);
}

/* ── Range query ─────────────────────────────────────────────────────────── */

HmRangeResult *hm_tpi_range_query(HmTpiReader *r,
                                   uint32_t start_ts,
                                   uint32_t end_ts)
{
    uint64_t t0 = now_us();

    uint32_t k_start = start_ts / r->header.bucket_seconds;
    uint32_t k_end   = end_ts   / r->header.bucket_seconds;

    /* Binary search for first directory entry with bucket_id >= k_start */
    uint32_t lo = 0, hi = r->header.bucket_count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (r->directory[mid].bucket_id < k_start) lo = mid + 1;
        else                                        hi = mid;
    }
    uint32_t first_idx = lo;

    uint32_t last_idx = first_idx;
    while (last_idx < r->header.bucket_count &&
           r->directory[last_idx].bucket_id <= k_end)
    {
        last_idx++;
    }

    uint32_t max_records = 0;
    for (uint32_t i = first_idx; i < last_idx; i++) {
        max_records += r->directory[i].record_count;
    }

    HmRangeResult *res = calloc(1, sizeof(HmRangeResult));
    if (!res) return NULL;

    if (max_records == 0) {
        snprintf(res->plan.strategy, sizeof(res->plan.strategy),
                 "TPI_BUCKET_PUSHDOWN");
        res->plan.buckets_scanned = 0;
        res->plan.total_buckets   = r->header.bucket_count;
        res->plan.speedup_factor  =
            r->header.bucket_count > 0 ? (float)r->header.bucket_count : 1.0f;
        res->plan.elapsed_us = now_us() - t0;
        return res;
    }

    /* Allocate result arrays (worst case: all records pass filter) */
    uint32_t *timestamps      = malloc(max_records * sizeof(uint32_t));
    uint8_t  *member_counts   = malloc(max_records * sizeof(uint8_t));
    float    *weights         = malloc(max_records * sizeof(float));
    float    *mean_dists      = malloc(max_records * sizeof(float));
    char     *formations_flat = malloc((size_t)max_records * HM_FORMATION_LEN);
    uint32_t *member_offsets  = malloc(max_records * sizeof(uint32_t));
    uint32_t *member_ids_flat =
        malloc((size_t)max_records * HM_MAX_MEMBERS * sizeof(uint32_t));
    /* Props arrays — allocated for V2; NULL for V1 */
    uint32_t *props_offsets   = NULL;
    uint32_t *props_lens      = NULL;
    uint8_t  *props_flat      = NULL;
    if (r->format_version == HM_VERSION_V2) {
        props_offsets = malloc(max_records * sizeof(uint32_t));
        props_lens    = malloc(max_records * sizeof(uint32_t));
        props_flat    = malloc((size_t)max_records * HM_MAX_PROPS_LEN);
    }

    if (!timestamps || !member_counts || !weights || !mean_dists ||
        !formations_flat || !member_offsets || !member_ids_flat ||
        (r->format_version == HM_VERSION_V2 &&
         (!props_offsets || !props_lens || !props_flat)))
    {
        free(timestamps); free(member_counts); free(weights);
        free(mean_dists); free(formations_flat);
        free(member_offsets); free(member_ids_flat);
        free(props_offsets); free(props_lens); free(props_flat);
        free(res);
        return NULL;
    }

    uint32_t out_count      = 0;
    uint32_t flat_cursor    = 0;
    uint32_t props_cursor   = 0;
    uint32_t read_buckets   = 0;

    /* Reusable buffer large enough for V1 or V2 header */
    size_t   hdr_buf_sz = sizeof(HmHyperedgeHeaderV2);
    uint8_t *hdr_buf    = malloc(hdr_buf_sz);
    if (!hdr_buf) {
        free(timestamps); free(member_counts); free(weights);
        free(mean_dists); free(formations_flat);
        free(member_offsets); free(member_ids_flat);
        free(props_offsets); free(props_lens); free(props_flat);
        free(res); return NULL;
    }

    size_t on_disk_hdr_sz = hdr_size_for_version(r->format_version);

    for (uint32_t bi = first_idx; bi < last_idx; bi++) {
        const HmBucketDirEntry *de = &r->directory[bi];
        off_t  cur_off = (off_t)de->file_offset;

        for (uint32_t ri = 0; ri < de->record_count; ri++) {
            /* Read on-disk header */
            ssize_t n = pread(r->he_fd, hdr_buf, on_disk_hdr_sz, cur_off);
            if (n != (ssize_t)on_disk_hdr_sz) break;

            uint32_t ts, props_len;
            uint8_t  mc;
            float    weight, mean_dist;
            char     formation[HM_FORMATION_LEN];

            size_t rec_stride = parse_record_header(
                r->format_version, hdr_buf,
                &ts, &mc, &weight, &mean_dist, formation, &props_len);

            if (mc > HM_MAX_MEMBERS) mc = HM_MAX_MEMBERS; /* safety clamp */
            if (props_len > HM_MAX_PROPS_LEN) props_len = HM_MAX_PROPS_LEN;

            /* Read member IDs */
            uint32_t members[HM_MAX_MEMBERS];
            memset(members, 0, sizeof(members));
            if (mc > 0) {
                ssize_t nm = pread(r->he_fd,
                                   members,
                                   (size_t)mc * sizeof(uint32_t),
                                   cur_off + (off_t)on_disk_hdr_sz);
                if (nm != (ssize_t)((size_t)mc * sizeof(uint32_t))) break;
            }

            /* Read props blob (V2 only) */
            uint8_t props_buf[HM_MAX_PROPS_LEN];
            memset(props_buf, 0, sizeof(props_buf));
            if (props_len > 0 && r->format_version == HM_VERSION_V2) {
                off_t props_off = cur_off + (off_t)on_disk_hdr_sz
                                  + (off_t)((size_t)mc * sizeof(uint32_t));
                ssize_t np = pread(r->he_fd, props_buf, props_len, props_off);
                if (np != (ssize_t)props_len) {
                    props_len = 0; /* truncated; treat as no props */
                }
            }

            cur_off += (off_t)rec_stride;

            /* Filter: keep only records within [start_ts, end_ts] */
            if (ts < start_ts || ts > end_ts) continue;

            timestamps[out_count]    = ts;
            member_counts[out_count] = mc;
            weights[out_count]       = weight;
            mean_dists[out_count]    = mean_dist;
            memcpy(&formations_flat[(size_t)out_count * HM_FORMATION_LEN],
                   formation, HM_FORMATION_LEN);
            formations_flat[(size_t)out_count * HM_FORMATION_LEN
                            + HM_FORMATION_LEN - 1] = '\0';
            member_offsets[out_count] = flat_cursor;
            memcpy(&member_ids_flat[flat_cursor],
                   members, (size_t)mc * sizeof(uint32_t));
            flat_cursor += mc;

            if (r->format_version == HM_VERSION_V2) {
                props_offsets[out_count] = props_cursor;
                props_lens[out_count]    = props_len;
                if (props_len > 0) {
                    memcpy(&props_flat[props_cursor], props_buf, props_len);
                }
                props_cursor += props_len;
            }

            out_count++;
        }
        read_buckets++;
    }

    free(hdr_buf);

    res->count           = out_count;
    res->timestamps      = timestamps;
    res->member_counts   = member_counts;
    res->weights         = weights;
    res->mean_dists      = mean_dists;
    res->formations_flat = formations_flat;
    res->member_ids_flat = member_ids_flat;
    res->member_offsets  = member_offsets;
    res->props_flat      = props_flat;
    res->props_offsets   = props_offsets;
    res->props_lens      = props_lens;

    snprintf(res->plan.strategy, sizeof(res->plan.strategy),
             "TPI_BUCKET_PUSHDOWN");
    res->plan.buckets_scanned = read_buckets;
    res->plan.total_buckets   = r->header.bucket_count;
    res->plan.speedup_factor  =
        read_buckets > 0
            ? (float)r->header.bucket_count / (float)read_buckets
            : (float)r->header.bucket_count;
    res->plan.elapsed_us = now_us() - t0;

    return res;
}
