# Nexus Coordinate — Production Implementation TODO

- [x] Extract and verify the user-provided Nexus source archive in an isolated working tree
- [x] Assess the current frontend, server, simulation dependencies, package scripts, and deployment configuration
- [x] Define and implement the durable operational database schema and migration path
- [x] Implement versioned live operational APIs for events, sources, evidence, incidents, recommendations, decisions, commitments, execution confirmations, audit, and realtime updates
- [x] Implement approval state transitions, optimistic concurrency, idempotency, stale-evidence checks, and live/training isolation
- [x] Remove simulation-only language, controls, rewards, timesteps, exploration indicators, and scenario execution from the operational UI
- [x] Build the production SEC Game Day command-center layout with status rail, operational map, situation brief, decision queue, approval review, and commitment rail
- [x] Ensure all primary interactions are touch-friendly and keyboard accessible
- [x] Add unit and integration tests for domain rules and APIs
- [x] Verify TypeScript, build, tests, API behavior, degraded-source states, and responsive UI
- [x] Package the updated source and document required database, identity, and agency connector configuration

## Authoritative Real-Data Connectors

- [x] Assess the operational repository and source-health extension points for connector ingestion
- [x] Identify authoritative traffic, parking, transit, closure, weather, and emergency-access feeds with ownership and access constraints
- [x] Implement a connector SDK with normalization, provenance, source freshness, retry, rate-limit, and health contracts
- [x] Implement authenticated ingestion APIs and persistence for source observations
- [x] Connect immediately available public authoritative feeds without synthetic fallback
- [x] Add disabled-by-default adapters for partner-gated parking, transit, road-control, and emergency-access systems
- [x] Update the command center to distinguish live, delayed, unavailable, and not-connected sources
- [x] Add connector, normalization, ingestion, idempotency, source-health, and degraded-mode tests
- [x] Package the connector-enabled source and document every credential or agency agreement still required

## Temporal Operational Graph

- [x] Assess current operational schema, connector observations, API router, authorization scopes, and command-center extension points
- [x] Implement graph nodes, graph edges, graph observation links, and append-only node/edge state-change history in PostgreSQL
- [x] Add mode isolation, ownership, validity intervals, optimistic versions, evidence linkage, referential constraints, and graph indexes
- [x] Provide executable SQL queries for current subgraphs, mobility impact paths, decision lineage, agency handoffs, source provenance, and state history
- [x] Implement authenticated, scoped, idempotent graph node/edge ingestion APIs for authoritative sources
- [x] Implement graph snapshot, neighborhood, path, lineage, coordination, and state-history query APIs
- [x] Define frontend graph contracts and component layouts for Mobility Flow, Decision Lineage, and Agency Coordination
- [x] Add migration and graph API tests, type-check, build, and package the graph-enabled source

## Command-Wall Visual Correction

- [x] Confirm the user-visible runtime is serving the latest GitHub `main` commit and identify any deployment or cache mismatch
- [x] Compare the supplied wall screenshot against the committed presentation layer and document why the prior update reads as only incremental
- [x] Replace the existing wall composition with a visibly differentiated operational layout while preserving all six workspaces and live behavior
- [x] Establish a stronger brand masthead, incident-led spatial hierarchy, route-based navigation, and visually dominant map/workspace canvas
- [x] Verify the revised wall at 3840×2160 and 1440×900, including evidence, workflow, and desk interactions
- [x] Run build, unit, and Playwright checks, then push the corrective commit to GitHub
