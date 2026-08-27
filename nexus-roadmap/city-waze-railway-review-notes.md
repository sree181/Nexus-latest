# City of Auburn and Waze Railway Review Notes

## Verified source behavior

The City of Auburn `RoadClosuresPublic` ArcGIS FeatureServer is an authoritative public snapshot endpoint. It does not expose a verified webhook or push stream in the current integration. Nexus should therefore treat it as a deterministic incremental polling source, using source edit timestamps or canonical hashes plus connector checkpoints to identify changes.

Waze for Cities provides approved partners with a localized XML or JSON GeoRSS data feed that is updated every two minutes. Partners retrieve that feed; it is not a webhook delivered to Nexus. Waze documentation also states that the active alerts feed does not maintain a complete incident lifecycle, so Nexus must persist UUID presence, first/last seen timestamps, state transitions, and expiry on its own.

Waze data access requires an approved Waze for Cities partnership. The program is intended for public authorities that manage traffic or infrastructure and may also approve major event or venue operators. Nexus should not use an unofficial Waze scraping service or represent Waze data as connected before a partner feed URL and permitted polygon are issued.

The opposite direction—City of Auburn data sent to Waze—uses a Waze partner feed. Waze prefers CIFS but can review ArcGIS, WZDx, DATEX II, or other feeds. This outbound publication path is separate from ingesting Waze alerts and jams into Nexus.

## Verified Railway capabilities

Railway supports an always-on background worker as a separate service in the same project as the API. It can share the PostgreSQL connection through reference variables and does not need a public domain. Railway private networking provides internal DNS and encrypted service-to-service communication.

Railway cron is unsuitable for the City/Waze near-real-time path because the minimum cron frequency is five minutes and execution can drift. Waze publishes a two-minute feed and the City feed is better monitored on a short connector cadence. The existing dedicated Nexus connector worker is therefore the correct runtime.

Railway PostgreSQL can remain private and supply `DATABASE_URL` to both API and worker services. Backups, monitoring, and production retention still require explicit configuration.

## Sources

1. https://support.google.com/waze/partners/answer/10618035 — Get traffic data with the Waze Data Feed
2. https://support.google.com/waze/partners/answer/13458165 — Waze Data Feed specifications
3. https://developers.google.com/waze/data-feed/overview — Waze Partner Feed overview
4. https://www.waze.com/wazeforcities/ — Waze for Cities program and eligibility
5. https://docs.railway.com/guides/cron-workers-queues — Railway workers, cron, and queues
6. https://docs.railway.com/cron-jobs — Railway cron behavior and minimum frequency
7. https://docs.railway.com/guides/private-networking — Railway private networking
8. https://docs.railway.com/guides/postgresql — Railway PostgreSQL deployment and connection
