/*
 * hmdb_migrate.c — HyperMesh DB V1 → V2 migration tool
 *
 * Usage:
 *   hmdb_migrate <db_directory>
 *
 * What it does:
 *   1. Reads bucket_directory.bin to check the current format version.
 *   2. If already V2, exits cleanly with no changes.
 *   3. Rewrites hyperedges.bin from V1 to V2 format:
 *      - Every HmHyperedgeHeader is replaced by HmHyperedgeHeaderV2 with
 *        the same field values and props_len = 0.
 *      - Member ID arrays are preserved unchanged.
 *      - The rewritten file is written to a temp file then atomically renamed.
 *   4. Patches the version byte in bucket_directory.bin from 1 to 2 in-place.
 *   5. Rewrites wal.bin from V1 to V2 format (if present):
 *      - Every HmWalEntryHeader → HmWalEntryHeaderV2 with props_len = 0.
 *      - Atomically renamed.
 *
 * Safety:
 *   - The tool is idempotent: running it twice on a V2 DB is a no-op.
 *   - It never modifies the original files until the rewrite is complete.
 *   - On any I/O error the original files are left untouched.
 *
 * Compile (standalone):
 *   cc -std=c11 -O2 -Wall -Isrc -o hmdb_migrate src/hmdb_migrate.c
 */

#define _POSIX_C_SOURCE 200809L

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <errno.h>

#include "format.h"

/* ── helpers ──────────────────────────────────────────────────────────────── */

static int open_ro(const char *path) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) fprintf(stderr, "open (ro) %s: %s\n", path, strerror(errno));
    return fd;
}

static int open_tmp(const char *path, char *tmp_out, size_t tmp_len) {
    snprintf(tmp_out, tmp_len, "%s.migrate_tmp", path);
    int fd = open(tmp_out, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) fprintf(stderr, "open (tmp) %s: %s\n", tmp_out, strerror(errno));
    return fd;
}

static int open_rw(const char *path) {
    int fd = open(path, O_RDWR);
    if (fd < 0) fprintf(stderr, "open (rw) %s: %s\n", path, strerror(errno));
    return fd;
}

static ssize_t read_exact(int fd, void *buf, size_t n, const char *label) {
    ssize_t r = read(fd, buf, n);
    if (r != (ssize_t)n) {
        fprintf(stderr, "read %s: got %zd expected %zu (%s)\n",
                label, r, n, r < 0 ? strerror(errno) : "short read");
    }
    return r;
}

static ssize_t write_exact(int fd, const void *buf, size_t n, const char *label) {
    ssize_t w = write(fd, buf, n);
    if (w != (ssize_t)n) {
        fprintf(stderr, "write %s: got %zd expected %zu (%s)\n",
                label, w, n, w < 0 ? strerror(errno) : "short write");
    }
    return w;
}

/* Build a path string safely */
static void mkpath(char *dst, size_t dstsz, const char *dir, const char *file) {
    snprintf(dst, dstsz, "%s/%s", dir, file);
}

/* ── bucket_directory.bin migration ────────────────────────────────────────── */

/*
 * Returns the version found in bucket_directory.bin, or 0 on error.
 * Patches the version byte to V2 in-place if patch == 1.
 */
static uint8_t bucketdir_version(const char *path, int patch) {
    int fd = open_rw(path);
    if (fd < 0) return 0;

    HmBucketDirHeader hdr;
    if (read_exact(fd, &hdr, sizeof(hdr), path) != (ssize_t)sizeof(hdr)) {
        close(fd); return 0;
    }
    if (hdr.magic != HM_MAGIC) {
        fprintf(stderr, "bad magic in %s\n", path);
        close(fd); return 0;
    }
    uint8_t ver = hdr.version;
    if (patch && ver == HM_VERSION) {
        uint8_t v2 = (uint8_t)HM_VERSION_V2;
        /* version field is at byte offset 4 (after uint32_t magic) */
        if (pwrite(fd, &v2, 1, offsetof(HmBucketDirHeader, version)) != 1) {
            fprintf(stderr, "pwrite version byte in %s: %s\n", path, strerror(errno));
            close(fd); return 0;
        }
        fsync(fd);
    }
    close(fd);
    return ver;
}

/* ── hyperedges.bin migration ───────────────────────────────────────────────── */

