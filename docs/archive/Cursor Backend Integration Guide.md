> **ARCHIVED — NON-AUTHORITATIVE.** This historical integration guide is retained for context only. It does not describe MeshAgent production v1 or its supported interfaces, configuration, or operations. Use the root [README](../../README.md) and [Production Operations](../PRODUCTION_OPERATIONS.md).

# Cursor Backend Integration Guide

**Audience:** Engineers using Cursor to connect the MeshAgent Security Workbench frontend to a production backend.

**Author:** Manus AI

## 1. Goal

Replace the illustrative gateway with production data while preserving the current product behavior, visual design, role-based navigation, and interaction model.

The frontend already has a stable integration boundary. Cursor should implement the backend against that boundary instead of rewriting the screens. The backend is authoritative for identity, tenant scope, graph membership, evidence, risk state, temporal reconstruction, removal impact, and audit records. The frontend remains responsible for presentation, local selection, zoom, and non-authoritative layout.

## 2. Non-negotiable requirements

The integration is complete only when all of the following are true:

1. No UI component imports production data directly.
2. All backend access passes through `MeshAgentGateway`.
3. All JSON responses pass Zod validation before reaching a screen.
4. Tenant identity is derived from validated authentication, never accepted from a browser query or header as authority.
5. Removal preview cannot mutate data.
6. Removal execution requires authorization, a reason, optimistic concurrency, and an idempotency key.
7. Evidence identifiers remain stable and resolve to immutable evidence records.
8. Historical graph requests return deterministic results for the requested effective time.
9. Production logs never contain access tokens, prompts, source text, secrets, or raw evidence bodies.
10. Mock mode remains available for isolated frontend development and visual regression testing.

## 3. Start here in Cursor

Open the repository root and give Cursor this sequence:

1. Read `README.md`.
2. Read `client/src/lib/meshagent-contracts.ts`.
3. Read `client/src/lib/meshagent-schemas.ts`.
4. Read `client/src/lib/http-gateway.ts`.
5. Read `client/src/lib/mock-gateway.ts`.
6. Read `client/src/components/RelationshipExplorer.tsx`.
7. Read `client/src/pages/Home.tsx`.
8. Implement the backend route by route, beginning with the fleet snapshot and graph query.
9. Switch the frontend to API mode only after the first two responses pass runtime validation.
10. Implement removal preview before implementing removal execution.

Do not begin by changing JSX. First make the backend satisfy the existing contract.

## 4. Current frontend architecture

`client/src/lib/gateway.ts` selects the data source.

```text
VITE_MESHAGENT_DATA_MODE=mock  -> MockMeshAgentGateway
VITE_MESHAGENT_DATA_MODE=api   -> HttpMeshAgentGateway
```

`client/src/lib/http-gateway.ts` provides:

- Bearer-token or cookie-compatible requests
- JSON response parsing
- Standard API error parsing
- Zod runtime validation
- URL-safe identifiers and query parameters
- Separate preview and destructive removal methods
- `If-Match` and `Idempotency-Key` enforcement for destructive removal

The environment variable names are documented in `env.template.txt`.

## 5. Recommended backend structure

The product architecture references FastAPI, but the contract is transport-neutral. A FastAPI implementation can use this structure:

```text
backend/
  app/
    main.py
    api/
      dependencies.py
      errors.py
      v1/
        fleet.py
        graph.py
        findings.py
        sources.py
        evidence.py
        activity.py
    auth/
      oidc.py
      authorization.py
      tenant_context.py
    schemas/
      fleet.py
      graph.py
      findings.py
      deletion.py
      events.py
    services/
      fleet_service.py
      graph_service.py
      finding_service.py
      retraction_service.py
      evidence_service.py
    repositories/
      graph_repository.py
      evidence_repository.py
      audit_repository.py
    observability/
      logging.py
      metrics.py
      tracing.py
    tests/
```

Keep HTTP routing thin. Validation belongs in request schemas, authorization in explicit dependencies or middleware, business rules in services, and storage access in repositories.

## 6. Authentication and tenant context

Use OpenID Connect or the company identity provider already selected for the platform. The backend must validate issuer, audience, signature, expiration, and required claims. It should then create an immutable request context similar to:

```python
class RequestContext(BaseModel):
    tenant_id: str
    actor_id: str
    actor_email: str | None
    roles: set[str]
    scopes: set[str]
    request_id: str
```

The browser may send a tenant hint for navigation, but the backend must not trust it. The authenticated subject and server-side authorization records determine `tenant_id`.

Recommended access rules:

| Operation | Minimum permission |
|---|---|
| Read own project graph | `graph:read:project` |
| Read organization graph | `graph:read:org` |
| Read a security finding | `finding:read` |
| Read evidence metadata | `evidence:read` |
| Preview source removal | `source:forget:preview` |
| Execute source removal | `source:forget:execute` plus approval policy |
| Read deletion certificates | `certificate:read` |

