/*
 * test_wal_recovery.c — P1.1/P1.2 WAL crash-recovery / integrity tests.
 *
 * P1.1 — V3 per-record integrity:
 *   1. round-trip: append N inserts, reopen, count == N.
 *   2. torn tail: append partial garbage bytes after a clean WAL; reopen must
 *      recover exactly the valid records AND truncate the garbage so a fresh
 *      append is visible on the next reopen (the "append-past-garbage" bug).
 *   3. CRC: corrupting a byte in the body of the last record drops exactly that
 *      record on reopen (and truncates it away).
 *
 * P1.2 — V4 atomic batch frames (group commit):
 *   4. batch round-trip: one batch of N entries → N recovered from one frame.
 *   5. scan_all expands a batch back into its sub-entries with contents intact.
 *   6. atomicity: a torn batch frame is dropped IN FULL (all-or-nothing).
 *   7. a fresh batch after an atomic drop commits cleanly.
 *   8. mixed single + batch: corrupting the batch frame drops only the batch,
 *      leaving the preceding single entry recovered.
 *
 * Build/run via:  make wal_test
 */

#include "wal.h"
#include "format.h"
#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int g_fail = 0;
#define CHECK(cond, msg) do { \
    if (!(cond)) { printf("  FAIL: %s\n", msg); g_fail = 1; } \
    else         { printf("  ok:   %s\n", msg); } \
} while (0)

static char g_dir[256];

static void make_tmpdir(void) {
    snprintf(g_dir, sizeof(g_dir), "/tmp/hm_wal_rec_XXXXXX");
    char *p = mkdtemp(g_dir);
    assert(p && "mkdtemp failed");
}

static void wal_path(char *out, size_t n) {
    snprintf(out, n, "%s/wal.bin", g_dir);
}

static HmRecord mk_rec(uint32_t ts, uint32_t a, uint32_t b) {
    HmRecord r;
    memset(&r, 0, sizeof(r));
    r.event_ts = ts;
    r.member_count = 2;
    r.members[0] = a;
    r.members[1] = b;
    r.weight = 1.0f;
    r.mean_dist_m = 0.0f;
    r.props_len = 0;
    return r;
}

static HmWalEntry mk_entry(uint8_t type, uint32_t ts, uint32_t a, uint32_t b) {
    HmWalEntry e;
    memset(&e, 0, sizeof(e));
    e.type = type;
    e.event_ts = ts;
    e.member_count = 2;
    e.members[0] = a;
    e.members[1] = b;
    e.weight = 1.0f;
    e.props_len = 0;
    return e;
}

static uint32_t append_n(int n, uint32_t base_ts) {
    HmWal *w = hm_wal_open(g_dir);
    assert(w);
    for (int i = 0; i < n; i++) {
        HmRecord r = mk_rec(base_ts + (uint32_t)i, 10 + (uint32_t)i, 20 + (uint32_t)i);
        assert(hm_wal_append_insert(w, &r) == 0);
    }
    uint32_t c = w->entry_count;
    hm_wal_close(w);
    return c;
}

static uint32_t reopen_count(void) {
    HmWal *w = hm_wal_open(g_dir);
    assert(w);
    uint32_t c = w->entry_count;
    hm_wal_close(w);
    return c;
}

static off_t file_size(void) {
    char p[300]; wal_path(p, sizeof(p));
    int fd = open(p, O_RDONLY);
    assert(fd >= 0);
    off_t sz = lseek(fd, 0, SEEK_END);
    close(fd);
    return sz;
}

