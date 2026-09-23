/*
 * hypermesh.c — Public API implementation
 *
 * HmStore wraps HmTpiReader + HmFmiReader behind the opaque handle.
 * hm_build_from_csv() parses the CSV format:
 *
 *   event_ts,members,member_count,weight,mean_dist_m,formation
 *   0,"[0, 2, 3, 4]",4,0.112,7.932,random
 *
 * The members field is a JSON-array string: "[id, id, ...]"
 */

#include "hypermesh.h"
#include "tpi.h"
#include "fmi.h"
#include "wal.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <pthread.h>

/* ── Monotonic clock helper (mirrors tpi.c — static to each TU) ─────────── */

static uint64_t now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)(ts.tv_nsec / 1000);
}

/* ── Forward declarations for _impl helpers ──────────────────────────────── */

static HmRangeResult *_range_query_impl(HmStore *, uint32_t, uint32_t);
static HmRangeResult *_fmi_query_impl(HmStore *, uint32_t);
static HmRangeResult *_full_scan_impl(HmStore *, uint32_t, uint32_t);
static int            _compact_impl(HmStore *, uint32_t);   /* min_event_ts; 0 = keep all */

/* ── Thread-local error string ───────────────────────────────────────────── */

static _Thread_local char g_last_error[256];

static void set_error(const char *msg) {
    strncpy(g_last_error, msg, sizeof(g_last_error) - 1);
    g_last_error[sizeof(g_last_error) - 1] = '\0';
}

const char *hm_last_error(void) { return g_last_error; }

/* ── Locking helpers ─────────────────────────────────────────────────────── */

#define RDLOCK(s)   pthread_rwlock_rdlock(&(s)->lock)
#define RDUNLOCK(s) pthread_rwlock_unlock(&(s)->lock)
#define WRLOCK(s)   pthread_rwlock_wrlock(&(s)->lock)
#define WRUNLOCK(s) pthread_rwlock_unlock(&(s)->lock)

/* ── HmStore ─────────────────────────────────────────────────────────────── */

struct HmStore {
    HmTpiReader     *tpi;
    HmFmiReader     *fmi;
    HmWal           *wal;
    char             dir_path[512];
    pthread_rwlock_t lock;                /* guards tpi, fmi, wal             */
    uint32_t         autocompact_threshold; /* 0 = disabled                   */
    uint32_t         query_timeout_ms;    /* 0 = no timeout (cooperative)      */
};

/*
 * Cooperative query deadline. Each query computes an absolute deadline from
 * store->query_timeout_ms at entry and the hot scan/merge loops poll it every
 * few thousand iterations. On expiry the loop stops early and the result's
 * plan.timed_out flag is set so the caller can surface a timeout (HTTP 504)
 * instead of letting a pathological scan pin the read lock indefinitely.
 */
static inline uint64_t query_deadline(const HmStore *store) {
    return store->query_timeout_ms
        ? now_us() + (uint64_t)store->query_timeout_ms * 1000ULL
        : 0;
}
static inline int deadline_hit(uint64_t deadline_us) {
    return deadline_us != 0 && now_us() > deadline_us;
}
/* Poll the deadline cheaply: only consult the clock every 4096 iterations. */
#define HM_DEADLINE_TRIPPED(dl, i) \
    ((dl) != 0 && (((i) & 0xFFFu) == 0) && deadline_hit(dl))

/* ── CSV parsing ─────────────────────────────────────────────────────────── */

/*
 * Parse a member array string of the form "[0, 2, 3, 4]" or "[0,2,3,4]"
 * into the members array. Returns the count of members parsed, or -1 on error.
 */
static int parse_members(const char *s, uint32_t *members, int max_count) {
    /* Skip leading whitespace and '[' */
    while (*s == ' ' || *s == '\t') s++;
    if (*s != '[') return -1;
    s++;

    int count = 0;
    while (*s && *s != ']') {
        while (*s == ' ' || *s == '\t' || *s == ',') s++;
        if (*s == ']' || *s == '\0') break;

        char *end;
        long val = strtol(s, &end, 10);
        if (end == s) return -1;  /* no digit parsed */
        if (count < max_count) members[count] = (uint32_t)val;
        count++;
        s = end;
    }
    return count;
}

/*
 * Minimal CSV line tokeniser.
 * Handles double-quoted fields (the members array is quoted in the CSV).
 * Writes field pointers into fields[], null-terminates each in-place.
 * Returns number of fields found.
 */
static int csv_split(char *line, char **fields, int max_fields) {
    int n = 0;
    char *p = line;
    while (n < max_fields) {
        fields[n++] = p;
        if (*p == '"') {
            /* Quoted field: scan to closing quote */
            fields[n-1] = p + 1;  /* skip opening quote */
            p++;
            while (*p && !(*p == '"' && (*(p+1) == ',' || *(p+1) == '\0'
                                         || *(p+1) == '\n' || *(p+1) == '\r')))
            {
                p++;
            }
            *p = '\0'; /* null-terminate at closing quote */
            p++;       /* skip closing quote */
        } else {
            while (*p && *p != ',' && *p != '\n' && *p != '\r') p++;
        }
        if (*p == '\0' || *p == '\n' || *p == '\r') { *p = '\0'; break; }
        *p++ = '\0';   /* null-terminate field, advance past comma */
    }
    return n;
}

/* ── Build ───────────────────────────────────────────────────────────────── */

int hm_build_from_csv(const char *dir_path,
                       const char *hyperedges_csv,
                       uint32_t    bucket_seconds)
{
    /* Create directory if missing */
    mkdir(dir_path, 0755);  /* ignore EEXIST */

    FILE *f = fopen(hyperedges_csv, "r");
    if (!f) {
        set_error("Cannot open hyperedges CSV");
        return -1;
    }

    HmTpiWriter *w = hm_tpi_writer_new(bucket_seconds);
    if (!w) { fclose(f); set_error("OOM: writer"); return -1; }

    char line[4096];
    /* Skip header line */
    if (!fgets(line, sizeof(line), f)) {
        fclose(f); hm_tpi_writer_free(w);
        set_error("CSV is empty"); return -1;
    }

    uint64_t line_no = 1;
    uint64_t skipped = 0;
    while (fgets(line, sizeof(line), f)) {
        line_no++;
        /* Strip trailing newline */
        size_t len = strlen(line);
        while (len > 0 && (line[len-1] == '\n' || line[len-1] == '\r'))
            line[--len] = '\0';
        if (len == 0) continue;

        char *fields[8];
        int nf = csv_split(line, fields, 8);
        /*
         * Expected columns:
         * 0: event_ts, 1: members, 2: member_count, 3: weight,
         * 4: mean_dist_m, 5: formation
         */
        if (nf < 6) { skipped++; continue; }

        HmRecord rec;
        memset(&rec, 0, sizeof(rec));

        rec.event_ts    = (uint32_t)strtoul(fields[0], NULL, 10);
        rec.weight      = (float)strtod(fields[3], NULL);
        rec.mean_dist_m = (float)strtod(fields[4], NULL);
        strncpy(rec.formation, fields[5], HM_FORMATION_LEN - 1);

        int mc = parse_members(fields[1], rec.members, HM_MAX_MEMBERS);
        if (mc <= 0) { skipped++; continue; }
        rec.member_count = (uint8_t)(mc < (int)HM_MAX_MEMBERS
                                     ? mc : HM_MAX_MEMBERS);

        if (hm_tpi_writer_insert(w, &rec) < 0) {
            fclose(f); hm_tpi_writer_free(w);
            set_error("OOM: insert"); return -1;
        }
    }
    fclose(f);

    fprintf(stderr, "[hypermesh] Parsed %llu records (%llu skipped)\n",
            (unsigned long long)(line_no - 1 - skipped),
            (unsigned long long)skipped);

    int rc = hm_tpi_writer_flush(w, dir_path);
    if (rc < 0) set_error("Flush failed");
    else fprintf(stderr, "[hypermesh] Index written to %s\n", dir_path);

    hm_tpi_writer_free(w);
    return rc;
}

int hm_init_empty(const char *dir_path, uint32_t bucket_seconds) {
    mkdir(dir_path, 0755);  /* ignore EEXIST */
    if (bucket_seconds == 0) bucket_seconds = 10;
    HmTpiWriter *w = hm_tpi_writer_new(bucket_seconds);
    if (!w) { set_error("hm_init_empty: OOM"); return -1; }
    int rc = hm_tpi_writer_flush(w, dir_path);
    if (rc < 0) set_error("hm_init_empty: flush failed");
    hm_tpi_writer_free(w);
    return rc;
}

/* ── Open / Close ────────────────────────────────────────────────────────── */

