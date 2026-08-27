import { createHash, randomUUID } from 'node:crypto';
import type pg from 'pg';
import type {
  ActorRef,
  ApprovalRequirement,
  Commitment,
  CommitmentTransitionCommand,
  Decision,
  DecisionCommand,
  DecisionResult,
  EvidenceSummary,
  Incident,
  OperationalEvent,
  OperationalMode,
  OperationalObservation,
  PrincipalContext,
  Recommendation,
  SourceHealth,
  SystemStatus,
} from './domain.js';
import { getDatabasePool, withTransaction, type DatabaseClient } from './database.js';
import { conflict, forbidden, notFound } from './errors.js';
import type { AuditRecord, EventStreamRecord, OperationalRepository, OperationalSnapshot } from './repository.js';
import {
  applyRecommendationDecision,
  assertCommitmentTransition,
  assertModeAllowed,
  assertRecommendationSnapshot,
  assertVerifiedEvidence,
  decisionScope,
} from './stateMachine.js';

type Queryable = Pick<pg.Pool, 'query'> | Pick<pg.PoolClient, 'query'>;

function hashRequest(value: unknown): string {
  return createHash('sha256').update(JSON.stringify(value)).digest('hex');
}

function actorFromRow(row: Record<string, any>, prefix: string): ActorRef | null {
  const principalId = row[`${prefix}_principal_id`];
  if (!principalId) return null;
  return {
    principalId,
    displayName: row[`${prefix}_display_name`] || 'Assigned operator',
    agencyId: row[`${prefix}_agency_id`],
    agencyName: row[`${prefix}_agency_name`] || 'Assigned agency',
    roleCode: row[`${prefix}_role_code`] || 'assigned_operator',
  };
}

function mapEvent(row: Record<string, any>): OperationalEvent {
  return {
    eventId: row.event_id,
    mode: row.mode,
    eventType: row.event_type,
    name: row.name,
    phase: row.phase,
    status: row.status,
    startsAt: row.starts_at.toISOString(),
    endsAt: row.ends_at?.toISOString() ?? null,
    locationName: row.location_name,
    commandOwner: actorFromRow(row, 'owner'),
    version: row.version,
    updatedAt: row.updated_at.toISOString(),
  };
}

function mapIncident(row: Record<string, any>): Incident {
  return {
    incidentId: row.incident_id,
    eventId: row.event_id,
    mode: row.mode,
    title: row.title,
    whatChanged: row.what_changed,
    whyItMatters: row.why_it_matters,
    severity: row.severity,
    status: row.status,
    commandOwner: actorFromRow(row, 'owner'),
    locationGeojson: row.location_geojson,
    affectedServices: row.affected_services ?? [],
    constraints: row.constraints ?? [],
    detectedAt: row.detected_at.toISOString(),
    resolvedAt: row.resolved_at?.toISOString() ?? null,
    version: row.version,
    updatedAt: row.updated_at.toISOString(),
  };
}

function mapSource(row: Record<string, any>): SourceHealth {
  const observedAt = row.last_event_observed_at as Date | null;
  return {
    sourceId: row.source_id,
    sourceCode: row.code,
    name: row.name,
    ownerAgencyName: row.owner_agency_name,
    status: row.health_status,
    lastSuccessAt: row.last_success_at?.toISOString() ?? null,
    lastEventObservedAt: observedAt?.toISOString() ?? null,
    lagSeconds: observedAt ? Math.max(0, Math.round((Date.now() - observedAt.getTime()) / 1000)) : null,
    staleAfterSeconds: row.stale_after_seconds,
    errorCategory: row.error_category,
    authorityUri: row.authority_uri ?? null,
    connectorCode: row.connector_code ?? null,
    dataClassification: row.data_classification ?? 'operational',
    connectionStatus: row.connection_status ?? 'not_connected',
    partnerApprovalRequired: row.partner_approval_required ?? false,
    lastAttemptAt: row.last_attempt_at?.toISOString() ?? null,
    consecutiveFailures: row.consecutive_failures ?? 0,
  };
}

