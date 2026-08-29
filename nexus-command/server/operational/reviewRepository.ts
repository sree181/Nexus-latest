import { createHash, randomUUID } from 'node:crypto';
import type {
  Commitment,
  CommitmentTransitionCommand,
  Decision,
  DecisionCommand,
  DecisionResult,
  OperationalEvent,
  OperationalMode,
  PrincipalContext,
  Recommendation,
  ScenarioPack,
  SystemStatus,
} from './domain.js';
import { conflict, notFound, OperationalError } from './errors.js';
import type { AuditRecord, EventStreamRecord, OperationalRepository, OperationalSnapshot } from './repository.js';
import {
  applyRecommendationDecision,
  assertCommitmentTransition,
  assertRecommendationSnapshot,
  assertVerifiedEvidence,
} from './stateMachine.js';

const EVENT_ID = '22222222-2222-4222-8222-222222222222';
const INCIDENT_ID = '33333333-3333-4333-8333-333333333333';
const RECOMMENDATION_ID = '44444444-4444-4444-8444-444444444444';
const COMMAND_AGENCY_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PARKING_AGENCY_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const TRAFFIC_AGENCY_ID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';

function iso(offsetMinutes = 0): string {
  return new Date(Date.now() + offsetMinutes * 60_000).toISOString();
}

export class ReviewOperationalRepository implements OperationalRepository {
  private readonly idempotency = new Map<string, { requestHash: string; response: unknown }>();
  private readonly auditRecords: AuditRecord[] = [];
  private readonly streamRecords: EventStreamRecord[] = [];
  private recommendationRecord: Recommendation;
  private commitments: Commitment[] = [];

  constructor() {
    this.recommendationRecord = {
      recommendationId: RECOMMENDATION_ID,
      incidentId: INCIDENT_ID,
      mode: 'live',
      version: 3,
      state: 'awaiting_approval',
      priority: 'high',
      whatChanged: 'Inbound travel speed on the remote-lot approach has remained below the event threshold for eight minutes while primary parking capacity is constrained.',
      whyItMatters: 'Continued spillback could obstruct the designated emergency-access corridor before kickoff.',
      recommendedAction: 'Activate the pre-approved remote-lot wayfinding plan and stage the next two available shuttles.',
      expectedEffect: 'Reduce queue growth while preserving ADA loading and emergency access. Outcome is not guaranteed.',
      limitations: 'ALGO camera status is delayed by two minutes. No signal timing or road-control action is included.',
      constraints: ['Preserve emergency corridor', 'Preserve ADA loading', 'No traffic-signal control', 'Use approved public-message template'],
      evidenceSnapshotHash: 'review-evidence-v3-7f2a',
      evidence: [
        {
          evidenceId: '55555555-5555-4555-8555-555555555551',
          sourceId: '66666666-6666-4666-8666-666666666661',
          sourceName: 'TomTom Traffic Flow',
          observedAt: iso(-1),
          receivedAt: iso(-1),
          summary: 'Approach speed has remained below the event threshold for eight minutes.',
          qualityFlags: [],
          attributes: { corridor: 'Remote-lot approach', trend: 'deteriorating' },
        },
        {
          evidenceId: '55555555-5555-4555-8555-555555555552',
          sourceId: '66666666-6666-4666-8666-666666666662',
          sourceName: 'Parking Capacity Feed',
          observedAt: iso(-2),
          receivedAt: iso(-2),
          summary: 'Primary-lot availability is constrained; remote inventory remains available.',
          qualityFlags: [],
          attributes: { primaryStatus: 'constrained', remoteStatus: 'available' },
        },
        {
          evidenceId: '55555555-5555-4555-8555-555555555553',
          sourceId: '66666666-6666-4666-8666-666666666663',
          sourceName: 'Tiger Transit Operations',
          observedAt: iso(-1),
          receivedAt: iso(-1),
          summary: 'Two shuttles are available for event staging.',
          qualityFlags: [],
          attributes: { availableUnits: 2, currentDelayMinutes: 4 },
        },
      ],
      approvalRequirements: [
        {
          requirementId: '77777777-7777-4777-8777-777777777771',
          agencyId: PARKING_AGENCY_ID,
          agencyName: 'Parking & Transit Operations',
          roleCode: 'parking_transit_lead',
          sequence: 1,
          quorum: 1,
          status: 'satisfied',
          satisfiedAt: iso(-3),
          delegationAllowed: true,
        },
        {
          requirementId: '77777777-7777-4777-8777-777777777772',
          agencyId: COMMAND_AGENCY_ID,
          agencyName: 'Auburn Event Mobility Command',
          roleCode: 'event_mobility_lead',
          sequence: 2,
          quorum: 1,
          status: 'pending',
          satisfiedAt: null,
          delegationAllowed: false,
        },
      ],
      generatedBy: { model: 'Nexus constrained coordination policy', version: 'sec-gameday-review-v1' },
      expiresAt: iso(18),
      createdAt: iso(-5),
      updatedAt: iso(-3),
    };
  }

