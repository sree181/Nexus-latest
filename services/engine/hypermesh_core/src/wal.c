/*
 * wal.c — HyperMesh DB Write-Ahead Log implementation
 *
 * Write protocol (crash-safe):
 *   1. pwrite() the full entry bytes at the current EOF.
 *   2. fdatasync() — entry is now durable.
 *   3. Increment wal->entry_count in memory (no file header update needed;
 *      the true count is always recovered by scanning to EOF at open time).
 *
 * Recovery protocol:
 *   On hm_wal_open() the file is scanned entry-by-entry from the start. The
 *   scan stops at the first record that is short-read, structurally invalid, or
 *   (V3) fails its per-record CRC. The file is then truncated to the end of the
 *   last valid record, removing a torn final write or trailing garbage so the
 *   next append stays contiguous.
 *
 * Format gating:
 *   V1 entries use HmWalEntryHeader   (no props_len field).
 *   V2 entries use HmWalEntryHeaderV2 (with props_len + props blob).
 *   V3 entries use HmWalEntryHeaderV3 (V2 + crc32 for corruption detection).
 *   scan_entries() decodes per the WAL version flag.
 *   New WAL files are created as V3; write_entry() honours the file's version
 *   (V1/V2 WALs upgrade to V3 on the next compaction/truncate).
 */

#include "wal.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>

/* ── Internal: CRC-32 (IEEE 802.3, poly 0xEDB88320) ───────────────────────── */

/*
 * Continuable CRC: `crc` is the *running* (inverted) register. Start a fresh
 * computation with 0xFFFFFFFFu and finalize the result with `~crc`. This lets a
 * single record's CRC span the header + members + props without a temp buffer.
 * Table-free (bitwise) so there is no first-call table-init race across threads.
 */
static uint32_t crc32_update(uint32_t crc, const uint8_t *p, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        crc ^= p[i];
        for (int k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)(-(int32_t)(crc & 1)));
    }
    return crc;
}

/* Upper bound on a single batch payload (guards corrupt-length allocations). */
#define HM_WAL_BATCH_MAX_PAYLOAD (64u * 1024u * 1024u)

/* ── Internal: V4 atomic batch frame scanner ──────────────────────────────── */

/*
 * Parse one HmWalBatchHeader frame at `pos`. The frame is validated as a whole
 * (single CRC over header+payload). On success its sub-entries are expanded
 * into out[] (when non-NULL, up to max_scan), *np is advanced by the number of
 * sub-entries, and the total bytes consumed (header + payload) is returned.
 * Returns -1 on a short read, structural error, or CRC mismatch (torn frame) —
 * the batch is then discarded in full (all-or-nothing).
 */
static long scan_batch(int fd, off_t pos, uint32_t max_scan,
                        HmWalEntry *out, uint32_t *np)
{
    HmWalBatchHeader bh;
    if (pread(fd, &bh, sizeof(bh), pos) != (ssize_t)sizeof(bh)) return -1;
    if (bh.type != HM_WAL_BATCH) return -1;
    if (bh.payload_len == 0 || bh.payload_len > HM_WAL_BATCH_MAX_PAYLOAD) return -1;

    uint8_t *payload = malloc(bh.payload_len);
    if (!payload) return -1;
    if (pread(fd, payload, bh.payload_len, pos + (off_t)sizeof(bh))
            != (ssize_t)bh.payload_len) { free(payload); return -1; }

    /* Whole-frame integrity: CRC over (header|crc=0) ++ payload. */
    uint32_t stored = bh.crc32;
    bh.crc32 = 0;
    uint32_t c = crc32_update(0xFFFFFFFFu, (const uint8_t *)&bh, sizeof(bh));
    c = crc32_update(c, payload, bh.payload_len);
    if ((~c) != stored) { free(payload); return -1; }

    /* Expand sub-entries: each = HmWalEntryHeaderV2 ++ members ++ props. */
    size_t off = 0;
    for (uint32_t e = 0; e < bh.entry_count; e++) {
        if (off + sizeof(HmWalEntryHeaderV2) > bh.payload_len) { free(payload); return -1; }
        HmWalEntryHeaderV2 eh;
        memcpy(&eh, payload + off, sizeof(eh));
        off += sizeof(eh);
        if ((eh.type != HM_WAL_INSERT && eh.type != HM_WAL_DELETE) ||
            eh.member_count > HM_MAX_MEMBERS || eh.props_len > HM_MAX_PROPS_LEN) {
            free(payload); return -1;
        }
        size_t mb = (size_t)eh.member_count * sizeof(uint32_t);
        if (off + mb + eh.props_len > bh.payload_len) { free(payload); return -1; }

        if (out && (max_scan == 0 || *np < max_scan)) {
            HmWalEntry *o = &out[*np];
            o->type         = eh.type;
            o->event_ts     = eh.event_ts;
            o->member_count = eh.member_count;
            o->weight       = eh.weight;
            o->mean_dist_m  = eh.mean_dist_m;
            memcpy(o->formation, eh.formation, HM_FORMATION_LEN);
            o->formation[HM_FORMATION_LEN - 1] = '\0';
            memset(o->members, 0, sizeof(o->members));
            if (mb > 0) memcpy(o->members, payload + off, mb);
            o->props_len = eh.props_len;
            memset(o->props, 0, sizeof(o->props));
            if (eh.props_len > 0) memcpy(o->props, payload + off + mb, eh.props_len);
        }
        off += mb + eh.props_len;
        (*np)++;
    }

    free(payload);
    return (long)(sizeof(bh) + (size_t)bh.payload_len);
}