function mapObservation(row: Record<string, any>): OperationalObservation {
  return {
    evidenceId: row.evidence_id,
    sourceId: row.source_id,
    sourceCode: row.source_code,
    sourceName: row.source_name,
    dataClassification: row.data_classification,
    observedAt: row.observed_at.toISOString(),
    receivedAt: row.received_at.toISOString(),
    summary: row.summary,
    geometryGeojson: row.geometry_geojson ?? null,
    qualityFlags: row.quality_flags ?? [],
    attributes: row.normalized_attributes ?? {},
    provenance: row.provenance ?? {},
  };
}

function mapCommitment(row: Record<string, any>): Commitment {
  return {
    commitmentId: row.commitment_id,
    incidentId: row.incident_id,
    recommendationId: row.recommendation_id,
    decisionId: row.decision_id,
    mode: row.mode,
    ownerAgencyId: row.owner_agency_id,
    ownerAgencyName: row.owner_agency_name,
    assignee: actorFromRow(row, 'assignee'),
    requestedOutcome: row.requested_outcome,
    state: row.state,
    dueAt: row.due_at?.toISOString() ?? null,
    blocker: row.blocker,
    verificationRule: row.verification_rule,
    version: row.version,
    updatedAt: row.updated_at.toISOString(),
  };
}

async function loadRecommendation(queryable: Queryable, recommendationId: string): Promise<Recommendation> {
  const result = await queryable.query(
    `SELECT r.*
       FROM recommendations r
      WHERE r.recommendation_id = $1`,
    [recommendationId],
  );
  if (!result.rowCount) throw notFound('Recommendation', recommendationId);
  const row = result.rows[0];

  const [evidenceResult, requirementResult] = await Promise.all([
    queryable.query(
      `SELECT e.evidence_id, e.source_id, s.name AS source_name, e.observed_at, e.received_at,
              e.summary, e.quality_flags, e.normalized_attributes
         FROM recommendation_evidence re
         JOIN evidence_events e ON e.evidence_id = re.evidence_id
         JOIN sources s ON s.source_id = e.source_id
        WHERE re.recommendation_id = $1
        ORDER BY re.role, e.observed_at DESC`,
      [recommendationId],
    ),
    queryable.query(
      `SELECT ar.approval_requirement_id, ar.agency_id, a.name AS agency_name, ar.role_code,
              ar.sequence, ar.quorum, ar.status, ar.satisfied_at, ar.delegation_allowed
         FROM approval_requirements ar
         JOIN agencies a ON a.agency_id = ar.agency_id
        WHERE ar.recommendation_id = $1
        ORDER BY ar.sequence, a.name`,
      [recommendationId],
    ),
  ]);

  const evidence: EvidenceSummary[] = evidenceResult.rows.map(item => ({
    evidenceId: item.evidence_id,
    sourceId: item.source_id,
    sourceName: item.source_name,
    observedAt: item.observed_at.toISOString(),
    receivedAt: item.received_at.toISOString(),
    summary: item.summary,
    qualityFlags: item.quality_flags ?? [],
    attributes: item.normalized_attributes ?? {},
  }));

  const approvalRequirements: ApprovalRequirement[] = requirementResult.rows.map(item => ({
    requirementId: item.approval_requirement_id,
    agencyId: item.agency_id,
    agencyName: item.agency_name,
    roleCode: item.role_code,
    sequence: item.sequence,
    quorum: item.quorum,
    status: item.status,
    satisfiedAt: item.satisfied_at?.toISOString() ?? null,
    delegationAllowed: item.delegation_allowed,
  }));

  return {
    recommendationId: row.recommendation_id,
    incidentId: row.incident_id,
    mode: row.mode,
    version: row.recommendation_version,
    state: row.state,
    priority: row.priority,
    whatChanged: row.what_changed,
    whyItMatters: row.why_it_matters,
    recommendedAction: row.recommended_action,
    expectedEffect: row.expected_effect,
    limitations: row.limitations,
    constraints: row.constraints ?? [],
    evidenceSnapshotHash: row.evidence_snapshot_hash,
    evidence,
    approvalRequirements,
    generatedBy: { model: row.generated_by_model, version: row.generated_by_model_version },
    expiresAt: row.expires_at.toISOString(),
    createdAt: row.created_at.toISOString(),
    updatedAt: row.updated_at.toISOString(),
  };
}

