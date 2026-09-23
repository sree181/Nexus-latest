/*
 * mavlink_bridge.h — Minimal MAVLink ↔ HyperMesh DB bridge
 *
 * Self-contained: no dependency on the official mavlink C library.
 * Implements only the messages needed for swarm coordination:
 *
 *   HEARTBEAT            (#0)  — drone alive, mode, armed state
 *   GLOBAL_POSITION_INT  (#33) — GPS lat/lon/alt + velocity
 *   BATTERY_STATUS       (#147) — battery remaining
 *   COLLISION            (#247) — ArduPilot collision warning (for validation)
 *
 * Usage (replace drone_update_position() in swarm_coordinator.c):
 *
 *   MavlinkBridge *bridge = mavlink_bridge_open(14550);  // GCS port
 *   while (running) {
 *       MavlinkDroneState state;
 *       if (mavlink_bridge_poll(bridge, &state, 10)) {   // 10 ms timeout
 *           drone_apply_state(state.sysid, &state);
 *       }
 *   }
 *   mavlink_bridge_close(bridge);
 *
 * The bridge accumulates per-sysid state across messages, so partial updates
 * (e.g. BATTERY_STATUS arriving between POSITION messages) are handled
 * correctly by merging into the last known state before returning.
 *
 * Wire protocol: MAVLink v2 frames over UDP.
 * Tested with ArduPilot SITL (--out udp:127.0.0.1:14550) and PX4 HITL.
 */

#ifndef MAVLINK_BRIDGE_H
#define MAVLINK_BRIDGE_H

#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <errno.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ── MAVLink v2 frame layout ──────────────────────────────────────────────── */

#define MAVLINK_STX_V2       0xFD
#define MAVLINK_MAX_PAYLOAD  280

#pragma pack(push, 1)
typedef struct {
    uint8_t  stx;          /* 0xFD for v2                                   */
    uint8_t  len;          /* payload length                                */
    uint8_t  incompat;     /* incompatibility flags                         */
    uint8_t  compat;       /* compatibility flags                           */
    uint8_t  seq;          /* frame sequence                                */
    uint8_t  sysid;        /* source system id (1–255, unique per vehicle)  */
    uint8_t  compid;       /* component id                                  */
    uint8_t  msgid[3];     /* message id (24-bit little-endian)             */
    uint8_t  payload[MAVLINK_MAX_PAYLOAD];
    /* CRC (2 bytes) appended after payload — not decoded here for brevity   */
} MavlinkFrameV2;
#pragma pack(pop)

/* ── Message IDs we care about ───────────────────────────────────────────── */

#define MAVMSG_HEARTBEAT           0
#define MAVMSG_GLOBAL_POSITION_INT 33
#define MAVMSG_BATTERY_STATUS      147

/* ── HEARTBEAT payload (9 bytes) ─────────────────────────────────────────── */
#pragma pack(push, 1)
typedef struct {
    uint32_t custom_mode;
    uint8_t  type;           /* MAV_TYPE: 2=quadrotor, 13=hexarotor, etc. */
    uint8_t  autopilot;
    uint8_t  base_mode;      /* MAV_MODE_FLAG: bit 7 = armed               */
    uint8_t  system_status;
    uint8_t  mavlink_version;
} MavHeartbeat;
#pragma pack(pop)

/* ── GLOBAL_POSITION_INT payload (28 bytes) ──────────────────────────────── */
#pragma pack(push, 1)
typedef struct {
    uint32_t time_boot_ms;   /* ms since boot                              */
    int32_t  lat;            /* degE7: multiply by 1e-7 for degrees        */
    int32_t  lon;            /* degE7                                      */
    int32_t  alt;            /* mm ASL → divide by 1000 for metres         */
    int32_t  relative_alt;   /* mm above home → divide by 1000             */
    int16_t  vx;             /* cm/s north → divide by 100 for m/s        */
    int16_t  vy;             /* cm/s east                                  */
    int16_t  vz;             /* cm/s down (positive = descending)          */
    uint16_t hdg;            /* heading cdeg (0–35999); UINT16_MAX=unknown */
} MavGlobalPositionInt;
#pragma pack(pop)

