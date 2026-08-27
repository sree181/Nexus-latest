# Nexus Coordinate — Transformation Roadmap

- [x] Define real-world responsibilities, data sources, escalation rules, and human approvals for each of the seven agents
- [x] Define the real-time event, alerting, recommendation, and operator-control model
- [x] Design the next-generation multi-agency command-center UX for large touch displays and operator workstations
- [x] Compare integration and hosting approaches for a reliable live operational platform
- [x] Produce a phased implementation roadmap with partnership dependencies and pilot milestones

## SEC Game Day Mobility Pilot

- [x] Validate the first pilot’s public, campus, city, and state mobility data inputs
- [x] Define the SEC Game Day operating timeline, agent roles, human approvals, and escalation workflow
- [x] Specify real-time command-center screens, decision cards, touch interactions, and outcome metrics
- [x] Deliver the pilot blueprint and confirm the first implementation slice

## Human-Approved Advisory UX

- [x] Define stakeholder roles, permissions, and tailored operational workspaces
- [x] Specify decision-card, approval, escalation, execution-confirmation, and audit interaction flows
- [x] Define large-touch-display, accessibility, live/training safety, and data-freshness requirements
- [x] Deliver the reusable Nexus human-approved advisory UI/UX specification and first redesign slice

## Real-World Architecture and Decision Workflow

- [x] Define production services, data flows, trust boundaries, persistence, event processing, and agency execution gateways
- [x] Specify versioned REST/SSE API endpoints for incidents, evidence, recommendations, approvals, commitments, execution, audit, and source health
- [x] Produce a draft OpenAPI contract with human-approval and idempotency safeguards
- [x] Create the SEC Game Day live decision-card wireframe, component hierarchy, and approval state machine
- [x] Deliver the technical package with a prioritized implementation sequence

## Simulation-Free Production Implementation

- [x] Recover and assess the current Nexus repository without modifying CyberPulse
- [x] Extract and verify the user-provided `nexus-command-master.zip` as the isolated Nexus working tree
- [x] Implement durable operational schema for agencies, users, events, sources, evidence, incidents, findings, recommendations, approvals, decisions, commitments, execution confirmations, idempotency, and audit
- [x] Implement versioned API contracts and business logic for live incident, evidence, recommendation, human approval, commitment, verification, and realtime workflows
- [x] Remove simulation language and simulation-only controls from the operational application
- [x] Build an enterprise SEC Game Day command-center UI with live status, evidence freshness, decision queue, named authority, approval review, and agency commitments
- [x] Add unit and integration tests for state transitions, idempotency, mode isolation, stale evidence, authorization, and audit behavior
- [x] Verify large-screen touch interactions, desktop responsiveness, accessibility, and error/degraded-source states
- [ ] Commit and push the Nexus changes separately from CyberPulse

## Authoritative Connector Implementation

- [x] Connect City of Auburn public road closures and detours
- [x] Connect Auburn Tiger Transit ETA Spot vehicle observations with explicit deployment approval
- [x] Connect ALDOT Auburn/Opelika station counts as reference data
- [x] Add optional TomTom live road-flow integration requiring an API key
- [x] Add permission-gated parking occupancy and emergency-access adapter boundaries
- [x] Add persistent connector runs, checkpoints, provenance, health, retries, idempotent worker claims, API controls, tests, and command-center rendering
- [x] Document remaining credentials and agency agreements

## Temporal Operational Graph

- [x] Implement PostgreSQL graph nodes, directed edges, evidence links, validity intervals, mode isolation, optimistic versions, ingestion idempotency, and append-only state history
- [x] Provide executable mobility, impact-path, decision-lineage, agency-coordination, provenance, and state-history SQL queries
- [x] Implement authenticated graph ingestion, snapshot, neighborhood, lineage, coordination, and history APIs
- [x] Implement and verify the Mobility Flow, Decision Lineage, and Agency Coordination component layouts
- [x] Validate the OpenAPI contract, migrations, real authoritative graph ingestion, UI, tests, builds, and dependency audit
- [x] Package the graph-enabled Nexus source and technical specifications

## City of Auburn and Waze Railway Configuration Review

- [x] Verify City of Auburn source delivery capabilities and distinguish polling from true streaming
- [x] Verify Waze for Cities access, feed delivery, licensing, and partnership requirements
- [x] Define source-to-graph normalization, idempotency, freshness, and temporal update behavior
- [x] Map the API, connector worker, PostgreSQL, realtime UI, secrets, and scheduling model to the existing Railway application
- [x] Deliver a no-change configuration guide with prerequisites and phased rollout
