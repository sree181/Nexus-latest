export type OperationalMode = 'live' | 'training' | 'replay';
export type EventPhase =
  | 'readiness'
  | 'arrival'
  | 'ingress'
  | 'in_game'
  | 'egress'
  | 'after_action'
  | 'closed'
  | 'steady_state'
  | 'response'
  | 'recovery';
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'informational';
export type IncidentStatus = 'new' | 'triaged' | 'active' | 'monitoring' | 'resolved' | 'closed';
export type SourceHealthStatus = 'healthy' | 'delayed' | 'unavailable' | 'unverified' | 'disabled';
export type SourceConnectionStatus = 'connected' | 'not_connected' | 'configuration_required' | 'permission_required' | 'disabled';
export type DataClassification = 'live' | 'near_real_time' | 'reference' | 'operational' | 'restricted';
export type ConnectorRunStatus = 'running' | 'succeeded' | 'partial' | 'failed' | 'skipped' | 'disabled';
export type ConnectorTrigger = 'scheduled' | 'manual' | 'webhook' | 'startup' | 'retry';
export type RecommendationState =
  | 'draft'
  | 'awaiting_acknowledgement'
  | 'awaiting_approval'
  | 'approved'
  | 'rejected'
  | 'revision_requested'
  | 'delegated'
  | 'escalated'
  | 'expired'
  | 'superseded';
export type DecisionAction = 'approve' | 'reject' | 'request_revision' | 'delegate' | 'escalate' | 'acknowledge' | 'withdraw';
export type CommitmentState = 'requested' | 'acknowledged' | 'approved' | 'executing' | 'blocked' | 'verified' | 'failed' | 'expired' | 'cancelled';

export interface PrincipalContext {
  principalId: string;
  externalSubject: string;
  displayName: string;
  agencyId: string;
  agencyName: string;
  roles: string[];
  scopes: string[];
  modes: OperationalMode[];
}

export interface OperationalEvent {
  eventId: string;
  mode: OperationalMode;
  eventType: string;
  name: string;
  phase: EventPhase;
  status: 'planned' | 'active' | 'monitoring' | 'closed' | 'cancelled';
  startsAt: string;
  endsAt: string | null;
  locationName: string;
  commandOwner: ActorRef | null;
  scenarioPackCode: string | null;
  version: number;
  updatedAt: string;
}

/**
 * A scenario pack is the configuration that makes one operating window behave differently from
 * another: which authoritative feeds are read, which agent desks are staffed, and which detection
 * rules may open an incident. Game Day is one pack among several, not the built-in assumption.
 */
export interface ScenarioPack {
  packCode: string;
  name: string;
  eventType: string;
  description: string;
  defaultPhase: EventPhase;
  connectorCodes: string[];
  agentCodes: string[];
  ruleCount: number;
}

export interface OpenOperatingWindowCommand {
  packCode: string;
  name: string;
  locationName: string;
  mode: OperationalMode;
  startsAt?: string;
  endsAt?: string | null;
}

export interface SourceHealth {
  sourceId: string;
  sourceCode: string;
  name: string;
  ownerAgencyName: string;
  status: SourceHealthStatus;
  lastSuccessAt: string | null;
  lastEventObservedAt: string | null;
  lagSeconds: number | null;
  staleAfterSeconds: number;
  errorCategory: string | null;
  authorityUri?: string | null;
  connectorCode?: string | null;
  dataClassification?: DataClassification;
  connectionStatus?: SourceConnectionStatus;
  partnerApprovalRequired?: boolean;
  lastAttemptAt?: string | null;
  consecutiveFailures?: number;
}

export interface ObservationProvenance {
  authority: string;
  authorityUri: string;
  sourceRecordUri?: string;
  connectorCode: string;
  schemaVersion: string;
  fetchedAt: string;
  upstreamObservedAt?: string;
  termsNote?: string;
}

export interface NormalizedObservation {
  sourceEventId: string;
  observedAt: string;
  summary: string;
  geometryGeojson: Record<string, unknown> | null;
  attributes: Record<string, unknown>;
  qualityFlags: string[];
  contentHash: string;
  provenance: ObservationProvenance;
}

