import { getDatabasePool, withTransaction } from '../database.js';
import { composeDeskFindings } from '../agents/orchestrator.js';
import {
  evaluateRules,
  hasPredicate,
  representativePoint,
  type DetectionEvidence,
  type DetectionMatch,
  type DetectionRuleDefinition,
  type Playbook,
  type Severity,
} from './rules.js';

const MODEL_NAME = 'Nexus evidence-bound detection';
const MODEL_VERSION = 'detection-v1';
/**
 * How recently a connector must have confirmed a record is still published upstream. This is
 * deliberately not the upstream `observed_at`: a City closure last edited in April can still be
 * in force today, while a crash withdrawn ten minutes ago must stop counting.
 */
const CONFIRMED_WITHIN_HOURS = 6;
const OPEN_STATES = ['draft', 'awaiting_acknowledgement', 'awaiting_approval'];
const OPEN_INCIDENT_STATES = ['new', 'triaged', 'active', 'monitoring'];

export interface DetectionQueryable {
  query<T = Record<string, unknown>>(text: string, values?: unknown[]): Promise<{ rows: T[]; rowCount: number }>;
}

/** Injectable so the projection can be exercised against an embedded Postgres in tests. */
export interface DetectionStore extends DetectionQueryable {
  transaction<T>(operation: (client: DetectionQueryable) => Promise<T>): Promise<T>;
}

export const databaseDetectionStore: DetectionStore = {
  async query<T>(text: string, values?: unknown[]) {
    const result = await getDatabasePool().query(text, values as never[]);
    return { rows: result.rows as T[], rowCount: result.rowCount ?? result.rows.length };
  },
  transaction(operation) {
    return withTransaction(client => operation({
      async query<T>(text: string, values?: unknown[]) {
        const result = await client.query(text, values as never[]);
        return { rows: result.rows as T[], rowCount: result.rowCount ?? result.rows.length };
      },
    }));
  },
};

export interface DetectionSummary {
  eventId: string;
  packCode: string | null;
  evidenceConsidered: number;
  incidentsOpened: number;
  incidentsUpdated: number;
  incidentsResolved: number;
  recommendationsOpened: number;
  skippedRules: string[];
}

interface EventRow {
  event_id: string;
  mode: string;
  event_type: string;
  scenario_pack_code: string | null;
  command_owner_principal_id: string | null;
  command_owner_agency_id: string | null;
}

interface PackRow {
  pack_code: string;
  connector_codes: string[];
  agent_codes: string[];
}

interface RuleRow {
  pack_code: string;
  rule_code: string;
  connector_code: string;
  agent_code: string;
  name: string;
  why_it_matters: string;
  severity: Severity;
  affected_services: string[];
  constraints: string[];
  playbook: Partial<Playbook> | null;
}

interface EvidenceRow {
  evidence_id: string;
  connector_code: string | null;
  source_event_id: string;
  source_name: string;
  summary: string;
  observed_at: Date;
  content_hash: string;
  geometry_geojson: unknown;
  normalized_attributes: Record<string, unknown> | null;
}

function toPlaybook(value: Partial<Playbook> | null): Playbook {
  return {
    recommendedAction: value?.recommendedAction ?? 'Review the cited authoritative record and record a named decision.',
    expectedEffect: value?.expectedEffect ?? 'Named agencies acknowledge the condition. Nexus records the decision only.',
    limitations: value?.limitations ?? 'Derived only from the cited authoritative records.',
    approvals: Array.isArray(value?.approvals) ? value.approvals : [],
    commitments: Array.isArray(value?.commitments) ? value.commitments : [],
  };
}

function toDetectionEvidence(row: EvidenceRow): DetectionEvidence {
  return {
    evidenceId: row.evidence_id,
    connectorCode: row.connector_code,
    sourceEventId: row.source_event_id,
    sourceName: row.source_name,
    summary: row.summary,
    observedAt: new Date(row.observed_at).toISOString(),
    contentHash: row.content_hash,
    geometryGeojson: row.geometry_geojson,
    attributes: row.normalized_attributes && typeof row.normalized_attributes === 'object' ? row.normalized_attributes : {},
  };
}

