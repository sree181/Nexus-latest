# Nexus Operational Graph UI Verification

## Verification context

- Viewport: 1920×1080
- Event: SEC Game Day Mobility — Connector Verification
- Repository: PostgreSQL-backed operational and temporal graph repositories
- Data basis: persisted City of Auburn Road Closures and Auburn Tiger Transit ETA Spot evidence

## Mobility Flow

- The graph workspace is visually distinct from the Command Center while sharing the event and live-operation context.
- Four real authoritative nodes are visible: Toomer Street closure, affected road segment, South Donahue transit route, and Tiger Transit vehicle 21-153.
- Two real relationships are present: closure restricts road segment; vehicle is assigned to route.
- Classification labels, graph versions, entity counts, relationship counts, and stale-state counts are readable in one 1920×1080 viewport.
- The right-side inspector remains available without covering the graph canvas.
- Primary workspace, graph-tab, node-card, inspector-close, and history controls meet the 56px large-touch requirement.

## Decision Lineage

- The layout preserves the intended evidence-to-verification workspace and event context.
- No lineage records are displayed because the verification event has no human decision chain; the UI explicitly states this rather than fabricating records.
- The empty state is centered, calm, and leaves the inspector and history regions consistent with Mobility Flow.

## Agency Coordination

- Browser interaction confirmed the third graph tab and its accountable-ownership purpose.
- The verification event has no approved agency commitments, so the view shows an honest empty state rather than synthetic coordination messages.

## Inspector and history

- Selecting the City closure shows its current state, authority URI, validity, classification, version, and `restricts` relationship.
- The history control is disabled until a previous authoritative state version exists; version 1 correctly remains the current authoritative state.
