# Nexus Temporal Operational Graph — Delivery Package

The Nexus operational graph is implemented as a temporal, evidence-bound projection inside PostgreSQL. It does not replace the operational database and does not introduce a separate graph database. Current state remains queryable as nodes and directed edges, while every material authoritative update creates append-only history and increments an optimistic version.

## Deliverables

| Deliverable | File |
|---|---|
| PostgreSQL graph schema, indexes, history triggers, mode isolation, and idempotency | `server/operational/migrations/003_temporal_operational_graph.sql` |
| Executable SQL query catalog | `server/graph/queries.sql` |
| Shared graph contracts | `server/graph/domain.ts` |
| Persistent repository implementation | `server/graph/postgresRepository.ts` |
| Authenticated and validated graph API | `server/graph/routes.ts` |
| Human-readable API behavioral contract | `docs/graph-api-contract.md` |
| Validated OpenAPI 3.1 contract | `docs/nexus-graph-api.openapi.yaml` |
| Frontend graph API types and client | `src/graphTypes.ts`, `src/graphApi.ts` |
| Three production graph workspaces | `src/components/OperationalGraphWorkspace.tsx` |
| Large-screen graph design system | `src/graphWorkspace.css` |
| UI layout and component specification | `docs/graph-ui-component-layout.md` |
| Visual verification record | `GRAPH_UI_VERIFICATION.md` |

## Implemented graph semantics

| Category | Node examples | Edge examples |
|---|---|---|
| Mobility | road segment, closure, parking lot, transit route, transit vehicle, emergency corridor | restricts, feeds traffic into, assigned to route, provides access to |
| Decision lineage | evidence, finding, incident, recommendation, decision, commitment, verification | supports, triggered, approved, assigned, verified by |
| Agency coordination | agency or team, request, commitment, escalation, verification | requested from, acknowledged by, owned by, escalated to, verified by |

Graph entities are scoped by operational event and mode. A live edge cannot point to a training or replay node. Source ownership, evidence references, authority URIs, data classification, quality flags, validity intervals, optimistic versions, and canonical state hashes are persisted. History is append-only and protected against update or deletion.

## API behavior

Authoritative publishers submit a batch to `POST /api/v1/events/{eventId}/graph/sources/{sourceId}/batches` with a verified identity, `graph:ingest` scope, source ownership, mode authority, and an `Idempotency-Key`. Current snapshots, bounded neighborhoods, state history, decision lineage, and agency coordination require `graph:read`.

## Verification

The migration executes in the PostgreSQL-compatible test runtime and validates version increments, append-only history, and cross-mode rejection. The graph API tests validate persistent-storage requirements, authentication context, request validation, idempotency requirements, ingestion responses, and query envelopes.

The implementation was also verified against local PostgreSQL with persisted real City of Auburn Road Closures and Tiger Transit ETA Spot evidence. The API successfully created four graph nodes and two relationships, replayed an identical idempotency key without duplicate writes, and returned the current Mobility Flow snapshot. The 1920×1080 UI shows those records with provenance and an entity inspector. Decision Lineage and Agency Coordination intentionally remain empty until real human decisions and agency commitments exist.

The final quality gate passes 29 tests, both frontend and server TypeScript checks, frontend/API/worker production builds, a warning-free OpenAPI lint, and zero production dependency vulnerabilities.
