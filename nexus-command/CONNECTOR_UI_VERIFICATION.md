# Nexus Authoritative Connector Verification

Verification was completed on **August 26, 2026** against an isolated PostgreSQL operational event. The process called the real public upstreams, persisted normalized evidence, exposed the evidence through `/api/v1`, and rendered it in the command center. No synthetic fallback was enabled.

| Authoritative source | Result | Persisted/observed behavior |
|---|---|---|
| City of Auburn `RoadClosuresPublic` | Connected | Five current or upcoming closure/detour records were persisted with authority URI, source event ID, content hash, geometry where provided, and source timestamps. |
| Auburn Tiger Transit ETA Spot | Connected | Fourteen real vehicle observations were persisted during the manual verification run; subsequent scheduled runs updated changing vehicles and deduplicated unchanged records. |
| ALDOT TDM `TDMPublic` | Connected as reference | Thirty-six Auburn/Opelika traffic-count station records were persisted and explicitly classified as reference rather than live road flow. |
| TomTom Traffic Flow | Configuration required | The connector is implemented but remained disabled because the verification environment did not contain `TOMTOM_API_KEY`. |
| Auburn University / FoPark parking occupancy | Permission required | Nexus displays the source and its restricted classification but does not claim connectivity without a partner interface and data-sharing agreement. |
| Event Command emergency access | Permission required | Nexus displays the restricted source boundary but accepts no dispatch, patient, or law-enforcement-sensitive data without an approved webhook contract. |

The scheduled worker was tested against the same live event. It used cadence-bucket idempotency keys, updated changed transit observations, counted unchanged closure and station records as duplicates, and recovered from transient upstream failures after bounded retry was added. Connector APIs, source health, recent authoritative observations, and partner/configuration states were visible in the operational snapshot.

The responsive command center was verified interactively. It rendered Tiger Transit markers on the Auburn basemap, presented closure/detour geometry when supplied, reported authoritative observation counts, showed all six source cards in one horizontally scrollable row, and distinguished **connected**, **configuration required**, and **permission required** states. The operational UI contains no simulation, reward, episode, timestep, exploration, or synthetic-agent controls.

The final automated quality gate passed **24 tests**, complete frontend and server TypeScript checks, production frontend/server/worker builds, executable PostgreSQL-compatible migrations, and `npm audit --omit=dev` with zero production dependency vulnerabilities.
