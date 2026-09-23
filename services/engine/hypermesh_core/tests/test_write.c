/*
 * test_write.c — Write-path smoke tests for HyperMesh DB
 *
 * Tests:
 *  T01  Build a small index from an in-memory CSV (uses existing path)
 *  T02  hm_open() opens the index and WAL in one call
 *  T03  hm_wal_pending() returns 0 on a fresh index
 *  T04  hm_insert_record() returns 0 and increments wal_pending
 *  T05  Inserted record is visible in hm_range_query() (same ts range)
 *  T06  Inserted record is NOT visible outside its ts range
 *  T07  hm_insert_record() with member_count > HM_MAX_MEMBERS returns -1
 *  T08  Two inserts in the same bucket both appear in range query
 *  T09  hm_delete_record() returns 0 and increments wal_pending
 *  T10  Deleted TPI record is hidden from range query after delete
 *  T11  WAL INSERT then WAL DELETE of same record: record absent
 *  T12  Delete with wrong member set does NOT hide original record
 *  T13  hm_compact() returns 0 and resets wal_pending to 0
 *  T14  After compact, inserted record survives in TPI range query
 *  T15  After compact, deleted record is permanently absent
 *  T16  hm_compact() on empty WAL is a no-op, returns 0
 *  T17  hm_close() then hm_open() replays WAL correctly
 *  T18  hm_wal_pending() returns 0 after compact
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/stat.h>
#include <unistd.h>

#include "../src/hypermesh.h"

/* ── Test harness ─────────────────────────────────────────────────────────── */

static int g_pass = 0, g_fail = 0;

#define CHECK(cond, name) do {                                \
    if (cond) {                                               \
        printf("  PASS  %s\n", name);                         \
        g_pass++;                                             \
    } else {                                                  \
        printf("  FAIL  %s  (line %d)\n", name, __LINE__);   \
        g_fail++;                                             \
    }                                                         \
} while (0)

/* ── Minimal CSV for a 3-record index ────────────────────────────────────── */
/* event_ts,members,member_count,weight,mean_dist_m,formation */
static const char *CSV_CONTENT =
    "event_ts,members,member_count,weight,mean_dist_m,formation\n"
    "100,\"[1, 2, 3]\",3,0.9,50.0,WEDGE\n"
    "200,\"[4, 5]\",2,0.7,30.0,LINE\n"
    "300,\"[6, 7, 8, 9]\",4,0.5,80.0,DIAMOND\n";

static const char *TEST_DIR = "/tmp/hm_test_write";

static void write_csv(const char *path, const char *content) {
    FILE *f = fopen(path, "w");
    if (!f) { perror("fopen csv"); exit(1); }
    fputs(content, f);
    fclose(f);
}

static void rm_rf(const char *path) {
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "rm -rf '%s'", path);
    (void)system(cmd);
}

/* ── main ─────────────────────────────────────────────────────────────────── */

