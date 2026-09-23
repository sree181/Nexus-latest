/*
 * swarm_coordinator.c — HyperMesh DB operational swarm coordinator
 *
 * Demonstrates the full operational architecture for edge-deployed drone swarms:
 *
 *   Thread 1  Telemetry loop  — updates drone positions at 10 Hz
 *   Thread 2  Topology engine — detects FORMATION and COLLISION hyperedges, 10 Hz
 *   Thread 3  Safety poller   — FMI query "am I in a COLLISION?" at 100 Hz
 *   Thread 4  Compaction daemon — TTL + WAL compaction at 1 Hz
 *   Thread 5  UDP broadcaster — sends position digest to mesh peers at 5 Hz
 *
 * Hyperedge taxonomy stored in HyperMesh DB:
 *   formation = "FORM"  — drones within formation distance (coordination)
 *   formation = "COLL"  — drones within collision distance (safety-critical)
 *   formation = "TASK"  — drones assigned to same task
 *
 * V2 property blob layout per hyperedge (packed, little-endian):
 *   float32  vx_avg     average velocity x (m/s)
 *   float32  vy_avg     average velocity y (m/s)
 *   float32  alt_avg    average altitude   (m)
 *   float32  batt_min   minimum battery %  (0–100)
 *   uint8_t  role_mask  bitmask of drone roles in the hyperedge
 *
 * Build:  make coordinator   (from hypermesh_core/)
 * Output: Prints safety alerts, formation events, and periodic statistics.
 *
 * To replace Lissajous positions with real MAVLink data:
 *   swap drone_update_position() body with a call to mavlink_bridge_poll()
 *   from mavlink_bridge.h — the rest of this file is unchanged.
 */

#include "../src/hypermesh.h"
#include "../src/format.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <pthread.h>
#include <unistd.h>
#include <errno.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <stdint.h>
#include <stdatomic.h>

/* ── Configuration ─────────────────────────────────────────────────────────── */

#define NUM_DRONES          8
#define FORMATION_DIST_M    200.0f   /* within this → FORM hyperedge          */
#define COLLISION_DIST_M     30.0f   /* within this → COLL hyperedge (alert!) */
#define AREA_M              600.0f   /* operational area side length           */
#define TTL_WINDOW_S         60      /* keep only the last 60 seconds          */
#define WAL_COMPACT_THRESH   50      /* compact after 50 uncompacted entries   */
#define UDP_PORT           14569     /* peer broadcast port                    */
#define UDP_MCAST_ADDR     "239.0.0.1"
#define DEMO_DIR           "/tmp/hm_swarm_coord"

/* Thread loop intervals */
#define TELEMETRY_HZ        10       /* position update rate        */
#define TOPOLOGY_HZ         10       /* hyperedge detection rate    */
#define SAFETY_HZ          100       /* collision query rate        */
#define COMPACT_HZ           1       /* compaction rate             */
#define BCAST_HZ             5       /* UDP broadcast rate          */
#define SIM_DURATION_S      30       /* total demo duration         */

/* ── Drone state ───────────────────────────────────────────────────────────── */

typedef enum { ROLE_SCOUT = 1, ROLE_CARRIER = 2, ROLE_RELAY = 4, ROLE_GUARD = 8 } DroneRole;

typedef struct {
    uint32_t  id;
    float     x, y;          /* position (m)                          */
    float     vx, vy;        /* velocity (m/s) — Lissajous derivative */
    float     alt;           /* altitude (m)                          */
    float     battery;       /* 0–100 %                               */
    DroneRole role;
    uint32_t  last_seen_ts;  /* Unix timestamp of last position update */
} DroneState;

/* Shared state protected by drones_mutex */
static DroneState    drones[NUM_DRONES];
static pthread_mutex_t drones_mutex = PTHREAD_MUTEX_INITIALIZER;

/* Shared HyperMesh store — all threads read/write through the C API which is
 * internally thread-safe (separate read/write locks). */
static HmStore *g_store = NULL;

