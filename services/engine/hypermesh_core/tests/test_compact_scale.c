/*
 * test_compact_scale.c — compaction scale + tombstone-reconciliation regression
 *
 * Guards the hash-indexed compaction path (hypermesh.c: HmTombIndex) against
 * two regressions:
 *
 *   1. SCALE — a single compaction of a large write batch must be near-linear.
 *      The historical nested-scan reconciliation was O((P+W)²); compacting the
 *      N records below took >50 s at 200 k and never finished at 700 k.  With the
 *      O(P+W) tombstone index it completes in well under a second, so if this
 *      test ever "hangs" the quadratic has returned.
 *
 *   2. SEMANTICS — the index must reproduce the exact delete/re-insert rules of
 *      the original nested scan, at scale and with order-independent keys:
 *        • a WAL DELETE removes a matching on-disk (TPI) record;
 *        • a WAL INSERT cancelled by a *later* DELETE disappears;
 *        • a DELETE followed by a re-INSERT of the same key (UPDATE) survives;
 *        • member order is irrelevant to identity.
 *
 *  T01  Bulk insert N distinct hyperedges into the WAL
 *  T02  compact() returns 0 and truncates the WAL
 *  T03  All N records survive compaction (total_records == N)
 *  T04  A representative record is queryable after compaction
 *  T05  WAL DELETE of a compacted (TPI) record → absent after re-compact
 *  T06  DELETE-then-reINSERT (UPDATE, reversed member order) → present
 *  T07  INSERT-then-DELETE in the same WAL → absent
 *  T08  An untouched record is still present; net count is N-1
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/stat.h>
#include <unistd.h>

#include "../src/hypermesh.h"

static int g_pass = 0, g_fail = 0;

#define CHECK(cond, name) do {                              \
    if (cond) { printf("  PASS  %s\n", name); g_pass++; }   \
    else      { printf("  FAIL  %s  (line %d)\n", name, __LINE__); g_fail++; } \
} while (0)

#define N 200000u

static const char *TEST_DIR = "/tmp/hm_test_compact_scale";

static void rm_rf(const char *path) {
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "rm -rf '%s'", path);
    (void)system(cmd);
}

/* Deterministic, distinct 2-member key for record i. */
static void key_for(uint32_t i, uint32_t *ts, uint32_t members[2]) {
    *ts        = i + 1;            /* avoid ts == 0 */
    members[0] = 2u * i + 100u;
    members[1] = 2u * i + 101u;
}

/* True iff some record with exactly this (ts, {a,b}) pair exists in [ts,ts]. */
static int present(HmStore *s, uint32_t ts, uint32_t a, uint32_t b) {
    HmRangeResult *r = hm_range_query(s, ts, ts);
    if (!r) return 0;
    int found = 0;
    for (uint32_t i = 0; i < hm_res_count(r) && !found; i++) {
        if (hm_res_member_count(r, i) != 2) continue;
        uint32_t m0 = hm_res_member_id(r, i, 0);
        uint32_t m1 = hm_res_member_id(r, i, 1);
        if ((m0 == a && m1 == b) || (m0 == b && m1 == a)) found = 1;
    }
    hm_range_result_free(r);
    return found;
}