async function resolvePack(store: DetectionStore, eventId: string): Promise<{ event: EventRow; pack: PackRow } | null> {
  const eventResult = await store.query<EventRow>(
    `SELECT event_id, mode, event_type, scenario_pack_code, command_owner_principal_id, command_owner_agency_id
       FROM operational_events WHERE event_id = $1`,
    [eventId],
  );
  const event = eventResult.rows[0];
  if (!event) return null;

  const packResult = await store.query<PackRow>(
    `SELECT pack_code, connector_codes, agent_codes FROM scenario_packs
      WHERE active AND (pack_code = $1 OR ($1 IS NULL AND event_type = $2))
      ORDER BY (pack_code = $1) DESC
      LIMIT 1`,
    [event.scenario_pack_code, event.event_type],
  );
  const pack = packResult.rows[0];
  return pack ? { event, pack } : null;
}

async function agencyIdsByCode(client: DetectionQueryable): Promise<Map<string, string>> {
  const result = await client.query<{ agency_id: string; code: string }>('SELECT agency_id, code FROM agencies WHERE active');
  return new Map(result.rows.map(row => [row.code, row.agency_id]));
}

function commitmentPlan(playbook: Playbook, agencies: Map<string, string>): Array<Record<string, unknown>> {
  return playbook.commitments.flatMap(commitment => {
    const ownerAgencyId = agencies.get(commitment.agencyCode);
    if (!ownerAgencyId) return [];
    return [{
      ownerAgencyId,
      requestedOutcome: commitment.requestedOutcome,
      verificationRule: commitment.verificationRule,
      dueAt: new Date(Date.now() + commitment.dueInMinutes * 60_000).toISOString(),
    }];
  });
}