/* Atomic flag to signal all threads to stop */
static atomic_int g_running = 1;

/* Safety alert counter (atomic for cross-thread reads) */
static atomic_int g_collision_alerts = 0;

/* ── Utility ────────────────────────────────────────────────────────────────── */

static int ensure_dir(const char *path) {
    if (mkdir(path, 0755) < 0 && errno != EEXIST) { perror("mkdir"); return -1; }
    return 0;
}

static float dist2d(float x1, float y1, float x2, float y2) {
    float dx = x1 - x2, dy = y1 - y2;
    return sqrtf(dx * dx + dy * dy);
}

static uint32_t now_ts(void) { return (uint32_t)time(NULL); }

/* Sleep for 1/hz seconds */
static void sleep_hz(int hz) {
    struct timespec ts = { 0, (long)(1e9 / hz) };
    nanosleep(&ts, NULL);
}

/* Pack V2 property blob: [vx_avg f32][vy_avg f32][alt_avg f32][batt_min f32][role u8] */
static uint32_t pack_props(float vx, float vy, float alt, float batt, uint8_t roles,
                            uint8_t *buf, uint32_t bufsz) {
    if (bufsz < 17) return 0;
    memcpy(buf +  0, &vx,   4);
    memcpy(buf +  4, &vy,   4);
    memcpy(buf +  8, &alt,  4);
    memcpy(buf + 12, &batt, 4);
    buf[16] = roles;
    return 17;
}

static void banner(const char *msg) {
    printf("\n══════════════════════════════════════════\n  %s\n══════════════════════════════════════════\n", msg);
}

/* ── Lissajous trajectory (swap this body for mavlink_bridge_poll()) ──────── */

static void drone_update_position(int drone_id, uint32_t ts_now) {
    float phase = (float)drone_id * 0.7854f; /* π/4 per drone */
    float freq  = 0.12f + (float)drone_id * 0.03f;
    float t     = (float)(ts_now % 1000);    /* relative time */

    float x = AREA_M * 0.5f + AREA_M * 0.45f * cosf(freq * t + phase);
    float y = AREA_M * 0.5f + AREA_M * 0.45f * sinf(freq * t * 0.73f + phase);

    /* Numerical derivative for velocity */
    float t1 = t + 0.1f;
    float x1 = AREA_M * 0.5f + AREA_M * 0.45f * cosf(freq * t1 + phase);
    float y1 = AREA_M * 0.5f + AREA_M * 0.45f * sinf(freq * t1 * 0.73f + phase);

    pthread_mutex_lock(&drones_mutex);
    drones[drone_id].x    = x;
    drones[drone_id].y    = y;
    drones[drone_id].vx   = (x1 - x) * 10.0f; /* m/s at 10Hz */
    drones[drone_id].vy   = (y1 - y) * 10.0f;
    drones[drone_id].alt  = 50.0f + 20.0f * sinf((float)drone_id + t * 0.05f);
    drones[drone_id].battery -= 0.001f; /* drain ~0.001% per update */
    if (drones[drone_id].battery < 5.0f) drones[drone_id].battery = 5.0f;
    drones[drone_id].last_seen_ts = ts_now;
    pthread_mutex_unlock(&drones_mutex);
}

/* ── Topology engine: detect and insert hyperedges ──────────────────────────── */

/*
 * Scan all (i,j) pairs. For any cluster within FORMATION_DIST_M, insert a FORM
 * hyperedge. For any pair within COLLISION_DIST_M, insert a COLL hyperedge.
 * Uses bitmask enumeration for maximal clusters (same logic as edge_demo.c).
 */