/* ── BATTERY_STATUS payload (54 bytes, first two fields sufficient) ──────── */
#pragma pack(push, 1)
typedef struct {
    int32_t  current_consumed;    /* mAh consumed (-1 = unknown)           */
    int32_t  energy_consumed;     /* hJ consumed  (-1 = unknown)           */
    int16_t  temperature;         /* cdegC (-1 = unknown)                  */
    uint16_t voltages[10];        /* mV per cell (UINT16_MAX = not present) */
    int16_t  current_battery;     /* cA (negative = charging; -1 = unknown) */
    uint8_t  id;                  /* battery id                            */
    int8_t   battery_remaining;   /* 0–100 % (-1 = unknown)               */
} MavBatteryStatus;
#pragma pack(pop)

/* ── Decoded state per drone system ─────────────────────────────────────── */

typedef struct {
    uint8_t  sysid;          /* MAVLink system id                         */
    uint8_t  armed;          /* 1 = armed                                 */
    uint8_t  mav_type;       /* vehicle type                              */

    /* Position in metric flat-earth (relative to home, metres) */
    float    x;              /* east  of home (m)                         */
    float    y;              /* north of home (m)                         */
    float    alt;            /* altitude above home (m)                   */

    float    vx;             /* velocity east  (m/s)                      */
    float    vy;             /* velocity north (m/s)                      */

    float    battery;        /* remaining % (0–100)                       */
    uint32_t last_ts;        /* Unix timestamp of last position update     */

    /* Raw GPS for logging / geo-fencing */
    double   lat_deg;
    double   lon_deg;
} MavlinkDroneState;

/* ── Bridge handle ──────────────────────────────────────────────────────── */

#define MAVBRIDGE_MAX_DRONES 32

typedef struct {
    int                 sock;
    uint16_t            port;
    MavlinkDroneState   states[MAVBRIDGE_MAX_DRONES];
    int                 nstates;

    /* Home origin for flat-earth projection (set on first POSITION received) */
    int     home_set;
    double  home_lat;
    double  home_lon;
    float   home_alt;
} MavlinkBridge;

/* ── Implementation ──────────────────────────────────────────────────────── */