HmStore *hm_open(const char *dir_path) {
    /* Remove any .tmp files left by a previous interrupted compaction.
     * The WAL is still intact in that case, so data is safe. */
    hm_tpi_cleanup_tmp_files(dir_path);

    HmTpiReader *tpi = hm_tpi_reader_open(dir_path);
    if (!tpi) { set_error("Cannot open TPI index"); return NULL; }

    HmFmiReader *fmi = hm_fmi_reader_open(dir_path);
    if (!fmi) {
        hm_tpi_reader_close(tpi);
        set_error("Cannot open FMI index");
        return NULL;
    }

    HmWal *wal = hm_wal_open(dir_path);
    if (!wal) {
        hm_tpi_reader_close(tpi);
        hm_fmi_reader_close(fmi);
        set_error("Cannot open WAL");
        return NULL;
    }

    HmStore *s = malloc(sizeof(HmStore));
    if (!s) {
        hm_tpi_reader_close(tpi);
        hm_fmi_reader_close(fmi);
        hm_wal_close(wal);
        set_error("OOM: HmStore");
        return NULL;
    }
    s->tpi = tpi;
    s->fmi = fmi;
    s->wal = wal;
    strncpy(s->dir_path, dir_path, sizeof(s->dir_path) - 1);
    s->dir_path[sizeof(s->dir_path) - 1] = '\0';
    s->autocompact_threshold = 0;
    s->query_timeout_ms      = 0;
    pthread_rwlock_init(&s->lock, NULL);
    return s;
}

void hm_close(HmStore *store) {
    if (!store) return;
    pthread_rwlock_destroy(&store->lock);
    hm_tpi_reader_close(store->tpi);
    hm_fmi_reader_close(store->fmi);
    hm_wal_close(store->wal);
    free(store);
}

void hm_set_autocompact(HmStore *store, uint32_t threshold) {
    if (store) store->autocompact_threshold = threshold;
}

void hm_set_query_timeout_ms(HmStore *store, uint32_t timeout_ms) {
    if (store) store->query_timeout_ms = timeout_ms;
}

uint8_t hm_res_timed_out(const HmRangeResult *r) {
    return (r) ? r->plan.timed_out : 0;
}

/* ── WAL helpers used by range_query and compact ─────────────────────────── */

static int cmp_uint32_asc(const void *a, const void *b) {
    uint32_t x = *(const uint32_t *)a;
    uint32_t y = *(const uint32_t *)b;
    return (x > y) - (x < y);
}

/*
 * Order-independent membership match: true iff both arrays contain the same
 * multiset of node IDs.  Sorts local copies to avoid modifying caller data.
 */
static int members_match(const uint32_t *a, uint8_t ac,
                          const uint32_t *b, uint8_t bc)
{
    if (ac != bc) return 0;
    if (ac == 0) return 1;
    uint32_t sa[HM_MAX_MEMBERS], sb[HM_MAX_MEMBERS];
    memcpy(sa, a, (size_t)ac * sizeof(uint32_t));
    memcpy(sb, b, (size_t)bc * sizeof(uint32_t));
    qsort(sa, ac, sizeof(uint32_t), cmp_uint32_asc);
    qsort(sb, bc, sizeof(uint32_t), cmp_uint32_asc);
    return memcmp(sa, sb, (size_t)ac * sizeof(uint32_t)) == 0;
}

/* ── TPI Range Query (with transparent WAL merge) ────────────────────────── */

/* Public locked wrapper */
HmRangeResult *hm_range_query(HmStore *store, uint32_t start_ts, uint32_t end_ts) {
    if (!store) return NULL;
    RDLOCK(store);
    HmRangeResult *r = _range_query_impl(store, start_ts, end_ts);
    RDUNLOCK(store);
    return r;
}

static HmRangeResult *_range_query_impl(HmStore *store,
                                         uint32_t start_ts,
                                         uint32_t end_ts)
{
    /* Step 1: baseline TPI query */
    HmRangeResult *primary = hm_tpi_range_query(store->tpi, start_ts, end_ts);
    if (!primary) return NULL;

    /* Step 2: fast-path — no WAL entries, nothing to merge */
    if (!store->wal || store->wal->entry_count == 0) return primary;

    /* Step 3: collect WAL entries that overlap [start_ts, end_ts] */
    HmWalEntry *wal_buf = malloc(
        (size_t)store->wal->entry_count * sizeof(HmWalEntry));
    if (!wal_buf) return primary;   /* OOM: fall back to TPI-only result */

    uint32_t wal_count = hm_wal_scan_range(store->wal, start_ts, end_ts,
                                            wal_buf,
                                            store->wal->entry_count);
    if (wal_count == 0) { free(wal_buf); return primary; }

    /* Count WAL inserts */
    uint32_t insert_count = 0;
    for (uint32_t i = 0; i < wal_count; i++)
        if (wal_buf[i].type == HM_WAL_INSERT) insert_count++;

    /* Step 4: allocate merged result (worst case: all records kept + WAL inserts) */
    uint32_t max_out = primary->count + insert_count;
    if (max_out == 0) {
        free(wal_buf);
        hm_range_result_free(primary);
        return calloc(1, sizeof(HmRangeResult)); /* empty result */
    }

    int has_props = (primary->props_flat != NULL);
    /* Check if any WAL insert has props */
    if (!has_props) {
        for (uint32_t i = 0; i < wal_count; i++) {
            if (wal_buf[i].type == HM_WAL_INSERT && wal_buf[i].props_len > 0) {
                has_props = 1; break;
            }
        }
    }

    HmRangeResult *merged = calloc(1, sizeof(HmRangeResult));
    uint32_t *timestamps    = malloc(max_out * sizeof(uint32_t));
    uint8_t  *mcounts       = malloc(max_out * sizeof(uint8_t));
    float    *weights       = malloc(max_out * sizeof(float));
    float    *mean_dists    = malloc(max_out * sizeof(float));
    char     *fmts          = malloc((size_t)max_out * HM_FORMATION_LEN);
    uint32_t *offsets       = malloc(max_out * sizeof(uint32_t));
    uint32_t *mids          = malloc(
        (size_t)max_out * HM_MAX_MEMBERS * sizeof(uint32_t));
    uint32_t *props_offsets = NULL;
    uint32_t *props_lens    = NULL;
    uint8_t  *props_flat    = NULL;
    if (has_props) {
        props_offsets = malloc(max_out * sizeof(uint32_t));
        props_lens    = malloc(max_out * sizeof(uint32_t));
        props_flat    = malloc((size_t)max_out * HM_MAX_PROPS_LEN);
    }

    if (!merged || !timestamps || !mcounts || !weights || !mean_dists
        || !fmts || !offsets || !mids
        || (has_props && (!props_offsets || !props_lens || !props_flat)))
    {
        free(merged); free(timestamps); free(mcounts); free(weights);
        free(mean_dists); free(fmts); free(offsets); free(mids);
        free(props_offsets); free(props_lens); free(props_flat);
        free(wal_buf);
        return primary;
    }

    uint32_t out_count  = 0;
    uint32_t flat_cur   = 0;
    uint32_t props_cur  = 0;
    uint64_t deadline   = query_deadline(store);
    int      timed_out  = 0;

    /* Step 4a: copy primary records, skipping tombstoned ones */
    for (uint32_t i = 0; i < primary->count; i++) {
        if (HM_DEADLINE_TRIPPED(deadline, i)) { timed_out = 1; break; }
        uint32_t        ts   = primary->timestamps[i];
        uint8_t         mc   = primary->member_counts[i];
        const uint32_t *mptr = &primary->member_ids_flat[primary->member_offsets[i]];

        int deleted = 0;
        for (uint32_t d = 0; d < wal_count && !deleted; d++) {
            if (wal_buf[d].type == HM_WAL_DELETE &&
                wal_buf[d].event_ts == ts &&
                members_match(mptr, mc,
                              wal_buf[d].members, wal_buf[d].member_count))
                deleted = 1;
        }
        if (deleted) continue;

        timestamps[out_count] = ts;
        mcounts[out_count]    = mc;
        weights[out_count]    = primary->weights[i];
        mean_dists[out_count] = primary->mean_dists[i];
        memcpy(&fmts[(size_t)out_count * HM_FORMATION_LEN],
               &primary->formations_flat[(size_t)i * HM_FORMATION_LEN],
               HM_FORMATION_LEN);
        offsets[out_count] = flat_cur;
        memcpy(&mids[flat_cur], mptr, (size_t)mc * sizeof(uint32_t));
        flat_cur += mc;

        if (has_props) {
            uint32_t plen = (primary->props_lens ? primary->props_lens[i] : 0);
            props_offsets[out_count] = props_cur;
            props_lens[out_count]    = plen;
            if (plen > 0 && primary->props_flat)
                memcpy(&props_flat[props_cur],
                       primary->props_flat + primary->props_offsets[i], plen);
            props_cur += plen;
        }

        out_count++;
    }

    /* Step 4b: append WAL inserts that are not cancelled by a later tombstone */
    for (uint32_t i = 0; !timed_out && i < wal_count; i++) {
        if (HM_DEADLINE_TRIPPED(deadline, i)) { timed_out = 1; break; }
        if (wal_buf[i].type != HM_WAL_INSERT) continue;
        const HmWalEntry *e = &wal_buf[i];

        int cancelled = 0;
        for (uint32_t d = 0; d < wal_count && !cancelled; d++) {
            if (wal_buf[d].type == HM_WAL_DELETE &&
                wal_buf[d].event_ts == e->event_ts &&
                members_match(e->members, e->member_count,
                              wal_buf[d].members, wal_buf[d].member_count))
                cancelled = 1;
        }
        if (cancelled) continue;

        timestamps[out_count] = e->event_ts;
        mcounts[out_count]    = e->member_count;
        weights[out_count]    = e->weight;
        mean_dists[out_count] = e->mean_dist_m;
        memcpy(&fmts[(size_t)out_count * HM_FORMATION_LEN],
               e->formation, HM_FORMATION_LEN);
        offsets[out_count] = flat_cur;
        memcpy(&mids[flat_cur], e->members,
               (size_t)e->member_count * sizeof(uint32_t));
        flat_cur += e->member_count;

        if (has_props) {
            props_offsets[out_count] = props_cur;
            props_lens[out_count]    = e->props_len;
            if (e->props_len > 0)
                memcpy(&props_flat[props_cur], e->props, e->props_len);
            props_cur += e->props_len;
        }

        out_count++;
    }

    free(wal_buf);

    merged->count           = out_count;
    merged->timestamps      = timestamps;
    merged->member_counts   = mcounts;
    merged->weights         = weights;
    merged->mean_dists      = mean_dists;
    merged->formations_flat = fmts;
    merged->member_ids_flat = mids;
    merged->member_offsets  = offsets;
    merged->props_flat      = props_flat;
    merged->props_offsets   = props_offsets;
    merged->props_lens      = props_lens;
    merged->plan            = primary->plan;
    merged->plan.timed_out  = (uint8_t)timed_out;

    hm_range_result_free(primary);
    return merged;
}

