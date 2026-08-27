# Nexus City of Auburn and Waze Near-Real-Time Ingestion on Railway

**Author:** Manus AI  
**Scope:** Architecture and configuration guidance only. No Nexus source code, database, or Railway service was changed.

## Executive conclusion

Most of the runtime is already suitable for the current Railway project. Nexus already has a production API, PostgreSQL migrations, an always-on connector worker artifact, connector run/checkpoint persistence, source-health reporting, temporal graph tables, idempotent graph batch APIs, and a realtime frontend update path. The City of Auburn public road-closure connector can run on Railway now. Waze cannot be enabled through configuration alone because the deployment first needs an approved **Waze for Cities** partner feed URL and a Waze-specific connector/parser. Waze delivers an inbound localized XML or JSON GeoRSS snapshot that is updated every two minutes; it is not a webhook stream into Nexus.[1]

> **Operational definition:** For these sources, “real-time streaming” means an always-on Railway worker repeatedly retrieving authoritative or approved source snapshots, detecting changes, persisting evidence and graph state, and then pushing Nexus UI updates immediately. The upstream City and Waze interfaces are pull feeds, not true server-push streams.

## Source access reality

| Source | Delivery model | Realistic cadence | Access condition | Nexus authority treatment |
|---|---|---:|---|---|
| City of Auburn `RoadClosuresPublic` ArcGIS | Public FeatureServer snapshot polled by Nexus | 60 seconds | Public endpoint | Authoritative for published City closures and detours |
| Waze for Cities Data Feed | Partner-specific XML or JSON GeoRSS feed retrieved by Nexus | Feed updated every 2 minutes[1] | Approved Waze for Cities partnership and polygon/feed URL | Crowdsourced corroborative alert/jam evidence; not agency authorization |
| City-to-Waze partner feed | City publishes CIFS, ArcGIS, WZDx, or reviewed format for Waze to fetch | On change or partner schedule | Waze Partner Hub review | Outbound publication, separate from Nexus ingest |

Waze for Cities is available to public authorities managing transportation or infrastructure and may approve major event or venue operators. Application and Partner Hub approval are required; Waze states that the program itself is free.[2] Waze also states that its active alerts feed does not provide a complete incident lifecycle, so Nexus must maintain first-seen, last-seen, missing, superseded, and expired state itself.[3]

## End-to-end ingestion chain

| Stage | City of Auburn | Waze | Nexus behavior |
|---|---|---|---|
| 1. Acquire | Query ArcGIS FeatureServer layers with GeoJSON output | Retrieve approved GeoRSS JSON/XML feed URL | Worker uses bounded timeout, retry, backoff, and source-specific cadence |
| 2. Normalize | Preserve `GlobalID`/record URI, dates, geometry, road, direction, department, lane impact | Preserve Waze UUID, alert/jam type, reliability/confidence, location/line geometry, speed/delay, publication time | Convert to a common observation envelope; never discard source identity |
| 3. Persist evidence | Upsert deterministic City observation identity | Upsert deterministic Waze UUID or stable jam identity | Store `observed_at`, `received_at`, authority URI, raw hash, normalized attributes, quality flags, and geometry |
| 4. Project graph | Create/update closure and affected road-segment nodes; `closure RESTRICTS road_segment` edge | Create/update alert, jam, or irregularity node; `AFFECTS road_segment` edge | Graph writes are event/mode/source scoped, versioned, and idempotent |
| 5. Correlate | City closure remains official source | Waze report may corroborate or challenge operational conditions | Create `CORROBORATES` only when spatial, temporal, and semantic thresholds pass; never auto-promote Waze to official closure |
| 6. Expire | Use explicit closure end date when structured; otherwise last-seen policy | Maintain lifecycle because Waze does not provide complete state transitions[3] | Close validity after a configured number of missed source snapshots; preserve append-only history |
| 7. Notify | Committed evidence/graph transaction emits outbox event | Same | API/SSE pushes changed source and graph state to authenticated clients |

## Graph node and edge mapping

### City of Auburn

