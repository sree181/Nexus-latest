# Nexus Temporal Graph API Contract

The graph API is part of `/api/v1`, uses the existing verified operator identity, and never accepts unauthenticated source writes. Every operation is bound to an operational event and mode. Graph ingestion requires the `graph:ingest` scope; graph queries require `graph:read`.

## Endpoint inventory

| Method | Endpoint | Scope | Purpose |
|---|---|---|---|
| `POST` | `/events/{eventId}/graph/sources/{sourceId}/batches` | `graph:ingest` | Idempotently upsert authoritative nodes and edges for one event and source |
| `GET` | `/events/{eventId}/graph?mode=live&view=mobility&asOf=…` | `graph:read` | Current or effective-time graph snapshot |
| `GET` | `/events/{eventId}/graph/nodes/{nodeId}/neighborhood?mode=live&depth=2` | `graph:read` | Bounded one-to-four-hop neighborhood |
| `GET` | `/graph/entities/{node|edge}/{entityId}/history?mode=live&limit=100` | `graph:read` | Append-only state and geometry history |
| `GET` | `/graph/decision-lineage/{recommendationId}?mode=live` | `graph:read` | Evidence-to-verification decision lineage |
| `GET` | `/events/{eventId}/graph/agency-coordination?mode=live` | `graph:read` | Current accountable agency commitments and blockers |

## Authoritative ingestion request

The caller sends `Idempotency-Key` and may send `X-Request-ID`. The source path identifies the registered authoritative source; it cannot be overridden in the body. A batch may contain at most 1,000 nodes and 2,000 edges.

```json
{
  "mode": "live",
  "schemaVersion": "mobility-graph/1.0.0",
  "nodes": [
    {
      "nodeType": "parking_lot",
      "externalKey": "au-parking:west-campus",
      "label": "West Campus Parking",
      "ownerAgencyId": "00000000-0000-4000-8000-000000000001",
      "authorityUri": "https://approved-partner.example/lots/west-campus",
      "dataClassification": "live",
      "geometryGeojson": { "type": "Point", "coordinates": [-85.502, 32.598] },
      "state": { "capacity": 1200, "occupied": 1140, "occupancyPercent": 95 },
      "qualityFlags": [],
      "validFrom": "2026-08-26T17:45:00Z",
      "validUntil": "2026-08-26T17:47:00Z",
      "evidenceIds": ["00000000-0000-4000-8000-000000000101"]
    }
  ],
  "edges": [
    {
      "edgeType": "feeds_traffic_into",
      "externalKey": "west-campus:wire-road",
      "from": { "nodeType": "parking_lot", "externalKey": "au-parking:west-campus" },
      "to": { "nodeType": "road_segment", "externalKey": "city-road:wire-road-01" },
      "directed": true,
      "dataClassification": "operational",
      "state": { "permitted": true, "estimatedVehiclesPerMinute": 18 },
      "validFrom": "2026-08-26T17:45:00Z"
    }
  ]
}
```

The response is `202 Accepted` because persistence succeeded but downstream graph consumers may update asynchronously.

```json
{
  "data": {
    "batchId": "00000000-0000-4000-8000-000000000201",
    "status": "succeeded",
    "nodeCount": 1,
    "edgeCount": 1,
    "unchangedCount": 0,
    "rejectedCount": 0,
    "requestId": "graph-request-2026-08-26-001"
  },
  "requestId": "graph-request-2026-08-26-001"
}
```

## Update semantics

Nodes are uniquely addressed by `(event, mode, nodeType, externalKey)`. Edges are uniquely addressed by `(event, mode, edgeType, externalKey)`. The server computes a canonical state hash. If an authoritative payload has not changed, Nexus records it as unchanged and does not create a false version. When state, geometry, validity, quality, or activity changes, PostgreSQL increments the version and appends a `graph_state_changes` record.

An edge is rejected when either endpoint does not exist in the same batch or current event graph. The batch becomes `partial`, and the rejected count identifies incomplete relationships. Graph history is append-only and cannot be updated or deleted through the API.

## Error contract

| Code | HTTP | Meaning |
|---|---:|---|
| `AUTHENTICATION_REQUIRED` | 401 | No verified operator identity |
| `FORBIDDEN` | 403 | Missing graph scope |
| `MODE_AUTHORITY_REQUIRED` | 403 | Operator cannot access the requested mode |
| `SOURCE_AUTHORITY_REQUIRED` | 403 | Caller agency does not own the source and lacks cross-agency ingestion authority |
| `VALIDATION_FAILED` | 422 | Invalid node, edge, geometry envelope, validity interval, or batch size |
| `GRAPH_MODE_MISMATCH` | 409 | Batch, source event, operator, and operational event modes differ |
| `IDEMPOTENCY_KEY_REUSED` | 409 | Existing key was reused with a different payload |
| `GRAPH_STORAGE_NOT_CONFIGURED` | 503 | PostgreSQL graph repository is unavailable |

## SQL catalog

`server/graph/queries.sql` contains executable parameterized queries for current mobility graphs, bounded downstream impact paths, source/evidence provenance, entity state history, decision lineage, unresolved agency coordination, and stale graph state.