/* hm_range_result_free is re-exported from tpi.c via the header */

/* Accessors */
uint32_t hm_res_count(const HmRangeResult *r) {
    return r ? r->count : 0;
}
uint32_t hm_res_timestamp(const HmRangeResult *r, uint32_t i) {
    return (r && i < r->count) ? r->timestamps[i] : 0;
}
uint8_t hm_res_member_count(const HmRangeResult *r, uint32_t i) {
    return (r && i < r->count) ? r->member_counts[i] : 0;
}
float hm_res_weight(const HmRangeResult *r, uint32_t i) {
    return (r && i < r->count) ? r->weights[i] : 0.0f;
}
float hm_res_mean_dist(const HmRangeResult *r, uint32_t i) {
    return (r && i < r->count) ? r->mean_dists[i] : 0.0f;
}
const char *hm_res_formation(const HmRangeResult *r, uint32_t i) {
    if (!r || i >= r->count) return "";
    return &r->formations_flat[(size_t)i * HM_FORMATION_LEN];
}
uint32_t hm_res_member_id(const HmRangeResult *r, uint32_t i, uint32_t j) {
    if (!r || i >= r->count || j >= r->member_counts[i]) return UINT32_MAX;
    return r->member_ids_flat[r->member_offsets[i] + j];
}
const char *hm_res_strategy(const HmRangeResult *r) {
    return r ? r->plan.strategy : "UNKNOWN";
}
uint32_t hm_res_buckets_scanned(const HmRangeResult *r) {
    return r ? r->plan.buckets_scanned : 0;
}
uint32_t hm_res_total_buckets(const HmRangeResult *r) {
    return r ? r->plan.total_buckets : 0;
}
float hm_res_speedup(const HmRangeResult *r) {
    return r ? r->plan.speedup_factor : 0.0f;
}
uint64_t hm_res_elapsed_us(const HmRangeResult *r) {
    return r ? r->plan.elapsed_us : 0;
}

/* ── FMI Coalition Ranking ───────────────────────────────────────────────── */

HmCoalitionResult *hm_get_coalition_ranking(HmStore *store) {
    if (!store) return NULL;
    RDLOCK(store);
    HmCoalitionResult *cr = hm_coalition_ranking(store->fmi);
    RDUNLOCK(store);
    return cr;
}

uint32_t hm_coal_count(const HmCoalitionResult *cr) {
    return cr ? cr->count : 0;
}
uint32_t hm_coal_node_id(const HmCoalitionResult *cr, uint32_t i) {
    return (cr && i < cr->count) ? cr->node_ids[i] : UINT32_MAX;
}
uint32_t hm_coal_appearances(const HmCoalitionResult *cr, uint32_t i) {
    return (cr && i < cr->count) ? cr->appearances[i] : 0;
}

/* ── FMI Point Lookup ────────────────────────────────────────────────────── */

uint32_t *hm_fmi_lookup(HmStore *store,
                          uint32_t  node_id,
                          uint32_t *out_count)
{
    if (!store) return NULL;
    RDLOCK(store);
    uint32_t *r = hm_fmi_reader_lookup(store->fmi, node_id, out_count);
    RDUNLOCK(store);
    return r;
}

/* ── FMI Full Query (binary search + record fetch + WAL merge) ───────────── */

/* Public locked wrapper */
HmRangeResult *hm_fmi_query(HmStore *store, uint32_t node_id) {
    if (!store) return NULL;
    RDLOCK(store);
    HmRangeResult *r = _fmi_query_impl(store, node_id);
    RDUNLOCK(store);
    return r;
}

