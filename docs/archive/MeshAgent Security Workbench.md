# MeshAgent Security Workbench

MeshAgent Security Workbench is a frontend prototype for governed AI-agent memory, software provenance, reachable security risk, supply-chain analysis, and audit evidence. The application contains separate developer and CISO journeys inside one product shell. Its Relationship Explorer renders first-class multi-entity relationships as interactive ribbons rather than reducing them to pairwise graph edges.

The repository is designed as a **frontend handoff**. It runs immediately with illustrative mock data and includes a typed HTTP adapter for integration with a real backend.

## What is included

The application contains the following workspaces:

| Workspace | Intended user | Purpose |
|---|---|---|
| Start a task | Developer | Choose a repository, define work, select memory scope, and review controls before an agent starts. |
| My agent | Developer | Observe current work, memory formation, controls, and generated code. |
| Why & Forget | Developer and CISO | Inspect provenance, preview source removal, and display deletion evidence. |
| Project graph / Structure analysis | Developer and CISO | Explore sources, decisions, code, packages, agents, risks, certificates, and their multi-entity relationships. |
| Fleet overview | CISO | Review governed agents, ownership, policy posture, and exceptions. |
| Security findings | CISO and AppSec | Distinguish package presence from reachable exposure and inspect evidence. |
| Supply chain | CISO and AppSec | Review package inventory, versions, licenses, reachability, and ownership. |
| Audit evidence | CISO, risk, and audit | Review certificates, control coverage, policy drift, and immutable evidence. |

All company names, events, metrics, findings, evidence identifiers, and graph records in mock mode are **illustrative**.

## Technology

The frontend uses React 19, TypeScript, Vite, Tailwind CSS 4, Wouter, Lucide icons, Radix-based UI components, Zod runtime validation, and native SVG for the current hypergraph visualization.

`server/index.ts` is only a small static production server for the built frontend. It is **not** the MeshAgent application backend.

## Quick start

### Requirements

Use Node.js 22 or newer and pnpm 10.

```bash
pnpm install
pnpm dev
```

Open the local Vite URL printed by the command. The default route starts the developer journey. Useful direct routes are:

```text
/?role=developer&view=start
/?role=developer&view=structure
/?role=ciso&view=fleet
/?role=ciso&view=structure
```

Run validation before committing changes:

```bash
pnpm check
pnpm build
```

## Data modes

The app supports two data modes.

**Mock mode** is the default. It uses `client/src/lib/mock-gateway.ts` and requires no backend.

**API mode** uses `client/src/lib/http-gateway.ts`. Copy the variable names from `env.template.txt` into a local `.env` file, then set:

```text
VITE_MESHAGENT_DATA_MODE=api
VITE_MESHAGENT_API_BASE_URL=http://localhost:8000
```

Leave `VITE_MESHAGENT_API_BASE_URL` empty when the frontend and API share an origin and `/api` is reverse-proxied to the backend.

The gateway factory is `client/src/lib/gateway.ts`. UI components import this factory rather than importing a mock or HTTP implementation directly.

## Integration boundary

The authoritative TypeScript contracts are in:

```text
client/src/lib/meshagent-contracts.ts
```

Runtime response validation is in:

```text
client/src/lib/meshagent-schemas.ts
```

The live HTTP adapter is in:

```text
client/src/lib/http-gateway.ts
```

The mock implementation is in:

```text
client/src/lib/mock-gateway.ts
```

The UI depends on `MeshAgentGateway`, not on a specific transport or database. The initial integration should therefore keep screen components unchanged and make backend responses conform to the existing contracts.

## Critical safety boundary

**Removal preview and removal execution are separate operations.**

```text
POST /api/v1/sources/:id/forget/preview
POST /api/v1/sources/:id/forget
```

The preview endpoint must never mutate data. The execution endpoint should require an authenticated and authorized actor, a reason, an expected graph version, and an idempotency key. The included HTTP gateway sends `If-Match` and `Idempotency-Key` headers for execution and refuses to call the destructive endpoint when either value is absent.

The browser must not be trusted to choose a tenant. The backend should derive immutable tenant context from the validated identity and verify authorization on every request.

## Relationship Explorer contract

`GraphPayload.relations` contains first-class relationships. Each relationship has an exact member set, evidence identifiers, risk state, and valid-time range.

```ts
interface HyperRelation {
  id: string;
  label: string;
  kind: "PROVENANCE" | "DEPENDENCY" | "REACHABILITY" | "OWNERSHIP";
  memberIds: string[];
  state: "HEALTHY" | "WATCH" | "EXPLOITABLE" | "QUARANTINED";
  evidenceIds: string[];
  validFrom: string;
  validTo?: string;
}
```

The backend is authoritative for membership, evidence, effective time, risk state, and removal impact. The frontend controls only layout, visual selection, zoom, local filtering, and presentation.

## Repository map

```text
client/
  index.html
  src/
    App.tsx
    components/
      RelationshipExplorer.tsx
      ui/
    contexts/
    hooks/
    lib/
      gateway.ts
      http-gateway.ts
      meshagent-contracts.ts
      meshagent-schemas.ts
      mock-gateway.ts
    pages/
      Home.tsx
    index.css
server/
  index.ts
shared/
INTEGRATION_GUIDE.md
HYPERGRAPH_EXPLORER_INTEGRATION.md
CURSOR_BACKEND_INTEGRATION.md
CURSOR_HANDOFF_PROMPT.md
env.template.txt
```

## Backend handoff

Give Cursor both `CURSOR_BACKEND_INTEGRATION.md` and `CURSOR_HANDOFF_PROMPT.md`. The guide defines the endpoint sequence, security model, payload shapes, error behavior, WebSocket event format, implementation phases, and acceptance tests. The prompt turns that guide into an explicit implementation task.

## Production expectations

Before production release, replace all illustrative data, add authenticated loading and error states, move the security finding screen from `sampleFinding` to `gateway.getFinding`, and connect active-agent updates to a backend event stream. The final backend should log request IDs and audit correlation IDs without logging access tokens, source content, prompts, or sensitive evidence payloads.

The current native SVG renderer is suitable for explanation views and controlled result sizes. For hundreds or thousands of visible entities, keep the same `GraphPayload` contract but add server-side subgraph selection and a viewport-aware graph renderer.

## Documentation

- `CURSOR_BACKEND_INTEGRATION.md` is the primary implementation guide.
- `CURSOR_HANDOFF_PROMPT.md` is the copy-paste task prompt for Cursor.
- `INTEGRATION_GUIDE.md` explains the product workspaces and UI architecture.
- `HYPERGRAPH_EXPLORER_INTEGRATION.md` explains the relationship visualization and its data boundary.

## References

[1]: https://vite.dev/guide/env-and-mode "Vite Environment Variables and Modes"
[2]: https://zod.dev/ "Zod TypeScript-first Schema Validation"
[3]: https://www.rfc-editor.org/rfc/rfc9110 "HTTP Semantics"
[4]: https://www.rfc-editor.org/rfc/rfc6750 "OAuth 2.0 Bearer Token Usage"
[5]: https://www.rfc-editor.org/rfc/rfc6455 "The WebSocket Protocol"