/* ── Internal: version-aware entry scanner ────────────────────────────────── */

/*
 * Reads at most max_scan entries (0 = unlimited) from fd.
 * format_version: HM_WAL_VERSION (V1), _V2, or _V3.
 * If out != NULL, fills it with decoded entries.
 * If out_valid_end != NULL, receives the byte offset just past the last fully
 * valid record (== header size when the WAL is empty) — used by hm_wal_open to
 * truncate a torn tail.
 * Returns the number of valid entries found. The scan stops at the first record
 * that is short-read, structurally invalid, or (V3) fails its CRC check.
 */
static uint32_t scan_entries(int fd, uint8_t format_version,
                              uint32_t max_scan, HmWalEntry *out,
                              off_t *out_valid_end)
{
    off_t    pos = (off_t)sizeof(HmWalFileHeader);
    uint32_t n   = 0;
    if (out_valid_end) *out_valid_end = pos;

    for (;;) {
        if (max_scan > 0 && n >= max_scan) break;

        /* V4: a leading HM_WAL_BATCH byte marks an atomic batch frame that
         * expands to several entries. Single inserts/deletes fall through to
         * the normal per-entry path below. */
        if (format_version == HM_WAL_VERSION_V4) {
            uint8_t peek;
            if (pread(fd, &peek, 1, pos) != 1) break;
            if (peek == HM_WAL_BATCH) {
                long adv = scan_batch(fd, pos, max_scan, out, &n);
                if (adv <= 0) break;          /* torn/corrupt batch → stop */
                pos += (off_t)adv;
                if (out_valid_end) *out_valid_end = pos;
                continue;
            }
        }

        uint8_t  type;
        uint32_t event_ts;
        uint8_t  member_count;
        float    weight;
        float    mean_dist_m;
        char     formation[HM_FORMATION_LEN];
        uint32_t props_len = 0;
        size_t   hdr_sz;
        uint8_t  hdr_buf[sizeof(HmWalEntryHeaderV3)];

        if (format_version == HM_WAL_VERSION) {
            HmWalEntryHeader eh;
            if (pread(fd, &eh, sizeof(eh), pos) != (ssize_t)sizeof(eh)) break;
            if ((eh.type != HM_WAL_INSERT && eh.type != HM_WAL_DELETE) ||
                eh.member_count > HM_MAX_MEMBERS)
                break;
            type = eh.type; event_ts = eh.event_ts; member_count = eh.member_count;
            weight = eh.weight; mean_dist_m = eh.mean_dist_m;
            memcpy(formation, eh.formation, HM_FORMATION_LEN);
            props_len = 0; hdr_sz = sizeof(eh);
            memcpy(hdr_buf, &eh, sizeof(eh));
        } else if (format_version == HM_WAL_VERSION_V2) {
            HmWalEntryHeaderV2 eh;
            if (pread(fd, &eh, sizeof(eh), pos) != (ssize_t)sizeof(eh)) break;
            if ((eh.type != HM_WAL_INSERT && eh.type != HM_WAL_DELETE) ||
                eh.member_count > HM_MAX_MEMBERS || eh.props_len > HM_MAX_PROPS_LEN)
                break;
            type = eh.type; event_ts = eh.event_ts; member_count = eh.member_count;
            weight = eh.weight; mean_dist_m = eh.mean_dist_m;
            memcpy(formation, eh.formation, HM_FORMATION_LEN);
            props_len = eh.props_len; hdr_sz = sizeof(eh);
            memcpy(hdr_buf, &eh, sizeof(eh));
        } else {
            HmWalEntryHeaderV3 eh;
            if (pread(fd, &eh, sizeof(eh), pos) != (ssize_t)sizeof(eh)) break;
            if ((eh.type != HM_WAL_INSERT && eh.type != HM_WAL_DELETE) ||
                eh.member_count > HM_MAX_MEMBERS || eh.props_len > HM_MAX_PROPS_LEN)
                break;
            type = eh.type; event_ts = eh.event_ts; member_count = eh.member_count;
            weight = eh.weight; mean_dist_m = eh.mean_dist_m;
            memcpy(formation, eh.formation, HM_FORMATION_LEN);
            props_len = eh.props_len; hdr_sz = sizeof(eh);
            memcpy(hdr_buf, &eh, sizeof(eh));
        }

        size_t   mb = (size_t)member_count * sizeof(uint32_t);
        uint32_t members_buf[HM_MAX_MEMBERS];
        uint8_t  props_buf[HM_MAX_PROPS_LEN];

        /* The body must be fully present, else this is a torn final write. */
        if (mb > 0 &&
            pread(fd, members_buf, mb, pos + (off_t)hdr_sz) != (ssize_t)mb)
            break;
        if (props_len > 0 &&
            pread(fd, props_buf, props_len,
                  pos + (off_t)hdr_sz + (off_t)mb) != (ssize_t)props_len)
            break;

        /* V3/V4 single entries carry a per-record CRC over
         * (header|crc=0) ++ members ++ props — verify it. */
        if (format_version == HM_WAL_VERSION_V3 ||
            format_version == HM_WAL_VERSION_V4) {
            HmWalEntryHeaderV3 *eh3 = (HmWalEntryHeaderV3 *)hdr_buf;
            uint32_t stored = eh3->crc32;
            eh3->crc32 = 0;
            uint32_t c = crc32_update(0xFFFFFFFFu, hdr_buf, hdr_sz);
            if (mb > 0)        c = crc32_update(c, (const uint8_t *)members_buf, mb);
            if (props_len > 0) c = crc32_update(c, props_buf, props_len);
            if ((~c) != stored) break;   /* corrupt / torn → stop here */
        }

        if (out) {
            out[n].type         = type;
            out[n].event_ts     = event_ts;
            out[n].member_count = member_count;
            out[n].weight       = weight;
            out[n].mean_dist_m  = mean_dist_m;
            memcpy(out[n].formation, formation, HM_FORMATION_LEN);
            out[n].formation[HM_FORMATION_LEN - 1] = '\0';
            memset(out[n].members, 0, sizeof(out[n].members));
            if (mb > 0) memcpy(out[n].members, members_buf, mb);
            out[n].props_len = props_len;
            memset(out[n].props, 0, sizeof(out[n].props));
            if (props_len > 0) memcpy(out[n].props, props_buf, props_len);
        }

        pos += (off_t)(hdr_sz + mb + props_len);
        n++;
        if (out_valid_end) *out_valid_end = pos;
    }

    return n;
}