If cookie authentication is used, implement CSRF protection for state-changing requests. If bearer tokens are used, keep tokens in memory where possible. The provided `sessionStorage` token hook is an integration fallback, not the preferred production design.

## 7. API conventions

Use JSON with UTF-8 encoding. Use ISO 8601 timestamps with an explicit UTC `Z` or offset. Keep identifiers opaque and stable.

Successful responses return the resource directly. Failed responses return:

```json
{
  "error": {
    "code": "GRAPH_VERSION_CONFLICT",
    "message": "The graph changed after the removal preview was generated.",
    "requestId": "req_01K8...",
    "details": {
      "expectedGraphVersion": "graph-v1842",
      "currentGraphVersion": "graph-v1843"
    }
  }
}
```

Recommended status codes:

| Status | Use |
|---|---|
| `400` | Invalid request shape or missing mutation context |
| `401` | No valid authenticated identity |
| `403` | Identity is valid but lacks permission |
| `404` | Tenant-scoped resource does not exist |
| `409` | Idempotency conflict or incompatible operation state |
| `412` | `If-Match` graph version is stale |
| `422` | Request is syntactically valid but violates a domain rule |
| `429` | Rate limit exceeded |
| `500` | Unexpected server failure |
| `503` | Required graph, evidence, or policy dependency is unavailable |

Every response should include an `X-Request-ID`. Mutations should also return an audit correlation identifier in the resource or response headers.

## 8. Required endpoints

### 8.1 Fleet snapshot

```http
GET /api/v1/fleet/snapshot
Authorization: Bearer <token>
Accept: application/json
```

The response must match `FleetSnapshotSchema`.

```json
{
  "tenantId": "tenant-42",
  "tenantName": "Example Financial Services",
  "environment": "SELF_HOSTED",
  "generatedAt": "2026-09-22T15:42:00Z",
  "agents": [
    {
      "id": "agt-01",
      "name": "Maya",
      "owner": "A. Chen",
      "project": "payments-api",
      "runtime": "Cursor",
      "memoryCount": 1842,
      "lastSeen": "32 sec",
      "state": "EXPLOITABLE",
      "policyVersion": "4.2"
    }
  ],
  "memoriesGoverned": 1842,
  "quarantined": 1,
  "exploitableFindings": 1,
  "certificates": [],
  "graph": {
    "graphId": "graph-payments-api-42",
    "graphVersion": "graph-v1842",
    "policyVersion": "mem-policy-4.2",
    "tenantId": "tenant-42",
    "effectiveAt": "2026-09-22T15:42:00Z",
    "scope": "PROJECT",
    "nodes": [],
    "edges": [],
    "relations": []
  }
}
```

The fleet endpoint may return a compact graph summary if the full graph is expensive. If so, keep the response schema stable and use empty arrays until the dedicated graph request completes.

### 8.2 Graph query

```http
GET /api/v1/graph?scope=PROJECT&effectiveAt=2026-09-22T15%3A42%3A00Z
Authorization: Bearer <token>
```

The response must match `GraphPayloadSchema`.

Important rules:

- `scope` is one of `PRIVATE`, `PROJECT`, or `ORG`.
- The backend verifies that the actor can read the requested scope.
- `effectiveAt` reconstructs valid-time state. It is not merely a frontend filter.
- Every `memberId` in a relationship must reference a node included in the payload.
- Every edge source and target must reference included nodes.
- Evidence identifiers should be stable references, not display text.
- `graphVersion` changes whenever a mutation changes the authoritative graph.

### 8.3 Finding detail

```http
GET /api/v1/findings/finding-sarif-98
Authorization: Bearer <token>
```

The response must match `SecurityFindingSchema`.

```json
{
  "id": "finding-sarif-98",
  "title": "Untrusted shard reaches unsafe deserialization",
  "weakness": "CWE-502: Deserialization of Untrusted Data",
  "severity": "CRITICAL",
  "presence": "EXPLOITABLE",
  "scanner": "Semgrep SARIF import",
  "ruleId": "python.lang.security.deserialization.pickle",
  "sourceEntry": "request.files['shard']",
  "sink": "pickle.load(shard_stream)",
  "evidenceId": "sarif-98/codeFlow-2",
  "affectedAgents": 1
}
```

The browser should not query scanners or vulnerability databases directly. The backend imports and normalizes those records, evaluates reachability, and returns evidence references.

### 8.4 Removal preview

```http
POST /api/v1/sources/source%3Adocs-mirror.example/forget/preview
Authorization: Bearer <token>
Content-Type: application/json

{
  "reason": "Security review preview"
}
```