  async systemStatus(): Promise<SystemStatus> {
    return {
      status: 'degraded',
      mode: 'live',
      checkedAt: iso(),
      database: 'review_repository',
      sourceSummary: { healthy: 3, delayed: 1, unavailable: 0, unverified: 0 },
      message: 'Local review data is active. No agency system is connected or controlled.',
    };
  }

  async scenarioPacks(): Promise<ScenarioPack[]> {
    return [
      {
        packCode: 'road_closure',
        name: 'Everyday road and mobility operations',
        eventType: 'road_closure',
        description: 'Continuous weekday operating window for published restrictions and corridor flow.',
        defaultPhase: 'steady_state',
        connectorCodes: ['coa-road-closures-v1', 'aldot-algo-traffic-v1', 'tomtom-traffic-flow-v1'],
        agentCodes: ['atlas', 'forge', 'nexus'],
        ruleCount: 5,
      },
      {
        packCode: 'sec_gameday',
        name: 'SEC Game Day mobility operations',
        eventType: 'sec_gameday',
        description: 'Event-day window adding transit, parking, and public-safety desks.',
        defaultPhase: 'readiness',
        connectorCodes: ['coa-road-closures-v1', 'aldot-algo-traffic-v1', 'auburn-eta-spot-v1'],
        agentCodes: ['atlas', 'aqua', 'sentinel', 'phoenix', 'echo', 'nexus'],
        ruleCount: 5,
      },
    ];
  }

  async activeEvent(mode: OperationalMode): Promise<OperationalEvent | null> {
    if (mode !== 'live') return null;
    return {
      eventId: EVENT_ID,
      mode: 'live',
      eventType: 'sec_football_gameday',
      name: 'SEC Game Day Mobility Operations',
      phase: 'arrival',
      status: 'active',
      startsAt: iso(-150),
      endsAt: iso(300),
      locationName: 'Auburn, Alabama',
      commandOwner: {
        principalId: '11111111-1111-4111-8111-111111111111',
        displayName: 'Jordan Smith',
        agencyId: COMMAND_AGENCY_ID,
        agencyName: 'Auburn Event Mobility Command',
        roleCode: 'event_mobility_lead',
      },
      scenarioPackCode: 'sec_gameday',
      version: 7,
      updatedAt: iso(-1),
    };
  }

  async openOperatingWindow(): Promise<OperationalEvent> {
    throw new OperationalError(
      503,
      'OPERATIONAL_STORAGE_NOT_CONFIGURED',
      'Opening an operating window requires persistent operational storage',
    );
  }

  async closeOperatingWindow(): Promise<OperationalEvent> {
    throw new OperationalError(
      503,
      'OPERATIONAL_STORAGE_NOT_CONFIGURED',
      'Closing an operating window requires persistent operational storage',
    );
  }