/* ── Open / create ────────────────────────────────────────────────────────── */

HmWal *hm_wal_open(const char *dir_path)
{
    char path[512];
    snprintf(path, sizeof(path), "%s/wal.bin", dir_path);

    int fd = open(path, O_RDWR | O_CREAT, 0644);
    if (fd < 0) return NULL;

    HmWalFileHeader fhdr;
    ssize_t n = pread(fd, &fhdr, sizeof(fhdr), 0);

    uint8_t format_version;

    if (n == 0) {
        /* Brand-new file: write a V4 header (CRC entries + atomic batches) */
        fhdr.magic   = HM_WAL_MAGIC;
        fhdr.version = HM_WAL_VERSION_V4;
        if (pwrite(fd, &fhdr, sizeof(fhdr), 0) != (ssize_t)sizeof(fhdr)) {
            close(fd); return NULL;
        }
        fdatasync(fd);
        format_version = HM_WAL_VERSION_V4;
    } else if (n == (ssize_t)sizeof(fhdr) &&
               fhdr.magic == HM_WAL_MAGIC &&
               (fhdr.version == HM_WAL_VERSION ||
                fhdr.version == HM_WAL_VERSION_V2 ||
                fhdr.version == HM_WAL_VERSION_V3 ||
                fhdr.version == HM_WAL_VERSION_V4)) {
        /* Existing V1/V2/V3/V4 WAL — open in its current version */
        format_version = fhdr.version;
    } else {
        close(fd); errno = EINVAL; return NULL;
    }

    HmWal *wal = malloc(sizeof(HmWal));
    if (!wal) { close(fd); return NULL; }

    wal->fd = fd;
    strncpy(wal->path, path, sizeof(wal->path) - 1);
    wal->path[sizeof(wal->path) - 1] = '\0';
    wal->format_version = format_version;

    /* Scan existing entries to establish entry_count and the end of the last
     * valid record. A torn final write or trailing garbage (e.g. a crash mid-
     * append, or a detected CRC failure) is truncated away so the next append
     * stays contiguous — otherwise lseek(SEEK_END) would write past the garbage
     * and the new record would be unreachable on the following scan. */
    off_t valid_end = (off_t)sizeof(HmWalFileHeader);
    wal->entry_count = scan_entries(fd, format_version, 0, NULL, &valid_end);

    off_t fsize = lseek(fd, 0, SEEK_END);
    if (fsize > valid_end) {
        if (ftruncate(fd, valid_end) == 0)
            fdatasync(fd);
    }

    return wal;
}