This endpoint must be side-effect free. It computes the server-authoritative impact and returns `ForgetPreviewSchema`.

```json
{
  "id": "forget-preview-01842",
  "sourceId": "source:docs-mirror.example",
  "generatedAt": "2026-09-22T15:45:00Z",
  "expectedGraphVersion": "graph-v1842",
  "affectedMemoryCount": 18,
  "affectedClassCount": 2,
  "affectedRelationIds": ["r-provenance", "r-retraction"],
  "warnings": ["Approval is required before execution."]
}
```

Store the returned graph version with the pending confirmation state. Do not infer deletion impact in the browser.

### 8.5 Removal execution

```http
POST /api/v1/sources/source%3Adocs-mirror.example/forget
Authorization: Bearer <token>
Content-Type: application/json
If-Match: graph-v1842
Idempotency-Key: 7f904cb0-09bf-4d50-93f3-7f3f926ae627

{
  "reason": "Approved security remediation"
}
```

The endpoint must execute atomically or fail without partial deletion. It returns `DeletionCertificateSchema`.

```json
{
  "id": "del-cert-2026-0918-044",
  "createdAt": "2026-09-22T15:46:12Z",
  "sourceId": "source:docs-mirror.example",
  "purgedCount": 18,
  "classesPruned": 2,
  "retainedHash": "sha256:9af813bc...",
  "policyVersion": "mem-policy-4.2",
  "approvedBy": "principal-73"
}
```

If the graph version changed after preview, return `412 GRAPH_VERSION_CONFLICT` and require a new preview. If the same idempotency key is retried with the same request, return the original result. If it is reused with a different request, return `409 IDEMPOTENCY_CONFLICT`.

### 8.6 Agent activity WebSocket

Recommended URL:

```text
wss://<host>/api/v1/agents/:agentId/activity
```

Use a versioned envelope:

```json
{
  "version": 1,
  "eventId": "evt_01842",
  "occurredAt": "2026-09-22T15:42:04Z",
  "tenantId": "tenant-42",
  "agentId": "agt-01",
  "runId": "run-7fd1c2",
  "type": "MEMORY_QUARANTINED",
  "payload": {
    "memoryId": "mem-77",
    "reasonCode": "UNTRUSTED_EXTERNAL_SOURCE"
  }
}
```

Reconnect with exponential backoff and jitter. Deduplicate by `eventId`. Treat completed historical views as REST resources rather than replaying a live stream.

## 9. Hypergraph and relationship rules

The backend should return both pairwise edges and first-class relationships when both are available. They serve different purposes.

`GraphEdge` supports conventional traversal and evidence chains. `HyperRelation` describes one fact that has several members. Do not synthesize `HyperRelation.memberIds` from a frontend layout.

The server should enforce these invariants:

1. Relationship membership is exact for the requested tenant, scope, and effective time.
2. A member identifier resolves to one returned node.
3. `validFrom` is inclusive and `validTo` is exclusive.
4. A relationship with no valid members is omitted.
5. Evidence IDs remain stable across display-label changes.
6. Risk state is computed from server policy and evidence.
7. Layout coordinates are not required in the first API version.

The current renderer uses deterministic review positions. For production, return stable optional layout hints in node metadata or introduce a versioned layout object only after the backend contract is stable.

## 10. Frontend integration steps

### Phase 1: Read-only data

1. Start the backend on a separate port.
2. Implement CORS only when the UI and API use different origins.
3. Implement `GET /api/v1/fleet/snapshot`.
4. Implement `GET /api/v1/graph`.
5. Copy the values from `env.template.txt` into a local `.env`.
6. Set `VITE_MESHAGENT_DATA_MODE=api`.
7. Restart Vite because Vite reads environment variables at startup.
8. Confirm that Zod accepts both responses.
9. Test Developer and CISO direct routes.

### Phase 2: Finding detail

The current prototype imports `sampleFinding` for a static screen. Replace that dependency with `gateway.getFinding(id)` and add loading, empty, forbidden, not-found, malformed-response, and retry states.

Do not remove the mock finding. Keep it in the mock gateway so visual testing remains deterministic.

### Phase 3: Removal preview

Connect the existing confirmation flow to `gateway.previewForgetSource`. Store the returned preview ID and expected graph version. Display server-returned impact counts and warnings rather than hard-coded values.

### Phase 4: Removal execution

Add a separately labeled execution action only after product and security approval. Generate a UUID idempotency key when the user opens the final confirmation. Preserve that key across retries. Store the expected graph version from the preview. The included HTTP gateway refuses execution when either value is absent.

After success, invalidate the fleet snapshot, graph, source detail, and certificate queries. Display the server-issued certificate rather than constructing one in the browser.

### Phase 5: Live activity