async function projectMatch(
  client: DetectionQueryable,
  event: EventRow,
  packCode: string,
  match: DetectionMatch,
  agencies: Map<string, string>,
  summary: DetectionSummary,
  /** Every observation in the window. The desks review the same snapshot, not just the match. */
  snapshot: DetectionEvidence[],
  staffedAgents: string[],
  liveConnectors: string[],
): Promise<string | null> {
  const { rule } = match;
  const playbook = rule.playbook;
  const geometry = representativePoint(match.primary.geometryGeojson);
  const evidenceIds = match.evidence.map(item => item.evidenceId);

  const existing = await client.query<{ incident_id: string; status: string; version: number }>(
    `SELECT incident_id, status, version FROM incidents
      WHERE event_id = $1 AND origin_connector_code = $2 AND origin_external_key = $3`,
    [event.event_id, rule.connectorCode, match.externalKey],
  );

  // A human who closed an incident has made a judgement. Detection does not overrule it.
  if (existing.rows[0]?.status === 'closed') return null;

  let incidentId: string;
  let incidentVersion: number;

  if (existing.rowCount) {
    const updated = await client.query<{ version: number }>(
      `UPDATE incidents SET
         title = $2, what_changed = $3, why_it_matters = $4, severity = $5,
         location_geojson = $6::jsonb, affected_services = $7, constraints = $8,
         scenario_pack_code = $9, detection_rule_code = $10,
         status = CASE WHEN status = 'resolved' THEN 'active' ELSE status END,
         resolved_at = NULL, version = version + 1, updated_at = now()
       WHERE incident_id = $1
       RETURNING version`,
      [
        existing.rows[0].incident_id, match.title, match.whatChanged, rule.whyItMatters, match.severity,
        geometry ? JSON.stringify(geometry) : null, rule.affectedServices, rule.constraints,
        packCode, rule.ruleCode,
      ],
    );
    incidentId = existing.rows[0].incident_id;
    incidentVersion = updated.rows[0].version;
    summary.incidentsUpdated += 1;
  } else {
    const inserted = await client.query<{ incident_id: string; version: number }>(
      `INSERT INTO incidents (
         event_id, mode, title, what_changed, why_it_matters, severity, status,
         command_owner_principal_id, command_owner_agency_id, location_geojson,
         affected_services, constraints, detected_at,
         scenario_pack_code, detection_rule_code, origin_connector_code, origin_external_key
       ) VALUES ($1,$2,$3,$4,$5,$6,'active',$7,$8,$9::jsonb,$10,$11,$12,$13,$14,$15,$16)
       RETURNING incident_id, version`,
      [
        event.event_id, event.mode, match.title, match.whatChanged, rule.whyItMatters, match.severity,
        event.command_owner_principal_id, event.command_owner_agency_id,
        geometry ? JSON.stringify(geometry) : null,
        rule.affectedServices, rule.constraints, match.primary.observedAt,
        packCode, rule.ruleCode, rule.connectorCode, match.externalKey,
      ],
    );
    incidentId = inserted.rows[0].incident_id;
    incidentVersion = inserted.rows[0].version;
    summary.incidentsOpened += 1;
  }

  for (const evidenceId of evidenceIds) {
    await client.query(
      `INSERT INTO incident_evidence (incident_id, evidence_id, material)
       VALUES ($1,$2,true) ON CONFLICT (incident_id, evidence_id) DO NOTHING`,
      [incidentId, evidenceId],
    );
  }

  const composition = await composeDeskFindings({
    staffedAgentCodes: staffedAgents,
    match,
    snapshot,
    liveConnectors,
  });
  for (const finding of composition.findings) {
    await client.query(
      `INSERT INTO agent_findings (
         incident_id, agent_code, model_name, model_version, incident_snapshot_version,
         evidence_snapshot_hash, observation, interpretation, candidate_action, confidence,
         limitations, status, cited_evidence_ids, conflicts
       ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::uuid[],$14::jsonb)
       ON CONFLICT (incident_id, agent_code, evidence_snapshot_hash) DO UPDATE SET
         incident_snapshot_version = EXCLUDED.incident_snapshot_version,
         observation = EXCLUDED.observation,
         interpretation = EXCLUDED.interpretation,
         candidate_action = EXCLUDED.candidate_action,
         confidence = EXCLUDED.confidence,
         limitations = EXCLUDED.limitations,
         status = EXCLUDED.status,
         cited_evidence_ids = EXCLUDED.cited_evidence_ids,
         conflicts = EXCLUDED.conflicts,
         model_name = EXCLUDED.model_name,
         model_version = EXCLUDED.model_version`,
      [
        incidentId, finding.agentCode, finding.modelName ?? MODEL_NAME, finding.modelVersion ?? MODEL_VERSION,
        incidentVersion, match.evidenceHash,
        finding.observation, finding.interpretation, finding.candidateAction, finding.confidence,
        finding.limitations, finding.status, finding.citedEvidenceIds, JSON.stringify(finding.conflicts),
      ],
    );
  }

  const latest = await client.query<{
    recommendation_id: string; recommendation_version: number; state: string; evidence_snapshot_hash: string;
  }>(
    `SELECT recommendation_id, recommendation_version, state, evidence_snapshot_hash
       FROM recommendations WHERE incident_id = $1 ORDER BY recommendation_version DESC LIMIT 1`,
    [incidentId],
  );
  const current = latest.rows[0];
  const plan = commitmentPlan(playbook, agencies);

  if (current && OPEN_STATES.includes(current.state)) {
    if (current.evidence_snapshot_hash !== match.evidenceHash) {
      await client.query(
        `UPDATE recommendations SET
           priority = $2, what_changed = $3, why_it_matters = $4, recommended_action = $5,
           expected_effect = $6, limitations = $7, constraints = $8, commitment_plan = $9::jsonb,
           evidence_snapshot_hash = $10, expires_at = now() + interval '6 hours', updated_at = now()
         WHERE recommendation_id = $1`,
        [
          current.recommendation_id, match.severity, match.whatChanged, rule.whyItMatters, playbook.recommendedAction,
          playbook.expectedEffect, playbook.limitations, rule.constraints, JSON.stringify(plan), match.evidenceHash,
        ],
      );
      await syncRecommendationEvidence(client, current.recommendation_id, evidenceIds);
    }
    return incidentId;
  }

  // A decided recommendation is a recorded human decision. New evidence supersedes it with a
  // new version rather than quietly rewriting what somebody already approved or rejected.
  if (current && current.evidence_snapshot_hash === match.evidenceHash) return incidentId;

  const version = current ? current.recommendation_version + 1 : 1;
  const inserted = await client.query<{ recommendation_id: string }>(
    `INSERT INTO recommendations (
       incident_id, mode, recommendation_version, state, priority,
       what_changed, why_it_matters, recommended_action, expected_effect, limitations, constraints,
       commitment_plan, evidence_snapshot_hash, generated_by_model, generated_by_model_version, expires_at
     ) VALUES ($1,$2,$3,'awaiting_approval',$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,now() + interval '6 hours')
     RETURNING recommendation_id`,
    [
      incidentId, event.mode, version, match.severity,
      match.whatChanged, rule.whyItMatters, playbook.recommendedAction, playbook.expectedEffect,
      playbook.limitations, rule.constraints, JSON.stringify(plan), match.evidenceHash, MODEL_NAME, MODEL_VERSION,
    ],
  );
  const recommendationId = inserted.rows[0].recommendation_id;
  summary.recommendationsOpened += 1;

  if (current) {
    await client.query(
      'UPDATE recommendations SET superseded_by_recommendation_id = $2, updated_at = now() WHERE recommendation_id = $1',
      [current.recommendation_id, recommendationId],
    );
  }

  await syncRecommendationEvidence(client, recommendationId, evidenceIds);

  for (const [index, approval] of playbook.approvals.entries()) {
    const agencyId = agencies.get(approval.agencyCode);
    if (!agencyId) continue;
    await client.query(
      `INSERT INTO approval_requirements (recommendation_id, agency_id, role_code, sequence, quorum, delegation_allowed, status)
       VALUES ($1,$2,$3,$4,1,false,'pending')
       ON CONFLICT (recommendation_id, agency_id, role_code) DO NOTHING`,
      [recommendationId, agencyId, approval.roleCode, index + 1],
    );
  }

  return incidentId;
}