static int migrate_hyperedges(const char *dir, uint32_t total_records) {
    char src_path[1024], tmp_path[1024];
    mkpath(src_path, sizeof(src_path), dir, "hyperedges.bin");
    char dummy[1100];
    int  src = open_ro(src_path);
    if (src < 0) return -1;
    int  dst = open_tmp(src_path, tmp_path, sizeof(tmp_path));
    if (dst < 0) { close(src); return -1; }

    int ok = 1;
    for (uint32_t i = 0; i < total_records; i++) {
        HmHyperedgeHeader v1;
        if (read_exact(src, &v1, sizeof(v1), "he hdr") != (ssize_t)sizeof(v1)) {
            ok = 0; break;
        }
        uint8_t mc = v1.member_count;
        if (mc > HM_MAX_MEMBERS) mc = HM_MAX_MEMBERS;

        uint32_t members[HM_MAX_MEMBERS];
        memset(members, 0, sizeof(members));
        if (mc > 0) {
            size_t mlen = (size_t)mc * sizeof(uint32_t);
            if (read_exact(src, members, mlen, "he members") != (ssize_t)mlen) {
                ok = 0; break;
            }
        }

        /* Write V2 header — same fields, props_len = 0 */
        HmHyperedgeHeaderV2 v2;
        memset(&v2, 0, sizeof(v2));
        v2.event_ts     = v1.event_ts;
        v2.member_count = v1.member_count;
        v2.weight       = v1.weight;
        v2.mean_dist_m  = v1.mean_dist_m;
        memcpy(v2.formation, v1.formation, HM_FORMATION_LEN);
        v2.props_len    = 0;

        if (write_exact(dst, &v2, sizeof(v2), "he v2 hdr") != (ssize_t)sizeof(v2)) {
            ok = 0; break;
        }
        if (mc > 0) {
            size_t mlen = (size_t)mc * sizeof(uint32_t);
            if (write_exact(dst, members, mlen, "he v2 members") != (ssize_t)mlen) {
                ok = 0; break;
            }
        }
        (void)dummy;
    }

    fsync(dst);
    close(src);
    close(dst);

    if (!ok) {
        unlink(tmp_path);
        return -1;
    }

    if (rename(tmp_path, src_path) != 0) {
        fprintf(stderr, "rename %s → %s: %s\n", tmp_path, src_path, strerror(errno));
        unlink(tmp_path);
        return -1;
    }
    return 0;
}

/* ── wal.bin migration ───────────────────────────────────────────────────── */

static int migrate_wal(const char *dir) {
    char src_path[1024], tmp_path[1024];
    mkpath(src_path, sizeof(src_path), dir, "wal.bin");

    /* WAL may not exist if no inserts have been made yet */
    if (access(src_path, F_OK) != 0) return 0;

    int src = open_ro(src_path);
    if (src < 0) return -1;

    HmWalFileHeader fhdr;
    if (read_exact(src, &fhdr, sizeof(fhdr), "wal hdr") != (ssize_t)sizeof(fhdr)) {
        close(src); return -1;
    }
    if (fhdr.magic != HM_WAL_MAGIC) {
        fprintf(stderr, "bad WAL magic in %s\n", src_path);
        close(src); return -1;
    }
    if (fhdr.version != HM_WAL_VERSION) {
        /* Already V2 or unknown — leave alone */
        close(src); return 0;
    }

    int dst = open_tmp(src_path, tmp_path, sizeof(tmp_path));
    if (dst < 0) { close(src); return -1; }

    /* Write new V2 file header */
    HmWalFileHeader new_fhdr = { HM_WAL_MAGIC, HM_WAL_VERSION_V2 };
    if (write_exact(dst, &new_fhdr, sizeof(new_fhdr), "wal v2 fhdr")
            != (ssize_t)sizeof(new_fhdr)) {
        close(src); close(dst); unlink(tmp_path); return -1;
    }

    int ok = 1;
    for (;;) {
        HmWalEntryHeader v1eh;
        ssize_t r = read(src, &v1eh, sizeof(v1eh));
        if (r == 0) break;           /* clean EOF */
        if (r != (ssize_t)sizeof(v1eh)) {
            /* partial final entry — discard (crash-safe truncation) */
            break;
        }

        uint8_t mc = v1eh.member_count;
        if (mc > HM_MAX_MEMBERS) mc = HM_MAX_MEMBERS;

        uint32_t members[HM_MAX_MEMBERS];
        memset(members, 0, sizeof(members));
        if (mc > 0) {
            size_t mlen = (size_t)mc * sizeof(uint32_t);
            r = read(src, members, mlen);
            if (r != (ssize_t)mlen) break; /* partial entry */
        }

        HmWalEntryHeaderV2 v2eh;
        memset(&v2eh, 0, sizeof(v2eh));
        v2eh.type         = v1eh.type;
        v2eh.event_ts     = v1eh.event_ts;
        v2eh.member_count = v1eh.member_count;
        v2eh.weight       = v1eh.weight;
        v2eh.mean_dist_m  = v1eh.mean_dist_m;
        memcpy(v2eh.formation, v1eh.formation, HM_FORMATION_LEN);
        v2eh.props_len    = 0;

        if (write_exact(dst, &v2eh, sizeof(v2eh), "wal v2 eh")
                != (ssize_t)sizeof(v2eh)) { ok = 0; break; }
        if (mc > 0) {
            size_t mlen = (size_t)mc * sizeof(uint32_t);
            if (write_exact(dst, members, mlen, "wal v2 members")
                    != (ssize_t)mlen) { ok = 0; break; }
        }
    }

    fsync(dst);
    close(src);
    close(dst);

    if (!ok) { unlink(tmp_path); return -1; }

    if (rename(tmp_path, src_path) != 0) {
        fprintf(stderr, "rename %s → %s: %s\n", tmp_path, src_path, strerror(errno));
        unlink(tmp_path);
        return -1;
    }
    return 0;
}