/* ── Internal: write one V2 entry ─────────────────────────────────────────── */

static int write_entry(HmWal          *wal,
                        uint8_t         type,
                        uint32_t        event_ts,
                        uint8_t         member_count,
                        const uint32_t *members,
                        float           weight,
                        float           mean_dist_m,
                        const char     *formation,
                        const uint8_t  *props,
                        uint32_t        props_len)
{
    /* Clamp props_len for safety */
    if (props_len > HM_MAX_PROPS_LEN) props_len = HM_MAX_PROPS_LEN;
    /* On a V1 WAL, discard props silently (props_len forced to 0) */
    if (wal->format_version == HM_WAL_VERSION) {
        props     = NULL;
        props_len = 0;
    }

    off_t end = lseek(wal->fd, 0, SEEK_END);
    if (end < 0) return -1;

    off_t  cur = end;
    size_t mb  = (size_t)member_count * sizeof(uint32_t);

    if (wal->format_version == HM_WAL_VERSION) {
        /* Write V1 header */
        HmWalEntryHeader eh;
        memset(&eh, 0, sizeof(eh));
        eh.type         = type;
        eh.event_ts     = event_ts;
        eh.member_count = member_count;
        eh.weight       = weight;
        eh.mean_dist_m  = mean_dist_m;
        if (formation)
            strncpy(eh.formation, formation, HM_FORMATION_LEN - 1);
        if (pwrite(wal->fd, &eh, sizeof(eh), cur) != (ssize_t)sizeof(eh))
            return -1;
        cur += (off_t)sizeof(eh);
    } else if (wal->format_version == HM_WAL_VERSION_V2) {
        /* Write V2 header */
        HmWalEntryHeaderV2 eh;
        memset(&eh, 0, sizeof(eh));
        eh.type         = type;
        eh.event_ts     = event_ts;
        eh.member_count = member_count;
        eh.weight       = weight;
        eh.mean_dist_m  = mean_dist_m;
        eh.props_len    = props_len;
        if (formation)
            strncpy(eh.formation, formation, HM_FORMATION_LEN - 1);
        if (pwrite(wal->fd, &eh, sizeof(eh), cur) != (ssize_t)sizeof(eh))
            return -1;
        cur += (off_t)sizeof(eh);
    } else {
        /* Write V3 header with a CRC over (header|crc=0) ++ members ++ props */
        HmWalEntryHeaderV3 eh;
        memset(&eh, 0, sizeof(eh));
        eh.type         = type;
        eh.event_ts     = event_ts;
        eh.member_count = member_count;
        eh.weight       = weight;
        eh.mean_dist_m  = mean_dist_m;
        eh.props_len    = props_len;
        if (formation)
            strncpy(eh.formation, formation, HM_FORMATION_LEN - 1);
        eh.crc32 = 0;
        uint32_t c = crc32_update(0xFFFFFFFFu, (const uint8_t *)&eh, sizeof(eh));
        if (mb > 0 && members)
            c = crc32_update(c, (const uint8_t *)members, mb);
        if (props_len > 0 && props)
            c = crc32_update(c, props, props_len);
        eh.crc32 = ~c;
        if (pwrite(wal->fd, &eh, sizeof(eh), cur) != (ssize_t)sizeof(eh))
            return -1;
        cur += (off_t)sizeof(eh);
    }

    /* Write member IDs */
    if (mb > 0 && members) {
        if (pwrite(wal->fd, members, mb, cur) != (ssize_t)mb) return -1;
        cur += (off_t)mb;
    }

    /* Write props blob (V2/V3 only, already clamped) */
    if (props_len > 0 && props) {
        if (pwrite(wal->fd, props, props_len, cur) != (ssize_t)props_len)
            return -1;
    }

    fdatasync(wal->fd);
    wal->entry_count++;
    return 0;
}

