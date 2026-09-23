/*
 * psi.c — Property Secondary Index implementation for HyperMesh DB
 *
 * See psi.h for the file format specification.
 */

#include "psi.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <math.h>

/* ── Internal helpers ────────────────────────────────────────────────────── */

/*
 * Upper-case a string into dst (up to dstsz-1 chars + NUL).
 */
static void str_upper(const char *src, char *dst, size_t dstsz)
{
    size_t i;
    for (i = 0; i < dstsz - 1 && src[i]; i++)
        dst[i] = (char)toupper((unsigned char)src[i]);
    dst[i] = '\0';
}

/* ── hm_psi_filename ─────────────────────────────────────────────────────── */

char *hm_psi_filename(const char *dir,
                      const char *table,
                      const char *col,
                      char       *buf,
                      size_t      bufsz)
{
    char tbl_up[128] = {0};
    char col_up[128] = {0};
    str_upper(table, tbl_up, sizeof(tbl_up));
    str_upper(col,   col_up, sizeof(col_up));

    int n = snprintf(buf, bufsz, "%s/psi_%s_%s.bin", dir, tbl_up, col_up);
    if (n < 0 || (size_t)n >= bufsz)
        return NULL;
    return buf;
}

/* ── Sort comparator ─────────────────────────────────────────────────────── */

static int entry_cmp(const void *a, const void *b)
{
    const HmPsiEntry *ea = (const HmPsiEntry *)a;
    const HmPsiEntry *eb = (const HmPsiEntry *)b;

    if (ea->value < eb->value) return -1;
    if (ea->value > eb->value) return  1;
    /* Stable tie-break on event_ts */
    if (ea->event_ts < eb->event_ts) return -1;
    if (ea->event_ts > eb->event_ts) return  1;
    return 0;
}

/* ── hm_psi_write ────────────────────────────────────────────────────────── */

int hm_psi_write(const char       *dir,
                 const char       *table,
                 const char       *col,
                 uint8_t           col_type,
                 const HmPsiEntry *entries,
                 uint32_t          n)
{
    char path[512];
    if (!hm_psi_filename(dir, table, col, path, sizeof(path)))
        return -1;

    /* Sort a mutable copy */
    HmPsiEntry *sorted = NULL;
    if (n > 0) {
        sorted = (HmPsiEntry *)malloc((size_t)n * sizeof(HmPsiEntry));
        if (!sorted) return -1;
        memcpy(sorted, entries, (size_t)n * sizeof(HmPsiEntry));
        qsort(sorted, (size_t)n, sizeof(HmPsiEntry), entry_cmp);
    }

    FILE *fp = fopen(path, "wb");
    if (!fp) { free(sorted); return -1; }

    HmPsiHeader hdr;
    memset(&hdr, 0, sizeof(hdr));
    hdr.magic       = HM_PSI_MAGIC;
    hdr.version     = HM_PSI_VERSION;
    hdr.entry_count = n;
    hdr.col_type    = col_type;

    if (fwrite(&hdr, sizeof(hdr), 1, fp) != 1) {
        fclose(fp); free(sorted); return -1;
    }

    if (n > 0) {
        if (fwrite(sorted, sizeof(HmPsiEntry), (size_t)n, fp) != (size_t)n) {
            fclose(fp); free(sorted); return -1;
        }
    }

    fclose(fp);
    free(sorted);
    return 0;
}

/* ── Binary search helpers ───────────────────────────────────────────────── */

/*
 * lower_bound — smallest index i such that entries[i].value >= val.
 * Returns n if all values < val.
 */
static uint32_t lower_bound(const HmPsiEntry *e, uint32_t n, float val)
{
    uint32_t lo = 0, hi = n;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (e[mid].value < val) lo = mid + 1;
        else                     hi = mid;
    }
    return lo;
}

/*
 * upper_bound — smallest index i such that entries[i].value > val.
 * Returns n if all values <= val.
 */
static uint32_t upper_bound(const HmPsiEntry *e, uint32_t n, float val)
{
    uint32_t lo = 0, hi = n;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (e[mid].value <= val) lo = mid + 1;
        else                      hi = mid;
    }
    return lo;
}

/* ── hm_psi_lookup ───────────────────────────────────────────────────────── */

int hm_psi_lookup(const char *dir,
                  const char *table,
                  const char *col,
                  uint8_t     op,
                  float       threshold,
                  uint32_t   *out_ts,
                  uint32_t    max_out,
                  uint32_t   *out_count)
{
    char path[512];
    if (!hm_psi_filename(dir, table, col, path, sizeof(path)))
        return -1;

    FILE *fp = fopen(path, "rb");
    if (!fp) return -1;

    /* Read and validate header */
    HmPsiHeader hdr;
    if (fread(&hdr, sizeof(hdr), 1, fp) != 1 ||
        hdr.magic   != HM_PSI_MAGIC       ||
        hdr.version != HM_PSI_VERSION) {
        fclose(fp);
        return -1;
    }

    uint32_t n = hdr.entry_count;
    if (n == 0) {
        *out_count = 0;
        fclose(fp);
        return 0;
    }

    /* Load all entries (PSI files are small by design) */
    HmPsiEntry *entries = (HmPsiEntry *)malloc((size_t)n * sizeof(HmPsiEntry));
    if (!entries) { fclose(fp); return -1; }

    if (fread(entries, sizeof(HmPsiEntry), (size_t)n, fp) != (size_t)n) {
        free(entries); fclose(fp); return -1;
    }
    fclose(fp);

    /* Determine the matching range [lo, hi) using binary search */
    uint32_t lo, hi;
    switch (op) {
        case HM_PSI_EQ:
            lo = lower_bound(entries, n, threshold);
            hi = upper_bound(entries, n, threshold);
            break;
        case HM_PSI_GT:
            lo = upper_bound(entries, n, threshold);
            hi = n;
            break;
        case HM_PSI_GEQ:
            lo = lower_bound(entries, n, threshold);
            hi = n;
            break;
        case HM_PSI_LT:
            lo = 0;
            hi = lower_bound(entries, n, threshold);
            break;
        case HM_PSI_LEQ:
            lo = 0;
            hi = upper_bound(entries, n, threshold);
            break;
        default:
            free(entries);
            return -1;
    }

    uint32_t count = (hi > lo) ? (hi - lo) : 0;
    *out_count = count;

    int rc = 0;
    if (count > max_out) {
        /* Output buffer too small; fill what we can and signal truncation */
        for (uint32_t i = 0; i < max_out; i++)
            out_ts[i] = entries[lo + i].event_ts;
        rc = -2;
    } else {
        for (uint32_t i = 0; i < count; i++)
            out_ts[i] = entries[lo + i].event_ts;
    }

    free(entries);
    return rc;
}

/* ── hm_psi_exists ───────────────────────────────────────────────────────── */

int hm_psi_exists(const char *dir, const char *table, const char *col)
{
    char path[512];
    if (!hm_psi_filename(dir, table, col, path, sizeof(path)))
        return 0;
    FILE *fp = fopen(path, "rb");
    if (!fp) return 0;
    fclose(fp);
    return 1;
}

/* ── hm_psi_remove ───────────────────────────────────────────────────────── */

int hm_psi_remove(const char *dir, const char *table, const char *col)
{
    char path[512];
    if (!hm_psi_filename(dir, table, col, path, sizeof(path)))
        return -1;
    /* remove() returns 0 on success, non-zero otherwise. Treat ENOENT as OK. */
    int rc = remove(path);
    return (rc == 0) ? 0 : 0;   /* silently ignore absent file */
}
