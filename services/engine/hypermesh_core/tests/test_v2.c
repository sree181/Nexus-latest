/*
 * test_v2.c — Phase 1 (V2 schema + property blob) tests for HyperMesh DB
 *
 * Tests:
 *  TV01  hm_build_from_csv() produces a V2 bucket directory
 *  TV02  hm_insert_record_v2() with a property blob returns 0
 *  TV03  hm_range_query() returns the property blob for a V2 insert
 *  TV04  Property blob content survives close + reopen
 *  TV05  hm_compact() preserves property blobs
 *  TV06  hm_insert_record_v2() with props_len > HM_MAX_PROPS_LEN returns -1
 *  TV07  hm_range_query() with mixed V2 (has props) and V1-compat (no props)
 *  TV08  hmdb_migrate converts a V1 DB to V2 correctly
 *  TV09  FMI lookup (hm_fmi_range()) returns correct members after V2 insert
 *  TV10  Full-scan (hm_full_scan_range) works on a V2 database
 */

#define _POSIX_C_SOURCE 200809L

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>

#include "../src/hypermesh.h"
#include "../src/format.h"

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

/* ── Helpers ──────────────────────────────────────────────────────────────── */

static const char *CSV_3 =
    "event_ts,members,member_count,weight,mean_dist_m,formation\n"
    "100,\"[1, 2, 3]\",3,0.9,50.0,WEDGE\n"
    "200,\"[4, 5]\",2,0.7,30.0,LINE\n"
    "300,\"[6, 7, 8, 9]\",4,0.5,80.0,DIAMOND\n";

static const char *TEST_DIR  = "/tmp/hm_test_v2";
static const char *TEST_V1   = "/tmp/hm_test_v1_mig";

static void rm_rf(const char *p) {
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "rm -rf '%s'", p);
    (void)system(cmd);
}

static void write_csv(const char *path, const char *content) {
    FILE *f = fopen(path, "w");
    if (!f) { perror("fopen"); exit(1); }
    fputs(content, f);
    fclose(f);
}

static uint8_t read_bucketdir_version(const char *dir) {
    char path[512];
    snprintf(path, sizeof(path), "%s/bucket_directory.bin", dir);
    int fd = open(path, O_RDONLY);
    if (fd < 0) return 0;
    HmBucketDirHeader hdr;
    ssize_t r = read(fd, &hdr, sizeof(hdr));
    close(fd);
    if (r != (ssize_t)sizeof(hdr)) return 0;
    return hdr.version;
}

/* Build a simple V2 DB with one property-bearing hyperedge */
static HmStore *build_v2_store(void) {
    rm_rf(TEST_DIR);
    mkdir(TEST_DIR, 0755);
    char csv[256];
    snprintf(csv, sizeof(csv), "%s/edges.csv", TEST_DIR);
    write_csv(csv, CSV_3);
    if (hm_build_from_csv(TEST_DIR, csv, 10) != 0) return NULL;
    return hm_open(TEST_DIR);
}

/* ── main ─────────────────────────────────────────────────────────────────── */

