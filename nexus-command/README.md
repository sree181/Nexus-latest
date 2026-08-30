# Nexus Coordinate

**Nexus Coordinate** is a simulation-free, human-authorized operational command center for multi-agency mobility coordination. It consolidates verified evidence, produces evidence-bound advisory recommendations, records named human decisions, and creates accountable agency commitments. It does **not** directly control traffic signals, signs, public-safety dispatch, parking equipment, or transit vehicles.

An operating window runs under a **scenario pack**, so the same platform serves an everyday road-closure day, an SEC Game Day, a severe-weather response, or a cyber incident. Game Day is one pack, not a built-in assumption.

## Operational workflow

1. Authorized connectors normalize real traffic, parking, transit, closure, and emergency-access observations into `evidence_events`.
2. Detection evaluates the active scenario pack's rules against that evidence and opens one `incident` per qualifying upstream record, keyed by its connector and upstream identity. Quiet periods open nothing.
3. Every staffed agent desk reviews the same evidence snapshot. A desk that cannot evaluate the incident is recorded as abstained, not as agreeing. NEXUS composes those findings without inventing a different action than the playbook.
4. Nexus shows the recommendation, which desks contributed or stayed silent, any dissent, material evidence, known limitations, constraints, approval authorities, and expiry.
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
| Detection | Scenario packs bind connectors, agent desks, and named detection rules; rule predicates live in `server/operational/detection/rules.ts`, playbooks live in the `detection_rules` table |
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
| `ALGO_TRAVELER_FEED=false` | Optional | Set only to disable the public ALGO traveler-map connector |
| `NEXUS_ENABLE_PUBLIC_FEEDS=true` | Optional | Same transit enablement as the ETA Spot approval flag |
| `TOMTOM_API_KEY` | Optional | Enables live TomTom road-flow observations; remains `configuration_required` when absent |
| `ATLAS_AI_ENABLED=true` | Optional | Turns ATLAS into a tool-using agent loop. Requires `GROQ_API_KEY` or `ATLAS_AI_API_KEY`. Off = current rule assessor |
| `GROQ_API_KEY` | Optional | Free Groq key (`console.groq.com`). Default model is `openai/gpt-oss-20b`. Does not change signals or publish messages |
| `ATLAS_AI_BASE_URL` | Optional | OpenAI-compatible host. Default Groq. Point at Ollama (`http://127.0.0.1:11434/v1`) to run local |
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
| `nexus-worker` | No | Image default | `NEXUS_SERVICE_ROLE=connector-worker` | Always-on ingestion; shares `DATABASE_URL` with the API |

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
3. On `nexus-worker`, set `NEXUS_SERVICE_ROLE=connector-worker`. The image default command then runs the ingestion loop instead of the API and answers `/api/health` with the loop's own liveness, so the platform health check still passes. Prefer this to overriding the start command: a service left on the default silently becomes a second API and ingestion stops between deploys.
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
| `NEXUS_SERVICE_ROLE` | Worker | `connector-worker`; leave unset on the API |
| `CONNECTOR_WORKER_TICK_MS` | Worker | `15000` |
| `CONNECTOR_RUN_TIMEOUT_MS` | Worker | `65000` |

`NODE_ENV=production` is already set in the image. Do **not** set `NEXUS_REPOSITORY=review` or `NEXUS_AUTH_MODE=review` on Railway; both are rejected in production.

Without a configured OIDC provider, `/api/health` can still pass and the UI will load, but every authenticated `/api/v1` route returns `OIDC_NOT_CONFIGURED` or `AUTHENTICATION_REQUIRED`. Auth0, Clerk, Keycloak, or another OIDC IdP must issue tokens with `nexus_principal_id`, `nexus_agency_id`, `nexus_agency_name`, `nexus_roles`, `nexus_scopes`, and `nexus_modes`.

The worker pauses until PostgreSQL contains an active or monitoring `live` operational event. Migration `004_live_command_window.sql` inserts the named command owner, agencies, and the SEC Game Day window; `005_scenario_packs_and_detection.sql` binds that window to the `sec_gameday` scenario pack and seeds the detection rules; `006_public_hazard_sources.sql` adds the free National Weather Service and USGS hazard sources to the packs that need them. None of them insert simulated incidents or recommendations. After ingestion, detection opens one incident per qualifying upstream record and one recommendation per incident. If nothing qualifies, the decision queue stays empty. Approval creates agency commitments only; it does not invent observations or control signals.

### Scenario packs

A pack binds an operating window to the feeds it reads, the agent desks it staffs, and the detection rules that may open an incident.

