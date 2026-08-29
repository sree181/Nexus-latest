import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { PGlite } from '@electric-sql/pglite';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { runDetection, type DetectionQueryable, type DetectionStore } from './service.js';

const TIMEOUT_MS = 60_000;
const EVENT_ID = '22222222-2222-4222-8222-222222222222';
const ALGO_SOURCE_ID = '77777777-7777-4777-8777-777777777771';
const CITY_SOURCE_ID = '77777777-7777-4777-8777-777777777772';
const ALDOT_AGENCY_ID = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';

let database: PGlite;
let cachedSql: string | null = null;

async function migrationSql(): Promise<string> {
  if (cachedSql) return cachedSql;
  const files = [
    '001_operational_foundation.sql',
    '002_authoritative_connectors.sql',
    '003_temporal_operational_graph.sql',
    '004_live_command_window.sql',
    '005_scenario_packs_and_detection.sql',
    '006_public_hazard_sources.sql',
  ];
  const parts = await Promise.all(files.map(file => readFile(
    path.resolve(process.cwd(), 'server/operational/migrations', file),
    'utf8',
  )));
  cachedSql = parts.join('\n').replace(/CREATE EXTENSION IF NOT EXISTS pgcrypto;?/i, '');
  return cachedSql;
}

function storeFor(target: PGlite | { query: PGlite['query'] }): DetectionQueryable {
  return {
    async query<T>(text: string, values?: unknown[]) {
      const result = await target.query<T>(text, values as never[]);
      return { rows: result.rows as T[], rowCount: result.rows.length || (result.affectedRows ?? 0) };
    },
  };
}

const detectionStore: DetectionStore = {
  query: (text, values) => storeFor(database).query(text, values),
  async transaction(operation) {
    // PGlite serialises statements, so the projection runs against the same connection.
    await database.query('BEGIN');
    try {
      const result = await operation(storeFor(database));
      await database.query('COMMIT');
      return result;
    } catch (error) {
      await database.query('ROLLBACK');
      throw error;
    }
  },
};

async function seedSources(): Promise<void> {
  await database.query(
    `INSERT INTO agencies (agency_id, code, name, agency_type)
     VALUES ($1, 'aldot', 'Alabama Department of Transportation', 'authority')
     ON CONFLICT (code) DO NOTHING`,
    [ALDOT_AGENCY_ID],
  );
  for (const [sourceId, code, name, connectorCode] of [
    [ALGO_SOURCE_ID, 'aldot-algo-traffic', 'ALGO Traffic', 'aldot-algo-traffic-v1'],
    [CITY_SOURCE_ID, 'coa-road-closures', 'City of Auburn Road Closures', 'coa-road-closures-v1'],
  ]) {
    await database.query(
      `INSERT INTO sources (source_id, owner_agency_id, code, name, source_type, schema_version,
                            stale_after_seconds, permitted_use, connector_code)
       VALUES ($1,$2,$3,$4,'api','v1',300,'Public',$5)`,
      [sourceId, ALDOT_AGENCY_ID, code, name, connectorCode],
    );
  }
}

/** Mirrors the connector store: a known upstream record is updated in place, keeping its id. */
async function upsertEvidence(
  evidenceId: string,
  sourceId: string,
  sourceEventId: string,
  summary: string,
  attributes: Record<string, unknown>,
  observedAtOffsetMinutes = -5,
  receivedAtOffsetMinutes = -1,
): Promise<void> {
  const contentHash = createHash('sha256').update(JSON.stringify(attributes)).digest('hex');
  await database.query(
    `INSERT INTO evidence_events (
       evidence_id, event_id, source_id, source_event_id, schema_version, observed_at, received_at,
       summary, normalized_attributes, content_hash, quality_flags
     ) VALUES ($1,$2,$3,$4,'test-v1', now() + ($5 || ' minutes')::interval,
               now() + ($9 || ' minutes')::interval, $6, $7::jsonb, $8, ARRAY[]::text[])
     ON CONFLICT (source_id, source_event_id) DO UPDATE SET
       summary = EXCLUDED.summary,
       normalized_attributes = EXCLUDED.normalized_attributes,
       content_hash = EXCLUDED.content_hash,
       observed_at = EXCLUDED.observed_at,
       received_at = EXCLUDED.received_at,
       version = evidence_events.version + 1`,
    [
      evidenceId, EVENT_ID, sourceId, sourceEventId, String(observedAtOffsetMinutes),
      summary, JSON.stringify(attributes), contentHash, String(receivedAtOffsetMinutes),
    ],
  );
}

const crashAttributes = {
  layer: 'traffic_event',
  algoEventId: 2357202,
  eventType: 'Crash',
  title: 'Crash blocking the right lane',
  subtitle: 'I-85 NB near Exit 51',
  route: 'I-85 NB',
};

beforeEach(async () => {
  database = new PGlite();
  await database.exec(await migrationSql());
  await seedSources();
});

afterEach(async () => {
  await database.close();
});

async function countRows(table: string, where = 'true', values: unknown[] = []): Promise<number> {
  const result = await database.query<{ count: string }>(
    `SELECT count(*)::text AS count FROM ${table} WHERE ${where}`,
    values as never[],
  );
  return Number(result.rows[0].count);
}