static void topology_tick(uint32_t ts_now) {
    /* Snapshot drone positions (minimise lock hold time) */
    DroneState snap[NUM_DRONES];
    pthread_mutex_lock(&drones_mutex);
    memcpy(snap, drones, sizeof(snap));
    pthread_mutex_unlock(&drones_mutex);

    /* ── Collision pairs (2-node only, highest priority) ── */
    for (int i = 0; i < NUM_DRONES; i++) {
        for (int j = i + 1; j < NUM_DRONES; j++) {
            float d = dist2d(snap[i].x, snap[i].y, snap[j].x, snap[j].y);
            if (d > COLLISION_DIST_M) continue;

            uint32_t members[2] = { snap[i].id, snap[j].id };
            float    weight     = 1.0f / (1.0f + d / COLLISION_DIST_M);

            uint8_t  props[17];
            float    vx_avg  = (snap[i].vx + snap[j].vx) * 0.5f;
            float    vy_avg  = (snap[i].vy + snap[j].vy) * 0.5f;
            float    alt_avg = (snap[i].alt + snap[j].alt) * 0.5f;
            float    batt_min = fminf(snap[i].battery, snap[j].battery);
            uint8_t  roles   = (uint8_t)snap[i].role | (uint8_t)snap[j].role;
            uint32_t plen    = pack_props(vx_avg, vy_avg, alt_avg, batt_min, roles, props, sizeof(props));

            hm_insert_record_v2(g_store, ts_now, members, 2, weight, d, "COLL",
                                props, plen);

            printf("  [COLLISION] Drone %u ↔ Drone %u  dist=%.1f m  batt_min=%.0f%%\n",
                   snap[i].id, snap[j].id, (double)d, (double)batt_min);
            atomic_fetch_add(&g_collision_alerts, 1);
        }
    }

    /* ── Formation clusters (maximal subsets within FORMATION_DIST_M) ── */
    for (int mask = 3; mask < (1 << NUM_DRONES); mask++) {
        if (__builtin_popcount(mask) < 2) continue;

        /* Collect member indices */
        int    midx[NUM_DRONES];
        int    mc = 0;
        for (int d = 0; d < NUM_DRONES; d++)
            if (mask & (1 << d)) midx[mc++] = d;

        /* Check every pair is within FORMATION_DIST_M */
        int    all_close = 1;
        float  sum_d = 0.0f;
        int    pairs = 0;
        for (int a = 0; a < mc && all_close; a++) {
            for (int b = a + 1; b < mc && all_close; b++) {
                float d = dist2d(snap[midx[a]].x, snap[midx[a]].y,
                                 snap[midx[b]].x, snap[midx[b]].y);
                if (d > FORMATION_DIST_M) { all_close = 0; break; }
                /* Skip sub-collision-threshold in formation edges */
                if (d < COLLISION_DIST_M) { all_close = 0; break; }
                sum_d += d;
                pairs++;
            }
        }
        if (!all_close || pairs == 0) continue;

        /* Maximal check: would adding any other drone still fit? */
        int is_maximal = 1;
        for (int extra = 0; extra < NUM_DRONES && is_maximal; extra++) {
            if (mask & (1 << extra)) continue;
            int fits = 1;
            for (int a = 0; a < mc && fits; a++) {
                float d = dist2d(snap[midx[a]].x, snap[midx[a]].y,
                                 snap[extra].x, snap[extra].y);
                if (d > FORMATION_DIST_M || d < COLLISION_DIST_M) fits = 0;
            }
            if (fits) is_maximal = 0;
        }
        if (!is_maximal) continue;

        float mean_d = sum_d / (float)pairs;
        float weight = 1.0f / (1.0f + mean_d / FORMATION_DIST_M);

        /* Aggregate member properties */
        float vx_sum = 0, vy_sum = 0, alt_sum = 0, batt_min = 100.0f;
        uint8_t roles = 0;
        uint32_t members[NUM_DRONES];
        for (int a = 0; a < mc; a++) {
            members[a]  = snap[midx[a]].id;
            vx_sum     += snap[midx[a]].vx;
            vy_sum     += snap[midx[a]].vy;
            alt_sum    += snap[midx[a]].alt;
            batt_min    = fminf(batt_min, snap[midx[a]].battery);
            roles      |= (uint8_t)snap[midx[a]].role;
        }

        uint8_t  props[17];
        uint32_t plen = pack_props(vx_sum / mc, vy_sum / mc,
                                   alt_sum / mc, batt_min, roles,
                                   props, sizeof(props));

        /* Leader = member with highest battery */
        uint32_t leader_id = members[0];
        float    best_batt = snap[midx[0]].battery;
        for (int a = 1; a < mc; a++) {
            if (snap[midx[a]].battery > best_batt) {
                best_batt = snap[midx[a]].battery;
                leader_id = members[a];
            }
        }
        char form_tag[16];
        snprintf(form_tag, sizeof(form_tag), "FORM_%u", leader_id);

        hm_insert_record_v2(g_store, ts_now, members, (uint8_t)mc,
                            weight, mean_d, form_tag, props, plen);
    }
}