static HmRangeResult *_fmi_query_impl(HmStore *store, uint32_t node_id)
{
    uint64_t t0 = now_us();

    /* ── Step 1: FMI binary search → sorted seq-IDs ─────────────────── */
    uint32_t  seq_count = 0;
    uint32_t *seq_ids   = hm_fmi_reader_lookup(store->fmi, node_id, &seq_count);
    /* seq_ids is sorted ascending (written in seq-ID order by tpi.c) */

    /* ── Step 2: Scan WAL (small by design) ──────────────────────────── */
    uint32_t    wal_count = 0;
    HmWalEntry *wal_buf   = NULL;
    if (store->wal->entry_count > 0)
        wal_buf = hm_wal_scan_all(store->wal, &wal_count);

    /* ── Step 3: Build tombstone list; count WAL inserts for node_id ─── */
    typedef struct {
        uint32_t event_ts;
        uint32_t members[HM_MAX_MEMBERS];
        uint8_t  mc;
    } Tombstone;

    Tombstone *tombstones    = NULL;
    uint32_t   tomb_count    = 0;
    uint32_t   wal_match_cap = 0;

    for (uint32_t i = 0; i < wal_count; i++) {
        if (wal_buf[i].type == HM_WAL_DELETE) {
            tomb_count++;
        } else if (wal_buf[i].type == HM_WAL_INSERT) {
            for (uint8_t j = 0; j < wal_buf[i].member_count; j++) {
                if (wal_buf[i].members[j] == node_id) { wal_match_cap++; break; }
            }
        }
    }

    if (tomb_count > 0) {
        tombstones = malloc(tomb_count * sizeof(Tombstone));
        if (!tombstones) { free(seq_ids); free(wal_buf); return NULL; }
        uint32_t ti = 0;
        for (uint32_t i = 0; i < wal_count; i++) {
            if (wal_buf[i].type != HM_WAL_DELETE) continue;
            tombstones[ti].event_ts = wal_buf[i].event_ts;
            tombstones[ti].mc       = wal_buf[i].member_count;
            memcpy(tombstones[ti].members, wal_buf[i].members,
                   (size_t)wal_buf[i].member_count * sizeof(uint32_t));
            ti++;
        }
    }

    /* ── Step 4: Allocate result arrays ──────────────────────────────── */
    uint32_t max_results = seq_count + wal_match_cap;

    HmRangeResult *res = calloc(1, sizeof(HmRangeResult));
    if (!res) { free(seq_ids); free(wal_buf); free(tombstones); return NULL; }

    if (max_results == 0) {
        snprintf(res->plan.strategy, sizeof(res->plan.strategy), "FMI_LOOKUP");
        res->plan.buckets_scanned = 0;
        res->plan.total_buckets   = store->tpi->header.total_records;
        res->plan.speedup_factor  = 1.0f;
        res->plan.elapsed_us      = (now_us() - t0);
        free(wal_buf); free(tombstones);
        return res;
    }

    res->timestamps      = malloc(max_results * sizeof(uint32_t));
    res->member_counts   = malloc(max_results * sizeof(uint8_t));
    res->weights         = malloc(max_results * sizeof(float));
    res->mean_dists      = malloc(max_results * sizeof(float));
    res->formations_flat = malloc((size_t)max_results * HM_FORMATION_LEN);
    res->member_offsets  = malloc(max_results * sizeof(uint32_t));
    size_t flat_cap      = (size_t)max_results * HM_MAX_MEMBERS;
    res->member_ids_flat = malloc(flat_cap * sizeof(uint32_t));
    /* V2: allocate per-record props arrays */
    res->props_lens      = calloc(max_results, sizeof(uint32_t));
    res->props_offsets   = calloc(max_results, sizeof(uint32_t));
    size_t props_cap     = (size_t)max_results * HM_MAX_PROPS_LEN;
    res->props_flat      = malloc(props_cap);

    if (!res->timestamps || !res->member_counts || !res->weights ||
        !res->mean_dists || !res->formations_flat ||
        !res->member_ids_flat || !res->member_offsets ||
        !res->props_lens || !res->props_offsets || !res->props_flat)
    {
        hm_range_result_free(res);
        free(seq_ids); free(wal_buf); free(tombstones);
        return NULL;
    }

    uint32_t out_count    = 0;
    uint32_t flat_cur     = 0;
    uint32_t props_flat_cur = 0;   /* byte cursor into res->props_flat */
    uint64_t deadline     = query_deadline(store);
    int      timed_out    = 0;

    /* ── Step 5: Scan hyperedges.bin, stop when all seq-IDs found ─────── */
    if (seq_count > 0) {
        uint32_t si      = 0;
        uint32_t cur_seq = 0;
        off_t    pos     = 0;
        int      he_fd   = store->tpi->he_fd;
        uint8_t  fmtver  = store->tpi->format_version;
        size_t   hdr_sz  = (fmtver == HM_VERSION)
                           ? sizeof(HmHyperedgeHeader)
                           : sizeof(HmHyperedgeHeaderV2);
        uint8_t  hdr_buf[sizeof(HmHyperedgeHeaderV2)];

        while (si < seq_count) {
            if (HM_DEADLINE_TRIPPED(deadline, cur_seq)) { timed_out = 1; break; }
            ssize_t r = pread(he_fd, hdr_buf, hdr_sz, pos);
            if (r != (ssize_t)hdr_sz) break;

            uint32_t ts;
            uint8_t  mc;
            float    weight, mean_dist;
            char     formation[HM_FORMATION_LEN];
            uint32_t props_len = 0;

            if (fmtver == HM_VERSION) {
                const HmHyperedgeHeader *h = (const HmHyperedgeHeader *)hdr_buf;
                ts        = h->event_ts;
                mc        = h->member_count;
                weight    = h->weight;
                mean_dist = h->mean_dist_m;
                memcpy(formation, h->formation, HM_FORMATION_LEN);
                props_len = 0;
            } else {
                const HmHyperedgeHeaderV2 *h = (const HmHyperedgeHeaderV2 *)hdr_buf;
                ts        = h->event_ts;
                mc        = h->member_count;
                weight    = h->weight;
                mean_dist = h->mean_dist_m;
                memcpy(formation, h->formation, HM_FORMATION_LEN);
                props_len = h->props_len;
                if (props_len > HM_MAX_PROPS_LEN) props_len = HM_MAX_PROPS_LEN;
            }
            if (mc > HM_MAX_MEMBERS) mc = HM_MAX_MEMBERS;
            size_t rec_bytes = (fmtver == HM_VERSION)
                               ? HM_RECORD_BYTES(mc)
                               : HM_RECORD_BYTES_V2(mc, props_len);

            if (cur_seq == seq_ids[si]) {
                uint32_t members[HM_MAX_MEMBERS];
                memset(members, 0, sizeof(members));
                if (mc > 0)
                    pread(he_fd, members, (size_t)mc * sizeof(uint32_t),
                          pos + (off_t)hdr_sz);

                int cancelled = 0;
                for (uint32_t ti = 0; ti < tomb_count && !cancelled; ti++) {
                    if (tombstones[ti].event_ts != ts ||
                        tombstones[ti].mc       != mc) continue;
                    int match = 1;
                    for (uint8_t m = 0; m < mc && match; m++) {
                        int found = 0;
                        for (uint8_t n2 = 0; n2 < tombstones[ti].mc; n2++) {
                            if (members[m] == tombstones[ti].members[n2]) {
                                found = 1; break;
                            }
                        }
                        if (!found) match = 0;
                    }
                    if (match) cancelled = 1;
                }

                if (!cancelled) {
                    res->timestamps[out_count]    = ts;
                    res->member_counts[out_count] = mc;
                    res->weights[out_count]       = weight;
                    res->mean_dists[out_count]    = mean_dist;
                    memcpy(&res->formations_flat[(size_t)out_count * HM_FORMATION_LEN],
                           formation, HM_FORMATION_LEN);
                    res->formations_flat[(size_t)out_count * HM_FORMATION_LEN
                                        + HM_FORMATION_LEN - 1] = '\0';
                    res->member_offsets[out_count] = flat_cur;
                    memcpy(&res->member_ids_flat[flat_cur], members,
                           (size_t)mc * sizeof(uint32_t));
                    flat_cur  += mc;
                    /* V2: copy property blob if present */
                    res->props_offsets[out_count] = props_flat_cur;
                    if (props_len > 0 && fmtver != HM_VERSION) {
                        off_t props_off = pos + (off_t)hdr_sz
                                        + (off_t)((size_t)mc * sizeof(uint32_t));
                        ssize_t pr = pread(he_fd,
                                           res->props_flat + props_flat_cur,
                                           props_len, props_off);
                        uint32_t stored = (pr > 0) ? (uint32_t)pr : 0;
                        res->props_lens[out_count] = stored;
                        props_flat_cur += stored;
                    } else {
                        res->props_lens[out_count] = 0;
                    }
                    out_count++;
                }
                si++;
            }

            pos     += (off_t)rec_bytes;
            cur_seq++;
        }
    }

    /* ── Step 6: Append WAL INSERT records containing node_id ─────────── */
    for (uint32_t i = 0; i < wal_count; i++) {
        if (wal_buf[i].type != HM_WAL_INSERT) continue;
        int has_node = 0;
        for (uint8_t j = 0; j < wal_buf[i].member_count; j++) {
            if (wal_buf[i].members[j] == node_id) { has_node = 1; break; }
        }
        if (!has_node) continue;

        uint8_t mc = wal_buf[i].member_count;
        if (mc > HM_MAX_MEMBERS) mc = HM_MAX_MEMBERS;

        res->timestamps[out_count]    = wal_buf[i].event_ts;
        res->member_counts[out_count] = mc;
        res->weights[out_count]       = wal_buf[i].weight;
        res->mean_dists[out_count]    = wal_buf[i].mean_dist_m;
        memcpy(&res->formations_flat[(size_t)out_count * HM_FORMATION_LEN],
               wal_buf[i].formation, HM_FORMATION_LEN);
        res->formations_flat[(size_t)out_count * HM_FORMATION_LEN
                            + HM_FORMATION_LEN - 1] = '\0';
        res->member_offsets[out_count] = flat_cur;
        memcpy(&res->member_ids_flat[flat_cur], wal_buf[i].members,
               (size_t)mc * sizeof(uint32_t));
        flat_cur  += mc;
        /* V2: copy WAL property blob if present */
        res->props_offsets[out_count] = props_flat_cur;
        uint32_t wplen = wal_buf[i].props_len;
        if (wplen > HM_MAX_PROPS_LEN) wplen = HM_MAX_PROPS_LEN;
        if (wplen > 0 && wal_buf[i].props) {
            memcpy(res->props_flat + props_flat_cur,
                   wal_buf[i].props, wplen);
            res->props_lens[out_count] = wplen;
            props_flat_cur += wplen;
        } else {
            res->props_lens[out_count] = 0;
        }
        out_count++;
    }

    free(seq_ids);
    free(wal_buf);
    free(tombstones);

    uint32_t total_tpi = store->tpi->header.total_records;
    res->count                = out_count;
    res->plan.buckets_scanned = seq_count;   /* actual degree (records fetched) */
    res->plan.total_buckets   = total_tpi;
    res->plan.speedup_factor  = (seq_count > 0 && total_tpi > 0)
                                ? (float)total_tpi / (float)seq_count
                                : 1.0f;
    res->plan.elapsed_us      = now_us() - t0;
    res->plan.timed_out       = (uint8_t)timed_out;
    snprintf(res->plan.strategy, sizeof(res->plan.strategy), "FMI_LOOKUP");
    return res;
}