/* ── Public write API ─────────────────────────────────────────────────────── */

int hm_wal_append_insert(HmWal *wal, const HmRecord *rec)
{
    return write_entry(wal, HM_WAL_INSERT,
                       rec->event_ts, rec->member_count, rec->members,
                       rec->weight, rec->mean_dist_m, rec->formation,
                       rec->props, rec->props_len);
}

int hm_wal_append_delete(HmWal          *wal,
                          uint32_t        event_ts,
                          uint8_t         member_count,
                          const uint32_t *members)
{
    return write_entry(wal, HM_WAL_DELETE,
                       event_ts, member_count, members,
                       0.0f, 0.0f, NULL, NULL, 0);
}

/* ── Atomic group commit ──────────────────────────────────────────────────── */

int hm_wal_append_batch(HmWal *wal, const HmWalEntry *entries, uint32_t n)
{
    if (!wal || wal->fd < 0) return -1;
    if (n == 0) return 0;

    /* Pre-V4 WALs have no batch record type: write entries individually.
     * (Each write_entry fsyncs; correct, just not a single group sync.) */
    if (wal->format_version != HM_WAL_VERSION_V4) {
        for (uint32_t i = 0; i < n; i++) {
            const HmWalEntry *e = &entries[i];
            if (write_entry(wal, e->type, e->event_ts, e->member_count,
                            e->members, e->weight, e->mean_dist_m,
                            e->formation, e->props, e->props_len) < 0)
                return -1;
        }
        return 0;
    }

    /* Size the payload: each sub-entry = HmWalEntryHeaderV2 ++ members ++ props */
    size_t payload_len = 0;
    for (uint32_t i = 0; i < n; i++) {
        uint8_t  mc = entries[i].member_count;
        if (mc > HM_MAX_MEMBERS) mc = HM_MAX_MEMBERS;
        uint32_t pl = entries[i].props_len;
        if (pl > HM_MAX_PROPS_LEN) pl = HM_MAX_PROPS_LEN;
        payload_len += sizeof(HmWalEntryHeaderV2)
                     + (size_t)mc * sizeof(uint32_t) + pl;
    }
    if (payload_len == 0 || payload_len > HM_WAL_BATCH_MAX_PAYLOAD) return -1;

    uint8_t *payload = malloc(payload_len);
    if (!payload) return -1;

    size_t off = 0;
    for (uint32_t i = 0; i < n; i++) {
        const HmWalEntry *e = &entries[i];
        uint8_t  mc = e->member_count;
        if (mc > HM_MAX_MEMBERS) mc = HM_MAX_MEMBERS;
        uint32_t pl = e->props_len;
        if (pl > HM_MAX_PROPS_LEN) pl = HM_MAX_PROPS_LEN;

        HmWalEntryHeaderV2 eh;
        memset(&eh, 0, sizeof(eh));
        eh.type         = e->type;
        eh.event_ts     = e->event_ts;
        eh.member_count = mc;
        eh.weight       = e->weight;
        eh.mean_dist_m  = e->mean_dist_m;
        eh.props_len    = pl;
        memcpy(eh.formation, e->formation, HM_FORMATION_LEN);
        eh.formation[HM_FORMATION_LEN - 1] = '\0';

        memcpy(payload + off, &eh, sizeof(eh));
        off += sizeof(eh);
        size_t mb = (size_t)mc * sizeof(uint32_t);
        if (mb > 0) { memcpy(payload + off, e->members, mb); off += mb; }
        if (pl > 0) { memcpy(payload + off, e->props, pl);  off += pl; }
    }

    HmWalBatchHeader bh;
    memset(&bh, 0, sizeof(bh));
    bh.type        = HM_WAL_BATCH;
    bh.entry_count = n;
    bh.payload_len = (uint32_t)payload_len;
    bh.crc32       = 0;
    uint32_t c = crc32_update(0xFFFFFFFFu, (const uint8_t *)&bh, sizeof(bh));
    c = crc32_update(c, payload, payload_len);
    bh.crc32 = ~c;

    off_t end = lseek(wal->fd, 0, SEEK_END);
    if (end < 0) { free(payload); return -1; }
    if (pwrite(wal->fd, &bh, sizeof(bh), end) != (ssize_t)sizeof(bh)) {
        free(payload); return -1;
    }
    if (pwrite(wal->fd, payload, payload_len, end + (off_t)sizeof(bh))
            != (ssize_t)payload_len) {
        free(payload); return -1;
    }
    free(payload);

    fdatasync(wal->fd);          /* one sync makes the whole batch durable */
    wal->entry_count += n;
    return 0;
}

