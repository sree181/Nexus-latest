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

## Map-First Hierarchy Refinement

- [x] Reduce the command wall to one display family, one body family, and one monospace family with a fixed six-step type scale
- [x] Make the live map the dominant visual field and move incident, telemetry, desks, and record into clearly subordinate overlays or rails
- [x] Remove competing card weight, decorative density, and redundant metadata from the primary scan path
- [x] Preserve all six workspaces, desk configuration, live data, map behavior, and decision-state interactions
- [x] Verify the refined wall at 2048×1273, 1440×900, and 3840×2160, then run build, unit, and Playwright checks
- [x] Push the final map-first refinement to GitHub and confirm Railway deployment status

## Auburn–Opelika Agent Map

- [x] Verify one appropriate public operational facility and precise coordinate pair for ATLAS, AQUA, SENTINEL, PHOENIX, FORGE, and ECHO
- [x] Center and fit the real interactive map to the Auburn–Opelika operating area while retaining the active incident
- [x] Render each agent as an accessible avatar marker with role, facility, address, state, and evidence details
- [x] Keep every non-map surface and workflow unchanged
- [x] Validate map controls, marker interactions, responsive framing, build, unit tests, and wall browser tests
- [ ] Push the contained map update to GitHub and confirm Railway deployment

## Notification and Auburn Business Branding

- [x] Verify the official Auburn business-school logo asset and Auburn navy/orange brand treatment
- [x] Convert the operations warning into a compact, non-intrusive notification without changing its content or behavior
- [x] Display “Nexus Coordinate” clearly beside the Auburn business-school logo using a map-complementary Auburn color treatment
- [x] Confirm the map, markers, desks, timeline, navigation, and all other workspaces remain unchanged
- [ ] Run visual, build, unit, and wall browser validation, then push and confirm Railway deployment

## Full-Screen Agent Desks

- [x] Make the shared Agent Desk overlay occupy the complete viewport for ATLAS, AQUA, SENTINEL, PHOENIX, FORGE, and ECHO
- [x] Hide the wall’s top feeds/KPI strip whenever an Agent Desk is open
- [x] Add an accessible collapse/expand control for the stages 1–6 navigation rail while a desk is open
- [x] Reorganize Prompt, Model, Tools, and Policies into a professional structured configuration workspace inspired by CrewAI without copying it
- [x] Preserve each agent’s existing content, fields, handlers, save/cancel behavior, and constraints
- [x] Confirm map, markers, notification, branding, desks list, timeline, and non-desk workspaces remain unchanged
- [x] Test every agent desk plus build, unit, and browser suites, then push and confirm Railway deployment

## Stage 2 Deliberation Refinement

- [x] Keep Stage 2 as a large workspace and limit all changes to the Deliberation presentation
- [x] Align the Stage 2 Nexus Coordinate header with the Auburn-branded home treatment
- [x] Add each agent avatar beside its name and each desk logo beside a clear desk title
- [x] Replace truncated action, priority, evidence, and dissent text with legible multi-line content
- [x] Distinguish Contributed and Abstained using accessible icons and restrained status colors
- [x] Preserve all Stage 2 data, decisions, timestamps, navigation behavior, and right-side composition content
- [x] Confirm every non-Deliberation surface is unchanged, run build/unit/browser validation, then push and verify deployment

## Stage 3 Evidence Lineage Sankey

- [x] Keep all changes confined to Stage 3 Evidence Lineage and preserve the existing lineage records and inspector behavior
- [x] Replace the card-column lineage with a true Sankey layout using a dedicated visualization library
- [x] Color-code stages and flows consistently across evidence, agents/findings, incident, recommendation, decision, commitments, and verification
- [x] Make stage names, stakeholder/agent identity, decision text, commitments, and verification states explicit and readable
- [x] Preserve click-to-inspect lineage tracing with clear active-path emphasis and subdued unrelated flows
- [x] Validate Stage 3 at desktop and command-wall resolutions, run build/unit/browser suites, and confirm every other stage is unchanged
- [ ] Push the contained Stage 3 update and verify Railway deployment

## Stage 3 Sankey Fit and Hierarchy Refinement

- [x] Keep every change confined to Stage 3 Evidence Lineage
- [x] Hide the Nexus logo/header identity only while Stage 3 is active
- [x] Give the Sankey the full content width and keep all stages, nodes, labels, and links inside the visible screen
- [x] Replace the full-height Record Inspector column with a compact contextual box that appears without consuming the canvas
- [x] Arrange the Stage 3 title, selected-path context, and system/citation status as a clear top-to-bottom hierarchy
- [x] Normalize Stage 3 typography to a consistent display, body, and data-role scale
- [x] Validate default and selected-path states at desktop and 4K, rerun build/unit/browser suites, and confirm other stages are unchanged
- [ ] Push the contained refinement and verify Railway deployment