int main(void) {
    printf("WAL recovery tests\n");
    make_tmpdir();
    char p[300]; wal_path(p, sizeof(p));

    /* 1. Round-trip */
    append_n(3, 1000);
    CHECK(reopen_count() == 3, "round-trip: 3 inserts recovered");
    off_t clean_size = file_size();

    /* 2. Torn tail: append 9 bytes of garbage (a partial record) */
    {
        int fd = open(p, O_WRONLY | O_APPEND);
        assert(fd >= 0);
        uint8_t junk[9] = { HM_WAL_INSERT, 0xAA, 0xBB, 0xCC, 0xDD, 0x02, 0,0,0 };
        assert(write(fd, junk, sizeof(junk)) == (ssize_t)sizeof(junk));
        close(fd);
    }
    CHECK(file_size() == clean_size + 9, "torn tail: garbage written");
    uint32_t after_torn = reopen_count();
    CHECK(after_torn == 3, "torn tail: recovers exactly 3 valid records");
    CHECK(file_size() == clean_size, "torn tail: garbage truncated away");

    /* The real bug: a fresh append after recovery must be visible. */
    append_n(1, 2000);
    CHECK(reopen_count() == 4, "append after torn-tail recovery is visible");
    off_t four_size = file_size();

    /* 3. CRC: flip a byte in the last record's body → that record is dropped. */
    {
        int fd = open(p, O_RDWR);
        assert(fd >= 0);
        /* Corrupt the very last byte of the file (inside the 4th record). */
        uint8_t b;
        assert(pread(fd, &b, 1, four_size - 1) == 1);
        b ^= 0xFF;
        assert(pwrite(fd, &b, 1, four_size - 1) == 1);
        close(fd);
    }
    uint32_t after_crc = reopen_count();
    CHECK(after_crc == 3, "CRC: corrupted last record dropped on reopen");
    CHECK(file_size() == clean_size, "CRC: corrupted record truncated away");

    /* ── P1.2: atomic batch frames (group commit) ─────────────────────────── */
    printf("\nWAL atomic batch tests\n");
    make_tmpdir();
    wal_path(p, sizeof(p));

    /* 4. Batch round-trip: a single frame of 5 (incl. one DELETE) → 5 entries. */
    {
        HmWalEntry es[5];
        for (int i = 0; i < 5; i++)
            es[i] = mk_entry(HM_WAL_INSERT, 3000 + (uint32_t)i, 1 + (uint32_t)i, 100 + (uint32_t)i);
        es[2] = mk_entry(HM_WAL_DELETE, 3002, 3, 102);

        HmWal *w = hm_wal_open(g_dir);
        assert(w);
        CHECK(w->format_version == HM_WAL_VERSION_V4, "batch: new WAL is V4");
        assert(hm_wal_append_batch(w, es, 5) == 0);
        CHECK(w->entry_count == 5, "batch: append of 5 yields entry_count 5");
        hm_wal_close(w);
    }
    CHECK(reopen_count() == 5, "batch: 5 entries recovered after reopen");
    off_t batch_size = file_size();

    /* 5. scan_all expands the frame back into 5 sub-entries with contents. */
    {
        HmWal *w = hm_wal_open(g_dir);
        assert(w);
        uint32_t cnt = 0;
        HmWalEntry *all = hm_wal_scan_all(w, &cnt);
        CHECK(cnt == 5 && all != NULL, "batch: scan_all expands to 5 entries");
        int ok = (all != NULL);
        for (uint32_t i = 0; ok && i < cnt; i++) {
            if (all[i].event_ts != 3000 + i || all[i].members[0] != 1 + i)
                ok = 0;
        }
        CHECK(ok, "batch: expanded entries preserve event_ts/members");
        CHECK(all && all[2].type == HM_WAL_DELETE, "batch: DELETE sub-entry round-trips");
        free(all);
        hm_wal_close(w);
    }

    /* 6. Atomicity: lop bytes off the frame tail → the WHOLE batch is dropped. */
    {
        int fd = open(p, O_RDWR);
        assert(fd >= 0);
        assert(ftruncate(fd, batch_size - 5) == 0);
        close(fd);
    }
    CHECK(reopen_count() == 0, "batch atomicity: torn frame drops all 5 entries");
    CHECK(file_size() == (off_t)sizeof(HmWalFileHeader),
          "batch atomicity: file truncated back to header");

    /* 7. A fresh batch after an atomic drop commits cleanly. */
    {
        HmWalEntry es[3];
        for (int i = 0; i < 3; i++)
            es[i] = mk_entry(HM_WAL_INSERT, 4000 + (uint32_t)i, 7 + (uint32_t)i, 70 + (uint32_t)i);
        HmWal *w = hm_wal_open(g_dir);
        assert(w);
        assert(hm_wal_append_batch(w, es, 3) == 0);
        hm_wal_close(w);
    }
    CHECK(reopen_count() == 3, "batch: fresh batch after atomic drop is durable");

    /* 8. Mixed single + batch: corrupting the batch frame drops only the batch. */
    make_tmpdir();
    wal_path(p, sizeof(p));
    {
        HmWal *w = hm_wal_open(g_dir);
        assert(w);
        HmRecord r = mk_rec(5000, 11, 12);
        assert(hm_wal_append_insert(w, &r) == 0);   /* one durable single */
        HmWalEntry es[4];
        for (int i = 0; i < 4; i++)
            es[i] = mk_entry(HM_WAL_INSERT, 5100 + (uint32_t)i, 21 + (uint32_t)i, 210 + (uint32_t)i);
        assert(hm_wal_append_batch(w, es, 4) == 0); /* then a batch of 4 */
        hm_wal_close(w);
    }
    CHECK(reopen_count() == 5, "mixed: single + batch(4) = 5 entries");
    off_t mixed_size = file_size();
    {
        int fd = open(p, O_RDWR);
        assert(fd >= 0);
        uint8_t b;
        assert(pread(fd, &b, 1, mixed_size - 1) == 1);
        b ^= 0xFF;
        assert(pwrite(fd, &b, 1, mixed_size - 1) == 1);
        close(fd);
    }
    CHECK(reopen_count() == 1, "mixed: corrupt batch dropped, single survives");

    /* cleanup */
    unlink(p);
    rmdir(g_dir);

    printf(g_fail ? "\nFAILED\n" : "\nALL PASSED\n");
    return g_fail;
}
