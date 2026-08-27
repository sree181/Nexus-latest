# SEC Game Day Live Decision Card — Component Structure

## 1. Page Hierarchy

```text
<LiveOperationsShell mode="live">
  <OperationalStatusRail
    eventPhase
    commandOwner
    sourceHealthSummary
    criticalIncidentCount
    localClock
  />

  <CommandWorkspace>
    <SituationPane>
      <IncidentHeader />
      <PlainLanguageBrief />
      <AffectedServices />
      <ContributingAgentDomains />
      <PrimaryOwnerAndExpiry />
    </SituationPane>

    <OperationalMapPane>
      <SourceFreshnessLegend />
      <IncidentLayer />
      <EmergencyCorridorLayer />
      <ClosureAndRouteLayer />
      <ParkingCapacityLayer />
      <TransitVehicleLayer />
      <TrafficFlowLayer />
      <DecisionImpactLayer />
    </OperationalMapPane>

    <DecisionQueuePane>
      <DecisionQueueFilters />
      <LiveDecisionCard>
        <DecisionStateHeader />
        <WhatChanged />
        <WhyItMatters />
        <RecommendedAction />
        <ExpectedEffectAndLimitations />
        <OperationalConstraints />
        <EvidenceFreshnessSummary />
        <ApprovalRequirements />
        <DecisionActionBar />
      </LiveDecisionCard>
    </DecisionQueuePane>
  </CommandWorkspace>

  <AgencyCommitmentRail>
    <CommitmentItem state="requested" />
    <CommitmentItem state="acknowledged" />
    <CommitmentItem state="approved" />
    <CommitmentItem state="executing" />
    <CommitmentItem state="verified" />
  </AgencyCommitmentRail>

  <EvidenceDrawer />
  <DecisionReviewSheet />
  <RevisionRequestSheet />
  <DelegationSheet />
  <EscalationSheet />
</LiveOperationsShell>
```

## 2. `LiveDecisionCard` Contract

| Prop | Type | Purpose |
|---|---|---|
| `recommendation` | `Recommendation` | Exact versioned action, rationale, effect, constraints, evidence, and expiry |
| `incident` | `IncidentSummary` | Current plain-language incident and command context |
| `approvalState` | `ApprovalState` | Required roles, quorum, acknowledgement, and decision history |
| `sourceHealth` | `SourceHealthSummary[]` | Freshness and availability of every material source |
| `currentUserCapabilities` | `CapabilitySet` | Server-returned operations the current user is permitted to request |
| `onReview` | `() => void` | Opens the deliberate review/confirmation sheet |
| `onReject` | `() => void` | Opens structured rejection reasons |
| `onRequestRevision` | `() => void` | Opens bounded revision request flow |
| `onDelegate` | `() => void` | Opens eligible approver selection |
| `onEscalate` | `() => void` | Opens authority/safety escalation flow |
| `onOpenEvidence` | `(evidenceId) => void` | Opens lineage, quality, and audit detail |

## 3. Frontend State Model

| State | Owner | Frontend behavior |
|---|---|---|
| **Server resource state** | REST resources and SSE invalidation events | Query cache stores incidents, recommendations, source health, decisions, and commitments by ID/version. |
| **Selection state** | URL or workspace context | Selected operational event, incident, recommendation, and map focus are URL-addressable. |
| **Review state** | Local transient UI | Confirmation sheet, selected action, reason/comment, and exact confirmation-text hash. |
| **Touch interaction state** | Local transient UI | Pressed/selected/focus state only; never treated as approval. |
| **Realtime state** | SSE cursor | Event ID resumes after reconnect; SSE invalidates/refetches resources rather than becoming operational truth. |

## 4. API Bindings

| Component/action | API |
|---|---|
| `OperationalStatusRail` | `GET /system/status`, `GET /operational-events/{eventId}`, SSE `source.health.changed` |
| `SituationPane` | `GET /incidents/{incidentId}` |
| `OperationalMapPane` | `GET /operational-events/{eventId}/evidence`, incident resource, source health |
| `DecisionQueuePane` | `GET /decision-queue?operationalEventId=...` |
| `LiveDecisionCard` | `GET /recommendations/{recommendationId}` |
| `EvidenceDrawer` | `GET /evidence/{evidenceId}` and role-filtered audit endpoints |
| Approve/reject/revise/delegate/escalate | `POST /recommendations/{recommendationId}/decisions` |
| `AgencyCommitmentRail` | `GET /incidents/{incidentId}/commitments`, SSE commitment updates |
| Commitment acknowledgement/status | `POST /commitments/{id}/acknowledgements` and `/status-transitions` |
| Execution handoff | Validate, then create `/commitments/{id}/execution-requests` |

## 5. Decision Review Sheet

The approval button opens a review sheet; it does not approve immediately.

| Region | Required content |
|---|---|
| **Exact action** | Bounded advisory action in one sentence, including what it does *not* do |
| **Current evidence** | Source names, freshness, material conflicts, and evidence snapshot version |
| **Constraints** | Emergency route, ADA, closure, policy, authority, and data-quality limits |
| **Resulting commitments** | Tasks created after approval, with agencies, owners, and due times |
| **Required authority** | Current user role/agency, satisfied and remaining quorum, delegation rule |
| **Confirmation** | Deliberate touch-safe control submitting recommendation version, evidence version, expected state, reason, and confirmation-text hash |

## 6. Component Rules

1. The client renders actions from server-returned capabilities, but the server reauthorizes every command.
2. A decision card is immutable for a recommendation version. Material evidence creates a new or superseding version.
3. When the API returns `412 evidence_changed`, the sheet opens a comparison state; it never retries automatically.
4. Stale sources cause a policy-defined warning, explicit override, or approval block.
5. `approved` and `verified` use different visuals: approval indicates authority; verification indicates observed completion.
6. Agent chips show domain contributors and conflicts; they do not replace the incident owner or approvers.

## 7. Suggested TypeScript Interfaces

```ts
type OperationalMode = "live" | "training" | "replay";
type DecisionAction = "approve" | "reject" | "request_revision" | "delegate" | "escalate";
type RecommendationState =
  | "draft"
  | "awaiting_acknowledgement"
  | "awaiting_approval"
  | "approved"
  | "rejected"
  | "revision_requested"
  | "delegated"
  | "escalated"
  | "expired"
  | "superseded";

interface LiveDecisionCardModel {
  recommendationId: string;
  recommendationVersion: number;
  evidenceSnapshotVersion: string;
  mode: OperationalMode;
  state: RecommendationState;
  severity: "critical" | "high" | "medium" | "low" | "informational";
  expiresAt: string;
  whatChanged: string;
  whyItMatters: string;
  recommendedAction: string;
  expectedEffect: string;
  limitations: string;
  constraints: string[];
  evidence: EvidenceSummary[];
  approvalRequirements: ApprovalRequirement[];
  availableActions: DecisionAction[];
}

interface DecisionCommand {
  action: DecisionAction;
  recommendationVersion: number;
  expectedState: RecommendationState;
  evidenceSnapshotVersion: string;
  reasonCode: string;
  comment?: string;
  delegateToUserId?: string;
  escalateToRole?: string;
  confirmationTextHash: string;
}
```