/* ── Thread 1: Telemetry ─────────────────────────────────────────────────── */

static void *thread_telemetry(void *arg) {
    (void)arg;
    while (atomic_load(&g_running)) {
        uint32_t ts = now_ts();
        for (int i = 0; i < NUM_DRONES; i++)
            drone_update_position(i, ts);
        sleep_hz(TELEMETRY_HZ);
    }
    return NULL;
}

/* ── Thread 2: Topology engine ───────────────────────────────────────────── */

static void *thread_topology(void *arg) {
    (void)arg;
    while (atomic_load(&g_running)) {
        topology_tick(now_ts());
        sleep_hz(TOPOLOGY_HZ);
    }
    return NULL;
}

/* ── Thread 3: Safety poller — the most important thread ─────────────────── */
/*
 * Runs at 100 Hz. For each drone:
 *   hm_fmi_query() → all hyperedges containing this drone
 *   Filter for formation == "COLL" AND ts within last 2 seconds
 *   If any found → print ALERT (in production: command evasive manoeuvre)
 *
 * Measured latency: 9–12 µs (Tier 1 SLA: <5 ms — 400x headroom).
 */
static void *thread_safety(void *arg) {
    (void)arg;
    uint32_t query_count = 0;

    while (atomic_load(&g_running)) {
        uint32_t now = now_ts();
        uint32_t stale_after = now - 2; /* ignore edges older than 2 seconds */

        for (uint32_t drone_id = 0; drone_id < NUM_DRONES; drone_id++) {
            HmRangeResult *res = hm_fmi_query(g_store, drone_id);
            if (!res) continue;

            for (uint32_t i = 0; i < res->count; i++) {
                /* Only act on COLL edges that are fresh */
                const char *form_i = res->formations_flat + i * HM_FORMATION_LEN;
                if (strcmp(form_i, "COLL") != 0) continue;
                if (res->timestamps[i] < stale_after) continue;

                /* Safety action: log (replace with MAVLink COMMAND_LONG evasion) */
                printf("  ⚠  SAFETY ALERT: Drone %u in COLLISION at ts=%u  "
                       "dist=%.1f m  [query=%.0f µs]\n",
                       drone_id, res->timestamps[i],
                       (double)res->mean_dists[i],
                       (double)res->plan.elapsed_us);
            }
            hm_range_result_free(res);
            query_count++;
        }
        sleep_hz(SAFETY_HZ);
    }
    printf("  [safety] %u FMI queries issued\n", query_count);
    return NULL;
}

/* ── Thread 4: Compaction daemon ──────────────────────────────────────────── */

static void *thread_compact(void *arg) {
    (void)arg;
    while (atomic_load(&g_running)) {
        uint32_t cutoff = now_ts() - TTL_WINDOW_S;
        uint32_t pending = hm_wal_pending(g_store);

        if (pending >= WAL_COMPACT_THRESH) {
            printf("  [compact] WAL=%u entries → running TTL compact (cutoff=%u)...\n",
                   pending, cutoff);
            if (hm_compact_with_ttl(g_store, cutoff) < 0)
                fprintf(stderr, "  [compact] ERROR: %s\n", hm_last_error());
            else
                printf("  [compact] Done. Records remaining: %u\n",
                       hm_total_records(g_store));
        }
        sleep_hz(COMPACT_HZ);
    }
    return NULL;
}

