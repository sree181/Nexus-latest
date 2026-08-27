# Nexus Operational Graph UI Component Layout

The Operational Graph is a peer workspace to the Command Center. It does not replace the decision queue and does not introduce decorative agent animation. The global switcher clearly separates **Command center** from **Operational graph** while preserving the same live event and identity context.

## Shared workspace frame

| Region | Component | Requirement |
|---|---|---|
| Workspace switcher | `WorkspaceSwitcher` | Two 56 px minimum controls: Command center and Operational graph |
| Context header | `GraphHeader` | `LIVE`, event name, selected graph, meaning, effective time, and generation time |
| Graph selector | `GraphTabs` | Mobility Flow, Decision Lineage, and Agency Coordination; each tab explains its operational question |
| Primary canvas | View-specific component | Uses the full remaining horizontal space and never hides connection or freshness problems |
| Inspector | `EntityInspector` | Selected state, source authority, validity, version, quality flags, and relationships |
| History rail | `GraphTimeline` | Opens append-only node or edge history and highlights state changes rather than animation |

## Mobility Flow

The Mobility Flow view answers: **Where is capacity changing, how can movement propagate, and which protected corridors are at risk?**

```text
┌ LIVE / SEC GAME DAY / MOBILITY FLOW ─────────────────────────────┐
│ Current assets │ relationships │ stale assets │ effective time   │
├ Filters ┬───────────────────────────────┬ Entity inspector ───────┤
│ Roads   │ Road segments / closures      │ Current state           │
│ Lots    │ Parking / staging areas       │ Authoritative source    │
│ Transit │ Stops / routes / vehicles     │ Validity and quality    │
│ Safety  │ Emergency gates / corridors   │ Inbound/outbound edges  │
└─────────┴───────────────────────────────┴──────────────────────────┘
```

`MobilityFlow` groups nodes into operational lanes when no spatial geometry exists. A subsequent map renderer may place the same entities geographically, but node cards remain available for accessibility and for records without geometry. A node card shows classification, label, version, state freshness, and quality warnings. Selecting it opens provenance and relationships.

## Decision Lineage

The Decision Lineage view answers: **Why did Nexus recommend this, who authorized it, what was assigned, and what verified the outcome?**

```text
Evidence → Finding → Incident → Recommendation → Decision → Commitment → Verification
```

`DecisionLineage` is a seven-column, left-to-right causal chain. Each column contains current records of one type. Arrows communicate direction; they do not animate. The inspector exposes the exact evidence version and audit correlation. On smaller screens the chain scrolls horizontally without collapsing or reordering its causal sequence.

## Agency Coordination

The Agency Coordination view answers: **Which organization owns each action, what is waiting, and where is the blocker?**

```text
┌ Event Command ┐  requests / approves  ┌ Traffic Engineering ┐
└───────────────┘ ─────────────────────→ └─────────────────────┘
          │                                       │
          └──── verifies / escalates ─────────────┘
```

`AgencyCoordinationView` presents one accountable card per agency or operational team. It shows incoming requests, outgoing requests, unresolved commitments, due times, and data-quality warnings. Relationships are derived from decisions and commitments, not from synthetic agent messages.

## Component hierarchy

```text
App
├─ WorkspaceSwitcher
├─ OperationalCommandCenter
└─ OperationalGraphWorkspace
   ├─ GraphHeader
   ├─ GraphTabs
   ├─ GraphCanvas
   │  ├─ MobilityFlow
   │  │  ├─ MobilityMetrics
   │  │  ├─ GraphLane[]
   │  │  └─ GraphNodeCard[]
   │  ├─ DecisionLineage
   │  │  ├─ LineageStage[]
   │  │  └─ LineageEntityCard[]
   │  └─ AgencyCoordinationView
   │     └─ AgencyCard[]
   ├─ EntityInspector
   └─ GraphTimeline
```

## State handling

| State | UI behavior |
|---|---|
| Loading | Calm progress message; existing command data remains unaffected |
| Empty | Explicitly states that no authoritative graph batch has been published |
| Storage unavailable | Displays `Graph unavailable` and the safe operational error; never falls back to fabricated records |
| Stale source | Node remains visible with a stale warning and previous valid time |
| Restricted data | Shows classification and ownership; sensitive state fields must be redacted server-side by scope |
| Partial batch | Preserves accepted nodes, identifies rejected relationships, and links to source/run diagnostics |
| Entity selected | Opens inspector; selection is keyboard reachable and does not alter operational state |

The implemented layout uses a 340 px inspector on large screens, three 64 px graph tabs, node cards of at least 84 px, and responsive single-column behavior below 700 px. All primary controls must remain at least 56 px high for the large planar touchscreen.
