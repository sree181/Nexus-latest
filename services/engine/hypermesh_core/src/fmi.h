/*
 * fmi.h — Forward Member Index reader
 *
 * The FMI is written by hm_tpi_writer_flush() as two files:
 *   fmi_nodes.bin      — HmFmiHeader + sorted HmFmiNodeEntry × node_count
 *   fmi_adjacency.bin  — packed uint32_t hyperedge seq-IDs
 *
 * The reader loads fmi_nodes.bin fully into memory and pread()s the
 * adjacency file on demand.
 */

#ifndef HM_FMI_H
#define HM_FMI_H

#include <stdint.h>
#include "format.h"

/* ── Coalition ranking result ────────────────────────────────────────────── */

typedef struct {
    uint32_t  count;
    uint32_t *node_ids;           /* sorted descending by appearances       */
    uint32_t *appearances;        /* coalition count for each node_id       */
} HmCoalitionResult;

/* ── FMI Reader ──────────────────────────────────────────────────────────── */

typedef struct {
    HmFmiHeader     header;
    HmFmiNodeEntry *nodes;    /* loaded fully into memory at open()         */
    int             adj_fd;   /* file descriptor for fmi_adjacency.bin      */
} HmFmiReader;

/* Open the FMI index in dir_path. Returns NULL on error. */
HmFmiReader *hm_fmi_reader_open(const char *dir_path);

/*
 * Return all hyperedge sequence IDs for node_id.
 * *out_count is set to the number of IDs returned.
 * Caller must free() the returned array.
 * Returns NULL if node_id not found (out_count = 0).
 */
uint32_t *hm_fmi_reader_lookup(HmFmiReader *fmi,
                                uint32_t node_id,
                                uint32_t *out_count);

/*
 * Return all nodes ranked descending by coalition appearance count.
 * Caller must free with hm_coalition_result_free().
 */
HmCoalitionResult *hm_coalition_ranking(HmFmiReader *fmi);

/* Free a coalition result. */
void hm_coalition_result_free(HmCoalitionResult *cr);

/* Close the reader. */
void hm_fmi_reader_close(HmFmiReader *fmi);

#endif /* HM_FMI_H */
