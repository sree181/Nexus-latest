/*
 * edge_demo.c — HyperMesh DB edge deployment demonstration
 *
 * Simulates a 5-drone swarm generating coalition hyperedges in real-time:
 *   - Each "tick" advances simulated time by 1 second.
 *   - Drone positions update (simple Lissajous trajectories).
 *   - Any cluster of drones within COALITION_DIST_M metres forms a coalition
 *     hyperedge and is inserted into HyperMesh DB.
 *   - Auto-compaction fires after WAL_AUTOCOMPACT entries.
 *   - TTL compaction at the end evicts records older than 10 ticks.
 *   - FMI point-lookup queries are issued for each drone showing which
 *     coalitions it participated in.
 *
 * Build: make demo   (from hypermesh_core/)
 */

#include "../src/hypermesh.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <errno.h>
#include <unistd.h>

/* ── Configuration ─────────────────────────────────────────────────────── */
#define NUM_DRONES          5
#define SIM_TICKS           30
#define COALITION_DIST_M    150.0f    /* metres — drones closer than this coalesce */
#define WAL_AUTOCOMPACT     10        /* compact after every 10 WAL entries         */
#define TTL_WINDOW_TICKS    10        /* keep only the last N ticks of records      */
#define AREA_M              500.0f    /* operational area side length               */

/* ── Helpers ────────────────────────────────────────────────────────────── */

static const char *DEMO_DIR = "/tmp/hm_edge_demo";

/* Create the index directory; tolerates EEXIST. */
static int ensure_dir(const char *path) {
    if (mkdir(path, 0755) < 0 && errno != EEXIST) {
        perror("mkdir");
        return -1;
    }
    return 0;
}

/* Simple 2-D Lissajous-like trajectory for drone i at tick t. */
static void drone_pos(int drone_id, int tick, float *x, float *y) {
    float phase = (float)drone_id * 1.2566f; /* 2π/5 * drone_id */
    float freq  = 0.15f + (float)drone_id * 0.04f;
    *x = AREA_M * 0.5f + AREA_M * 0.4f * cosf(freq * (float)tick + phase);
    *y = AREA_M * 0.5f + AREA_M * 0.4f * sinf(freq * (float)tick * 0.7f + phase);
}

static float dist_m(float x1, float y1, float x2, float y2) {
    float dx = x1 - x2, dy = y1 - y2;
    return sqrtf(dx*dx + dy*dy);
}

/* ── Bootstrap empty index ──────────────────────────────────────────────── */

static int bootstrap_empty_index(const char *dir) {
    /*
     * Create a header-only CSV and call hm_build_from_csv() — the only public
     * API that writes both TPI and FMI files simultaneously.
     */
    char csv_path[256];
    snprintf(csv_path, sizeof(csv_path), "%s/bootstrap.csv", dir);

    FILE *f = fopen(csv_path, "w");
    if (!f) { perror("fopen bootstrap.csv"); return -1; }
    fprintf(f, "event_ts,members,member_count,weight,mean_dist_m,formation\n");
    fclose(f);

    int rc = hm_build_from_csv(dir, csv_path, 1 /* 1-second buckets */);
    unlink(csv_path);  /* clean up temp file */
    if (rc < 0) {
        fprintf(stderr, "hm_build_from_csv failed: %s\n", hm_last_error());
        return -1;
    }
    return 0;
}

/* ── Print banner ───────────────────────────────────────────────────────── */
static void banner(const char *msg) {
    printf("\n══════════════════════════════════════════════\n");
    printf("  %s\n", msg);
    printf("══════════════════════════════════════════════\n");
}

/* ── Main ───────────────────────────────────────────────────────────────── */