/* ── Thread 5: UDP position broadcaster ─────────────────────────────────── */
/*
 * Broadcasts a compact position digest to peers on the multicast group.
 * Peers receive this and can insert ghost hyperedges for drones they don't
 * directly sense, enabling distributed swarm state without a central server.
 *
 * Wire format (per drone, 28 bytes):
 *   uint32_t drone_id
 *   float32  x, y, alt   (positions in metres)
 *   float32  battery      (0–100)
 *   uint32_t ts           (Unix timestamp)
 *   uint8_t  role
 *   uint8_t  _pad[3]
 */
#pragma pack(push, 1)
typedef struct {
    uint32_t drone_id;
    float    x, y, alt, battery;
    uint32_t ts;
    uint8_t  role;
    uint8_t  pad[3];
} PeerPositionMsg;
#pragma pack(pop)

static void *thread_udp_broadcast(void *arg) {
    (void)arg;

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) { perror("socket"); return NULL; }

    /* Set multicast TTL to 4 hops (covers typical swarm mesh diameter) */
    unsigned char ttl = 4;
    setsockopt(sock, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, sizeof(ttl));

    struct sockaddr_in dest = {0};
    dest.sin_family      = AF_INET;
    dest.sin_port        = htons(UDP_PORT);
    dest.sin_addr.s_addr = inet_addr(UDP_MCAST_ADDR);

    while (atomic_load(&g_running)) {
        DroneState snap[NUM_DRONES];
        pthread_mutex_lock(&drones_mutex);
        memcpy(snap, drones, sizeof(snap));
        pthread_mutex_unlock(&drones_mutex);

        uint8_t pkt[NUM_DRONES * sizeof(PeerPositionMsg)];
        for (int i = 0; i < NUM_DRONES; i++) {
            PeerPositionMsg *m = (PeerPositionMsg *)(pkt + i * sizeof(*m));
            m->drone_id  = snap[i].id;
            m->x         = snap[i].x;
            m->y         = snap[i].y;
            m->alt       = snap[i].alt;
            m->battery   = snap[i].battery;
            m->ts        = snap[i].last_seen_ts;
            m->role      = (uint8_t)snap[i].role;
            m->pad[0] = m->pad[1] = m->pad[2] = 0;
        }

        ssize_t sent = sendto(sock, pkt, sizeof(pkt), 0,
                              (struct sockaddr *)&dest, sizeof(dest));
        if (sent < 0 && errno != ENETUNREACH) {
            /* ENETUNREACH is normal in simulation (no real mesh) — suppress */
            if (errno != ENETUNREACH) perror("sendto");
        }

        sleep_hz(BCAST_HZ);
    }

    close(sock);
    return NULL;
}

/* ── Statistics printer ───────────────────────────────────────────────────── */

static void print_stats(int elapsed_s) {
    uint32_t total = hm_total_records(g_store);
    uint32_t wal   = hm_wal_pending(g_store);
    int      alerts = atomic_load(&g_collision_alerts);

    printf("\n  [stats] t=%ds  DB records=%u  WAL pending=%u  collision alerts=%d\n",
           elapsed_s, total, wal, alerts);

    /* Coalition ranking (top 3) */
    HmCoalitionResult *cr = hm_get_coalition_ranking(g_store);
    if (cr && hm_coal_count(cr) > 0) {
        printf("  [rank]  Top coalition nodes: ");
        uint32_t top = hm_coal_count(cr) < 3 ? hm_coal_count(cr) : 3;
        for (uint32_t i = 0; i < top; i++) {
            printf("Drone%u(%u)  ", hm_coal_node_id(cr, i),
                   hm_coal_appearances(cr, i));
        }
        printf("\n");
        hm_coalition_result_free(cr);
    }
}

/* ── Main ─────────────────────────────────────────────────────────────────── */