Add a small event-stream client under `client/src/lib`. Keep connection state outside page components. Normalize incoming events and update a client cache rather than directly mutating screen state from WebSocket callbacks.

## 11. Error and loading behavior

Every screen should support:

- Initial loading
- Refreshing while retaining previous data
- Empty result
- Permission denied
- Resource not found
- Contract validation failure
- Backend unavailable
- Stale graph version
- Rate limiting with retry guidance

A malformed success response is a backend integration failure. Do not silently render partial data. `HttpMeshAgentGateway` raises `MeshAgentApiError` with code `INVALID_RESPONSE` and Zod details.

## 12. Observability

The backend should emit structured logs with request ID, actor ID, tenant ID, operation, resource ID, authorization decision, duration, result, graph version, and audit correlation ID.

Do not log bearer tokens, session cookies, prompt text, source content, code snippets, or raw evidence payloads. Where a correlation value is needed, log an opaque identifier or a one-way hash approved by the security team.

Recommended metrics include request latency, validation failures, authorization denials, graph query size, preview duration, mutation conflicts, idempotent retries, WebSocket connection count, and event lag.

## 13. Testing strategy

### Contract tests

Export representative backend fixtures and validate them with the frontend Zod schemas in continuous integration. Contract tests should include the largest expected identifiers, optional fields, empty arrays, temporal boundaries, and every enum value.

### Backend tests

Test tenant isolation, scope authorization, expired tokens, stale graph versions, idempotent retries, preview side-effect freedom, atomic deletion, deterministic historical queries, and evidence resolution.

### Frontend tests

Test mock and API modes. Verify that response validation failures produce a usable error state. Verify that switching scope refetches the graph. Verify that relationship selection, comparison, exact-data mode, time changes, and removal preview continue to work with API responses.

### End-to-end tests

At minimum, automate these scenarios:

1. A developer starts a governed task and opens the project graph.
2. A CISO analyst opens a reachable security finding and its relationship path.
3. Two relationships are compared and the shared members match the server response.
4. A source-removal preview returns impact without mutation.
5. A stale execution request returns `412` and cannot alter data.
6. A retried successful deletion with the same idempotency key returns the same certificate.
7. An actor from one tenant cannot read or mutate another tenant’s data.

## 14. Performance guidance

Return a focused subgraph rather than the entire tenant graph. Add pagination or cursor-based expansion for large entity sets. Use server-side filters for scope, effective time, project, entity type, risk state, and relationship kind.

Compress JSON responses. Cache immutable evidence metadata. Use entity and relationship counts to enforce response limits. If a response is truncated, make that explicit in the payload; never silently omit members from a relationship that is presented as exact.

For large graphs, keep the contract and replace only the rendering adapter. Consider Cytoscape or a WebGL renderer with viewport culling after the backend can provide bounded subgraphs.

## 15. Suggested pull-request sequence

1. Add backend health, identity, and request-context infrastructure.
2. Add Pydantic models that mirror the TypeScript contracts.
3. Implement fleet and graph read endpoints with contract fixtures.
4. Enable API mode in a local integration environment.
5. Move finding detail to the gateway.
6. Implement removal preview and tests proving no mutation.
7. Implement authorized, versioned, idempotent removal execution.
8. Add certificate retrieval and audit logging.
9. Add WebSocket activity after the REST flows are stable.
10. Add end-to-end tenant-isolation and stale-version tests.

Keep each pull request reviewable. Do not combine identity, graph storage migration, deletion execution, and UI refactoring into one change.

## 16. Definition of done

The backend integration is ready for product review when:

- `pnpm check` and `pnpm build` pass.
- The backend test suite passes.
- API fixtures pass frontend Zod validation.
- Mock mode and API mode both work.
- No production screen imports `sampleFinding` or other mock records directly.
- The user’s tenant cannot be changed from browser input.
- Preview and execution are visibly and technically separate.
- Execution is authorized, version-checked, idempotent, atomic, and audited.
- The Relationship Explorer renders exact memberships returned by the backend.
- Historical graph results are deterministic.
- Error states are actionable and do not expose sensitive data.

## 17. References

[1]: https://fastapi.tiangolo.com/ "FastAPI Documentation"
[2]: https://docs.pydantic.dev/latest/ "Pydantic Documentation"
[3]: https://zod.dev/ "Zod TypeScript-first Schema Validation"
[4]: https://vite.dev/guide/env-and-mode "Vite Environment Variables and Modes"
[5]: https://www.rfc-editor.org/rfc/rfc9110 "HTTP Semantics"
[6]: https://www.rfc-editor.org/rfc/rfc6750 "OAuth 2.0 Bearer Token Usage"
[7]: https://www.rfc-editor.org/rfc/rfc6455 "The WebSocket Protocol"