/* ── Full-scan baseline ──────────────────────────────────────────────────── */

/* Public locked wrapper */
HmRangeResult *hm_full_scan_range(HmStore *store, uint32_t start_ts, uint32_t end_ts) {
    if (!store) return NULL;
    RDLOCK(store);
    HmRangeResult *r = _full_scan_impl(store, start_ts, end_ts);
    RDUNLOCK(store);
    return r;
}

static HmRangeResult *_full_scan_impl(HmStore *store,
                                       uint32_t start_ts,
                                       uint32_t end_ts)
{
    uint64_t t0 = 0;
    {
        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        t0 = (uint64_t)ts.tv_sec * 1000000u + (uint64_t)(ts.tv_nsec / 1000);
    }

    uint32_t total = store->tpi->header.total_records;

    /* Allocate worst-case result arrays */
    HmRangeResult *res       = calloc(1, sizeof(HmRangeResult));
    uint32_t *timestamps     = malloc(total * sizeof(uint32_t));
    uint8_t  *member_counts  = malloc(total * sizeof(uint8_t));
    float    *weights        = malloc(total * sizeof(float));
    float    *mean_dists     = malloc(total * sizeof(float));
    char     *formations_flat= malloc((size_t)total * HM_FORMATION_LEN);
    uint32_t *member_offsets = malloc(total * sizeof(uint32_t));
    uint32_t *member_ids_flat= malloc((size_t)total * HM_MAX_MEMBERS * sizeof(uint32_t));
    size_t    max_rec        = HM_RECORD_BYTES_V2(HM_MAX_MEMBERS, HM_MAX_PROPS_LEN);
    uint8_t  *buf            = malloc(max_rec);

    if (!res || !timestamps || !member_counts || !weights || !mean_dists ||
        !formations_flat || !member_offsets || !member_ids_flat || !buf)
    {
        free(res); free(timestamps); free(member_counts); free(weights);
        free(mean_dists); free(formations_flat); free(member_offsets);
        free(member_ids_flat); free(buf);
        return NULL;
    }

    int      he_fd      = store->tpi->he_fd;
    uint8_t  fmtver     = store->tpi->format_version;
    size_t   hdr_sz     = (fmtver == HM_VERSION)
                          ? sizeof(HmHyperedgeHeader)
                          : sizeof(HmHyperedgeHeaderV2);
    uint32_t out_count  = 0;
    uint32_t flat_cursor= 0;
    off_t    offset     = 0;
    uint64_t deadline   = query_deadline(store);
    int      timed_out  = 0;

    /* Sequential scan — no bucket directory, no binary search */
    for (uint32_t i = 0; i < total; i++) {
        if (HM_DEADLINE_TRIPPED(deadline, i)) { timed_out = 1; break; }
        ssize_t n = pread(he_fd, buf, hdr_sz, offset);
        if (n != (ssize_t)hdr_sz) break;

        uint32_t ts;
        uint8_t  mc;
        float    weight, mean_dist;
        char     formation[HM_FORMATION_LEN];
        uint32_t props_len = 0;

        if (fmtver == HM_VERSION) {
            const HmHyperedgeHeader *h = (const HmHyperedgeHeader *)buf;
            ts        = h->event_ts;
            mc        = h->member_count;
            weight    = h->weight;
            mean_dist = h->mean_dist_m;
            memcpy(formation, h->formation, HM_FORMATION_LEN);
        } else {
            const HmHyperedgeHeaderV2 *h = (const HmHyperedgeHeaderV2 *)buf;
            ts        = h->event_ts;
            mc        = h->member_count;
            weight    = h->weight;
            mean_dist = h->mean_dist_m;
            memcpy(formation, h->formation, HM_FORMATION_LEN);
            props_len = h->props_len;
            if (props_len > HM_MAX_PROPS_LEN) props_len = HM_MAX_PROPS_LEN;
        }
        if (mc > HM_MAX_MEMBERS) mc = HM_MAX_MEMBERS;

        uint32_t members[HM_MAX_MEMBERS];
        memset(members, 0, sizeof(members));
        if (mc > 0)
            pread(he_fd, members, (size_t)mc * sizeof(uint32_t),
                  offset + (off_t)hdr_sz);

        size_t rec_bytes = (fmtver == HM_VERSION)
                           ? HM_RECORD_BYTES(mc)
                           : HM_RECORD_BYTES_V2(mc, props_len);
        offset += (off_t)rec_bytes;

        if (ts < start_ts || ts > end_ts) continue;

        timestamps[out_count]    = ts;
        member_counts[out_count] = mc;
        weights[out_count]       = weight;
        mean_dists[out_count]    = mean_dist;
        memcpy(&formations_flat[(size_t)out_count * HM_FORMATION_LEN],
               formation, HM_FORMATION_LEN);
        formations_flat[(size_t)out_count * HM_FORMATION_LEN + HM_FORMATION_LEN - 1] = '\0';
        member_offsets[out_count] = flat_cursor;
        memcpy(&member_ids_flat[flat_cursor], members, (size_t)mc * sizeof(uint32_t));
        flat_cursor += mc;
        out_count++;
    }
    free(buf);

    res->count           = out_count;
    res->timestamps      = timestamps;
    res->member_counts   = member_counts;
    res->weights         = weights;
    res->mean_dists      = mean_dists;
    res->formations_flat = formations_flat;
    res->member_ids_flat = member_ids_flat;
    res->member_offsets  = member_offsets;

    struct timespec ts2;
    clock_gettime(CLOCK_MONOTONIC, &ts2);
    uint64_t t1 = (uint64_t)ts2.tv_sec * 1000000u + (uint64_t)(ts2.tv_nsec / 1000);

    snprintf(res->plan.strategy, sizeof(res->plan.strategy), "FULL_SCAN");
    res->plan.buckets_scanned = store->tpi->header.bucket_count;
    res->plan.total_buckets   = store->tpi->header.bucket_count;
    res->plan.speedup_factor  = 1.0f;
    res->plan.elapsed_us      = t1 - t0;
    res->plan.timed_out       = (uint8_t)timed_out;
    return res;
}

/* ── Index metadata ──────────────────────────────────────────────────────── */

uint32_t hm_total_records(HmStore *store) {
    if (!store) return 0;
    RDLOCK(store);
    uint32_t v = store->tpi->header.total_records;
    RDUNLOCK(store);
    return v;
}
uint32_t hm_bucket_count(HmStore *store) {
    if (!store) return 0;
    RDLOCK(store);
    uint32_t v = store->tpi->header.bucket_count;
    RDUNLOCK(store);
    return v;
}
uint32_t hm_bucket_seconds(HmStore *store) {
    if (!store) return 0;
    RDLOCK(store);
    uint32_t v = store->tpi->header.bucket_seconds;
    RDUNLOCK(store);
    return v;
}
uint32_t hm_node_count(HmStore *store) {
    if (!store) return 0;
    RDLOCK(store);
    uint32_t v = store->fmi->header.node_count;
    RDUNLOCK(store);
    return v;
}

/* ── Write path (WAL-backed) ─────────────────────────────────────────────── */

/* Internal: build HmRecord and append to WAL */
static int _insert_impl(HmStore        *store,
                         uint32_t        event_ts,
                         const uint32_t *members,
                         uint8_t         member_count,
                         float           weight,
                         float           mean_dist_m,
                         const char     *formation,
                         const uint8_t  *props,
                         uint32_t        props_len)
{
    if (!store || !store->wal) { set_error("Store or WAL not open"); return -1; }
    if (member_count > HM_MAX_MEMBERS) { set_error("member_count exceeds HM_MAX_MEMBERS"); return -1; }
    if (props_len > HM_MAX_PROPS_LEN)  { set_error("props_len exceeds HM_MAX_PROPS_LEN");  return -1; }

    /* Props require a V2 WAL */
    if (props && props_len > 0 &&
        store->wal->format_version == HM_WAL_VERSION)
    {
        set_error("Property blobs require a V2 WAL. Call hm_compact() first "
                  "to upgrade this V1 store, then retry.");
        return -1;
    }

    HmRecord rec;
    memset(&rec, 0, sizeof(rec));
    rec.event_ts     = event_ts;
    rec.member_count = member_count;
    rec.weight       = weight;
    rec.mean_dist_m  = mean_dist_m;
    if (formation)
        strncpy(rec.formation, formation, HM_FORMATION_LEN - 1);
    if (members && member_count > 0)
        memcpy(rec.members, members, (size_t)member_count * sizeof(uint32_t));
    if (props && props_len > 0) {
        rec.props_len = props_len;
        memcpy(rec.props, props, props_len);
    }

    WRLOCK(store);
    if (hm_wal_append_insert(store->wal, &rec) < 0) {
        WRUNLOCK(store);
        set_error("WAL append failed");
        return -1;
    }
    if (store->autocompact_threshold > 0 &&
        store->wal->entry_count >= store->autocompact_threshold)
        _compact_impl(store, 0);
    WRUNLOCK(store);
    return 0;
}

