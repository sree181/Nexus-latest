import { createHash } from 'node:crypto';
import { getDatabasePool, withTransaction } from './database.js';

const LIVE_INCIDENT_ID = '33333333-3333-4333-8333-333333333333';
const LIVE_RECOMMENDATION_ID = '44444444-4444-4444-8444-444444444444';
const COMMAND_AGENCY_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PARKING_AGENCY_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const TRAFFIC_AGENCY_ID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const COMMAND_PRINCIPAL_ID = '11111111-1111-4111-8111-111111111111';

export interface AdvisoryEvidenceRow {
  evidence_id: string;
  source_name: string;
  summary: string;
  geometry_geojson: unknown;
  connector_code: string | null;
}

export function snapshotHash(evidenceIds: string[]): string {
  return createHash('sha256').update(evidenceIds.slice().sort().join('|')).digest('hex');
}

export function representativePoint(geometry: unknown): Record<string, unknown> | null {
  if (!geometry || typeof geometry !== 'object') return null;
  const value = geometry as { type?: string; coordinates?: unknown };
  const coordinates = value.coordinates;
  if (value.type === 'Point' && Array.isArray(coordinates) && coordinates.length >= 2) {
    return { type: 'Point', coordinates: [Number(coordinates[0]), Number(coordinates[1])] };
  }
  if (value.type === 'LineString' && Array.isArray(coordinates) && Array.isArray(coordinates[0])) {
    const midpoint = coordinates[Math.floor(coordinates.length / 2)] as unknown[];
    if (Array.isArray(midpoint) && midpoint.length >= 2) {
      return { type: 'Point', coordinates: [Number(midpoint[0]), Number(midpoint[1])] };
    }
  }
  if (value.type === 'MultiLineString' && Array.isArray(coordinates) && Array.isArray(coordinates[0]) && Array.isArray((coordinates[0] as unknown[])[0])) {
    const first = (coordinates[0] as unknown[])[0] as unknown[];
    return { type: 'Point', coordinates: [Number(first[0]), Number(first[1])] };
  }
  if (value.type === 'Polygon' && Array.isArray(coordinates) && Array.isArray(coordinates[0]) && Array.isArray((coordinates[0] as unknown[])[0])) {
    const first = (coordinates[0] as unknown[])[0] as unknown[];
    return { type: 'Point', coordinates: [Number(first[0]), Number(first[1])] };
  }
  return null;
}

export function composeLiveAdvisory(rows: AdvisoryEvidenceRow[]) {
  const city = rows.filter(row => row.connector_code === 'coa-road-closures-v1');
  const transit = rows.filter(row => row.connector_code === 'auburn-eta-spot-v1');
  const material = (city.length ? city : rows).slice(0, 8);
  const evidenceIds = material.map(row => row.evidence_id);
  const roads = [...new Set(city.map(row => row.summary.replace(/^(Block|Closure|Detour): /, '').split(' — ')[0]))].slice(0, 4);
  return {
    evidenceIds,
    hash: snapshotHash(evidenceIds),
    cityCount: city.length,
    transitCount: transit.length,
    geometry: representativePoint(material.find(row => row.geometry_geojson)?.geometry_geojson) ?? null,
    title: city.length
      ? 'City-published mobility restrictions near Jordan-Hare'
      : 'Authoritative mobility observations require command review',
    whatChanged: city.length
      ? `City of Auburn published ${city.length} current or recent road restriction record${city.length === 1 ? '' : 's'}${roads.length ? ` including ${roads.join(', ')}` : ''}.`
      : `${rows.length} authoritative observations were ingested for the live event.`,
    whyItMatters: 'Unreviewed published restrictions can conflict with ingress, remote-lot movement, or emergency-access assumptions during the Game Day window.',
    recommendedAction: city.length
      ? 'Review the cited City restriction records, confirm they remain in effect with Event Command, and issue manual agency handoffs for affected ingress and wayfinding. Do not change signal timing or close a road from Nexus.'
      : 'Review the cited authoritative observations and record a named decision. No agency system is controlled from this approval.',
  };
}

