export type OperationalMode = 'live' | 'training' | 'replay';
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'informational';
export type RecommendationState = 'draft' | 'awaiting_acknowledgement' | 'awaiting_approval' | 'approved' | 'rejected' | 'revision_requested' | 'delegated' | 'escalated' | 'expired' | 'superseded';
export type CommitmentState = 'requested' | 'acknowledged' | 'approved' | 'executing' | 'blocked' | 'verified' | 'failed' | 'expired' | 'cancelled';

export interface ActorRef {
  principalId: string;
  displayName: string;
  agencyId: string;
  agencyName: string;
  roleCode: string;
}

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

export type EventPhase =
  | 'readiness' | 'arrival' | 'ingress' | 'in_game' | 'egress' | 'after_action' | 'closed'
  | 'steady_state' | 'response' | 'recovery';

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

export interface ReferenceLayerDefinition {
  code: string;
  name: string;
  description: string;
  geometryType: 'point' | 'polygon';
  limitations: string;
}

export interface ReferenceLayer {
  definition: ReferenceLayerDefinition;
  featureCollection: { type: 'FeatureCollection'; features: unknown[] };
  retrievedAt: string;
  attribution: string;
}

export interface WeatherOverlay {
  retrievedAt: string;
  attribution: string;
  limitations: string;
  alertCount: number;
  forecastSummary: string | null;
  featureCollection: { type: 'FeatureCollection'; features: unknown[] };
}

export interface SourceHealth {
  sourceId: string;
  sourceCode: string;
  name: string;
  ownerAgencyName: string;
  status: 'healthy' | 'delayed' | 'unavailable' | 'unverified' | 'disabled';
  lastSuccessAt: string | null;
  lastEventObservedAt: string | null;
  lagSeconds: number | null;
  staleAfterSeconds: number;
  errorCategory: string | null;
  authorityUri?: string | null;
  connectorCode?: string | null;
  dataClassification?: 'live' | 'near_real_time' | 'reference' | 'operational' | 'restricted';
  connectionStatus?: 'connected' | 'not_connected' | 'configuration_required' | 'permission_required' | 'disabled';
  partnerApprovalRequired?: boolean;
  lastAttemptAt?: string | null;
  consecutiveFailures?: number;
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
  dataClassification: 'live' | 'near_real_time' | 'reference' | 'operational' | 'restricted';
  geometryGeojson: Record<string, unknown> | null;
  provenance: {
    authority: string;
    authorityUri: string;
    sourceRecordUri?: string;
    connectorCode: string;
    schemaVersion: string;
    fetchedAt: string;
    upstreamObservedAt?: string;
    termsNote?: string;
  };
}

export interface Incident {
  incidentId: string;
  eventId: string;
  mode: OperationalMode;
  title: string;
  impact?: string;
  whatChanged: string;
  whyItMatters: string;
  severity: Severity;
  status: 'new' | 'triaged' | 'active' | 'monitoring' | 'resolved' | 'closed';
  commandOwner: ActorRef | null;
  locationGeojson: { type?: string; coordinates?: number[] } | null;
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

export interface AgentConflict {
  withAgentCode: string;
  concern: string;
  basis: string;
}

/**
 * What one agent desk made of the evidence. An abstained finding is not an absence of data in the
 * UI; it is the desk saying which part of the picture Nexus cannot see.
 */
export interface AgentFinding {
  agentCode: string;
  agentName: string;
  status: 'contributed' | 'abstained';
  observation: string;
  interpretation: string;
  candidateAction: string;
  confidence: number | null;
  limitations: string;
  citedEvidenceIds: string[];
  conflicts: AgentConflict[];
  createdAt: string;
  modelName?: string;
  modelVersion?: string;
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
  agentFindings: AgentFinding[];
  generatedBy: { model: string; version: string };
  expiresAt: string;
  createdAt: string;
  updatedAt: string;
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

export interface OperationalSnapshot {
  event: OperationalEvent;
  incidents: Incident[];
  decisionQueue: Recommendation[];
  commitments: Commitment[];
  sources: SourceHealth[];
  observations: OperationalObservation[];
}

export interface SystemStatus {
  status: 'operational' | 'degraded' | 'major_degradation' | 'configuration_required';
  mode: OperationalMode | null;
  checkedAt: string;
  database: 'connected' | 'unavailable' | 'not_configured' | 'review_repository';
  sourceSummary: { healthy: number; delayed: number; unavailable: number; unverified: number };
  message: string;
}

export interface AtlasPolicy {
  id: string;
  title: string;
  jurisdiction: 'department' | 'city' | 'county' | 'state';
  source: string;
  body: string;
}

export interface AtlasToolOption {
  name: string;
  label: string;
  description: string;
  required: boolean;
  enabled: boolean;
}

export interface AtlasAgentProfile {
  deskCode: 'atlas' | 'aqua';
  name: string;
  role: string;
  backstory: string;
  instructions: string;
  llm: { model: string; temperature: number; maxTurns: number; timeoutMs: number };
  tools: AtlasToolOption[];
  policies: AtlasPolicy[];
  locked: {
    mission: string;
    boundary: string;
    allowedConnectors: string[];
    actionFamilies: string[];
  };
  runtime: {
    enabled: boolean;
    host: string;
    keyConfigured: boolean;
    models: Array<{ id: string; label: string }>;
  };
  updatedAt: string;
}

export interface ApiEnvelope<T> {
  data: T;
  requestId: string;
}

export interface ApiError {
  error: { code: string; message: string; details?: Record<string, unknown> };
  requestId?: string;
}