int hm_insert_record(HmStore        *store,
                      uint32_t        event_ts,
                      const uint32_t *members,
                      uint8_t         member_count,
                      float           weight,
                      float           mean_dist_m,
                      const char     *formation)
{
    return _insert_impl(store, event_ts, members, member_count,
                        weight, mean_dist_m, formation, NULL, 0);
}

int hm_insert_record_v2(HmStore        *store,
                         uint32_t        event_ts,
                         const uint32_t *members,
                         uint8_t         member_count,
                         float           weight,
                         float           mean_dist_m,
                         const char     *formation,
                         const uint8_t  *props,
                         uint32_t        props_len)
{
    return _insert_impl(store, event_ts, members, member_count,
                        weight, mean_dist_m, formation, props, props_len);
}

int hm_delete_record(HmStore        *store,
                      uint32_t        event_ts,
                      const uint32_t *members,
                      uint8_t         member_count)
{
    if (!store || !store->wal) { set_error("Store or WAL not open"); return -1; }
    if (member_count > HM_MAX_MEMBERS) { set_error("member_count exceeds max"); return -1; }

    WRLOCK(store);
    if (hm_wal_append_delete(store->wal, event_ts, member_count, members) < 0) {
        WRUNLOCK(store);
        set_error("WAL tombstone append failed");
        return -1;
    }
    if (store->autocompact_threshold > 0 &&
        store->wal->entry_count >= store->autocompact_threshold)
        _compact_impl(store, 0);
    WRUNLOCK(store);
    return 0;
}

/*
 * hm_commit_batch — atomic group commit of a whole transaction.
 *
 * Writes `n` ops (inserts and/or deletes, described by parallel flat arrays)
 * to the WAL as a single crash-atomic batch frame under one write lock and one
 * fdatasync. All-or-nothing on recovery, and no concurrent reader can observe a
 * partial transaction (the write lock is held for the entire append). This is
 * the primitive the Python transaction()/commit() path is built on.
 *
 * Arrays (length n unless noted):
 *   types          : HM_WAL_INSERT (1) or HM_WAL_DELETE (2) per op
 *   event_ts       : event timestamp per op
 *   member_counts  : member count per op (<= HM_MAX_MEMBERS)
 *   members_flat   : concatenated member ids (length = sum(member_counts))
 *   member_offsets : start index into members_flat per op
 *   weights        : weight per op (may be NULL → 0)
 *   mean_dists     : mean_dist_m per op (may be NULL → 0)
 *   formations_flat: n * HM_FORMATION_LEN bytes of fixed-width formation strings
 *                    (may be NULL → empty)
 *
 * Returns 0 on success, -1 on error. n == 0 is a no-op returning 0.
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
                     const char     *formations_flat)
{
    if (!store || !store->wal) { set_error("Store or WAL not open"); return -1; }
    if (n == 0) return 0;
    if (!types || !event_ts || !member_counts) {
        set_error("hm_commit_batch: required array is NULL");
        return -1;
    }

    HmWalEntry *es = calloc(n, sizeof(HmWalEntry));
    if (!es) { set_error("OOM: commit batch"); return -1; }

    for (uint32_t i = 0; i < n; i++) {
        uint8_t mc = member_counts[i];
        if (mc > HM_MAX_MEMBERS) {
            free(es);
            set_error("member_count exceeds HM_MAX_MEMBERS");
            return -1;
        }
        es[i].type         = (types[i] == HM_WAL_DELETE) ? HM_WAL_DELETE : HM_WAL_INSERT;
        es[i].event_ts     = event_ts[i];
        es[i].member_count = mc;
        es[i].weight       = weights    ? weights[i]    : 0.0f;
        es[i].mean_dist_m  = mean_dists ? mean_dists[i] : 0.0f;
        if (formations_flat)
            strncpy(es[i].formation,
                    formations_flat + (size_t)i * HM_FORMATION_LEN,
                    HM_FORMATION_LEN - 1);
        if (mc > 0 && members_flat && member_offsets)
            memcpy(es[i].members,
                   members_flat + member_offsets[i],
                   (size_t)mc * sizeof(uint32_t));
        es[i].props_len = 0;
    }

    WRLOCK(store);
    int rc = hm_wal_append_batch(store->wal, es, n);
    if (rc == 0 &&
        store->autocompact_threshold > 0 &&
        store->wal->entry_count >= store->autocompact_threshold)
        _compact_impl(store, 0);
    WRUNLOCK(store);
    free(es);

    if (rc < 0) { set_error("WAL batch append failed"); return -1; }
    return 0;
}

/* ──────────────────────────────────────────────────────────────────────────
 * Compaction tombstone index
 *
 * Reconciling WAL DELETE tombstones against (a) the existing on-disk TPI
 * records and (b) earlier WAL INSERTs is the heart of compaction.  The original
 * implementation did this with two nested scans — O(P·W) for the TPI pass and
 * O(W²) for the WAL self-cancellation pass — so a single compaction of a large
 * write batch was quadratic: a 5 M-row bulk load implied ~2.5·10¹³ member
 * comparisons and never completed in practice.
 *
 * We replace both scans with one open-addressing hash index over the DELETE
 * tombstones, keyed by the order-independent (event_ts, member-multiset)
 * identity of a hyperedge and valued by the *latest* WAL position at which that
 * key was deleted.  Slots store positions into the caller's `wal_all` array
 * (8 bytes/slot, no key copies), so the index is memory-light even for large
 * delete batches.  Reconciliation then becomes O(1) per record:
 *
 *   • a pre-WAL TPI record is dropped  ⇔  its key appears in the index
 *     (every tombstone post-dates the on-disk index);
 *   • a WAL INSERT at position i is dropped  ⇔  the key's latest tombstone
 *     position is > i (a later DELETE cancels it; an earlier DELETE followed by
 *     this re-INSERT — the UPDATE pattern — must survive).
 *
 * This is exactly the semantics of the previous nested scans, at O(P + W)
 * instead of O((P + W)²), leaving the O(N log N) record sort in the TPI writer
 * as the dominant compaction term.
 * ────────────────────────────────────────────────────────────────────────── */

#define HM_TOMB_EMPTY UINT32_MAX   /* sentinel: slot empty / key absent */

typedef struct {
    uint32_t rep_pos;   /* wal_all position of a representative DELETE for the
                         * key (HM_TOMB_EMPTY ⇒ empty slot) */
    uint32_t last_pos;  /* latest wal_all position of a DELETE for the key */
} HmTombSlot;

typedef struct {
    HmTombSlot       *slots;
    const HmWalEntry *wal;   /* backing store for key comparison */
    uint32_t          cap;   /* power of two; 0 ⇒ empty index (all lookups miss) */
    uint32_t          mask;
    uint32_t          count;
} HmTombIndex;

/* Sort a copy of `src` ascending into `dst` so equal multisets hash equally. */
static void tomb_canon(const uint32_t *src, uint8_t mc, uint32_t *dst) {
    memcpy(dst, src, (size_t)mc * sizeof(uint32_t));
    qsort(dst, mc, sizeof(uint32_t), cmp_uint32_asc);
}

/* FNV-1a over (event_ts, canonical member array). */
static uint64_t tomb_hash(uint32_t event_ts, const uint32_t *sorted, uint8_t mc) {
    uint64_t h = 1469598103934665603ULL;
    const uint8_t *p = (const uint8_t *)&event_ts;
    for (size_t i = 0; i < sizeof(event_ts); i++) { h ^= p[i]; h *= 1099511628211ULL; }
    for (uint8_t i = 0; i < mc; i++) {
        uint32_t v = sorted[i];
        const uint8_t *q = (const uint8_t *)&v;
        for (size_t j = 0; j < sizeof(v); j++) { h ^= q[j]; h *= 1099511628211ULL; }
    }
    return h;
}

/* Size the table for `n_deletes` keys at ≤0.7 load.  Returns -1 on OOM. */
static int tomb_init(HmTombIndex *ti, const HmWalEntry *wal, uint32_t n_deletes) {
    memset(ti, 0, sizeof(*ti));
    ti->wal = wal;
    if (n_deletes == 0) return 0;                 /* empty index, all lookups miss */
    uint32_t cap = 16;
    while ((uint64_t)cap * 7ULL < (uint64_t)n_deletes * 10ULL) cap <<= 1;
    ti->slots = malloc((size_t)cap * sizeof(HmTombSlot));
    if (!ti->slots) return -1;
    memset(ti->slots, 0xFF, (size_t)cap * sizeof(HmTombSlot)); /* rep_pos = HM_TOMB_EMPTY */
    ti->cap  = cap;
    ti->mask = cap - 1;
    return 0;
}