| Source object | Graph entity | Stable key | Important state |
|---|---|---|---|
| Published closure | `closure` node | `city-closure:{GlobalID}` | road, direction, reason, department, lane impact, start/end, status |
| Affected roadway | `road_segment` node | official segment ID when available; otherwise a documented normalized road/geometry key | name, geometry, operational direction, capacity classification |
| Closure impact | `restricts` edge | `city-restricts:{closureGlobalId}:{segmentKey}` | lane effect, full/partial status, validity interval |

### Waze for Cities

| Source object | Graph entity | Stable key | Important state |
|---|---|---|---|
| Driver alert | `traffic_alert` node | `waze-alert:{uuid}` | type/subtype, reliability, confidence, report rating, publication time, coordinates |
| Jam | `traffic_jam` node | Waze jam identifier when supplied; otherwise approved stable OpenLR/line signature | speed, delay, length, severity, line geometry |
| Irregularity | `traffic_irregularity` node | Waze identifier | trend, detection time, severity, geometry |
| Matched roadway | `road_segment` node | official City/ALDOT segment ID or governed conflation key | geometry and current operational state |
| Source impact | `affects` edge | `{wazeEntityKey}:{segmentKey}` | confidence, spatial match method, valid interval |
| Cross-source support | `corroborates` edge | `{wazeEntityKey}:{cityEntityKey}` | distance, time difference, semantic match, confidence |

Road matching should prefer an official City or ALDOT segment identifier. Geometry snapping is acceptable only when the matching method and confidence are persisted. A failed or ambiguous match should leave the Waze entity as an unmatched graph node rather than inventing a relationship.

## Lifecycle and freshness rules

| Rule | City recommendation | Waze recommendation |
|---|---:|---:|
| Worker wake interval | 15 seconds | 15 seconds |
| Source fetch cadence | 60 seconds | 120 seconds, aligned with Waze feed update frequency[1] |
| Stale warning | 5 minutes without successful City refresh | 5 minutes without successful Waze refresh |
| Missing-record grace | Two completed City fetches, unless an explicit end date is reached | Two or three completed Waze feed snapshots because active alerts do not have a complete lifecycle[3] |
| Graph expiry | Set `valid_until`; do not delete | Set `valid_until`; do not delete |
| Historical retention | City/event retention policy | Must follow Waze partner terms and attribution/retention rules |

Polling more frequently than the two-minute Waze publication interval does not make Waze data fresher. It mainly repeats identical payloads and raises unnecessary upstream and processing load. The current Nexus idempotency and canonical hashing would prevent duplicate graph versions, but a 120-second Waze fetch cadence is the appropriate starting point.

## Railway deployment topology

Railway explicitly supports a continuous background worker as a separate service in the same project as the API and recommends that pattern for realtime event processing and streaming pipelines. Services can share database credentials and communicate over encrypted private networking.[4] Railway cron is not appropriate here because its minimum interval is five minutes and scheduled starts may drift by several minutes.[4] [5]

| Railway service | Public domain | Start command | Responsibility | Deployable now? |
|---|---|---|---|---|
| Nexus API + frontend | Yes | Existing image default / `npm start` | OIDC API, migrations, graph queries, SSE, command center | **Yes** |
| PostgreSQL | No; private project service | Railway PostgreSQL | Evidence, connector runs, graph current state/history, audit, outbox | **Yes** |
| Nexus connector worker | No | `npm run start:connector-worker` | City, transit, ALDOT, TomTom, and future Waze polling | **Yes** |
| Waze connector adapter | Runs inside connector worker | No separate service | Parse approved GeoRSS feed and normalize alerts/jams/irregularities | **Not yet; adapter + partner feed required** |
| Optional queue | No | Redis/Postgres queue worker | Needed only if volume or decoupled retries outgrow current DB claims/outbox | **Not required for pilot** |

Railway allows an always-on worker in the same project and automatic restart on failure. The worker should have no public domain. It should share the same private `DATABASE_URL` as the API and use Railway reference variables. Railway PostgreSQL can remain private; backups, retention, and monitoring should be configured before the operational pilot.[4] [6]

## Existing Nexus readiness