| Pack | Opens for | Reads |
|---|---|---|
| `road_closure` | Everyday mobility operations | City closures, ALDOT ALGO traveler events and travel times, licensed road flow, traffic counts, NWS alerts, USGS stream gauges |
| `sec_gameday` | Event day | The everyday feeds plus transit, parking occupancy, emergency access, and NWS alerts |
| `severe_weather` | Watches, warnings, and hazard impacts | NWS alerts and USGS gauges and seismicity, plus closures, ALGO, and transit |
| `cyber_incident` | Communications and OT disruption | Security alerts plus closures |

A command lead opens and closes windows from the header, or through `GET /api/v1/scenario-packs`, `POST /api/v1/events`, and `POST /api/v1/events/:eventId/close`. Both write operations require the `event:manage` scope. Rules whose connector is not yet built stay dormant rather than producing anything.

### Agent desks

ATLAS, FORGE, AQUA, SENTINEL, PHOENIX, and ECHO each declare the only connectors they may read. A contributing desk must cite evidence IDs. A staffed desk with no permitted feed, or with nothing that bears on the incident, is recorded as `abstained` and named on the decision card. NEXUS composes those findings; it does not author a different action than the playbook.

When `ATLAS_AI_ENABLED` and a Groq (or other OpenAI-compatible) key are set, ATLAS runs a tool loop on each detection heartbeat: list evidence, read rows, compare speed to free flow, draft a cited finding, or stay quiet. A failed or illegal draft falls back to the rule assessor. ATLAS still cannot change a signal, close a road, or publish a message. Approve still only records a human decision.

To see which rules would fire against the live feeds without writing to the database:

```bash
npm run detection:dry-run -- road_closure
```

A client demonstration still requires Auth0 (or another OIDC provider) and browser sign-in. Follow `docs/auth0-client-demo.md`.

### Validation

| Check | Pass condition |
|---|---|
| API health | `GET https://<api-domain>/api/health` returns `200` with `database: "connected"` |
| Worker role | Worker log: `[connector-worker] Authoritative ingestion worker started` listing configured connectors. If it logs `Operational server listening` instead, `NEXUS_SERVICE_ROLE` is unset and the service is a second API |
| Worker health | `GET http://nexus-worker.railway.internal:8080/api/health` returns `role: "connector-worker"` with a recent `lastTickAt` |
| Empty live window | Worker log: `No active live operational event; ingestion paused` until a live event exists |
| City connector | After a live event exists, `GET /api/v1/connectors` shows City of Auburn closures as connected |

### Connected authoritative sources

| Source | Operational classification | Status |
|---|---|---|
| City of Auburn `RoadClosuresPublic` ArcGIS service | Live closure/detour observations | Public connector implemented |
| ALGO Traffic traveler map (`algotraffic.com/map`) | ALDOT events, I-85 travel times, Auburn message signs | Public connector implemented; Auburn operating box only; no cameras or ALEA alerts |
| Auburn Tiger Transit ETA Spot | Live vehicle/route observations | Public connector implemented; explicit production approval flag required |
| ALDOT TDM `TDMPublic` | Auburn/Opelika station counts | Connected as **reference**, not live traffic flow |
| TomTom Traffic Flow | Live road-flow observations | Connector implemented; API key required |
| NOAA National Weather Service (`api.weather.gov`) | Lee County watches, warnings, advisories, and 12-hour gridded forecast | Public connector implemented; no key or agreement |
| USGS stream gauges and earthquake catalog | Lee County stage and discharge, regional seismicity | Public connector implemented; provisional readings, no flood-stage determination |
| Auburn University / FoPark parking occupancy | Restricted lot capacity | Partner interface and data-sharing agreement required |
| Event Command emergency-access state | Restricted corridor status | Approved webhook contract and secret required |

### City asset reference layers

`GET /api/v1/reference-layers` lists the City of Auburn GIS asset geometry the map can overlay, and `GET /api/v1/reference-layers/:code` returns it as GeoJSON, cached server-side for 12 hours and clipped to the operating extent. Two layers ship: `traffic-signals` (93 signalised intersections) and `parking-spaces` (957 inventoried public spaces).

These are asset inventories, not live status. Signals carry no state, timing plan, or preemption, and Nexus cannot control one. Parking spaces carry no occupancy. They are served as map reference and are never ingested as evidence, so nothing here can open an incident.

Deploy the connector worker as a **separate Railway service** using the same image and shared `DATABASE_URL`, with `NEXUS_SERVICE_ROLE=connector-worker` set on it. Do not run the worker inside the API process; separate services provide independent scaling and failure isolation.

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