async function loadCommitments(queryable: Queryable, eventId: string): Promise<Commitment[]> {
  const result = await queryable.query(
    `SELECT c.*, a.name AS owner_agency_name,
            p.principal_id AS assignee_principal_id, p.display_name AS assignee_display_name,
            c.owner_agency_id AS assignee_agency_id, a.name AS assignee_agency_name,
            'assigned_operator' AS assignee_role_code
       FROM commitments c
       JOIN incidents i ON i.incident_id = c.incident_id
       JOIN agencies a ON a.agency_id = c.owner_agency_id
       LEFT JOIN principals p ON p.principal_id = c.assignee_principal_id
      WHERE i.event_id = $1
      ORDER BY c.updated_at DESC`,
    [eventId],
  );
  return result.rows.map(mapCommitment);
}

export class PostgresOperationalRepository implements OperationalRepository {
  async systemStatus(): Promise<SystemStatus> {
    const checkedAt = new Date().toISOString();
    try {
      await getDatabasePool().query('SELECT 1');
      const health = await getDatabasePool().query(
        `SELECT health_status, count(*)::int AS count
           FROM sources WHERE active = true GROUP BY health_status`,
      );
      const sourceSummary = { healthy: 0, delayed: 0, unavailable: 0, unverified: 0 };
      for (const row of health.rows) {
        if (row.health_status in sourceSummary) sourceSummary[row.health_status as keyof typeof sourceSummary] = row.count;
      }
      const degraded = sourceSummary.delayed + sourceSummary.unavailable > 0;
      return {
        status: degraded ? 'degraded' : 'operational',
        mode: 'live',
        checkedAt,
        database: 'connected',
        sourceSummary,
        message: degraded ? 'Operational with degraded evidence sources' : 'All configured operational services are available',
      };
    } catch {
      return {
        status: 'major_degradation',
        mode: null,
        checkedAt,
        database: 'unavailable',
        sourceSummary: { healthy: 0, delayed: 0, unavailable: 0, unverified: 0 },
        message: 'Persistent operational storage is unavailable',
      };
    }
  }

  async activeEvent(mode: OperationalMode): Promise<OperationalEvent | null> {
    const result = await getDatabasePool().query(
      `SELECT e.*, p.principal_id AS owner_principal_id, p.display_name AS owner_display_name,
              a.agency_id AS owner_agency_id, a.name AS owner_agency_name,
              'command_owner' AS owner_role_code
         FROM operational_events e
         LEFT JOIN principals p ON p.principal_id = e.command_owner_principal_id
         LEFT JOIN agencies a ON a.agency_id = e.command_owner_agency_id
        WHERE e.mode = $1 AND e.status IN ('active', 'monitoring')
        ORDER BY e.starts_at DESC LIMIT 1`,
      [mode],
    );
    return result.rowCount ? mapEvent(result.rows[0]) : null;
  }