export async function projectLiveAdvisory(eventId: string): Promise<void> {
  const pool = getDatabasePool();
  const evidence = await pool.query<AdvisoryEvidenceRow>(
    `SELECT e.evidence_id, s.name AS source_name, e.summary, e.geometry_geojson, s.connector_code
       FROM evidence_events e
       JOIN sources s ON s.source_id = e.source_id
      WHERE e.event_id = $1
      ORDER BY e.observed_at DESC
      LIMIT 40`,
    [eventId],
  );
  if (!evidence.rowCount) {
    console.info('[advisory] No authoritative evidence yet; recommendation not opened');
    return;
  }

  const advisory = composeLiveAdvisory(evidence.rows);
  await withTransaction(async client => {
    const existing = await client.query(
      'SELECT state, evidence_snapshot_hash FROM recommendations WHERE recommendation_id = $1',
      [LIVE_RECOMMENDATION_ID],
    );
    if (existing.rowCount && ['approved', 'rejected'].includes(existing.rows[0].state)) {
      console.info('[advisory] A completed recommendation already exists; not replacing a recorded human decision');
      return;
    }

    await client.query(
      `INSERT INTO incidents (
         incident_id, event_id, mode, title, what_changed, why_it_matters, severity, status,
         command_owner_principal_id, command_owner_agency_id, location_geojson, affected_services,
         constraints, detected_at
       ) VALUES ($1,$2,'live',$3,$4,$5,'high','active',$6,$7,$8::jsonb,$9,$10,now())
       ON CONFLICT (incident_id) DO UPDATE SET
         title = EXCLUDED.title,
         what_changed = EXCLUDED.what_changed,
         why_it_matters = EXCLUDED.why_it_matters,
         location_geojson = EXCLUDED.location_geojson,
         affected_services = EXCLUDED.affected_services,
         status = 'active',
         version = incidents.version + 1,
         updated_at = now()`,
      [
        LIVE_INCIDENT_ID, eventId, advisory.title, advisory.whatChanged, advisory.whyItMatters,
        COMMAND_PRINCIPAL_ID, COMMAND_AGENCY_ID,
        advisory.geometry ? JSON.stringify(advisory.geometry) : null,
        advisory.cityCount ? ['traffic', 'ingress', 'event_command'] : ['event_command'],
        ['Preserve emergency corridor', 'No traffic-signal control', 'Manual or deep-link agency handoff only'],
      ],
    );

    for (const evidenceId of advisory.evidenceIds) {
      await client.query(
        `INSERT INTO incident_evidence (incident_id, evidence_id, material)
         VALUES ($1,$2,true)
         ON CONFLICT (incident_id, evidence_id) DO NOTHING`,
        [LIVE_INCIDENT_ID, evidenceId],
      );
    }

    const existingFinding = await client.query(
      'SELECT finding_id FROM agent_findings WHERE incident_id = $1 AND evidence_snapshot_hash = $2 LIMIT 1',
      [LIVE_INCIDENT_ID, advisory.hash],
    );
    if (!existingFinding.rowCount) {
      await client.query(
        `INSERT INTO agent_findings (
           incident_id, agent_code, model_name, model_version, incident_snapshot_version,
           evidence_snapshot_hash, observation, interpretation, candidate_action, confidence, limitations
         ) VALUES ($1,'evidence-advisory','Nexus evidence-bound advisory','live-public-v1',1,$2,$3,$4,$5,0.6200,$6)`,
        [
          LIVE_INCIDENT_ID, advisory.hash, advisory.whatChanged, advisory.whyItMatters, advisory.recommendedAction,
          'This advisory is derived only from ingested authoritative records. It does not include parking occupancy, emergency-access state, or TomTom flow unless those connectors are configured.',
        ],
      );
    }

    const commitmentPlan = [
      {
        ownerAgencyId: TRAFFIC_AGENCY_ID,
        requestedOutcome: 'Confirm the cited City restriction records with field operations and apply the pre-approved ingress adjustment if still in effect.',
        verificationRule: 'Field confirmation or updated City RoadClosuresPublic record',
        dueAt: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
      },
      {
        ownerAgencyId: PARKING_AGENCY_ID,
        requestedOutcome: 'Notify parking/transit supervisors of the restriction and keep remote-lot wayfinding consistent with the published City record.',
        verificationRule: 'Supervisor acknowledgement recorded in Nexus',
        dueAt: new Date(Date.now() + 3 * 60 * 60 * 1000).toISOString(),
      },
    ];

    await client.query(
      `INSERT INTO recommendations (
         recommendation_id, incident_id, mode, recommendation_version, state, priority,
         what_changed, why_it_matters, recommended_action, expected_effect, limitations, constraints,
         commitment_plan, evidence_snapshot_hash, generated_by_model, generated_by_model_version, expires_at
       ) VALUES ($1,$2,'live',1,'awaiting_approval','high',$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,now() + interval '6 hours')
       ON CONFLICT (recommendation_id) DO UPDATE SET
         what_changed = EXCLUDED.what_changed,
         why_it_matters = EXCLUDED.why_it_matters,
         recommended_action = EXCLUDED.recommended_action,
         expected_effect = EXCLUDED.expected_effect,
         limitations = EXCLUDED.limitations,
         commitment_plan = EXCLUDED.commitment_plan,
         evidence_snapshot_hash = EXCLUDED.evidence_snapshot_hash,
         expires_at = EXCLUDED.expires_at,
         updated_at = now()
         WHERE recommendations.state IN ('draft', 'awaiting_acknowledgement', 'awaiting_approval')`,
      [
        LIVE_RECOMMENDATION_ID, LIVE_INCIDENT_ID, advisory.whatChanged, advisory.whyItMatters, advisory.recommendedAction,
        'Named agencies review and acknowledge the published restriction. Approval creates commitments only; Nexus does not execute road control.',
        'Derived only from ingested public authoritative records. Parking occupancy, emergency-access state, and licensed road-flow are absent unless their connectors are configured.',
        ['Preserve emergency corridor', 'Preserve ADA loading', 'No traffic-signal control', 'Manual agency handoff only'],
        JSON.stringify(commitmentPlan), advisory.hash,
        'Nexus evidence-bound advisory', 'live-public-v1',
      ],
    );

    await client.query('DELETE FROM recommendation_evidence WHERE recommendation_id = $1', [LIVE_RECOMMENDATION_ID]);
    for (const evidenceId of advisory.evidenceIds) {
      await client.query(
        `INSERT INTO recommendation_evidence (recommendation_id, evidence_id, role)
         VALUES ($1,$2,'material')
         ON CONFLICT (recommendation_id, evidence_id) DO NOTHING`,
        [LIVE_RECOMMENDATION_ID, evidenceId],
      );
    }

    await client.query(
      `INSERT INTO approval_requirements (recommendation_id, agency_id, role_code, sequence, quorum, delegation_allowed, status)
       VALUES ($1,$2,'event_mobility_lead',1,1,false,'pending')
       ON CONFLICT (recommendation_id, agency_id, role_code) DO NOTHING`,
      [LIVE_RECOMMENDATION_ID, COMMAND_AGENCY_ID],
    );
  });

  console.info('[advisory] Opened evidence-bound recommendation from authoritative observations', {
    eventId,
    evidenceCount: advisory.evidenceIds.length,
    cityRecords: advisory.cityCount,
    transitRecords: advisory.transitCount,
  });
}