int main(void)
{
    printf("=== HyperMesh V2 Property Tests ===\n\n");

    /* TV01 — bucket directory should be written as V2 */
    printf("[TV01] New DB is V2 format\n");
    {
        rm_rf(TEST_DIR);
        mkdir(TEST_DIR, 0755);
        char csv[256];
        snprintf(csv, sizeof(csv), "%s/edges.csv", TEST_DIR);
        write_csv(csv, CSV_3);
        int rc = hm_build_from_csv(TEST_DIR, csv, 10);
        CHECK(rc == 0, "TV01a: build_from_csv returns 0");
        uint8_t ver = read_bucketdir_version(TEST_DIR);
        CHECK(ver == HM_VERSION_V2, "TV01b: bucket_directory.bin version == 2");
    }

    /* TV02 — insert_v2 with props */
    printf("\n[TV02] hm_insert_record_v2 with property blob\n");
    HmStore *store = hm_open(TEST_DIR);
    CHECK(store != NULL, "TV02a: hm_open returns non-NULL");

    const uint8_t PROPS[] = { 0x01, 0x02, 0x03, 0x04, 0xDE, 0xAD };
    uint32_t m_v2[2] = { 20, 21 };
    int rc = hm_insert_record_v2(store, 150, m_v2, 2,
                                  1.0f, 10.0f, "ECHELON",
                                  PROPS, (uint32_t)sizeof(PROPS));
    CHECK(rc == 0, "TV02b: insert_v2 returns 0");
    CHECK(hm_wal_pending(store) == 1, "TV02c: wal_pending == 1");

    /* TV03 — range query returns prop blob */
    printf("\n[TV03] range query returns property blob\n");
    {
        HmRangeResult *res = hm_range_query(store, 140, 160);
        CHECK(res != NULL, "TV03a: range_query non-NULL");
        if (res) {
            CHECK(hm_res_count(res) == 1, "TV03b: 1 record in [140,160]");
            if (hm_res_count(res) >= 1) {
                uint32_t plen = hm_res_props_len(res, 0);
                const uint8_t *pb = hm_res_props(res, 0);
                CHECK(plen == (uint32_t)sizeof(PROPS), "TV03c: props_len correct");
                CHECK(pb != NULL && memcmp(pb, PROPS, sizeof(PROPS)) == 0,
                      "TV03d: props bytes correct");
            }
            hm_range_result_free(res);
        }
    }

    /* TV04 — property survives close + reopen */
    printf("\n[TV04] property blob survives close + reopen\n");
    hm_close(store);
    store = hm_open(TEST_DIR);
    CHECK(store != NULL, "TV04a: reopen OK");
    if (store) {
        HmRangeResult *res = hm_range_query(store, 140, 160);
        CHECK(res != NULL, "TV04b: range_query OK after reopen");
        if (res) {
            CHECK(hm_res_count(res) == 1, "TV04c: 1 record after reopen");
            if (hm_res_count(res) >= 1) {
                uint32_t plen = hm_res_props_len(res, 0);
                const uint8_t *pb = hm_res_props(res, 0);
                CHECK(plen == (uint32_t)sizeof(PROPS), "TV04d: props_len correct");
                CHECK(pb != NULL && memcmp(pb, PROPS, sizeof(PROPS)) == 0,
                      "TV04e: props bytes correct after reopen");
            }
            hm_range_result_free(res);
        }
    }

    /* TV05 — compact preserves property blob */
    printf("\n[TV05] compact preserves property blob\n");
    if (store) {
        rc = hm_compact(store);
        CHECK(rc == 0, "TV05a: compact returns 0");
        CHECK(hm_wal_pending(store) == 0, "TV05b: wal_pending == 0 after compact");

        HmRangeResult *res = hm_range_query(store, 140, 160);
        CHECK(res != NULL, "TV05c: range_query OK after compact");
        if (res) {
            CHECK(hm_res_count(res) == 1, "TV05d: 1 record after compact");
            if (hm_res_count(res) >= 1) {
                uint32_t plen = hm_res_props_len(res, 0);
                const uint8_t *pb = hm_res_props(res, 0);
                CHECK(plen == (uint32_t)sizeof(PROPS), "TV05e: props_len correct after compact");
                CHECK(pb != NULL && memcmp(pb, PROPS, sizeof(PROPS)) == 0,
                      "TV05f: props bytes correct after compact");
            }
            hm_range_result_free(res);
        }
    }

    /* TV06 — props_len > HM_MAX_PROPS_LEN must fail */
    printf("\n[TV06] props_len > HM_MAX_PROPS_LEN rejected\n");
    if (store) {
        uint8_t big[HM_MAX_PROPS_LEN + 1];
        memset(big, 0xAB, sizeof(big));
        uint32_t mm[1] = { 99 };
        int bad = hm_insert_record_v2(store, 999, mm, 1,
                                       0.1f, 1.0f, "BAD",
                                       big, (uint32_t)sizeof(big));
        CHECK(bad != 0, "TV06: oversized props rejected");
    }

    /* TV07 — mixed inserts: one with props, one without */
    printf("\n[TV07] mixed V2 and plain inserts in same range\n");
    if (store) {
        uint32_t mp[1] = { 30 };
        rc = hm_insert_record(store, 155, mp, 1, 0.5f, 5.0f, "PLAIN");
        CHECK(rc == 0, "TV07a: plain insert OK");

        HmRangeResult *res = hm_range_query(store, 140, 160);
        CHECK(res != NULL, "TV07b: range_query OK");
        if (res) {
            CHECK(hm_res_count(res) == 2, "TV07c: 2 records (1 with props, 1 without)");
            if (hm_res_count(res) >= 1) {
                /* At least one entry must carry props */
                int found_props = 0;
                for (uint32_t i = 0; i < hm_res_count(res); i++) {
                    if (hm_res_props_len(res, i) > 0) found_props = 1;
                }
                CHECK(found_props, "TV07d: at least one record has props");
            }
            hm_range_result_free(res);
        }
    }
    if (store) hm_close(store);

    /* TV08 — migration tool converts V1 DB to V2 */
    printf("\n[TV08] hmdb_migrate converts V1 DB to V2\n");
    {
        /* Build a fresh DB — it will be V2 by default.
         * To simulate a V1 DB we patch the version byte to 1 in place,
         * then run the migration tool and confirm it becomes 2 again.
         * (Full V1 writer is removed; this exercises the tool's read logic.) */
        rm_rf(TEST_V1);
        mkdir(TEST_V1, 0755);
        char csv[256];
        snprintf(csv, sizeof(csv), "%s/edges.csv", TEST_V1);
        write_csv(csv, CSV_3);
        CHECK(hm_build_from_csv(TEST_V1, csv, 10) == 0, "TV08a: build OK");

        /* Downgrade to V1 in-place to mimic legacy data */
        char bdpath[512];
        snprintf(bdpath, sizeof(bdpath), "%s/bucket_directory.bin", TEST_V1);
        {
            int fd = open(bdpath, O_RDWR);
            HmBucketDirHeader hdr;
            read(fd, &hdr, sizeof(hdr));
            uint8_t v1 = (uint8_t)HM_VERSION;
            pwrite(fd, &v1, 1, offsetof(HmBucketDirHeader, version));
            close(fd);
        }
        /* Also downgrade hyperedges.bin: re-encode every record from V2 → V1.
         * Since we just built the DB it has exactly 3 records with props_len=0,
         * so we can treat each V2 header as if it were V1+4-byte suffix and
         * strip the props_len field. */
        {
            char hepath[512], htmp[512];
            snprintf(hepath, sizeof(hepath), "%s/hyperedges.bin", TEST_V1);
            snprintf(htmp,   sizeof(htmp),   "%s.tmp",            hepath);
            int src = open(hepath, O_RDONLY);
            int dst = open(htmp, O_WRONLY | O_CREAT | O_TRUNC, 0644);
            for (int i = 0; i < 3; i++) {
                HmHyperedgeHeaderV2 v2h;
                if (read(src, &v2h, sizeof(v2h)) != (ssize_t)sizeof(v2h)) break;
                uint32_t mc = v2h.member_count;
                if (mc > HM_MAX_MEMBERS) mc = HM_MAX_MEMBERS;
                HmHyperedgeHeader v1h;
                memset(&v1h, 0, sizeof(v1h));
                v1h.event_ts     = v2h.event_ts;
                v1h.member_count = v2h.member_count;
                v1h.weight       = v2h.weight;
                v1h.mean_dist_m  = v2h.mean_dist_m;
                memcpy(v1h.formation, v2h.formation, HM_FORMATION_LEN);
                write(dst, &v1h, sizeof(v1h));
                if (mc > 0) {
                    uint32_t ids[HM_MAX_MEMBERS];
                    read(src, ids, mc * sizeof(uint32_t));
                    write(dst, ids, mc * sizeof(uint32_t));
                }
                /* skip props blob from V2 (props_len == 0 here so no extra bytes) */
            }
            close(src); close(dst);
            rename(htmp, hepath);
        }
        CHECK(read_bucketdir_version(TEST_V1) == HM_VERSION,
              "TV08b: DB is V1 after downgrade");

        /* Run migration tool */
        char cmd[512];
        /* make v2_test runs this executable from tests/, where the migration
         * binary built by the same target is one directory up. */
        snprintf(cmd, sizeof(cmd),
                 "../hmdb_migrate %s >/dev/null 2>&1",
                 TEST_V1);
        int mrc = system(cmd);
        CHECK(mrc == 0, "TV08c: hmdb_migrate exits 0");
        CHECK(read_bucketdir_version(TEST_V1) == HM_VERSION_V2,
              "TV08d: version == 2 after migration");

        /* Open and query the migrated DB */
        HmStore *ms = hm_open(TEST_V1);
        CHECK(ms != NULL, "TV08e: migrated DB opens OK");
        if (ms) {
            HmRangeResult *res = hm_range_query(ms, 0, 9999);
            CHECK(res != NULL && hm_res_count(res) == 3,
                  "TV08f: migrated DB has 3 records");
            if (res) hm_range_result_free(res);
            hm_close(ms);
        }
        rm_rf(TEST_V1);
    }

    /* TV09 — FMI lookup after V2 insert */
    printf("\n[TV09] FMI lookup after V2 insert\n");
    {
        store = build_v2_store();
        CHECK(store != NULL, "TV09a: store OK");
        if (store) {
            uint32_t mv[3] = { 40, 41, 42 };
            hm_insert_record_v2(store, 250, mv, 3, 0.8f, 15.0f, "VOPEN",
                                 PROPS, (uint32_t)sizeof(PROPS));
            hm_compact(store); /* flush WAL so FMI is updated */

            HmRangeResult *res = hm_fmi_query(store, 41);
            CHECK(res != NULL, "TV09b: fmi_range non-NULL");
            if (res) {
                int found = 0;
                for (uint32_t i = 0; i < hm_res_count(res); i++) {
                    uint8_t mc = hm_res_member_count(res, i);
                    for (uint8_t j = 0; j < mc; j++) {
                        if (hm_res_member_id(res, i, j) == 41) found = 1;
                    }
                }
                CHECK(found, "TV09c: node 41 present in FMI result members");
                hm_range_result_free(res);
            }
            hm_close(store);
        }
    }

    /* TV10 — full-scan on V2 DB */
    printf("\n[TV10] full-scan on V2 database\n");
    {
        store = build_v2_store();
        CHECK(store != NULL, "TV10a: store OK");
        if (store) {
            /* Insert one V2 record */
            uint32_t mv[2] = { 50, 51 };
            hm_insert_record_v2(store, 150, mv, 2,
                                 0.6f, 12.0f, "COLM",
                                 PROPS, (uint32_t)sizeof(PROPS));
            hm_compact(store); /* push to TPI */

            HmRangeResult *res = hm_full_scan_range(store, 0, 9999);
            CHECK(res != NULL, "TV10b: full_scan_range non-NULL");
            if (res) {
                /* 3 records from CSV + 1 V2 insert = 4 */
                CHECK(hm_res_count(res) == 4, "TV10c: 4 records in full scan");
                hm_range_result_free(res);
            }
            hm_close(store);
        }
    }

    /* ── Results ────────────────────────────────────────────────────────────── */
    printf("\n=== Results: %d passed, %d failed ===\n", g_pass, g_fail);
    return g_fail > 0 ? 1 : 0;
}