static void tomb_free(HmTombIndex *ti) {
    if (ti) { free(ti->slots); ti->slots = NULL; ti->cap = 0; ti->mask = 0; }
}

/* Record a DELETE located at wal position `pos`, keeping the latest position. */
static void tomb_put(HmTombIndex *ti, uint32_t pos) {
    if (ti->cap == 0) return;
    const HmWalEntry *e = &ti->wal[pos];
    uint32_t sorted[HM_MAX_MEMBERS];
    tomb_canon(e->members, e->member_count, sorted);
    uint32_t idx = (uint32_t)tomb_hash(e->event_ts, sorted, e->member_count) & ti->mask;
    for (;;) {
        HmTombSlot *s = &ti->slots[idx];
        if (s->rep_pos == HM_TOMB_EMPTY) {
            s->rep_pos = pos; s->last_pos = pos; ti->count++;
            return;
        }
        const HmWalEntry *r = &ti->wal[s->rep_pos];
        if (r->event_ts == e->event_ts &&
            members_match(r->members, r->member_count, e->members, e->member_count)) {
            if (pos > s->last_pos) s->last_pos = pos;
            return;
        }
        idx = (idx + 1) & ti->mask;
    }
}

/* Latest DELETE position for (event_ts, members), or HM_TOMB_EMPTY if none. */
static uint32_t tomb_get(const HmTombIndex *ti,
                         uint32_t event_ts, const uint32_t *members, uint8_t mc) {
    if (ti->cap == 0) return HM_TOMB_EMPTY;
    uint32_t sorted[HM_MAX_MEMBERS];
    tomb_canon(members, mc, sorted);
    uint32_t idx = (uint32_t)tomb_hash(event_ts, sorted, mc) & ti->mask;
    for (;;) {
        const HmTombSlot *s = &ti->slots[idx];
        if (s->rep_pos == HM_TOMB_EMPTY) return HM_TOMB_EMPTY;
        const HmWalEntry *r = &ti->wal[s->rep_pos];
        if (r->event_ts == event_ts &&
            members_match(r->members, r->member_count, members, mc))
            return s->last_pos;
        idx = (idx + 1) & ti->mask;
    }
}

/*
 * _compact_impl — core compaction logic (caller must hold write lock).
 *
 * min_event_ts: records with event_ts < min_event_ts are discarded (TTL).
 *               Pass 0 to keep all records.
 */
static int _compact_impl(HmStore *store, uint32_t min_event_ts)
{
    /* Step 1: Full scan of the current TPI (all timestamps) */
    HmRangeResult *primary = hm_tpi_range_query(store->tpi, 0, UINT32_MAX);
    if (!primary) { set_error("Full scan failed during compact"); return -1; }

    /* Step 2: Read all WAL entries */
    uint32_t   wal_total = 0;
    HmWalEntry *wal_all  = NULL;
    if (store->wal && store->wal->entry_count > 0)
        wal_all = hm_wal_scan_all(store->wal, &wal_total);

    /* Step 3: Build merged record list via a fresh writer */
    HmTpiWriter *writer =
        hm_tpi_writer_new(store->tpi->header.bucket_seconds);
    if (!writer) {
        set_error("OOM: compact writer");
        hm_range_result_free(primary);
        free(wal_all);
        return -1;
    }

    /* Build the tombstone index from WAL DELETEs (O(W)) so the two
     * reconciliation passes below are O(1) per record instead of O(W). */
    uint32_t n_deletes = 0;
    for (uint32_t d = 0; d < wal_total; d++)
        if (wal_all[d].type == HM_WAL_DELETE) n_deletes++;

    HmTombIndex tomb;
    if (tomb_init(&tomb, wal_all, n_deletes) < 0) {
        set_error("OOM: compact tombstone index");
        hm_tpi_writer_free(writer);
        hm_range_result_free(primary);
        free(wal_all);
        return -1;
    }
    for (uint32_t d = 0; d < wal_total; d++)
        if (wal_all[d].type == HM_WAL_DELETE) tomb_put(&tomb, d);

    /* Add primary records that are NOT tombstoned and NOT expired (TTL).
     * A pre-WAL TPI record is tombstoned iff its key appears in any WAL
     * DELETE (every tombstone post-dates the on-disk index). */
    for (uint32_t i = 0; i < primary->count; i++) {
        uint32_t        ts   = primary->timestamps[i];
        uint8_t         mc   = primary->member_counts[i];
        const uint32_t *mptr = &primary->member_ids_flat[primary->member_offsets[i]];

        if (min_event_ts > 0 && ts < min_event_ts) continue;

        if (tomb_get(&tomb, ts, mptr, mc) != HM_TOMB_EMPTY) continue;

        HmRecord rec;
        memset(&rec, 0, sizeof(rec));
        rec.event_ts     = ts;
        rec.member_count = mc;
        rec.weight       = primary->weights[i];
        rec.mean_dist_m  = primary->mean_dists[i];
        memcpy(rec.formation,
               &primary->formations_flat[(size_t)i * HM_FORMATION_LEN],
               HM_FORMATION_LEN);
        memcpy(rec.members, mptr, (size_t)mc * sizeof(uint32_t));
        /* Carry props blob if present (V2 TPI) */
        if (primary->props_lens && primary->props_flat) {
            uint32_t plen = primary->props_lens[i];
            if (plen > HM_MAX_PROPS_LEN) plen = HM_MAX_PROPS_LEN;
            rec.props_len = plen;
            if (plen > 0)
                memcpy(rec.props,
                       primary->props_flat + primary->props_offsets[i], plen);
        }

        if (hm_tpi_writer_insert(writer, &rec) < 0) {
            set_error("OOM: compact insert");
            tomb_free(&tomb);
            hm_tpi_writer_free(writer);
            hm_range_result_free(primary);
            free(wal_all);
            return -1;
        }
    }
    hm_range_result_free(primary);

    /*
     * Add WAL inserts not cancelled by a *later* WAL delete and not expired.
     *
     * The ordering invariant: a DELETE at WAL position d only cancels an
     * INSERT at position i if d > i (the delete was written AFTER the insert).
     * A DELETE at d < i means the record was first deleted and then re-inserted
     * — the re-insert must survive.  Without this ordering check, a delete
     * followed by a re-insert of the same key (the UPDATE pattern) would
     * incorrectly drop the re-inserted record during compaction.
     */
    for (uint32_t i = 0; i < wal_total; i++) {
        if (wal_all[i].type != HM_WAL_INSERT) continue;
        if (min_event_ts > 0 && wal_all[i].event_ts < min_event_ts) continue;

        /* Cancelled iff a DELETE for the same key appears at a *later* WAL
         * position (delete-then-reinsert — the UPDATE pattern — survives). */
        uint32_t del_pos = tomb_get(&tomb, wal_all[i].event_ts,
                                    wal_all[i].members, wal_all[i].member_count);
        if (del_pos != HM_TOMB_EMPTY && del_pos > i) continue;

        HmRecord rec;
        memset(&rec, 0, sizeof(rec));
        rec.event_ts     = wal_all[i].event_ts;
        rec.member_count = wal_all[i].member_count;
        rec.weight       = wal_all[i].weight;
        rec.mean_dist_m  = wal_all[i].mean_dist_m;
        memcpy(rec.formation, wal_all[i].formation, HM_FORMATION_LEN);
        memcpy(rec.members, wal_all[i].members,
               (size_t)wal_all[i].member_count * sizeof(uint32_t));
        /* Carry props blob from WAL entry */
        uint32_t plen = wal_all[i].props_len;
        if (plen > HM_MAX_PROPS_LEN) plen = HM_MAX_PROPS_LEN;
        rec.props_len = plen;
        if (plen > 0)
            memcpy(rec.props, wal_all[i].props, plen);

        if (hm_tpi_writer_insert(writer, &rec) < 0) {
            set_error("OOM: compact WAL insert");
            tomb_free(&tomb);
            hm_tpi_writer_free(writer);
            free(wal_all);
            return -1;
        }
    }
    tomb_free(&tomb);
    free(wal_all);

    /* Step 4: Flush new index (overwrites existing binary files) */
    int rc = hm_tpi_writer_flush(writer, store->dir_path);
    hm_tpi_writer_free(writer);
    if (rc < 0) { set_error("Flush failed during compact"); return -1; }

    /* Step 5: Truncate WAL */
    if (store->wal && hm_wal_truncate(store->wal) < 0) {
        set_error("WAL truncate failed after compact");
        return -1;
    }

    /* Step 6: Reload TPI + FMI readers from the new files */
    hm_tpi_reader_close(store->tpi);
    hm_fmi_reader_close(store->fmi);
    store->tpi = hm_tpi_reader_open(store->dir_path);
    store->fmi = hm_fmi_reader_open(store->dir_path);

    if (!store->tpi || !store->fmi) {
        set_error("Failed to reload index after compact");
        return -1;
    }
    return 0;
}