  async snapshot(eventId: string, _principal: PrincipalContext): Promise<OperationalSnapshot> {
    if (eventId !== EVENT_ID) throw notFound('Operational event', eventId);
    const event = await this.activeEvent('live');
    if (!event) throw notFound('Operational event', eventId);
    return {
      event,
      incidents: [
        {
          incidentId: INCIDENT_ID,
          eventId: EVENT_ID,
          mode: 'live',
          title: 'Arrival congestion — remote-lot approach',
          whatChanged: this.recommendationRecord.whatChanged,
          whyItMatters: this.recommendationRecord.whyItMatters,
          severity: 'high',
          status: 'active',
          commandOwner: event.commandOwner,
          locationGeojson: { type: 'Point', coordinates: [-85.4876, 32.6069] },
          affectedServices: ['Event traffic', 'Parking access', 'Tiger Transit', 'Emergency access'],
          constraints: this.recommendationRecord.constraints,
          detectedAt: iso(-9),
          resolvedAt: null,
          version: 4,
          updatedAt: iso(-1),
        },
      ],
      decisionQueue: ['approved', 'rejected', 'revision_requested'].includes(this.recommendationRecord.state)
        ? []
        : [structuredClone(this.recommendationRecord)],
      commitments: structuredClone(this.commitments),
      observations: [],
      sources: [
        {
          sourceId: '66666666-6666-4666-8666-666666666661',
          sourceCode: 'tomtom-flow',
          name: 'TomTom Traffic Flow',
          ownerAgencyName: 'Traffic Operations',
          status: 'healthy',
          lastSuccessAt: iso(-1),
          lastEventObservedAt: iso(-1),
          lagSeconds: 58,
          staleAfterSeconds: 300,
          errorCategory: null,
        },
        {
          sourceId: '66666666-6666-4666-8666-666666666662',
          sourceCode: 'parking-capacity',
          name: 'Parking Capacity Feed',
          ownerAgencyName: 'Parking & Transit Operations',
          status: 'healthy',
          lastSuccessAt: iso(-2),
          lastEventObservedAt: iso(-2),
          lagSeconds: 114,
          staleAfterSeconds: 300,
          errorCategory: null,
        },
        {
          sourceId: '66666666-6666-4666-8666-666666666663',
          sourceCode: 'tiger-transit',
          name: 'Tiger Transit Operations',
          ownerAgencyName: 'Parking & Transit Operations',
          status: 'healthy',
          lastSuccessAt: iso(-1),
          lastEventObservedAt: iso(-1),
          lagSeconds: 42,
          staleAfterSeconds: 180,
          errorCategory: null,
        },
        {
          sourceId: '66666666-6666-4666-8666-666666666664',
          sourceCode: 'algo-camera-status',
          name: 'ALGO Camera Status',
          ownerAgencyName: 'Traffic Operations',
          status: 'delayed',
          lastSuccessAt: iso(-4),
          lastEventObservedAt: iso(-4),
          lagSeconds: 241,
          staleAfterSeconds: 180,
          errorCategory: 'upstream_delay',
        },
      ],
    };
  }

  async recommendation(recommendationId: string): Promise<Recommendation> {
    if (recommendationId !== RECOMMENDATION_ID) throw notFound('Recommendation', recommendationId);
    return structuredClone(this.recommendationRecord);
  }

  async decide(
    recommendationId: string,
    command: DecisionCommand,
    principal: PrincipalContext,
    idempotencyKey: string,
    requestId: string,
  ): Promise<DecisionResult> {
    const cacheKey = `decision:${principal.principalId}:${idempotencyKey}`;
    const requestHash = createHash('sha256').update(JSON.stringify({ recommendationId, command })).digest('hex');
    const cached = this.idempotency.get(cacheKey);
    if (cached) {
      if (cached.requestHash !== requestHash) throw conflict('IDEMPOTENCY_KEY_REUSED', 'The idempotency key was already used for a different request');
      return structuredClone(cached.response) as DecisionResult;
    }
    if (recommendationId !== RECOMMENDATION_ID) throw notFound('Recommendation', recommendationId);
    assertRecommendationSnapshot(
      this.recommendationRecord,
      command.recommendationVersion,
      command.expectedState,
      command.evidenceSnapshotHash,
    );
    const targetState = applyRecommendationDecision(this.recommendationRecord, command.action);
    const pending = this.recommendationRecord.approvalRequirements.find(
      requirement => requirement.status === 'pending'
        && requirement.agencyId === principal.agencyId
        && principal.roles.includes(requirement.roleCode),
    );
    if (command.action === 'approve' && !pending) throw conflict('APPROVAL_AUTHORITY_REQUIRED', 'Current operator is not the pending approver');
    if (pending && command.action === 'approve') {
      pending.status = 'satisfied';
      pending.satisfiedAt = iso();
    }
    this.recommendationRecord.state = targetState;
    this.recommendationRecord.updatedAt = iso();

    const decision: Decision = {
      decisionId: randomUUID(),
      recommendationId,
      recommendationVersion: command.recommendationVersion,
      action: command.action,
      actor: {
        principalId: principal.principalId,
        displayName: principal.displayName,
        agencyId: principal.agencyId,
        agencyName: principal.agencyName,
        roleCode: principal.roles[0] ?? 'operator',
      },
      reasonCode: command.reasonCode,
      comment: command.comment ?? null,
      decidedAt: iso(),
    };

    if (targetState === 'approved') {
      this.commitments = [
        this.createCommitment(decision, PARKING_AGENCY_ID, 'Parking & Transit Operations', 'Activate approved remote-lot wayfinding and stage two shuttles.', 'Operator confirms message activation; transit feed confirms staged units.'),
        this.createCommitment(decision, TRAFFIC_AGENCY_ID, 'Traffic Operations', 'Monitor queue spillback and preserve the emergency-access corridor.', 'Traffic source confirms corridor remains available.'),
      ];
    }

    const response = {
      decision,
      recommendation: structuredClone(this.recommendationRecord),
      createdCommitments: structuredClone(this.commitments),
    };
    this.recordActivity('recommendation.approve', 'recommendation', recommendationId, this.recommendationRecord.version, principal, requestId, response);
    this.idempotency.set(cacheKey, { requestHash, response: structuredClone(response) });
    return response;
  }

