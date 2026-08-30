import type {
  Commitment,
  CommitmentTransitionCommand,
  Decision,
  DecisionCommand,
  DecisionResult,
  Incident,
  OpenOperatingWindowCommand,
  OperationalEvent,
  OperationalMode,
  OperationalObservation,
  PrincipalContext,
  Recommendation,
  ScenarioPack,
  SourceHealth,
  SystemStatus,
} from './domain.js';

export interface EventStreamRecord {
  eventId: string;
  eventType: string;
  mode: OperationalMode;
  aggregateType: string;
  aggregateId: string;
  aggregateVersion: number | null;
  occurredAt: string;
  payload: Record<string, unknown>;
}

export interface OperationalSnapshot {
  event: OperationalEvent;
  incidents: Incident[];
  decisionQueue: Recommendation[];
  commitments: Commitment[];
  sources: SourceHealth[];
  observations: OperationalObservation[];
}

/** Every recommendation and decision for an event, including closed ones. Used by Lineage. */
export interface EventLineage {
  event: OperationalEvent;
  incidents: Incident[];
  recommendations: Recommendation[];
  decisions: Decision[];
  commitments: Commitment[];
}

export interface AuditRecord {
  auditId: string;
  sequenceNo: number;
  mode: OperationalMode;
  action: string;
  objectType: string;
  objectId: string;
  objectVersion: number | null;
  actorId: string;
  actorAgencyId: string | null;
  outcome: string;
  metadata: Record<string, unknown>;
  createdAt: string;
}

export interface OperationalRepository {
  systemStatus(): Promise<SystemStatus>;
  scenarioPacks(): Promise<ScenarioPack[]>;
  activeEvent(mode: OperationalMode): Promise<OperationalEvent | null>;
  openOperatingWindow(
    command: OpenOperatingWindowCommand,
    principal: PrincipalContext,
    idempotencyKey: string,
    requestId: string,
  ): Promise<OperationalEvent>;
  closeOperatingWindow(
    eventId: string,
    principal: PrincipalContext,
    requestId: string,
  ): Promise<OperationalEvent>;
  snapshot(eventId: string, principal: PrincipalContext): Promise<OperationalSnapshot>;
  eventLineage(eventId: string, principal: PrincipalContext): Promise<EventLineage>;
  recommendation(recommendationId: string, principal: PrincipalContext): Promise<Recommendation>;
  decide(
    recommendationId: string,
    command: DecisionCommand,
    principal: PrincipalContext,
    idempotencyKey: string,
    requestId: string,
  ): Promise<DecisionResult>;
  transitionCommitment(
    commitmentId: string,
    command: CommitmentTransitionCommand,
    principal: PrincipalContext,
    idempotencyKey: string,
    requestId: string,
  ): Promise<Commitment>;
  audit(eventId: string, afterSequence: number, limit: number, principal: PrincipalContext): Promise<AuditRecord[]>;
  stream(eventId: string, afterSequence: number, principal: PrincipalContext): Promise<EventStreamRecord[]>;
}