describe('detection projection', () => {
  it('opens nothing when no evidence qualifies', async () => {
    await upsertEvidence('11111111-1111-4111-8111-111111111aa1', ALGO_SOURCE_ID, 'travel-time:7', 'I-85 Auburn: 68 mph', {
      layer: 'travel_time', algoTravelTimeId: 7, name: 'I-85 Auburn', congestionLevel: 'Unaffected',
    });

    const summary = await runDetection(EVENT_ID, detectionStore);

    expect(summary.packCode).toBe('sec_gameday');
    expect(summary.evidenceConsidered).toBe(1);
    expect(summary.incidentsOpened).toBe(0);
    expect(await countRows('incidents')).toBe(0);
    expect(await countRows('recommendations')).toBe(0);
  }, TIMEOUT_MS);

  it('opens one incident and one recommendation per upstream record', async () => {
    await upsertEvidence('11111111-1111-4111-8111-111111111ab1', ALGO_SOURCE_ID, 'event:Crash:2357202', 'Crash: I-85 NB', crashAttributes);
    await upsertEvidence('11111111-1111-4111-8111-111111111ab2', CITY_SOURCE_ID, 'closure:abc', 'Closure: Donahue Drive', {
      kind: 'closure', road: 'Donahue Drive', description: 'Utility work', startsAt: null, endsAt: null,
    });

    const summary = await runDetection(EVENT_ID, detectionStore);

    expect(summary.incidentsOpened).toBe(2);
    expect(summary.recommendationsOpened).toBe(2);

    const incidents = await database.query<{ severity: string; origin_external_key: string; detection_rule_code: string; scenario_pack_code: string }>(
      'SELECT severity, origin_external_key, detection_rule_code, scenario_pack_code FROM incidents ORDER BY severity',
    );
    expect(incidents.rows.map(row => row.origin_external_key).sort()).toEqual(['algo-event:2357202', 'city-closure:closure:abc']);
    expect(incidents.rows.find(row => row.detection_rule_code === 'algo-crash')?.severity).toBe('high');
    expect(incidents.rows.every(row => row.scenario_pack_code === 'sec_gameday')).toBe(true);

    // The playbook resolves agency codes to real agencies so approval creates real commitments.
    const plans = await database.query<{ commitment_plan: Array<Record<string, unknown>> }>('SELECT commitment_plan FROM recommendations');
    for (const row of plans.rows) {
      expect(row.commitment_plan.length).toBeGreaterThan(0);
      for (const plan of row.commitment_plan) {
        expect(plan.ownerAgencyId).toBeTruthy();
        expect(plan.requestedOutcome).toBeTruthy();
      }
    }
    expect(await countRows('approval_requirements')).toBe(2);
    expect(await countRows('agent_findings')).toBe(2);
  }, TIMEOUT_MS);

  it('evaluates a long-standing restriction that the connector still confirms upstream', async () => {
    // The City last edited this record months ago; it is still published and still in force.
    await upsertEvidence(
      '11111111-1111-4111-8111-111111111ca1', CITY_SOURCE_ID, 'closure:hwy14', 'Closure: Hwy 14',
      { kind: 'closure', road: 'Martin Luther King Dr.', description: 'Resurfacing', startsAt: null, endsAt: null },
      -60 * 24 * 120, -2,
    );

    const summary = await runDetection(EVENT_ID, detectionStore);

    expect(summary.evidenceConsidered).toBe(1);
    expect(summary.incidentsOpened).toBe(1);
  }, TIMEOUT_MS);

  it('stops counting a record the connector no longer sees upstream', async () => {
    await upsertEvidence('11111111-1111-4111-8111-111111111cb1', ALGO_SOURCE_ID, 'event:Crash:2357202', 'Crash: I-85 NB', crashAttributes);
    await runDetection(EVENT_ID, detectionStore);

    // The crash was withdrawn from the feed, so its row is no longer being re-confirmed, while
    // the connector keeps reporting other records.
    await database.query(`UPDATE evidence_events SET received_at = now() - interval '9 hours'`);
    await upsertEvidence('11111111-1111-4111-8111-111111111cb2', ALGO_SOURCE_ID, 'travel-time:7', 'I-85 Auburn: 68 mph', {
      layer: 'travel_time', algoTravelTimeId: 7, name: 'I-85 Auburn', congestionLevel: 'Unaffected',
    });

    const summary = await runDetection(EVENT_ID, detectionStore);

    expect(summary.incidentsResolved).toBe(1);
    expect(await countRows('incidents', "status = 'resolved'")).toBe(1);
  }, TIMEOUT_MS);

  it('is idempotent across repeated passes over the same upstream record', async () => {
    await upsertEvidence('11111111-1111-4111-8111-111111111ac1', ALGO_SOURCE_ID, 'event:Crash:2357202', 'Crash: I-85 NB', crashAttributes);

    await runDetection(EVENT_ID, detectionStore);
    const second = await runDetection(EVENT_ID, detectionStore);

    expect(second.incidentsOpened).toBe(0);
    expect(second.incidentsUpdated).toBe(1);
    expect(await countRows('incidents')).toBe(1);
    expect(await countRows('recommendations')).toBe(1);
    expect(await countRows('agent_findings')).toBe(1);
  }, TIMEOUT_MS);

  it('resolves an incident when the upstream record clears but the feed is still reporting', async () => {
    await upsertEvidence('11111111-1111-4111-8111-111111111ad1', ALGO_SOURCE_ID, 'event:Crash:2357202', 'Crash: I-85 NB', crashAttributes);
    await runDetection(EVENT_ID, detectionStore);

    await database.query('DELETE FROM incident_evidence');
    await database.query('DELETE FROM recommendation_evidence');
    await database.query('DELETE FROM evidence_events');
    await upsertEvidence('11111111-1111-4111-8111-111111111ad2', ALGO_SOURCE_ID, 'travel-time:7', 'I-85 Auburn: 68 mph', {
      layer: 'travel_time', algoTravelTimeId: 7, name: 'I-85 Auburn', congestionLevel: 'Unaffected',
    });

    const summary = await runDetection(EVENT_ID, detectionStore);

    expect(summary.incidentsResolved).toBe(1);
    expect(await countRows('incidents', "status = 'resolved'")).toBe(1);
    expect(await countRows('recommendations', "state = 'expired'")).toBe(1);
  }, TIMEOUT_MS);

  it('does not resolve an incident when its connector reported nothing at all', async () => {
    await upsertEvidence('11111111-1111-4111-8111-111111111ae1', ALGO_SOURCE_ID, 'event:Crash:2357202', 'Crash: I-85 NB', crashAttributes);
    await runDetection(EVENT_ID, detectionStore);

    await database.query('DELETE FROM incident_evidence');
    await database.query('DELETE FROM recommendation_evidence');
    await database.query('DELETE FROM evidence_events');
    await upsertEvidence('11111111-1111-4111-8111-111111111ae2', CITY_SOURCE_ID, 'closure:zzz', 'Closure: Wire Road', {
      kind: 'closure', road: 'Wire Road', description: 'Paving', startsAt: null, endsAt: null,
    });

    const summary = await runDetection(EVENT_ID, detectionStore);

    expect(summary.incidentsResolved).toBe(0);
    expect(await countRows('incidents', "status = 'active' AND detection_rule_code = 'algo-crash'")).toBe(1);
  }, TIMEOUT_MS);

  it('supersedes a decided recommendation instead of rewriting it when evidence changes', async () => {
    await upsertEvidence('11111111-1111-4111-8111-111111111af1', ALGO_SOURCE_ID, 'event:Crash:2357202', 'Crash: I-85 NB', crashAttributes);
    await runDetection(EVENT_ID, detectionStore);
    await database.query(`UPDATE recommendations SET state = 'approved'`);

    // The connector rewrites the same evidence row when ALDOT revises the record.
    await upsertEvidence(
      '11111111-1111-4111-8111-111111111af1', ALGO_SOURCE_ID, 'event:Crash:2357202',
      'Crash: I-85 NB update', { ...crashAttributes, subtitle: 'All lanes reopened' }, -1,
    );
    await runDetection(EVENT_ID, detectionStore);

    const versions = await database.query<{ recommendation_version: number; state: string; superseded_by_recommendation_id: string | null }>(
      'SELECT recommendation_version, state, superseded_by_recommendation_id FROM recommendations ORDER BY recommendation_version',
    );
    expect(versions.rows).toHaveLength(2);
    expect(versions.rows[0].state).toBe('approved');
    expect(versions.rows[0].superseded_by_recommendation_id).toBeTruthy();
    expect(versions.rows[1].state).toBe('awaiting_approval');
  }, TIMEOUT_MS);

  it('leaves an incident alone once a human has closed it', async () => {
    await upsertEvidence('11111111-1111-4111-8111-111111111ba1', ALGO_SOURCE_ID, 'event:Crash:2357202', 'Crash: I-85 NB', crashAttributes);
    await runDetection(EVENT_ID, detectionStore);
    await database.query(`UPDATE incidents SET status = 'closed'`);

    const summary = await runDetection(EVENT_ID, detectionStore);

    expect(summary.incidentsOpened).toBe(0);
    expect(summary.incidentsUpdated).toBe(0);
    expect(await countRows('incidents', "status = 'closed'")).toBe(1);
  }, TIMEOUT_MS);

  it('evaluates only the connectors the active scenario pack reads', async () => {
    await database.query(`UPDATE operational_events SET scenario_pack_code = 'cyber_incident' WHERE event_id = $1`, [EVENT_ID]);
    await upsertEvidence('11111111-1111-4111-8111-111111111bb1', ALGO_SOURCE_ID, 'event:Crash:2357202', 'Crash: I-85 NB', crashAttributes);

    const summary = await runDetection(EVENT_ID, detectionStore);

    expect(summary.packCode).toBe('cyber_incident');
    expect(summary.evidenceConsidered).toBe(0);
    expect(await countRows('incidents')).toBe(0);
  }, TIMEOUT_MS);
});