int main(void)
{
    printf("=== HyperMesh Compaction Scale + Reconciliation Tests ===\n\n");

    rm_rf(TEST_DIR);
    mkdir(TEST_DIR, 0755);

    int init_rc = hm_init_empty(TEST_DIR, 3600);
    CHECK(init_rc == 0, "T00a: hm_init_empty returns 0");

    HmStore *store = hm_open(TEST_DIR);
    CHECK(store != NULL, "T00: hm_open returns non-NULL");
    if (!store) { printf("Cannot continue without store\n"); return 1; }

    /* T01 ── Bulk insert N distinct hyperedges via a single group-commit batch
     * (one fsync, so the test stresses compaction — not the WAL write path). */
    enum { FORM_LEN = 16 };
    uint8_t  *types = malloc((size_t)N);
    uint32_t *ev    = malloc((size_t)N * sizeof(uint32_t));
    uint8_t  *mcs   = malloc((size_t)N);
    uint32_t *offs  = malloc((size_t)N * sizeof(uint32_t));
    float    *ws    = calloc((size_t)N, sizeof(float));
    float    *mds   = calloc((size_t)N, sizeof(float));
    char     *forms = calloc((size_t)N, FORM_LEN);
    uint32_t *mflat = malloc((size_t)N * 2u * sizeof(uint32_t));
    int alloc_ok = types && ev && mcs && offs && ws && mds && forms && mflat;
    CHECK(alloc_ok, "T01a: batch buffers allocated");
    if (!alloc_ok) return 1;

    for (uint32_t i = 0; i < N; i++) {
        uint32_t ts, m[2];
        key_for(i, &ts, m);
        types[i] = 1;            /* insert */
        ev[i]    = ts;
        mcs[i]   = 2;
        offs[i]  = 2u * i;
        mflat[2u * i]      = m[0];
        mflat[2u * i + 1u] = m[1];
    }
    int rc = hm_commit_batch(store, N, types, ev, mcs, mflat, offs, ws, mds, forms);
    CHECK(rc == 0, "T01: bulk group-commit of N records succeeds");
    free(types); free(ev); free(mcs); free(offs);
    free(ws); free(mds); free(forms); free(mflat);
    CHECK(hm_wal_pending(store) == N, "T01b: wal_pending == N before compact");

    /* T02 ── Compact (this is the formerly-quadratic step). */
    rc = hm_compact(store);
    CHECK(rc == 0, "T02: hm_compact returns 0 at scale");
    CHECK(hm_wal_pending(store) == 0, "T02b: wal_pending == 0 after compact");

    /* T03 ── All records preserved. */
    CHECK(hm_total_records(store) == N, "T03: total_records == N after compact");

    /* T04 ── A representative record is queryable. */
    {
        uint32_t ts, m[2];
        key_for(12345u, &ts, m);
        CHECK(present(store, ts, m[0], m[1]), "T04: sample record present after compact");
    }

    /* ── Second WAL batch exercising every reconciliation branch ─────────── */

    /* K1: delete a record that now lives in the TPI. */
    uint32_t k1_ts, k1[2];  key_for(5u, &k1_ts, k1);
    rc = hm_delete_record(store, k1_ts, k1, 2);
    CHECK(rc == 0, "T05a: delete of compacted record accepted");

    /* K2: UPDATE pattern — DELETE then re-INSERT, with reversed member order
     * to prove identity is order-independent. */
    uint32_t k2_ts, k2[2];  key_for(7u, &k2_ts, k2);
    uint32_t k2_rev[2] = { k2[1], k2[0] };
    rc  = hm_delete_record(store, k2_ts, k2_rev, 2);
    rc |= hm_insert_record(store, k2_ts, k2_rev, 2, 0.9f, 2.0f, "UPD");
    CHECK(rc == 0, "T06a: delete+reinsert (reversed order) accepted");

    /* K3: a brand-new key INSERTed then DELETEd in the same WAL. */
    uint32_t k3_ts = N + 10u;
    uint32_t k3[2] = { 7000001u, 7000002u };
    rc  = hm_insert_record(store, k3_ts, k3, 2, 0.1f, 3.0f, "TMP");
    rc |= hm_delete_record(store, k3_ts, k3, 2);
    CHECK(rc == 0, "T07a: insert+delete in same WAL accepted");

    rc = hm_compact(store);
    CHECK(rc == 0, "T0x: second compact returns 0");

    /* T05 ── K1 gone. */
    CHECK(!present(store, k1_ts, k1[0], k1[1]),
          "T05: WAL DELETE removed the compacted record");

    /* T06 ── K2 survived (re-insert after delete). */
    CHECK(present(store, k2_ts, k2[0], k2[1]),
          "T06: delete-then-reinsert (UPDATE) survives, order-independent");

    /* T07 ── K3 never materialised. */
    CHECK(!present(store, k3_ts, k3[0], k3[1]),
          "T07: insert cancelled by later delete is absent");

    /* T08 ── Untouched record still present; net count is N-1. */
    {
        uint32_t ts, m[2];
        key_for(9u, &ts, m);
        CHECK(present(store, ts, m[0], m[1]), "T08a: untouched record still present");
    }
    CHECK(hm_total_records(store) == N - 1u,
          "T08b: net count is N-1 (only K1 removed)");

    hm_close(store);
    rm_rf(TEST_DIR);

    printf("\n=== Results: %d passed, %d failed ===\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