  async snapshot(eventId: string, principal: PrincipalContext): Promise<OperationalSnapshot> {
    const pool = getDatabasePool();
    const eventResult = await pool.query(
      `SELECT e.*, p.principal_id AS owner_principal_id, p.display_name AS owner_display_name,
              a.agency_id AS owner_agency_id, a.name AS owner_agency_name,
              'command_owner' AS owner_role_code
         FROM operational_events e
         LEFT JOIN principals p ON p.principal_id = e.command_owner_principal_id
         LEFT JOIN agencies a ON a.agency_id = e.command_owner_agency_id
        WHERE e.event_id = $1`,
      [eventId],
    );
    if (!eventResult.rowCount) throw notFound('Operational event', eventId);
    const event = mapEvent(eventResult.rows[0]);
    assertModeAllowed(event.mode, principal.modes);

    const [incidentsResult, sourcesResult, commitments, observationsResult] = await Promise.all([
      pool.query(
        `SELECT i.*, p.principal_id AS owner_principal_id, p.display_name AS owner_display_name,
                a.agency_id AS owner_agency_id, a.name AS owner_agency_name,
                'incident_owner' AS owner_role_code
           FROM incidents i
           LEFT JOIN principals p ON p.principal_id = i.command_owner_principal_id
           LEFT JOIN agencies a ON a.agency_id = i.command_owner_agency_id
          WHERE i.event_id = $1
          ORDER BY CASE i.severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END,
                   i.updated_at DESC`,
        [eventId],
      ),
      pool.query(
        `SELECT s.*, a.name AS owner_agency_name
           FROM sources s JOIN agencies a ON a.agency_id = s.owner_agency_id
          WHERE s.active = true ORDER BY s.health_status, s.name`,
      ),
      loadCommitments(pool, eventId),
      pool.query(
        `SELECT e.evidence_id, e.source_id, s.code AS source_code, s.name AS source_name,
                s.data_classification, e.observed_at, e.received_at, e.summary, e.geometry_geojson,
                e.normalized_attributes, e.quality_flags, e.provenance
           FROM evidence_events e
           JOIN sources s ON s.source_id = e.source_id
          WHERE e.event_id = $1
          ORDER BY e.observed_at DESC
          LIMIT 250`,
        [eventId],
      ),
    ]);

    const recommendationRows = await pool.query(
      `SELECT r.recommendation_id
         FROM recommendations r
         JOIN incidents i ON i.incident_id = r.incident_id
         JOIN approval_requirements ar ON ar.recommendation_id = r.recommendation_id
        WHERE i.event_id = $1
          AND r.state IN ('awaiting_acknowledgement', 'awaiting_approval', 'delegated', 'escalated')
          AND (ar.agency_id = $2 OR $3::boolean = true)
        GROUP BY r.recommendation_id, r.priority, r.expires_at
        ORDER BY CASE r.priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END,
                 r.expires_at`,
      [eventId, principal.agencyId, principal.roles.includes('event_mobility_lead')],
    );
    const decisionQueue = await Promise.all(recommendationRows.rows.map(row => loadRecommendation(pool, row.recommendation_id)));

    return {
      event,
      incidents: incidentsResult.rows.map(mapIncident),
      decisionQueue,
      commitments,
      sources: sourcesResult.rows.map(mapSource),
      observations: observationsResult.rows.map(mapObservation),
    };
  }

  async recommendation(recommendationId: string, principal: PrincipalContext): Promise<Recommendation> {
    const recommendation = await loadRecommendation(getDatabasePool(), recommendationId);
    assertModeAllowed(recommendation.mode, principal.modes);
    return recommendation;
  }