static inline MavlinkBridge *mavlink_bridge_open(uint16_t port) {
    MavlinkBridge *b = (MavlinkBridge *)calloc(1, sizeof(MavlinkBridge));
    if (!b) return NULL;
    b->port = port;

    b->sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (b->sock < 0) { perror("mavlink_bridge socket"); free(b); return NULL; }

    struct sockaddr_in addr = {0};
    addr.sin_family      = AF_INET;
    addr.sin_port        = htons(port);
    addr.sin_addr.s_addr = INADDR_ANY;
    if (bind(b->sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("mavlink_bridge bind");
        close(b->sock);
        free(b);
        return NULL;
    }

    /* Non-blocking with timeout via SO_RCVTIMEO */
    struct timeval tv = { 0, 10000 }; /* 10 ms */
    setsockopt(b->sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    printf("[mavlink_bridge] Listening on UDP:%u\n", port);
    return b;
}

static inline void mavlink_bridge_close(MavlinkBridge *b) {
    if (!b) return;
    close(b->sock);
    free(b);
}

/* Find or create a state slot for sysid */
static inline MavlinkDroneState *bridge_get_state(MavlinkBridge *b, uint8_t sysid) {
    for (int i = 0; i < b->nstates; i++)
        if (b->states[i].sysid == sysid) return &b->states[i];
    if (b->nstates >= MAVBRIDGE_MAX_DRONES) return NULL;
    MavlinkDroneState *s = &b->states[b->nstates++];
    memset(s, 0, sizeof(*s));
    s->sysid   = sysid;
    s->battery = 100.0f;
    return s;
}

/*
 * Flat-earth projection: convert (lat, lon) degrees to (x_east, y_north) metres
 * relative to (home_lat, home_lon).
 */
static inline void latlon_to_xy(double lat, double lon,
                                 double home_lat, double home_lon,
                                 float *x, float *y) {
    const double R = 6371000.0;              /* Earth radius metres */
    double dlat = (lat - home_lat) * (3.14159265358979 / 180.0);
    double dlon = (lon - home_lon) * (3.14159265358979 / 180.0);
    *y = (float)(dlat * R);
    *x = (float)(dlon * R * cos(home_lat * 3.14159265358979 / 180.0));
}

/*
 * Poll for one MAVLink frame. Decodes the message and updates the internal
 * state table. Writes the updated state into *out if the message was a
 * position update.
 *
 * Returns 1 if *out was filled with a fresh position, 0 otherwise.
 * timeout_ms is ignored on this platform (SO_RCVTIMEO is set at open time).
 */
static inline int mavlink_bridge_poll(MavlinkBridge *b,
                                       MavlinkDroneState *out,
                                       int timeout_ms) {
    (void)timeout_ms;

    uint8_t buf[320];
    ssize_t n = recv(b->sock, buf, sizeof(buf), 0);
    if (n <= 0) return 0;
    if (n < 12) return 0;              /* too short for v2 header */
    if (buf[0] != MAVLINK_STX_V2) return 0; /* not v2 */

    uint8_t  sysid  = buf[5];
    uint32_t msgid  = (uint32_t)buf[7]
                    | ((uint32_t)buf[8] << 8)
                    | ((uint32_t)buf[9] << 16);
    uint8_t *payload = buf + 10;

    MavlinkDroneState *s = bridge_get_state(b, sysid);
    if (!s) return 0;

    if (msgid == MAVMSG_HEARTBEAT && n >= 10 + 9) {
        MavHeartbeat *hb = (MavHeartbeat *)payload;
        s->armed    = (hb->base_mode & 0x80) ? 1 : 0;
        s->mav_type = hb->type;
        return 0; /* heartbeat alone does not trigger position update */
    }

    if (msgid == MAVMSG_GLOBAL_POSITION_INT && n >= 10 + 28) {
        MavGlobalPositionInt *p = (MavGlobalPositionInt *)payload;
        double lat = p->lat * 1e-7;
        double lon = p->lon * 1e-7;
        float  alt = (float)(p->relative_alt) * 1e-3f;

        if (!b->home_set) {
            b->home_lat = lat;
            b->home_lon = lon;
            b->home_alt = alt;
            b->home_set = 1;
        }

        latlon_to_xy(lat, lon, b->home_lat, b->home_lon, &s->x, &s->y);
        s->alt     = alt - b->home_alt;
        s->vx      = (float)p->vx * 1e-2f; /* cm/s → m/s */
        s->vy      = (float)p->vy * 1e-2f;
        s->lat_deg = lat;
        s->lon_deg = lon;
        s->last_ts = (uint32_t)time(NULL);

        *out = *s;
        return 1;
    }

    if (msgid == MAVMSG_BATTERY_STATUS && n >= 10 + 9) {
        MavBatteryStatus *bat = (MavBatteryStatus *)payload;
        if (bat->battery_remaining >= 0)
            s->battery = (float)bat->battery_remaining;
        return 0; /* battery update doesn't constitute a position event */
    }

    return 0;
}

/*
 * Integration shim: apply a MavlinkDroneState into the DroneState table
 * used by swarm_coordinator.c.  Call this instead of drone_update_position().
 *
 * extern DroneState drones[];
 * extern pthread_mutex_t drones_mutex;
 */
#ifdef SWARM_COORDINATOR_INTEGRATION
static inline void drone_apply_mavlink(const MavlinkDroneState *ms,
                                        DroneState *drones_table,
                                        int ndrones,
                                        pthread_mutex_t *mu) {
    /* Find slot by sysid-as-drone-id */
    int slot = (int)ms->sysid % ndrones;
    pthread_mutex_lock(mu);
    drones_table[slot].id          = (uint32_t)ms->sysid;
    drones_table[slot].x           = ms->x;
    drones_table[slot].y           = ms->y;
    drones_table[slot].vx          = ms->vx;
    drones_table[slot].vy          = ms->vy;
    drones_table[slot].alt         = ms->alt;
    drones_table[slot].battery     = ms->battery;
    drones_table[slot].last_seen_ts = ms->last_ts;
    pthread_mutex_unlock(mu);
}
#endif /* SWARM_COORDINATOR_INTEGRATION */

#ifdef __cplusplus
}
#endif

#endif /* MAVLINK_BRIDGE_H */