/* Public locked wrappers */
int hm_compact(HmStore *store)
{
    if (!store) { set_error("NULL store"); return -1; }
    WRLOCK(store);
    int rc = _compact_impl(store, 0);
    WRUNLOCK(store);
    return rc;
}

int hm_compact_with_ttl(HmStore *store, uint32_t min_event_ts)
{
    if (!store) { set_error("NULL store"); return -1; }
    WRLOCK(store);
    int rc = _compact_impl(store, min_event_ts);
    WRUNLOCK(store);
    return rc;
}

uint32_t hm_wal_pending(HmStore *store)
{
    if (!store || !store->wal) return 0;
    RDLOCK(store);
    uint32_t v = store->wal->entry_count;
    RDUNLOCK(store);
    return v;
}

/* ── Cypher Parser wrappers ───────────────────────────────────────────────── */

HmQueryAst *hm_parse_query_alloc(const char *cypher)
{
    HmQueryAst *ast = malloc(sizeof(HmQueryAst));
    if (!ast) return NULL;
    *ast = hm_parse_query(cypher);
    return ast;
}

void hm_query_ast_free(HmQueryAst *ast)
{
    free(ast);
}

int         hm_ast_kind    (const HmQueryAst *ast) { return ast ? (int)ast->kind    : HM_QUERY_PARSE_ERROR; }
uint32_t    hm_ast_ts_start(const HmQueryAst *ast) { return ast ? ast->ts_start     : 0; }
uint32_t    hm_ast_ts_end  (const HmQueryAst *ast) { return ast ? ast->ts_end       : 0; }
uint32_t    hm_ast_node_id (const HmQueryAst *ast) { return ast ? ast->node_id      : 0; }
const char *hm_ast_table   (const HmQueryAst *ast) { return ast ? ast->table        : ""; }
const char *hm_ast_alias   (const HmQueryAst *ast) { return ast ? ast->alias        : ""; }
const char *hm_ast_error   (const HmQueryAst *ast) { return ast ? ast->error        : ""; }
const char *hm_ast_props   (const HmQueryAst *ast) { return ast ? ast->props_csv    : ""; }

/* ── Phase 2: predicate / RETURN / ORDER BY / LIMIT accessors ─────────────── */

uint32_t    hm_ast_pred_count (const HmQueryAst *ast)           { return ast ? (uint32_t)ast->pred_count : 0; }
const char *hm_ast_pred_col   (const HmQueryAst *ast, uint32_t i) {
    if (!ast || i >= HM_MAX_PREDICATES) return "";
    return ast->pred_col[i];
}
uint8_t     hm_ast_pred_op    (const HmQueryAst *ast, uint32_t i) {
    if (!ast || i >= HM_MAX_PREDICATES) return 0;
    return ast->pred_op[i];
}
const char *hm_ast_pred_val   (const HmQueryAst *ast, uint32_t i) {
    if (!ast || i >= HM_MAX_PREDICATES) return "";
    return ast->pred_val[i];
}
uint8_t     hm_ast_pred_is_str(const HmQueryAst *ast, uint32_t i) {
    if (!ast || i >= HM_MAX_PREDICATES) return 0;
    return ast->pred_is_str[i];
}
const char *hm_ast_return_cols(const HmQueryAst *ast) { return ast ? ast->return_cols : ""; }
const char *hm_ast_order_col  (const HmQueryAst *ast) { return ast ? ast->order_col   : ""; }
uint8_t     hm_ast_order_desc (const HmQueryAst *ast) { return ast ? ast->order_desc  : 0;  }
uint32_t    hm_ast_limit_n    (const HmQueryAst *ast) { return ast ? ast->limit_n     : 0;  }

/* Phase 3: DML accessors */
const char *hm_ast_insert_cols(const HmQueryAst *ast) { return ast ? ast->insert_cols : ""; }
const char *hm_ast_insert_vals(const HmQueryAst *ast) { return ast ? ast->insert_vals : ""; }
const char *hm_ast_update_set (const HmQueryAst *ast) { return ast ? ast->update_set  : ""; }
/* Phase 4: PSI DDL accessor */
const char *hm_ast_index_col  (const HmQueryAst *ast) { return ast ? ast->index_col   : ""; }

/* ── Phase 7: Join accessors ───────────────────────────────────────────────
 *
 * Second HYPEREDGE pattern:
 *   hm_ast_table2    — second table name
 *   hm_ast_alias2    — second alias
 *   hm_ast_ts_start2 — TPI lower bound for table2  (0 = no lower bound)
 *   hm_ast_ts_end2   — TPI upper bound for table2  (UINT32_MAX = no upper)
 *   hm_ast_node_id2  — FMI node for table2         (0 = no FMI lookup)
 *
 * Per-predicate alias:
 *   hm_ast_pred_alias(ast, i) — alias string for predicate i (may be "")
 *
 * Cross-alias join predicates:
 *   hm_ast_join_count         — number of join predicates (0..HM_MAX_JOIN_PREDS)
 *   hm_ast_join_lhs_alias(i)  — left-hand alias
 *   hm_ast_join_lhs_col(i)    — left-hand column
 *   hm_ast_join_rhs_alias(i)  — right-hand alias
 *   hm_ast_join_rhs_col(i)    — right-hand column
 *   hm_ast_join_op(i)         — operator (HM_PRED_EQ / GT / GEQ / LT / LEQ)
 */
const char *hm_ast_table2   (const HmQueryAst *ast) { return ast ? ast->table2    : ""; }
const char *hm_ast_alias2   (const HmQueryAst *ast) { return ast ? ast->alias2    : ""; }
uint32_t    hm_ast_ts_start2(const HmQueryAst *ast) { return ast ? ast->ts_start2 : 0; }
uint32_t    hm_ast_ts_end2  (const HmQueryAst *ast) { return ast ? ast->ts_end2   : UINT32_MAX; }
uint32_t    hm_ast_node_id2 (const HmQueryAst *ast) { return ast ? ast->node_id2  : 0; }

const char *hm_ast_pred_alias(const HmQueryAst *ast, uint32_t i) {
    if (!ast || i >= HM_MAX_PREDICATES) return "";
    return ast->pred_alias[i];
}

uint8_t     hm_ast_join_count    (const HmQueryAst *ast) { return ast ? ast->join_count : 0; }
const char *hm_ast_join_lhs_alias(const HmQueryAst *ast, uint32_t i) {
    if (!ast || i >= HM_MAX_JOIN_PREDS) return "";
    return ast->join_lhs_alias[i];
}
const char *hm_ast_join_lhs_col  (const HmQueryAst *ast, uint32_t i) {
    if (!ast || i >= HM_MAX_JOIN_PREDS) return "";
    return ast->join_lhs_col[i];
}
const char *hm_ast_join_rhs_alias(const HmQueryAst *ast, uint32_t i) {
    if (!ast || i >= HM_MAX_JOIN_PREDS) return "";
    return ast->join_rhs_alias[i];
}
const char *hm_ast_join_rhs_col  (const HmQueryAst *ast, uint32_t i) {
    if (!ast || i >= HM_MAX_JOIN_PREDS) return "";
    return ast->join_rhs_col[i];
}
uint8_t     hm_ast_join_op       (const HmQueryAst *ast, uint32_t i) {
    if (!ast || i >= HM_MAX_JOIN_PREDS) return 0;
    return ast->join_op[i];
}

/* Phase 8: per-table TTL / autocompact threshold */
uint32_t    hm_ast_compact_threshold(const HmQueryAst *ast) {
    if (!ast) return 0;
    return ast->compact_threshold;
}

/* ── V2 property blob result accessors ───────────────────────────────────── */

const uint8_t *hm_res_props(const HmRangeResult *r, uint32_t i) {
    if (!r || i >= r->count || !r->props_flat || !r->props_lens) return NULL;
    if (r->props_lens[i] == 0) return NULL;
    return r->props_flat + r->props_offsets[i];
}

uint32_t hm_res_props_len(const HmRangeResult *r, uint32_t i) {
    if (!r || i >= r->count || !r->props_lens) return 0;
    return r->props_lens[i];
}