export interface ConnectorRun {
  connectorRunId: string;
  sourceId: string;
  eventId: string | null;
  requestId: string;
  triggerType: ConnectorTrigger;
  status: ConnectorRunStatus;
  startedAt: string;
  completedAt: string | null;
  fetchedCount: number;
  acceptedCount: number;
  duplicateCount: number;
  rejectedCount: number;
  errorCategory: string | null;
}

export interface EvidenceSummary {
  evidenceId: string;
  sourceId: string;
  sourceName: string;
  observedAt: string;
  receivedAt: string;
  summary: string;
  qualityFlags: string[];
  attributes: Record<string, unknown>;
}

export interface OperationalObservation extends EvidenceSummary {
  sourceCode: string;
  dataClassification: DataClassification;
  geometryGeojson: Record<string, unknown> | null;
  provenance: ObservationProvenance;
}

export interface Incident {
  incidentId: string;
  eventId: string;
  mode: OperationalMode;
  title: string;
  whatChanged: string;
  whyItMatters: string;
  severity: Severity;
  status: IncidentStatus;
  commandOwner: ActorRef | null;
  locationGeojson: Record<string, unknown> | null;
  affectedServices: string[];
  constraints: string[];
  detectedAt: string;
  resolvedAt: string | null;
  version: number;
  updatedAt: string;
}

export interface ApprovalRequirement {
  requirementId: string;
  agencyId: string;
  agencyName: string;
  roleCode: string;
  sequence: number;
  quorum: number;
  status: 'pending' | 'satisfied' | 'waived' | 'expired';
  satisfiedAt: string | null;
  delegationAllowed: boolean;
}

export interface Recommendation {
  recommendationId: string;
  incidentId: string;
  mode: OperationalMode;
  version: number;
  state: RecommendationState;
  priority: Severity;
  whatChanged: string;
  whyItMatters: string;
  recommendedAction: string;
  expectedEffect: string;
  limitations: string;
  constraints: string[];
  evidenceSnapshotHash: string;
  evidence: EvidenceSummary[];
  approvalRequirements: ApprovalRequirement[];
  generatedBy: {
    model: string;
    version: string;
  };
  expiresAt: string;
  createdAt: string;
  updatedAt: string;
}

export interface Decision {
  decisionId: string;
  recommendationId: string;
  recommendationVersion: number;
  action: DecisionAction;
  actor: ActorRef;
  reasonCode: string;
  comment: string | null;
  decidedAt: string;
}

export interface Commitment {
  commitmentId: string;
  incidentId: string;
  recommendationId: string;
  decisionId: string;
  mode: OperationalMode;
  ownerAgencyId: string;
  ownerAgencyName: string;
  assignee: ActorRef | null;
  requestedOutcome: string;
  state: CommitmentState;
  dueAt: string | null;
  blocker: string | null;
  verificationRule: string;
  version: number;
  updatedAt: string;
}

export interface ActorRef {
  principalId: string;
  displayName: string;
  agencyId: string;
  agencyName: string;
  roleCode: string;
}

export interface SystemStatus {
  status: 'operational' | 'degraded' | 'major_degradation' | 'configuration_required';
  mode: OperationalMode | null;
  checkedAt: string;
  database: 'connected' | 'unavailable' | 'not_configured' | 'review_repository';
  sourceSummary: {
    healthy: number;
    delayed: number;
    unavailable: number;
    unverified: number;
  };
  message: string;
}

export interface DecisionCommand {
  action: DecisionAction;
  recommendationVersion: number;
  expectedState: RecommendationState;
  evidenceSnapshotHash: string;
  reasonCode: string;
  comment?: string;
  delegateToPrincipalId?: string;
  escalateToRoleCode?: string;
  confirmationTextHash?: string;
}

export interface DecisionResult {
  decision: Decision;
  recommendation: Recommendation;
  createdCommitments: Commitment[];
}

export interface CommitmentTransitionCommand {
  expectedVersion: number;
  targetState: CommitmentState;
  reasonCode: string;
  comment?: string;
  evidenceIds?: string[];
}

export interface ExecutionConfirmationCommand {
  confirmationType: 'authoritative_system' | 'accountable_operator';
  confirmedAt: string;
  outcome: string;
  externalReceiptId?: string;
  evidenceIds?: string[];
}
