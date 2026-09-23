> **ARCHIVED — NON-AUTHORITATIVE.** This historical handoff prompt refers to an earlier prototype and API shape. Do not use it for MeshAgent production v1 implementation or operations. Use the root [README](../../README.md) and [Production Operations](../PRODUCTION_OPERATIONS.md).

# Cursor Handoff Prompt

Copy the text below into Cursor after opening the repository root.

---

You are integrating the MeshAgent Security Workbench frontend with a production backend. Preserve the existing interface, routes, role separation, Relationship Explorer behavior, and design system.

Before editing code, read these files in order:

1. `README.md`
2. `CURSOR_BACKEND_INTEGRATION.md`
3. `client/src/lib/meshagent-contracts.ts`
4. `client/src/lib/meshagent-schemas.ts`
5. `client/src/lib/http-gateway.ts`
6. `client/src/lib/gateway.ts`
7. `client/src/lib/mock-gateway.ts`
8. `client/src/components/RelationshipExplorer.tsx`
9. `client/src/pages/Home.tsx`

Implement the backend integration in small, reviewable phases.

## Required outcome

Create or connect a backend that implements:

- `GET /api/v1/fleet/snapshot`
- `GET /api/v1/graph?scope=&effectiveAt=`
- `GET /api/v1/findings/:id`
- `POST /api/v1/sources/:id/forget/preview`
- `POST /api/v1/sources/:id/forget`
- `WS /api/v1/agents/:id/activity`

Make every response conform to the TypeScript contracts and Zod schemas already in the repository. Do not weaken or bypass runtime validation.

## Security requirements

Derive tenant identity from a validated authenticated identity. Never trust a tenant ID supplied by the browser. Enforce permission checks for every operation.

Keep removal preview side-effect free. Removal execution must require:

- A valid authenticated and authorized actor
- A human-readable reason
- `If-Match` with the expected graph version
- `Idempotency-Key`
- An atomic transaction
- An immutable audit event
- A server-issued deletion certificate

Return `412 GRAPH_VERSION_CONFLICT` when the graph changed after preview. Return the original result for a same-request idempotent retry. Return `409 IDEMPOTENCY_CONFLICT` when a key is reused with a different request.

Do not log access tokens, cookies, prompts, source content, code content, or raw evidence payloads.

## Frontend requirements

Use `client/src/lib/gateway.ts` as the only data-source switch. Keep mock mode working.

Replace the remaining direct `sampleFinding` use in `Home.tsx` with `gateway.getFinding`. Add loading, empty, forbidden, not-found, malformed-response, unavailable, retry, and stale-version states.

Do not calculate graph membership, tenant scope, risk state, historical reconstruction, or removal impact in the browser. The frontend may calculate visual positions and client-only selection state.

Keep the Relationship Explorer’s:

- 46-entity visual-density fixture in mock mode
- First-class relationship ribbons
- Segmented membership rings
- Relationship selection and entity inspection
- Two-relationship overlap comparison
- Effective-time control
- Ribbon and skeleton modes
- Exact-data mode
- Zoom and reset controls
- Removal preview handoff

## Backend implementation guidance

If the backend is FastAPI, create thin route handlers, Pydantic request and response models, explicit authentication and authorization dependencies, service-layer business logic, repository abstractions, structured errors, and request-scoped logging.

Implement read-only fleet and graph endpoints first. Confirm the responses pass frontend Zod validation before adding mutations. Implement preview before execution. Add the WebSocket only after REST behavior is stable.

## Required tests

Add tests for:

- Contract-valid fleet, graph, finding, preview, and certificate payloads
- Empty and partial data allowed by the contract
- Cross-tenant read and mutation denial
- Unauthorized scope escalation
- Preview with no persistence changes
- Stale graph version rejection
- Idempotent execution retry
- Idempotency-key conflict
- Atomic rollback on deletion failure
- Deterministic effective-time graph queries
- Frontend API mode and mock mode
- Relationship selection, comparison, time control, and exact-data mode

## Validation before completion

Run:

```bash
pnpm check
pnpm build
```

Run the backend tests and end-to-end tests. Report the exact commands, results, changed files, assumptions, unresolved risks, and any API behavior that differs from `CURSOR_BACKEND_INTEGRATION.md`.

Do not report completion if destructive execution is reachable without authorization, graph-version checking, idempotency, or audit evidence.

---

## References

[1]: https://fastapi.tiangolo.com/ "FastAPI Documentation"
[2]: https://zod.dev/ "Zod TypeScript-first Schema Validation"
[3]: https://www.rfc-editor.org/rfc/rfc9110 "HTTP Semantics"