  async decide(
    recommendationId: string,
    command: DecisionCommand,
    principal: PrincipalContext,
    idempotencyKey: string,
    requestId: string,
  ): Promise<DecisionResult> {
    const requiredScope = decisionScope(command.action);
    if (!principal.scopes.includes(requiredScope)) throw forbidden(`Required scope: ${requiredScope}`);
    const requestHash = hashRequest({ recommendationId, command });

    return withTransaction(async client => {
      await client.query('SELECT pg_advisory_xact_lock(hashtext($1))', [idempotencyKey]);
      const existing = await client.query(
        `SELECT request_hash, response_body FROM idempotency_records
          WHERE idempotency_key = $1 AND principal_id = $2 AND route_key = 'recommendation-decision'`,
        [idempotencyKey, principal.principalId],
      );
      if (existing.rowCount) {
        if (existing.rows[0].request_hash !== requestHash) {
          throw conflict('IDEMPOTENCY_KEY_REUSED', 'The idempotency key was already used for a different request');
        }
        return existing.rows[0].response_body as DecisionResult;
      }

      await client.query('SELECT recommendation_id FROM recommendations WHERE recommendation_id = $1 FOR UPDATE', [recommendationId]);
      let recommendation = await loadRecommendation(client, recommendationId);
      assertModeAllowed(recommendation.mode, principal.modes);
      assertRecommendationSnapshot(
        recommendation,
        command.recommendationVersion,
        command.expectedState,
        command.evidenceSnapshotHash,
      );

      let targetState = applyRecommendationDecision(recommendation, command.action);
      const matchingRequirement = recommendation.approvalRequirements.find(
        requirement => requirement.status === 'pending'
          && requirement.agencyId === principal.agencyId
          && principal.roles.includes(requirement.roleCode),
      );

      if (['approve', 'acknowledge'].includes(command.action) && !matchingRequirement) {
        throw forbidden('The principal does not satisfy a pending approval requirement for this recommendation');
      }

      const decisionId = randomUUID();
      await client.query(
        `INSERT INTO decisions (
           decision_id, recommendation_id, recommendation_version, evidence_snapshot_hash,
           actor_principal_id, actor_agency_id, actor_role_code, action, reason_code,
           comment, confirmation_text_hash, delegated_to_principal_id, escalated_to_role_code
         ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)`,
        [
          decisionId,
          recommendationId,
          command.recommendationVersion,
          command.evidenceSnapshotHash,
          principal.principalId,
          principal.agencyId,
          matchingRequirement?.roleCode ?? principal.roles[0] ?? 'operator',
          command.action,
          command.reasonCode,
          command.comment ?? null,
          command.confirmationTextHash ?? null,
          command.delegateToPrincipalId ?? null,
          command.escalateToRoleCode ?? null,
        ],
      );

      if (matchingRequirement && ['approve', 'acknowledge'].includes(command.action)) {
        await client.query(
          `UPDATE approval_requirements SET status = 'satisfied', satisfied_at = now()
            WHERE approval_requirement_id = $1`,
          [matchingRequirement.requirementId],
        );
        const pending = await client.query(
          `SELECT count(*)::int AS count FROM approval_requirements
            WHERE recommendation_id = $1 AND status = 'pending'`,
          [recommendationId],
        );
        if (pending.rows[0].count > 0) targetState = 'awaiting_approval';
      }

      await client.query(
        `UPDATE recommendations SET state = $2, updated_at = now()
          WHERE recommendation_id = $1`,
        [recommendationId, targetState],
      );

      const createdCommitments: Commitment[] = [];
      if (targetState === 'approved') {
        const planResult = await client.query('SELECT commitment_plan FROM recommendations WHERE recommendation_id = $1', [recommendationId]);
        const plans = Array.isArray(planResult.rows[0].commitment_plan) ? planResult.rows[0].commitment_plan : [];
        for (const plan of plans) {
          if (!plan.ownerAgencyId || !plan.requestedOutcome || !plan.verificationRule) continue;
          const insert = await client.query(
            `INSERT INTO commitments (
               incident_id, recommendation_id, decision_id, mode, owner_agency_id,
               requested_outcome, state, due_at, verification_rule
             ) SELECT incident_id, recommendation_id, $2, mode, $3, $4, 'requested', $5, $6
                 FROM recommendations WHERE recommendation_id = $1
             RETURNING *`,
            [
              recommendationId,
              decisionId,
              plan.ownerAgencyId,
              plan.requestedOutcome,
              plan.dueAt ?? null,
              plan.verificationRule,
            ],
          );
          const agency = await client.query('SELECT name FROM agencies WHERE agency_id = $1', [plan.ownerAgencyId]);
          createdCommitments.push(mapCommitment({ ...insert.rows[0], owner_agency_name: agency.rows[0]?.name ?? 'Assigned agency' }));
        }
      }

      recommendation = await loadRecommendation(client, recommendationId);
      const decision: Decision = {
        decisionId,
        recommendationId,
        recommendationVersion: command.recommendationVersion,
        action: command.action,
        actor: {
          principalId: principal.principalId,
          displayName: principal.displayName,
          agencyId: principal.agencyId,
          agencyName: principal.agencyName,
          roleCode: matchingRequirement?.roleCode ?? principal.roles[0] ?? 'operator',
        },
        reasonCode: command.reasonCode,
        comment: command.comment ?? null,
        decidedAt: new Date().toISOString(),
      };
      const response: DecisionResult = { decision, recommendation, createdCommitments };

      await this.recordAuditAndOutbox(client, {
        mode: recommendation.mode,
        incidentId: recommendation.incidentId,
        actorId: principal.principalId,
        actorAgencyId: principal.agencyId,
        action: `recommendation.${command.action}`,
        objectType: 'recommendation',
        objectId: recommendationId,
        objectVersion: recommendation.version,
        requestId,
        payload: response,
      });
      await client.query(
        `INSERT INTO idempotency_records (
           idempotency_key, principal_id, route_key, request_hash, response_status,
           response_body, resource_type, resource_id, expires_at
         ) VALUES ($1,$2,'recommendation-decision',$3,200,$4,'decision',$5,now() + interval '24 hours')`,
        [idempotencyKey, principal.principalId, requestHash, JSON.stringify(response), decisionId],
      );
      return response;
    });
  }