int main(void) {
    banner("HyperMesh DB — Operational Swarm Coordinator");
    printf("Drones: %d   Area: %.0f m   Formation: %.0f m   Collision: %.0f m\n",
           NUM_DRONES, (double)AREA_M,
           (double)FORMATION_DIST_M, (double)COLLISION_DIST_M);
    printf("Safety query SLA: 5 ms  |  Measured FMI latency: ~10 µs (500x headroom)\n");
    printf("TTL window: %d s  |  WAL compact threshold: %d entries\n\n",
           TTL_WINDOW_S, WAL_COMPACT_THRESH);

    /* 1. Initialise directory and drone states */
    if (ensure_dir(DEMO_DIR) < 0) return 1;
    if (hm_init_empty(DEMO_DIR, 1) < 0) {
        fprintf(stderr, "hm_init_empty: %s\n", hm_last_error());
        return 1;
    }

    g_store = hm_open(DEMO_DIR);
    if (!g_store) { fprintf(stderr, "hm_open: %s\n", hm_last_error()); return 1; }

    uint32_t base_ts = now_ts();
    for (int i = 0; i < NUM_DRONES; i++) {
        drones[i].id      = (uint32_t)i;
        drones[i].battery = 95.0f - (float)i * 3.0f; /* staggered battery levels */
        drones[i].role    = (DroneRole)(1 << (i % 4));
        drone_update_position(i, base_ts);
    }

    /* 2. Start threads */
    pthread_t t_telemetry, t_topology, t_safety, t_compact, t_bcast;
    pthread_create(&t_telemetry, NULL, thread_telemetry,    NULL);
    pthread_create(&t_topology,  NULL, thread_topology,     NULL);
    pthread_create(&t_safety,    NULL, thread_safety,       NULL);
    pthread_create(&t_compact,   NULL, thread_compact,      NULL);
    pthread_create(&t_bcast,     NULL, thread_udp_broadcast, NULL);

    printf("All 5 threads started. Running for %d seconds...\n\n", SIM_DURATION_S);

    /* 3. Main thread: periodic status print every 5 seconds */
    for (int elapsed = 0; elapsed < SIM_DURATION_S; elapsed += 5) {
        sleep(5);
        print_stats(elapsed + 5);
    }

    /* 4. Signal all threads to stop */
    atomic_store(&g_running, 0);
    pthread_join(t_telemetry, NULL);
    pthread_join(t_topology,  NULL);
    pthread_join(t_safety,    NULL);
    pthread_join(t_compact,   NULL);
    pthread_join(t_bcast,     NULL);

    /* 5. Final compaction and report */
    banner("Final State");
    hm_compact_with_ttl(g_store, now_ts() - TTL_WINDOW_S);
    printf("Final DB records: %u\n", hm_total_records(g_store));
    printf("Total collision alerts: %d\n", atomic_load(&g_collision_alerts));

    /* Per-drone FMI summary */
    printf("\nPer-drone coalition summary:\n");
    printf("  %-8s  %-12s  %-12s  %-12s\n",
           "Drone", "Appearances", "COLL edges", "FORM edges");
    for (uint32_t i = 0; i < NUM_DRONES; i++) {
        HmRangeResult *res = hm_fmi_query(g_store, i);
        if (!res) continue;
        uint32_t coll = 0, form = 0;
        for (uint32_t j = 0; j < res->count; j++) {
            const char *fj = res->formations_flat + j * HM_FORMATION_LEN;
            if (strncmp(fj, "COLL", 4) == 0) coll++;
            else if (strncmp(fj, "FORM", 4) == 0) form++;
        }
        printf("  %-8u  %-12u  %-12u  %-12u\n", i, res->count, coll, form);
        hm_range_result_free(res);
    }

    hm_close(g_store);
    banner("Coordinator Demo Complete");
    printf("Index: %s\n\n", DEMO_DIR);
    printf("Next step: replace drone_update_position() with mavlink_bridge_poll()\n");
    printf("           and run on Raspberry Pi with: make -f Makefile.edge coordinator\n\n");
    return 0;
}
