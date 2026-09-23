/*
 * fmi.c — Forward Member Index reader implementation
 */

#include "fmi.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>

/* ── Open ────────────────────────────────────────────────────────────────── */

HmFmiReader *hm_fmi_reader_open(const char *dir_path) {
    char path[512];

    snprintf(path, sizeof(path), "%s/fmi_nodes.bin", dir_path);
    FILE *nf = fopen(path, "rb");
    if (!nf) return NULL;

    HmFmiHeader hdr;
    if (fread(&hdr, sizeof(hdr), 1, nf) != 1) { fclose(nf); return NULL; }

    HmFmiNodeEntry *nodes = malloc(hdr.node_count * sizeof(HmFmiNodeEntry));
    if (!nodes) { fclose(nf); return NULL; }

    if (fread(nodes, sizeof(HmFmiNodeEntry),
              hdr.node_count, nf) != hdr.node_count)
    {
        fclose(nf); free(nodes); return NULL;
    }
    fclose(nf);

    snprintf(path, sizeof(path), "%s/fmi_adjacency.bin", dir_path);
    int adj_fd = open(path, O_RDONLY);
    if (adj_fd < 0) { free(nodes); return NULL; }

    HmFmiReader *fmi = malloc(sizeof(HmFmiReader));
    if (!fmi) { close(adj_fd); free(nodes); return NULL; }
    fmi->header = hdr;
    fmi->nodes  = nodes;
    fmi->adj_fd = adj_fd;
    return fmi;
}

/* ── Lookup ──────────────────────────────────────────────────────────────── */

static int cmp_node_entry(const void *key, const void *entry) {
    uint32_t k = *(const uint32_t *)key;
    uint32_t e = ((const HmFmiNodeEntry *)entry)->node_id;
    return (k > e) - (k < e);
}

uint32_t *hm_fmi_reader_lookup(HmFmiReader *fmi,
                                uint32_t node_id,
                                uint32_t *out_count)
{
    *out_count = 0;

    HmFmiNodeEntry *entry = bsearch(&node_id,
                                     fmi->nodes,
                                     fmi->header.node_count,
                                     sizeof(HmFmiNodeEntry),
                                     cmp_node_entry);
    if (!entry || entry->count == 0) return NULL;

    uint32_t *ids = malloc(entry->count * sizeof(uint32_t));
    if (!ids) return NULL;

    ssize_t n = pread(fmi->adj_fd, ids,
                      entry->count * sizeof(uint32_t),
                      (off_t)entry->list_offset);
    if (n != (ssize_t)(entry->count * sizeof(uint32_t))) {
        free(ids); return NULL;
    }

    *out_count = entry->count;
    return ids;
}

/* ── Coalition ranking ───────────────────────────────────────────────────── */

typedef struct { uint32_t node_id; uint32_t count; } RankEntry;

static int cmp_rank_desc(const void *a, const void *b) {
    uint32_t ca = ((const RankEntry *)a)->count;
    uint32_t cb = ((const RankEntry *)b)->count;
    return (ca < cb) - (ca > cb);  /* descending */
}

HmCoalitionResult *hm_coalition_ranking(HmFmiReader *fmi) {
    uint32_t n = fmi->header.node_count;
    RankEntry *rank = malloc(n * sizeof(RankEntry));
    if (!rank) return NULL;

    for (uint32_t i = 0; i < n; i++) {
        rank[i].node_id = fmi->nodes[i].node_id;
        rank[i].count   = fmi->nodes[i].count;
    }
    qsort(rank, n, sizeof(RankEntry), cmp_rank_desc);

    HmCoalitionResult *cr = malloc(sizeof(HmCoalitionResult));
    uint32_t *node_ids    = malloc(n * sizeof(uint32_t));
    uint32_t *appearances = malloc(n * sizeof(uint32_t));

    if (!cr || !node_ids || !appearances) {
        free(rank); free(cr); free(node_ids); free(appearances);
        return NULL;
    }

    for (uint32_t i = 0; i < n; i++) {
        node_ids[i]    = rank[i].node_id;
        appearances[i] = rank[i].count;
    }
    free(rank);

    cr->count       = n;
    cr->node_ids    = node_ids;
    cr->appearances = appearances;
    return cr;
}

void hm_coalition_result_free(HmCoalitionResult *cr) {
    if (!cr) return;
    free(cr->node_ids);
    free(cr->appearances);
    free(cr);
}

/* ── Close ───────────────────────────────────────────────────────────────── */

void hm_fmi_reader_close(HmFmiReader *fmi) {
    if (!fmi) return;
    if (fmi->adj_fd >= 0) close(fmi->adj_fd);
    free(fmi->nodes);
    free(fmi);
}