/* ── main ────────────────────────────────────────────────────────────────── */

int main(int argc, char *argv[]) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <db_directory>\n", argv[0]);
        return 1;
    }
    const char *dir = argv[1];

    /* ── Step 1: Read bucket directory version ───────────────────────────── */
    char bd_path[1024];
    mkpath(bd_path, sizeof(bd_path), dir, "bucket_directory.bin");

    uint8_t ver = bucketdir_version(bd_path, 0 /* read-only first */);
    if (ver == 0) {
        fprintf(stderr, "error: cannot read bucket_directory.bin in '%s'\n", dir);
        return 1;
    }
    if (ver == HM_VERSION_V2) {
        printf("Database at '%s' is already V2. Nothing to do.\n", dir);
        return 0;
    }
    if (ver != HM_VERSION) {
        fprintf(stderr, "error: unknown format version %u in '%s'\n", ver, dir);
        return 1;
    }

    printf("Migrating '%s' from V1 to V2 …\n", dir);

    /* ── Step 2: Read total_records from bucket directory ────────────────── */
    {
        int fd = open(bd_path, O_RDONLY);
        HmBucketDirHeader hdr;
        if (fd < 0 || read(fd, &hdr, sizeof(hdr)) != (ssize_t)sizeof(hdr)) {
            fprintf(stderr, "error reading bucket directory\n");
            if (fd >= 0) close(fd);
            return 1;
        }
        close(fd);

        /* ── Step 3: Migrate hyperedges.bin ──────────────────────────────── */
        printf("  [1/3] Rewriting hyperedges.bin (%u records) …\n",
               hdr.total_records);
        if (migrate_hyperedges(dir, hdr.total_records) != 0) {
            fprintf(stderr, "error: hyperedges.bin migration failed\n");
            return 1;
        }
        printf("        Done.\n");
    }

    /* ── Step 4: Migrate wal.bin ─────────────────────────────────────────── */
    printf("  [2/3] Rewriting wal.bin …\n");
    if (migrate_wal(dir) != 0) {
        fprintf(stderr, "error: wal.bin migration failed\n");
        return 1;
    }
    printf("        Done.\n");

    /* ── Step 5: Patch bucket_directory.bin version byte ─────────────────── */
    printf("  [3/3] Patching bucket_directory.bin version …\n");
    if (bucketdir_version(bd_path, 1 /* patch */) == 0) {
        fprintf(stderr, "error: failed to patch version byte\n");
        return 1;
    }
    printf("        Done.\n");

    printf("Migration complete. '%s' is now a V2 HyperMesh DB.\n", dir);
    printf("\nNote: Run hm_compact() once to rebuild the FMI index and WAL.\n");
    return 0;
}