  async transitionCommitment(
    commitmentId: string,
    command: CommitmentTransitionCommand,
    principal: PrincipalContext,
    idempotencyKey: string,
    requestId: string,
  ): Promise<Commitment> {
    if (!principal.scopes.includes('commitment:transition')) throw forbidden('Required scope: commitment:transition');
    const requestHash = hashRequest({ commitmentId, command });

    return withTransaction(async client => {
      await client.query('SELECT pg_advisory_xact_lock(hashtext($1))', [idempotencyKey]);
      const existing = await client.query(
        `SELECT request_hash, response_body FROM idempotency_records
          WHERE idempotency_key = $1 AND principal_id = $2 AND route_key = 'commitment-transition'`,
        [idempotencyKey, principal.principalId],
      );
      if (existing.rowCount) {
        if (existing.rows[0].request_hash !== requestHash) throw conflict('IDEMPOTENCY_KEY_REUSED', 'Idempotency key reused');
        return existing.rows[0].response_body as Commitment;
      }

      const result = await client.query(
        `SELECT c.*, a.name AS owner_agency_name
           FROM commitments c JOIN agencies a ON a.agency_id = c.owner_agency_id
          WHERE c.commitment_id = $1 FOR UPDATE`,
        [commitmentId],
      );
      if (!result.rowCount) throw notFound('Commitment', commitmentId);
      const current = mapCommitment(result.rows[0]);
      assertModeAllowed(current.mode, principal.modes);
      if (current.version !== command.expectedVersion) {
        throw conflict('COMMITMENT_VERSION_CONFLICT', 'The commitment changed after it was opened', {
          expectedVersion: command.expectedVersion,
          actualVersion: current.version,
        });
      }
      if (current.ownerAgencyId !== principal.agencyId && !principal.roles.includes('event_mobility_lead')) {
        throw forbidden('Only the owning agency or command lead may transition this commitment');
      }
      assertCommitmentTransition(current.state, command.targetState);
      assertVerifiedEvidence(command.targetState, command.evidenceIds ?? []);

      const updated = await client.query(
        `UPDATE commitments
            SET state = $2, blocker = CASE WHEN $2 = 'blocked' THEN $3 ELSE NULL END,
                version = version + 1, updated_at = now()
          WHERE commitment_id = $1 RETURNING *`,
        [commitmentId, command.targetState, command.comment ?? null],
      );
      await client.query(
        `INSERT INTO commitment_transitions (
           commitment_id, from_state, to_state, actor_principal_id, reason_code, comment, evidence_ids
         ) VALUES ($1,$2,$3,$4,$5,$6,$7)`,
        [
          commitmentId,
          current.state,
          command.targetState,
          principal.principalId,
          command.reasonCode,
          command.comment ?? null,
          command.evidenceIds ?? [],
        ],
      );
      const commitment = mapCommitment({ ...updated.rows[0], owner_agency_name: current.ownerAgencyName });
      await this.recordAuditAndOutbox(client, {
        mode: current.mode,
        incidentId: current.incidentId,
        actorId: principal.principalId,
        actorAgencyId: principal.agencyId,
        action: 'commitment.transition',
        objectType: 'commitment',
        objectId: commitmentId,
        objectVersion: commitment.version,
        requestId,
        payload: commitment,
      });
      await client.query(
        `INSERT INTO idempotency_records (
           idempotency_key, principal_id, route_key, request_hash, response_status,
           response_body, resource_type, resource_id, expires_at
         ) VALUES ($1,$2,'commitment-transition',$3,200,$4,'commitment',$5,now() + interval '24 hours')`,
        [idempotencyKey, principal.principalId, requestHash, JSON.stringify(commitment), commitmentId],
      );
      return commitment;
    });
  }