| Capability | Current state | Configuration-only? |
|---|---|---:|
| API/frontend container | Built and production-ready | Yes |
| Connector-worker artifact | `dist-server/connectors/worker.js` and `npm run start:connector-worker` already exist | Yes |
| City road-closure connector | Implemented with 60-second cadence and provenance | Yes |
| PostgreSQL source/evidence/graph migrations | Implemented | Yes |
| Graph batch and query APIs | Implemented | Yes |
| SSE/UI source and graph updates | Implemented | Yes |
| Waze partnership/feed URL | Not available in current configuration | No; stakeholder action required |
| Waze parser/normalizer | Not implemented in the current package | No; small engineering addition required |
| Automatic evidence-to-graph projector | Current graph API exists, but connector observations are not automatically projected for every source | No; engineering addition required |
| Road-segment conflation index | No governed City/ALDOT road-segment index is configured | No; data governance and engineering required |

Therefore, approximately **70–80% of the platform runtime** can run in the existing Railway project: API, UI, PostgreSQL, migrations, City ingestion, connector scheduling, provenance, graph persistence/query, source health, and realtime client updates. The remaining work is not infrastructure. It is Waze authorization and source-specific engineering: feed parser, lifecycle adapter, road-segment matching, and automatic evidence-to-graph projection.

## Railway environment configuration

The current deployment can use the existing variables below. Proposed Waze variables are clearly marked because they are not currently implemented.

| Service | Variable | Status | Purpose |
|---|---|---|---|
| API + worker | `DATABASE_URL` | Existing | Shared private PostgreSQL connection |
| API + worker | `NEXUS_REPOSITORY=postgres` | Existing | Requires persistent operational repository |
| API | `NEXUS_AUTH_MODE=oidc` and OIDC issuer/audience/JWKS values | Existing | Verified operator identities and graph scopes |
| Worker | `CONNECTOR_WORKER_TICK_MS=15000` | Existing | Checks which connectors are due; does not force every connector to fetch every 15 seconds |
| Worker | `CONNECTOR_RUN_TIMEOUT_MS=65000` | Existing | Bounded connector execution |
| Worker | `WAZE_FOR_CITIES_FEED_URL` | **Proposed** | Secret partner GeoRSS feed URL |
| Worker | `WAZE_FOR_CITIES_FORMAT=json` | **Proposed** | Approved feed format |
| Worker | `WAZE_FOR_CITIES_APPROVED=true` | **Proposed** | Explicit deployment approval gate, analogous to Tiger Transit |
| Worker | `WAZE_FETCH_CADENCE_SECONDS=120` | **Proposed** | Match official feed update cadence |
| Worker | `WAZE_DATA_RETENTION_DAYS` | **Proposed** | Enforce the signed partner agreement |

The Waze feed URL should be treated as a credential, stored only in Railway service variables, and never sent to the browser or written to logs. Graph ingestion should use either an in-process graph projection service inside the worker or a service-account JWT sent to the API over Railway private networking. Browser credentials must never be reused by the worker.

## Exact Railway project configuration

No source or Railway change was made during this review. When the team is ready, the existing Railway project can be arranged as follows.

| Step | Railway action | Expected result |
|---:|---|---|
| 1 | Keep the current Nexus web/API service on the existing Dockerfile and public domain. Keep `/api/health` as its health check. | The browser, OIDC API, migrations, SSE, graph queries, and static frontend remain one deployment. |
| 2 | Add or retain a private PostgreSQL service in the same project/environment. Reference its `DATABASE_URL` from both API and worker services. | Both services use the same operational, connector, evidence, audit, and temporal-graph state. |
| 3 | Create a second service from the same repository and Dockerfile. Override only its start command with `npm run start:connector-worker`. Do not assign it a public domain. | The compiled worker artifact polls authoritative feeds continuously and survives browser/API restarts independently. |
| 4 | Give the worker `DATABASE_URL`, `NEXUS_REPOSITORY=postgres`, `CONNECTOR_WORKER_TICK_MS=15000`, `CONNECTOR_RUN_TIMEOUT_MS=65000`, and each approved source variable. | The worker wakes every 15 seconds but fetches only connectors whose source-specific cadence bucket is due. |
| 5 | Keep Waze variables absent until Partner Hub approval and adapter acceptance testing are complete. | The UI truthfully shows Waze as not connected rather than silently substituting another source. |
| 6 | Configure restart-on-failure for API and worker. Configure PostgreSQL backups and operational monitoring. | A process crash is recoverable; persisted checkpoints and idempotency prevent duplicate graph versions. |
| 7 | Ensure the database has one active `live` operational event during the monitored game-day window. | The worker pauses outside a live event rather than writing observations into an undefined context. |

