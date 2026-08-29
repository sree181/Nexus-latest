# Nexus Coordinate

**Nexus Coordinate** is a simulation-free, human-authorized operational command center for SEC Game Day mobility coordination. It consolidates verified evidence, produces evidence-bound advisory recommendations, records named human decisions, and creates accountable agency commitments. It does **not** directly control traffic signals, signs, public-safety dispatch, parking equipment, or transit vehicles.

## Operational workflow

1. Authorized connectors normalize real traffic, parking, transit, closure, and emergency-access observations into `evidence_events`.
2. Incident services detect material changes and create or update an `incident` in the same operational mode.
3. Approved analytical or policy services write evidence-bound `agent_findings` and a versioned `recommendation`.
4. Nexus shows the exact recommendation version, material evidence, known limitations, constraints, approval authorities, and expiry.
5. A named human approver accepts, rejects, revises, delegates, or escalates the recommendation.
6. Approval creates accountable `commitments`; it does not execute an agency action.
7. Agencies acknowledge, approve, execute, and verify commitments through guarded state transitions.
8. Every material operation writes an append-only `audit_event` and transactional `outbox_event`.

## Production boundaries

- Production requires PostgreSQL and OIDC/JWT authentication.
- Production cannot fall back to local review data.
- Live, training, and replay records are separated in the schema and enforced by database triggers and service rules.
- Human decisions require an idempotency key, expected recommendation version, expected state, and immutable evidence-snapshot hash.
- Commitment verification requires authoritative evidence IDs.
- Stage 1 uses manual or approved deep-link agency handoffs only.
- No simulation, reward, episode, timestep, exploration, synthetic communication, or scenario-start code is included in the operational application.

## Core services

| Layer | Implementation |
|---|---|
| Frontend | React 19, TypeScript, Vite, MapLibre loaded as an asynchronous map chunk |
| API | Express 5 under `/api/v1`, Zod request validation, SSE operational updates |
| Identity | OIDC/JWT via JWKS, agency/role/scope claims, explicitly gated local review identity |
| Persistence | PostgreSQL with transactional repository, optimistic concurrency, idempotency, audit, and outbox |
| Connectors | Separate idempotent worker with bounded retries, source checkpoints, provenance, run history, and explicit connection states |
| Operational graph | Temporal PostgreSQL node/edge projection with evidence links, source authority, validity, optimistic versions, append-only state history, and live/training/replay isolation |
| Deployment | Multi-stage Node container; migrations run before the API when `DATABASE_URL` is configured |

## Local review mode

Local review mode demonstrates the **real approval and commitment workflow** with clearly labeled local review records. It does not claim that an agency system or live feed is connected.

```bash
npm ci
NEXUS_REPOSITORY=review NEXUS_AUTH_MODE=review PORT=4002 npm run dev:server
npm run dev:frontend
```

Open `http://localhost:4001`. The UI displays a persistent `LOCAL REVIEW DATA` banner.

## Production environment

| Variable | Required | Purpose |
|---|---:|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `NEXUS_REPOSITORY=postgres` | Yes | Selects persistent operational repository |
| `NEXUS_AUTH_MODE=oidc` | Yes | Enables verified OIDC/JWT identities |
| `NEXUS_OIDC_ISSUER` | Yes | Expected token issuer |
| `NEXUS_OIDC_AUDIENCE` | Yes | Expected API audience |
| `NEXUS_OIDC_JWKS_URI` | Yes | JWKS endpoint used to verify signatures |
| `NEXUS_ALLOWED_ORIGINS` | Recommended | Comma-separated trusted browser origins; absent means same-origin only |
| `ETA_SPOT_PRODUCTION_APPROVED=true` | Yes for transit | Confirms Auburn ETA Spot public data use was reviewed for the deployment |
| `NEXUS_ENABLE_PUBLIC_FEEDS=true` | Optional | Same transit enablement as the ETA Spot approval flag |
| `TOMTOM_API_KEY` | Optional | Enables live TomTom road-flow observations; remains `configuration_required` when absent |
| `CONNECTOR_WORKER_TICK_MS` | Optional | Worker wake interval; connector-specific cadence still controls idempotent run buckets |
| `CONNECTOR_RUN_TIMEOUT_MS` | Optional | Overall connector-run deadline; defaults to 65 seconds |
| `PGSSL=true` | Provider-dependent | Enables PostgreSQL TLS |
| `PGSSL_REJECT_UNAUTHORIZED` | Recommended | Keep `true` unless the database provider uses a platform/self-signed certificate. Railway requires `false`. |
| `PORT` | Platform supplied | HTTP listening port |

Production startup fails into an explicit `configuration_required` API/UI state if PostgreSQL is unavailable. It never displays review records as live operations.

## Railway deployment

Nexus is meant to run as **three Railway services** from `nexus-command/`: a public API/UI, private PostgreSQL, and a private connector worker. Do not use Railway cron; City of Auburn closures refresh about every 60 seconds, which is faster than Railway cron’s five-minute minimum.