/* ── Scan API ─────────────────────────────────────────────────────────────── */

HmWalEntry *hm_wal_scan_all(HmWal *wal, uint32_t *out_count)
{
    *out_count = 0;
    if (!wal || wal->entry_count == 0) return NULL;

    HmWalEntry *entries = malloc((size_t)wal->entry_count * sizeof(HmWalEntry));
    if (!entries) return NULL;

    uint32_t n = scan_entries(wal->fd, wal->format_version,
                               wal->entry_count, entries, NULL);
    *out_count = n;
    return entries;
}

uint32_t hm_wal_scan_range(HmWal      *wal,
                             uint32_t    start_ts,
                             uint32_t    end_ts,
                             HmWalEntry *out,
                             uint32_t    out_cap)
{
    if (!wal || wal->entry_count == 0 || out_cap == 0) return 0;

    uint32_t total   = 0;
    HmWalEntry *all  = hm_wal_scan_all(wal, &total);
    if (!all) return 0;

    uint32_t result = 0;
    for (uint32_t i = 0; i < total && result < out_cap; i++) {
        if (all[i].event_ts >= start_ts && all[i].event_ts <= end_ts)
            out[result++] = all[i];
    }

    free(all);
    return result;
}

/* ── Truncate (post-compaction) ───────────────────────────────────────────── */

/*
 * Truncate the WAL back to the file header.
 * Any older header (V1/V2/V3) is upgraded to V4 in-place.
 * entry_count is reset to 0.
 */
int hm_wal_truncate(HmWal *wal)
{
    if (!wal || wal->fd < 0) return -1;
    if (ftruncate(wal->fd, (off_t)sizeof(HmWalFileHeader)) < 0) return -1;

    /* Upgrade any older (V1/V2/V3) header to V4 in-place so post-compaction
     * appends are CRC-protected and can use atomic batch frames. */
    if (wal->format_version != HM_WAL_VERSION_V4) {
        HmWalFileHeader fhdr;
        fhdr.magic   = HM_WAL_MAGIC;
        fhdr.version = HM_WAL_VERSION_V4;
        if (pwrite(wal->fd, &fhdr, sizeof(fhdr), 0) != (ssize_t)sizeof(fhdr))
            return -1;
        wal->format_version = HM_WAL_VERSION_V4;
    }

    fdatasync(wal->fd);
    wal->entry_count = 0;
    return 0;
}

/* ── Close ────────────────────────────────────────────────────────────────── */

void hm_wal_close(HmWal *wal)
{
    if (!wal) return;
    if (wal->fd >= 0) close(wal->fd);
    free(wal);
}