  async transitionCommitment(
    commitmentId: string,
    command: CommitmentTransitionCommand,
    principal: PrincipalContext,
    idempotencyKey: string,
    requestId: string,
  ): Promise<Commitment> {
    const cacheKey = `commitment:${principal.principalId}:${idempotencyKey}`;
    const requestHash = createHash('sha256').update(JSON.stringify({ commitmentId, command })).digest('hex');
    const cached = this.idempotency.get(cacheKey);
    if (cached) {
      if (cached.requestHash !== requestHash) throw conflict('IDEMPOTENCY_KEY_REUSED', 'The idempotency key was already used for a different request');
      return structuredClone(cached.response) as Commitment;
    }
    const commitment = this.commitments.find(item => item.commitmentId === commitmentId);
    if (!commitment) throw notFound('Commitment', commitmentId);
    if (commitment.version !== command.expectedVersion) throw conflict('COMMITMENT_VERSION_CONFLICT', 'Commitment changed after it was opened');
    assertCommitmentTransition(commitment.state, command.targetState);
    assertVerifiedEvidence(command.targetState, command.evidenceIds ?? []);
    commitment.state = command.targetState;
    commitment.blocker = command.targetState === 'blocked' ? command.comment ?? 'Blocked by agency constraint' : null;
    commitment.version += 1;
    commitment.updatedAt = iso();
    this.recordActivity('commitment.transition', 'commitment', commitmentId, commitment.version, principal, requestId, commitment);
    this.idempotency.set(cacheKey, { requestHash, response: structuredClone(commitment) });
    return structuredClone(commitment);
  }

  async audit(eventId: string, afterSequence: number, limit: number): Promise<AuditRecord[]> {
    if (eventId !== EVENT_ID) throw notFound('Operational event', eventId);
    return structuredClone(this.auditRecords.filter(record => record.sequenceNo > afterSequence).slice(0, limit));
  }

  async stream(eventId: string, afterSequence: number): Promise<EventStreamRecord[]> {
    if (eventId !== EVENT_ID) throw notFound('Operational event', eventId);
    return structuredClone(this.streamRecords.slice(afterSequence));
  }

  private createCommitment(
    decision: Decision,
    agencyId: string,
    agencyName: string,
    requestedOutcome: string,
    verificationRule: string,
  ): Commitment {
    return {
      commitmentId: randomUUID(),
      incidentId: INCIDENT_ID,
      recommendationId: RECOMMENDATION_ID,
      decisionId: decision.decisionId,
      mode: 'live',
      ownerAgencyId: agencyId,
      ownerAgencyName: agencyName,
      assignee: null,
      requestedOutcome,
      state: 'requested',
      dueAt: iso(10),
      blocker: null,
      verificationRule,
      version: 1,
      updatedAt: iso(),
    };
  }

  private recordActivity(
    action: string,
    objectType: string,
    objectId: string,
    objectVersion: number,
    principal: PrincipalContext,
    requestId: string,
    payload: unknown,
  ): void {
    const sequenceNo = this.auditRecords.length + 1;
    this.auditRecords.push({
      auditId: randomUUID(),
      sequenceNo,
      mode: 'live',
      action,
      objectType,
      objectId,
      objectVersion,
      actorId: principal.principalId,
      actorAgencyId: principal.agencyId,
      outcome: 'accepted',
      metadata: { requestId, reviewRepository: true },
      createdAt: iso(),
    });
    this.streamRecords.push({
      eventId: randomUUID(),
      eventType: action,
      mode: 'live',
      aggregateType: objectType,
      aggregateId: objectId,
      aggregateVersion: objectVersion,
      occurredAt: iso(),
      payload: { eventId: EVENT_ID, data: payload as Record<string, unknown> },
    });
  }
}