| Service | Public domain | Config file | Start command | Role |
|---|---|---|---|---|
| `nexus-api` | Yes | `railway.toml` (default) | Image default: migrate, then `node dist-server/index.js` | Frontend, `/api/v1`, SSE, health check at `/api/health` |
| `Postgres` | No | Railway plugin | Railway managed | Operational, connector, graph, audit, and outbox state |
| `nexus-worker` | No | `railway.worker.toml` | `npm run start:connector-worker` | Always-on ingestion; shares `DATABASE_URL` with the API |

### One-time setup

Railway CLI 4.6+ is already the expected tool. From `nexus-command/`:

```bash
railway login
railway init --name nexus-command
railway add --database postgres
railway add --service nexus-api
railway add --service nexus-worker
```

In the Railway dashboard:

1. Attach **both** application services to the Postgres plugin, or set `DATABASE_URL=${{Postgres.DATABASE_URL}}` on each.
2. On `nexus-api`, generate a public domain. Leave `nexus-worker` and Postgres private.
3. On `nexus-worker`, set the config-as-code file to `railway.worker.toml` (or override the start command to `npm run start:connector-worker` and disable HTTP health checks). The worker does not listen on a port; probing `/api/health` on it will fail the deploy.
4. Deploy `nexus-api` first so migrations apply, then deploy `nexus-worker`.

```bash
railway up --service nexus-api
railway up --service nexus-worker
```

`railway up` uploads the current directory and does not require a GitHub remote. Connecting the GitHub repo later is optional and enables automatic deploys.

### Service variables

Set these on **both** `nexus-api` and `nexus-worker` unless noted.

| Variable | Service | Value |
|---|---|---|
| `DATABASE_URL` | API + worker | `${{Postgres.DATABASE_URL}}` |
| `NEXUS_REPOSITORY` | API + worker | `postgres` |
| `PGSSL` | API + worker | `true` |
| `PGSSL_REJECT_UNAUTHORIZED` | API + worker | `false` |
| `NEXUS_AUTH_MODE` | API | `oidc` |
| `NEXUS_OIDC_ISSUER` | API | Identity-provider issuer URL |
| `NEXUS_OIDC_AUDIENCE` | API | API audience / client ID |
| `NEXUS_OIDC_JWKS_URI` | API | JWKS URL |
| `NEXUS_OIDC_CLIENT_ID` | API | Public SPA client ID used by the browser sign-in |
| `NEXUS_ALLOWED_ORIGINS` | API | Public Railway URL, for example `https://nexus-api-production.up.railway.app` |
| `ETA_SPOT_PRODUCTION_APPROVED` | API + worker | `true` to ingest Tiger Transit ETA Spot public vehicle locations |
| `NEXUS_ENABLE_PUBLIC_FEEDS` | API + worker | Optional alias that also enables ETA Spot |
| `TOMTOM_API_KEY` | API + worker | Optional; omit until a production key exists |
| `CONNECTOR_WORKER_TICK_MS` | Worker | `15000` |
| `CONNECTOR_RUN_TIMEOUT_MS` | Worker | `65000` |

`NODE_ENV=production` is already set in the image. Do **not** set `NEXUS_REPOSITORY=review` or `NEXUS_AUTH_MODE=review` on Railway; both are rejected in production.

Without a configured OIDC provider, `/api/health` can still pass and the UI will load, but every authenticated `/api/v1` route returns `OIDC_NOT_CONFIGURED` or `AUTHENTICATION_REQUIRED`. Auth0, Clerk, Keycloak, or another OIDC IdP must issue tokens with `nexus_principal_id`, `nexus_agency_id`, `nexus_agency_name`, `nexus_roles`, `nexus_scopes`, and `nexus_modes`.

The worker pauses until PostgreSQL contains an active or monitoring `live` operational event. Migration `004_live_command_window.sql` inserts the named command owner, agencies, and the SEC Game Day live event. It does not insert simulated incidents or recommendations. After City of Auburn (and any other configured public feed) records are ingested, Nexus opens one evidence-bound recommendation that a named operator can approve. Approval creates agency commitments only; it does not invent observations or control signals.

A client demonstration still requires Auth0 (or another OIDC provider) and browser sign-in. Follow `docs/auth0-client-demo.md`.

### Validation

| Check | Pass condition |
|---|---|
| API health | `GET https://<api-domain>/api/health` returns `200` with `database: "connected"` |
| Worker logs | Startup lists configured connectors and does not bind a public port |
| Empty live window | Worker log: `No active live operational event; ingestion paused` until a live event exists |
| City connector | After a live event exists, `GET /api/v1/connectors` shows City of Auburn closures as connected |

### Connected authoritative sources

| Source | Operational classification | Status |
|---|---|---|
| City of Auburn `RoadClosuresPublic` ArcGIS service | Live closure/detour observations | Public connector implemented |
| Auburn Tiger Transit ETA Spot | Live vehicle/route observations | Public connector implemented; explicit production approval flag required |
| ALDOT TDM `TDMPublic` | Auburn/Opelika station counts | Connected as **reference**, not live traffic flow |
| TomTom Traffic Flow | Live road-flow observations | Connector implemented; API key required |
| Auburn University / FoPark parking occupancy | Restricted lot capacity | Partner interface and data-sharing agreement required |
| Event Command emergency-access state | Restricted corridor status | Approved webhook contract and secret required |