The current Railway configuration file supplies the Dockerfile builder, API health path, and restart-on-failure policy. The Docker image already contains both `dist-server/index.js` and `dist-server/connectors/worker.js`; therefore, a second build pipeline is unnecessary. The worker service needs only a different start command and shared variables.

### Operational validation after configuration

| Validation | Pass condition |
|---|---|
| API health | `/api/health` reports operational repository and database availability |
| Worker startup | Logs list configured connectors and show no public listening port |
| Connector status | Authorized `GET /api/v1/connectors` shows City as connected and Waze as permission/configuration required until enabled |
| City ingestion | A new connector run stores evidence with City authority URI, observed/received timestamps, and source record identity |
| Graph projection | A changed closure increments the graph node/edge version and creates append-only history rather than a duplicate entity |
| Idempotency | Replaying the same source batch produces no new graph version |
| UI update | Authenticated clients receive the committed outbox event and refresh source/graph state |
| Degraded source | Timeout or upstream failure changes source health without deleting the last verified graph state |

## Two viable rollout approaches

| Approach | Tradeoffs | Cost | Setup Complexity |
|---|---|---|---|
| **Always-on connector worker in the existing Railway project** | Best latency; matches 60-second City and 120-second Waze cadence; uses current idempotent run claims and shared PostgreSQL. The worker continuously consumes resources. | One additional always-on Railway service plus existing PostgreSQL usage | Medium; City is configuration-only, while Waze requires partnership and adapter work |
| **Railway cron every five minutes** | Lighter service footprint but too slow for the two-minute Waze feed, can drift, and skips a run if the prior execution overlaps.[4] [5] Suitable only for non-operational reporting or fallback health checks. | Lower compute usage | Low |
| **True webhook ingestion** | Lowest latency, but neither the verified City ArcGIS endpoint nor inbound Waze Data Feed has been confirmed to push webhooks to Nexus. Appropriate only for future agency systems that explicitly support signed callbacks. | Depends on event volume | Medium to high; requires sender contract, signature verification, replay protection, and allowlisting |

For the SEC Game Day live command center, the always-on worker is the realistic choice. Railway cron is a lighter alternative for demonstrations or historical collection but should not be described as realtime.

## Recommended rollout

The first release should deploy the existing API, private PostgreSQL, and dedicated worker; enable City road closures; verify source freshness and graph updates; and operate for several non-game days. The second release should obtain a Waze for Cities partnership through an eligible agency or event operator, review attribution/retention terms, and validate the issued feed manually. The third release should add the Waze adapter and evidence-to-graph projection, then run shadow observation during one game without using Waze to authorize actions. Only after accuracy, lifecycle behavior, spatial matching, and stakeholder governance are accepted should Waze evidence inform human-reviewed recommendations.

## References

[1]: https://support.google.com/waze/partners/answer/10618035?hl=en "Get traffic data with the Waze Data Feed"
[2]: https://www.waze.com/wazeforcities/ "Waze for Cities and Event Partners"
[3]: https://support.google.com/waze/partners/answer/13458165?hl=en "Waze Data Feed specifications"
[4]: https://docs.railway.com/guides/cron-workers-queues "Choose Between Cron Jobs, Background Workers, and Queues"
[5]: https://docs.railway.com/cron-jobs "Railway Cron Jobs"
[6]: https://docs.railway.com/guides/postgresql "Railway PostgreSQL"
[7]: https://docs.railway.com/guides/private-networking "Railway Private Networking"
[8]: https://developers.google.com/waze/data-feed/overview "Waze Partner Feed Overview"