async function syncRecommendationEvidence(client: DetectionQueryable, recommendationId: string, evidenceIds: string[]): Promise<void> {
  await client.query(
    'DELETE FROM recommendation_evidence WHERE recommendation_id = $1 AND evidence_id <> ALL($2::uuid[])',
    [recommendationId, evidenceIds],
  );
  for (const evidenceId of evidenceIds) {
    await client.query(
      `INSERT INTO recommendation_evidence (recommendation_id, evidence_id, role)
       VALUES ($1,$2,'material') ON CONFLICT (recommendation_id, evidence_id) DO NOTHING`,
      [recommendationId, evidenceId],
    );
  }
}

/**
 * Closes incidents whose upstream record is gone. Only connectors that reported in this cycle
 * are considered, so a feed outage never looks like a corridor clearing.
 */
async function resolveClearedIncidents(
  client: DetectionQueryable,
  eventId: string,
  observedConnectors: string[],
  matchedKeys: Set<string>,
  summary: DetectionSummary,
): Promise<void> {
  if (!observedConnectors.length) return;
  const open = await client.query<{ incident_id: string; origin_connector_code: string; origin_external_key: string }>(
    `SELECT incident_id, origin_connector_code, origin_external_key FROM incidents
      WHERE event_id = $1 AND status = ANY($2::text[])
        AND origin_external_key IS NOT NULL AND origin_connector_code = ANY($3::text[])`,
    [eventId, OPEN_INCIDENT_STATES, observedConnectors],
  );

  for (const incident of open.rows) {
    if (matchedKeys.has(`${incident.origin_connector_code}|${incident.origin_external_key}`)) continue;
    await client.query(
      `UPDATE incidents SET status = 'resolved', resolved_at = now(), version = version + 1, updated_at = now()
        WHERE incident_id = $1`,
      [incident.incident_id],
    );
    await client.query(
      `UPDATE recommendations SET state = 'expired', updated_at = now()
        WHERE incident_id = $1 AND state = ANY($2::text[])`,
      [incident.incident_id, OPEN_STATES],
    );
    summary.incidentsResolved += 1;
  }
}