int main(void) {
    banner("HyperMesh DB — Edge Coalition Demo");
    printf("Drones: %d   Ticks: %d   Coalition radius: %.0f m\n",
           NUM_DRONES, SIM_TICKS, (double)COALITION_DIST_M);
    printf("Auto-compact threshold: %d WAL entries\n", WAL_AUTOCOMPACT);
    printf("Index directory: %s\n\n", DEMO_DIR);

    /* 1. Prepare directory and bootstrap empty index files. */
    if (ensure_dir(DEMO_DIR) < 0) return 1;
    if (bootstrap_empty_index(DEMO_DIR) < 0) return 1;

    /* 2. Open the store. */
    HmStore *store = hm_open(DEMO_DIR);
    if (!store) {
        fprintf(stderr, "hm_open failed: %s\n", hm_last_error());
        return 1;
    }

    /* 3. Enable auto-compaction so the WAL never grows unbounded. */
    hm_set_autocompact(store, WAL_AUTOCOMPACT);

    /* 4. Simulate SIM_TICKS seconds of flight. */
    uint32_t base_ts = (uint32_t)time(NULL);
    int total_coalitions = 0;

    printf("%-6s  %-30s  %s\n", "Tick", "Coalition members", "Dist(m)");
    printf("------  ------------------------------  --------\n");

    for (int tick = 0; tick < SIM_TICKS; tick++) {
        uint32_t event_ts = base_ts + (uint32_t)tick;

        /* Compute positions for all drones this tick. */
        float px[NUM_DRONES], py[NUM_DRONES];
        for (int i = 0; i < NUM_DRONES; i++)
            drone_pos(i, tick, &px[i], &py[i]);

        /*
         * Find all subsets of size ≥ 2 where every pair is within
         * COALITION_DIST_M.  For 5 drones this is tractable (2^5 subsets).
         * We use a bitmask enumeration.
         */
        for (int mask = 3; mask < (1 << NUM_DRONES); mask++) {
            /* Need at least 2 bits set. */
            if (__builtin_popcount(mask) < 2) continue;

            /* Collect members */
            uint32_t members[NUM_DRONES];
            uint8_t  mc = 0;
            for (int d = 0; d < NUM_DRONES; d++)
                if (mask & (1 << d)) members[mc++] = (uint32_t)d;

            /* Check pairwise distance */
            float max_d = 0.0f;
            int all_close = 1;
            for (int a = 0; a < mc && all_close; a++)
                for (int b = a + 1; b < mc && all_close; b++) {
                    float d = dist_m(px[members[a]], py[members[a]],
                                     px[members[b]], py[members[b]]);
                    if (d > COALITION_DIST_M) all_close = 0;
                    if (d > max_d) max_d = d;
                }

            if (!all_close) continue;

            /* Skip subsets that are strict subsets of a larger coalition
             * (only insert maximal coalitions). */
            int is_maximal = 1;
            for (int extra = 0; extra < NUM_DRONES && is_maximal; extra++) {
                if (mask & (1 << extra)) continue;
                int supermask = mask | (1 << extra);
                /* Quick check: would this extra drone be close enough? */
                float ok = 1;
                for (int a = 0; a < mc && ok; a++) {
                    float d = dist_m(px[members[a]], py[members[a]],
                                     px[extra], py[extra]);
                    if (d > COALITION_DIST_M) ok = 0;
                }
                (void)supermask;
                if (ok) is_maximal = 0;
            }
            if (!is_maximal) continue;

            /* Compute mean pairwise distance as hyperedge weight. */
            float sum_d = 0.0f;
            int   pairs = 0;
            for (int a = 0; a < mc; a++)
                for (int b = a + 1; b < mc; b++) {
                    sum_d += dist_m(px[members[a]], py[members[a]],
                                    px[members[b]], py[members[b]]);
                    pairs++;
                }
            float mean_d = pairs > 0 ? sum_d / (float)pairs : 0.0f;
            float weight = 1.0f / (1.0f + mean_d / COALITION_DIST_M);

            /* Print */
            char label[64];
            int off = 0;
            off += snprintf(label + off, sizeof(label) - (size_t)off, "[");
            for (int i = 0; i < mc; i++)
                off += snprintf(label + off, sizeof(label) - (size_t)off,
                                "%s%u", i ? "," : "", members[i]);
            snprintf(label + off, sizeof(label) - (size_t)off, "]");
            printf("  %4d  %-30s  %6.1f m  (w=%.3f)\n",
                   tick, label, (double)mean_d, (double)weight);

            /* Insert into HyperMesh DB. */
            int rc = hm_insert_record(store, event_ts, members, mc,
                                      weight, mean_d, "auto");
            if (rc < 0)
                fprintf(stderr, "  [WARN] insert failed: %s\n", hm_last_error());
            else
                total_coalitions++;
        }
    }

    printf("\nTotal coalition hyperedges inserted: %d\n", total_coalitions);
    printf("WAL pending after simulation:        %u\n", hm_wal_pending(store));

    /* 5. Manual compaction (in addition to auto-compact mid-sim). */
    banner("Compaction");
    printf("Calling hm_compact()...\n");
    if (hm_compact(store) < 0) {
        fprintf(stderr, "compact failed: %s\n", hm_last_error());
    } else {
        printf("After compact: total_records=%u  wal_pending=%u\n",
               hm_total_records(store), hm_wal_pending(store));
    }

    /* 6. TTL compaction: evict records older than the last TTL_WINDOW_TICKS. */
    banner("TTL Compaction");
    uint32_t ttl_cutoff = base_ts + (uint32_t)(SIM_TICKS - TTL_WINDOW_TICKS);
    printf("Evicting records with event_ts < %u (keeping last %d ticks)...\n",
           ttl_cutoff, TTL_WINDOW_TICKS);
    if (hm_compact_with_ttl(store, ttl_cutoff) < 0) {
        fprintf(stderr, "compact_with_ttl failed: %s\n", hm_last_error());
    } else {
        printf("After TTL compact: total_records=%u  wal_pending=%u\n",
               hm_total_records(store), hm_wal_pending(store));
    }

    /* 7. FMI point-lookup: for each drone, list coalitions it participated in. */
    banner("FMI Coalition Lookup per Drone");
    for (uint32_t drone_id = 0; drone_id < NUM_DRONES; drone_id++) {
        HmRangeResult *result = hm_fmi_query(store, drone_id);
        if (!result) {
            fprintf(stderr, "  Drone %u: fmi_query failed: %s\n",
                    drone_id, hm_last_error());
            continue;
        }
        printf("\n  Drone %u — %u coalition(s) in index (strategy=%s, %.0f µs):\n",
               drone_id, result->count,
               result->plan.strategy, (double)result->plan.elapsed_us);
        for (uint32_t i = 0; i < result->count && i < 5; i++) {
            uint8_t  mc  = result->member_counts[i];
            uint32_t off = result->member_offsets[i];
            printf("    ts=%u  members=[", result->timestamps[i]);
            for (uint8_t m = 0; m < mc; m++)
                printf("%s%u", m ? "," : "", result->member_ids_flat[off + m]);
            printf("]  dist=%.1f m\n", (double)result->mean_dists[i]);
        }
        if (result->count > 5)
            printf("    ... (%u more)\n", result->count - 5);
        hm_range_result_free(result);
    }

    /* 8. Coalition ranking (FMI-based): which drone appeared in most coalitions? */
    banner("Coalition Ranking");
    HmCoalitionResult *ranking = hm_get_coalition_ranking(store);
    if (ranking) {
        printf("  Rank  Drone  Appearances\n");
        printf("  ----  -----  -----------\n");
        for (uint32_t i = 0; i < hm_coal_count(ranking); i++) {
            printf("  %4u  %5u  %u\n", i + 1,
                   hm_coal_node_id(ranking, i),
                   hm_coal_appearances(ranking, i));
        }
        hm_coalition_result_free(ranking);
    } else {
        printf("  (no coalition data)\n");
    }

    /* 9. Cleanup. */
    hm_close(store);
    banner("Demo Complete");
    printf("Index files remain in: %s\n", DEMO_DIR);
    printf("Run `hmdb info %s` to inspect the workbench.\n\n", DEMO_DIR);
    return 0;
}