int main(void)
{
    printf("=== HyperMesh Write-Path Tests ===\n\n");

    rm_rf(TEST_DIR);
    mkdir(TEST_DIR, 0755);

    /* T01 ── Build from CSV */
    char csv_path[256];
    snprintf(csv_path, sizeof(csv_path), "%s/edges.csv", TEST_DIR);
    write_csv(csv_path, CSV_CONTENT);

    int rc = hm_build_from_csv(TEST_DIR, csv_path, 10);
    CHECK(rc == 0, "T01: build_from_csv returns 0");

    /* T02 ── Open */
    HmStore *store = hm_open(TEST_DIR);
    CHECK(store != NULL, "T02: hm_open returns non-NULL");
    if (!store) { printf("Cannot continue without store\n"); return 1; }

    /* T03 ── Fresh WAL is empty */
    CHECK(hm_wal_pending(store) == 0, "T03: wal_pending == 0 on fresh open");

    /* T04 ── Insert increments pending */
    uint32_t m1[2] = {10, 11};
    rc = hm_insert_record(store, 150, m1, 2, 0.85f, 25.0f, "TEST");
    CHECK(rc == 0, "T04a: hm_insert_record returns 0");
    CHECK(hm_wal_pending(store) == 1, "T04b: wal_pending == 1 after insert");

    /* T05 ── Inserted record visible in range */
    HmRangeResult *res = hm_range_query(store, 100, 200);
    CHECK(res != NULL, "T05a: range_query non-NULL");
    if (res) {
        /* Original TPI: ts=100, ts=200 → 2 records; plus WAL insert ts=150 → 3 */
        CHECK(hm_res_count(res) == 3,
              "T05b: 3 records in [100,200] (2 TPI + 1 WAL)");
        /* Find the WAL record */
        int found_wal = 0;
        for (uint32_t i = 0; i < hm_res_count(res); i++) {
            if (hm_res_timestamp(res, i) == 150 &&
                hm_res_member_count(res, i) == 2)
                found_wal = 1;
        }
        CHECK(found_wal == 1, "T05c: WAL insert at ts=150 found in result");
        hm_range_result_free(res);
    }

    /* T06 ── WAL insert not visible outside range */
    res = hm_range_query(store, 200, 300);
    CHECK(res != NULL, "T06a: range_query [200,300] non-NULL");
    if (res) {
        int found_wal = 0;
        for (uint32_t i = 0; i < hm_res_count(res); i++) {
            if (hm_res_timestamp(res, i) == 150) found_wal = 1;
        }
        CHECK(found_wal == 0, "T06b: WAL ts=150 absent from [200,300]");
        hm_range_result_free(res);
    }

    /* T07 ── Insert with too many members */
    uint32_t big[65];
    memset(big, 0, sizeof(big));
    rc = hm_insert_record(store, 999, big, 65, 0.0f, 0.0f, "BAD");
    CHECK(rc == -1, "T07: insert with member_count > HM_MAX_MEMBERS returns -1");

    /* T08 ── Two inserts in same bucket */
    uint32_t m2[1] = {20};
    rc = hm_insert_record(store, 155, m2, 1, 0.6f, 10.0f, "SOLO");
    CHECK(rc == 0, "T08a: second insert at ts=155 returns 0");
    res = hm_range_query(store, 150, 160);
    CHECK(res != NULL && hm_res_count(res) == 2,
          "T08b: both WAL inserts visible in [150,160]");
    if (res) hm_range_result_free(res);

    /* T09 ── Delete increments pending */
    uint32_t del_m[3] = {1, 2, 3};
    rc = hm_delete_record(store, 100, del_m, 3);
    CHECK(rc == 0, "T09a: hm_delete_record returns 0");
    uint32_t pend = hm_wal_pending(store);
    CHECK(pend == 3, "T09b: wal_pending == 3 (2 inserts + 1 delete)");

    /* T10 ── Deleted TPI record hidden */
    res = hm_range_query(store, 100, 100);
    CHECK(res != NULL, "T10a: range_query [100,100] non-NULL after delete");
    if (res) {
        int found_orig = 0;
        for (uint32_t i = 0; i < hm_res_count(res); i++) {
            if (hm_res_timestamp(res, i) == 100 &&
                hm_res_member_count(res, i) == 3)
                found_orig = 1;
        }
        CHECK(found_orig == 0, "T10b: deleted ts=100 m=[1,2,3] hidden");
        hm_range_result_free(res);
    }

    /* T11 ── WAL insert then WAL delete of same record: absent */
    uint32_t m3[2] = {30, 31};
    hm_insert_record(store, 250, m3, 2, 0.4f, 5.0f, "EPHEMERAL");
    hm_delete_record(store, 250, m3, 2);
    res = hm_range_query(store, 250, 250);
    if (res) {
        int found = 0;
        for (uint32_t i = 0; i < hm_res_count(res); i++) {
            if (hm_res_timestamp(res, i) == 250 &&
                hm_res_member_count(res, i) == 2)
                found = 1;
        }
        CHECK(found == 0, "T11: WAL insert+delete → record absent");
        hm_range_result_free(res);
    }

    /* T12 ── Delete with wrong member set does not hide original */
    uint32_t wrong_m[3] = {1, 2, 99};
    hm_delete_record(store, 200, wrong_m, 3);
    res = hm_range_query(store, 200, 200);
    if (res) {
        int found_200 = 0;
        for (uint32_t i = 0; i < hm_res_count(res); i++) {
            if (hm_res_timestamp(res, i) == 200) found_200 = 1;
        }
        CHECK(found_200 == 1, "T12: wrong delete members → original record still visible");
        hm_range_result_free(res);
    }

    /* T13 ── Compact returns 0 */
    rc = hm_compact(store);
    CHECK(rc == 0, "T13: hm_compact returns 0");

    /* T18 ── WAL pending is 0 after compact */
    CHECK(hm_wal_pending(store) == 0, "T18: wal_pending == 0 after compact");

    /* T14 ── Inserted record survives in TPI after compact */
    res = hm_range_query(store, 150, 160);
    CHECK(res != NULL, "T14a: range_query [150,160] non-NULL after compact");
    if (res) {
        CHECK(hm_res_count(res) == 2,
              "T14b: both WAL inserts now in TPI after compact");
        hm_range_result_free(res);
    }

    /* T15 ── Deleted record permanently absent after compact */
    res = hm_range_query(store, 100, 100);
    if (res) {
        int found = 0;
        for (uint32_t i = 0; i < hm_res_count(res); i++) {
            if (hm_res_timestamp(res, i) == 100 &&
                hm_res_member_count(res, i) == 3)
                found = 1;
        }
        CHECK(found == 0, "T15: deleted ts=100 m=[1,2,3] absent after compact");
        hm_range_result_free(res);
    }

    /* T16 ── Compact on empty WAL is a no-op */
    rc = hm_compact(store);
    CHECK(rc == 0, "T16: compact on empty WAL returns 0");

    /* T17 ── Close then re-open, insert survives */
    hm_insert_record(store, 500, m1, 2, 0.3f, 15.0f, "PERSIST");
    hm_close(store);
    store = hm_open(TEST_DIR);
    CHECK(store != NULL, "T17a: re-open after close succeeds");
    if (store) {
        CHECK(hm_wal_pending(store) == 1,
              "T17b: WAL entry count restored on re-open");
        res = hm_range_query(store, 500, 500);
        int found = 0;
        if (res) {
            for (uint32_t i = 0; i < hm_res_count(res); i++) {
                if (hm_res_timestamp(res, i) == 500) found = 1;
            }
            hm_range_result_free(res);
        }
        CHECK(found == 1, "T17c: WAL insert at ts=500 visible after re-open");
        hm_close(store);
    }

    /* ── Summary ────────────────────────────────────────────────────────── */
    printf("\n=== Results: %d passed, %d failed ===\n", g_pass, g_fail);
    rm_rf(TEST_DIR);
    return g_fail > 0 ? 1 : 0;
}