export async function runDetection(eventId: string, store: DetectionStore = databaseDetectionStore): Promise<DetectionSummary> {
  const summary: DetectionSummary = {
    eventId,
    packCode: null,
    evidenceConsidered: 0,
    incidentsOpened: 0,
    incidentsUpdated: 0,
    incidentsResolved: 0,
    recommendationsOpened: 0,
    skippedRules: [],
  };

  const resolved = await resolvePack(store, eventId);
  if (!resolved) {
    console.info('[detection] No scenario pack is bound to this operating window; nothing was evaluated', { eventId });
    return summary;
  }
  const { event, pack } = resolved;
  summary.packCode = pack.pack_code;

  const ruleResult = await store.query<RuleRow>(
    'SELECT * FROM detection_rules WHERE pack_code = $1 AND active ORDER BY rule_code',
    [pack.pack_code],
  );
  const rules: DetectionRuleDefinition[] = [];
  for (const row of ruleResult.rows) {
    if (!hasPredicate(row.rule_code)) {
      summary.skippedRules.push(row.rule_code);
      continue;
    }
    rules.push({
      packCode: row.pack_code,
      ruleCode: row.rule_code,
      connectorCode: row.connector_code,
      agentCode: row.agent_code,
      name: row.name,
      whyItMatters: row.why_it_matters,
      severity: row.severity,
      affectedServices: row.affected_services,
      constraints: row.constraints,
      playbook: toPlaybook(row.playbook),
    });
  }
  if (!rules.length) return summary;

  const evidenceResult = await store.query<EvidenceRow>(
    `SELECT e.evidence_id, s.connector_code, e.source_event_id, s.name AS source_name, e.summary,
            e.observed_at, e.content_hash, e.geometry_geojson, e.normalized_attributes
       FROM evidence_events e
       JOIN sources s ON s.source_id = e.source_id
      WHERE e.event_id = $1
        AND s.connector_code = ANY($2::text[])
        AND e.received_at > now() - ($3 || ' hours')::interval
      ORDER BY e.observed_at DESC
      LIMIT 800`,
    [eventId, pack.connector_codes, String(CONFIRMED_WITHIN_HOURS)],
  );
  const evidence = evidenceResult.rows.map(toDetectionEvidence);
  summary.evidenceConsidered = evidence.length;

  const observedConnectors = [...new Set(evidence.map(item => item.connectorCode).filter((code): code is string => Boolean(code)))];
  const matches = evaluateRules(rules, evidence);
  const matchedKeys = new Set(matches.map(match => `${match.rule.connectorCode}|${match.externalKey}`));

  await store.transaction(async client => {
    await client.query('SELECT pg_advisory_xact_lock(hashtext($1))', [`nexus-detection:${eventId}`]);
    const agencies = await agencyIdsByCode(client);
    for (const match of matches) {
      await projectMatch(client, event, pack.pack_code, match, agencies, summary, evidence, pack.agent_codes ?? [], observedConnectors);
    }
    await resolveClearedIncidents(client, eventId, observedConnectors, matchedKeys, summary);
  });

  console.info('[detection] Evaluated authoritative evidence against the active scenario pack', summary);
  return summary;
}