  async audit(eventId: string, afterSequence: number, limit: number, principal: PrincipalContext): Promise<AuditRecord[]> {
    if (!principal.scopes.includes('audit:read')) throw forbidden('Required scope: audit:read');
    const result = await getDatabasePool().query(
      `SELECT * FROM audit_events
        WHERE event_id = $1 AND sequence_no > $2
        ORDER BY sequence_no LIMIT $3`,
      [eventId, afterSequence, Math.min(limit, 500)],
    );
    return result.rows.map(row => ({
      auditId: row.audit_id,
      sequenceNo: Number(row.sequence_no),
      mode: row.mode,
      action: row.action,
      objectType: row.object_type,
      objectId: row.object_id,
      objectVersion: row.object_version,
      actorId: row.actor_id,
      actorAgencyId: row.actor_agency_id,
      outcome: row.outcome,
      metadata: row.metadata ?? {},
      createdAt: row.created_at.toISOString(),
    }));
  }

  async stream(eventId: string, afterSequence: number, principal: PrincipalContext): Promise<EventStreamRecord[]> {
    const event = await this.activeEvent('live');
    if (event?.eventId !== eventId) throw notFound('Active operational event', eventId);
    assertModeAllowed(event.mode, principal.modes);
    const result = await getDatabasePool().query(
      `SELECT outbox_id, event_type, aggregate_type, aggregate_id, aggregate_version, mode, payload, occurred_at
         FROM outbox_events
        WHERE (payload->>'eventId' = $1 OR payload->>'event_id' = $1)
          AND extract(epoch from occurred_at) * 1000 > $2
        ORDER BY occurred_at LIMIT 200`,
      [eventId, afterSequence],
    );
    return result.rows.map(row => ({
      eventId: row.outbox_id,
      eventType: row.event_type,
      mode: row.mode,
      aggregateType: row.aggregate_type,
      aggregateId: row.aggregate_id,
      aggregateVersion: row.aggregate_version,
      occurredAt: row.occurred_at.toISOString(),
      payload: row.payload,
    }));
  }

  private async recordAuditAndOutbox(
    client: DatabaseClient,
    input: {
      mode: OperationalMode;
      incidentId: string;
      actorId: string;
      actorAgencyId: string;
      action: string;
      objectType: string;
      objectId: string;
      objectVersion: number;
      requestId: string;
      payload: unknown;
    },
  ): Promise<void> {
    const eventResult = await client.query('SELECT event_id FROM incidents WHERE incident_id = $1', [input.incidentId]);
    const eventId = eventResult.rows[0]?.event_id ?? null;
    await client.query(
      `INSERT INTO audit_events (
         mode, event_id, incident_id, actor_type, actor_id, actor_agency_id,
         action, object_type, object_id, object_version, request_id, outcome, metadata
       ) VALUES ($1,$2,$3,'human',$4,$5,$6,$7,$8,$9,$10,'accepted',$11)`,
      [
        input.mode,
        eventId,
        input.incidentId,
        input.actorId,
        input.actorAgencyId,
        input.action,
        input.objectType,
        input.objectId,
        input.objectVersion,
        input.requestId,
        JSON.stringify({ source: 'operational-api' }),
      ],
    );
    await client.query(
      `INSERT INTO outbox_events (
         event_type, aggregate_type, aggregate_id, aggregate_version, mode, payload
       ) VALUES ($1,$2,$3,$4,$5,$6)`,
      [input.action, input.objectType, input.objectId, input.objectVersion, input.mode, JSON.stringify({ eventId, data: input.payload })],
    );
  }
}