Deploy the connector worker as a **separate Railway service** using the same image and shared `DATABASE_URL`. Override its start command with `npm run start:connector-worker`. Do not run the worker inside the API process; separate services provide independent scaling and failure isolation.

## Database migration

```bash
DATABASE_URL='postgresql://…' npm run db:migrate
```

The container compiles and runs `dist-server/operational/migrate.js` before starting the API when `DATABASE_URL` is present. Applied migrations are recorded in `schema_migrations`.

The migrations include agencies, principals, role assignments, operational events, participants, sources, evidence, incidents, evidence links, agent findings, recommendations, material evidence, approval requirements, human decisions, action templates, commitments, commitment transitions, execution requests and confirmations, connector runs, idempotency records, audit events, transactional outbox events, temporal graph nodes and edges, evidence links, ingestion batches, and append-only graph state changes.

## API surface

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/me` | Authenticated operator and authority context |
| `GET` | `/api/v1/events/active?mode=live` | Current operational event |
| `GET` | `/api/v1/events/:eventId/snapshot` | Event, incidents, sources, decisions, and commitments |
| `GET` | `/api/v1/events/:eventId/system-status` | Repository and source-health status |
| `GET` | `/api/v1/events/:eventId/stream` | Authenticated realtime SSE updates |
| `GET` | `/api/v1/recommendations/:id` | Exact recommendation and evidence snapshot |
| `POST` | `/api/v1/recommendations/:id/decisions` | Human approve/reject/revise/delegate/escalate mutation |
| `POST` | `/api/v1/commitments/:id/transitions` | Guarded agency commitment state transition |
| `GET` | `/api/v1/audit` | Authorized audit-history query |
| `GET` | `/api/v1/connectors` | Connector configuration, connection, health, and latest-run status |
| `POST` | `/api/v1/events/:eventId/connectors/:connectorCode/runs` | Authorized, idempotent manual connector run |
| `POST` | `/api/v1/events/:eventId/graph/sources/:sourceId/batches` | Authorized and idempotent authoritative node/edge batch |
| `GET` | `/api/v1/events/:eventId/graph` | Current or effective-time Mobility Flow, Decision Lineage, or Agency Coordination snapshot |
| `GET` | `/api/v1/events/:eventId/graph/nodes/:nodeId/neighborhood` | Bounded graph neighborhood |
| `GET` | `/api/v1/graph/entities/:kind/:entityId/history` | Append-only node or edge state history |
| `GET` | `/api/v1/graph/decision-lineage/:recommendationId` | Evidence-to-verification decision lineage |
| `GET` | `/api/v1/events/:eventId/graph/agency-coordination` | Current accountable agency coordination state |

All material mutations require `Idempotency-Key`. Responses include a request ID for audit correlation. The graph API requires `graph:read` or `graph:ingest`, enforces operational mode and source ownership, and accepts exact authoritative external identifiers without changing their provenance.

## Operational graph workspaces

The frontend exposes **Mobility Flow**, **Decision Lineage**, and **Agency Coordination** as a peer workspace to the Command Center. Mobility Flow groups current roads, closures, parking assets, transit routes, vehicles, and protected corridors. Decision Lineage preserves the causal sequence from evidence through verification. Agency Coordination shows accountable owners, requests, approvals, blockers, and verification. No view fabricates records when an authoritative batch, human decision, or agency commitment does not yet exist.

Implementation and contract references are in `docs/temporal-graph-package.md`, `docs/graph-api-contract.md`, `docs/nexus-graph-api.openapi.yaml`, `docs/graph-ui-component-layout.md`, and `server/graph/queries.sql`.

## Quality gates

```bash
npm test
npm run typecheck
npm run build:all
npm audit --omit=dev
```

The automated suite covers all PostgreSQL migrations, connector provenance fields, bounded HTTP retries, permission gating, idempotent worker claims, cross-mode isolation, live-only decisions, expiry, optimistic concurrency, evidence-snapshot binding, guarded commitment transitions, temporal graph versioning, append-only graph history, graph batch validation and idempotency, persistent graph requirements, API validation, and commitment creation after human approval.

## Remaining stakeholder integration work

Before a real SEC Game Day pilot, stakeholders must provide or approve:

1. OIDC identity provider, agency roles, named authorities, and escalation matrix.
2. PostgreSQL hosting, backups, retention, recovery objectives, and audit access.
3. Auburn/FoPark parking-occupancy contract and production API credentials.
4. Event Command emergency-access webhook contract, allowed fields, and signing secret.
5. TomTom production key if live road-flow observations are required.
6. Stakeholder-approved freshness thresholds, quality rules, and source ownership for every feed.
7. Pre-approved action templates and public-message templates.
8. Manual or deep-link agency handoff procedures and verification evidence.
9. Security assessment, incident response, privacy review, and operational acceptance testing.

Nexus now includes three verified public authoritative connectors and the persistent ingestion/runtime path. It must still be described as a **partial operational data integration** until parking, emergency access, live road flow, production identity, and agency operating agreements are completed.
